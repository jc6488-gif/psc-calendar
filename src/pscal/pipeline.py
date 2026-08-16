"""Orchestration: scrape every commission, normalise, dedupe, emit."""
from __future__ import annotations

import argparse
import re
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import classify, emit_ics, emit_site
from .extract import extract
from .models import Event, ScrapeResult, extract_dockets

log = logging.getLogger("pscal")

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"


def scrape_commission(spec: dict, now: datetime) -> ScrapeResult:
    """Scrape EVERY configured source and merge the results.

    An earlier version stopped at the first source that returned anything, which
    silently capped a commission at whatever its thinnest page happened to list -
    e.g. taking 2 items off an RSS summary and never reading the 40-entry month
    calendar next to it. Commissions routinely split open meetings, evidentiary
    hearings and local public hearings across separate pages, so the union of all
    sources is the only way to get complete coverage. Duplicates are collapsed
    downstream by Event.dedupe_key.
    """
    code = spec["code"]
    tzname = spec.get("timezone", "America/New_York")
    started = time.monotonic()
    errors: list[str] = []
    all_events: list[Event] = []
    used_strategies: list[str] = []
    used_urls: list[str] = []

    for source in spec.get("sources", []):
        url = source["url"]
        strategy = source.get("strategy", "auto")
        try:
            raws, used = extract(url, strategy, tzname, now)
        except Exception as e:
            errors.append(f"{url} [{strategy}] -> {type(e).__name__}: {e}")
            log.debug("%s: %s failed: %s", code, url, e)
            continue

        label = source.get("label", "")
        events: list[Event] = []
        for r in raws:
            title = classify.clean_title((r.get("title") or "").strip())
            if classify.is_uninformative(title):
                # Keep a date fragment when there is one - "Weekly hearing
                # schedule (PDF): 8/17/26 to 8/21/26" - but a bare day number
                # ("23 Minutes") adds nothing; the event date already shows.
                fallback = label or "Open meeting"
                has_date = re.search(r"\d{1,2}[/.-]\d{1,2}|\b20\d\d\b", title)
                title = f"{fallback}: {title}" if has_date else fallback
            if classify.is_noise(title):
                continue
            desc = r.get("description", "") or ""
            blob = f"{title} {desc}"

            etype, elabel, weight, relevance = classify.classify_type(blob)
            if etype == "other" and source.get("type_hint"):
                # The source itself knows what it serves (MA's API is all
                # hearings; RRC's ICS is the hearings calendar) even when
                # titles carry no type words.
                etype, elabel, weight, relevance = classify.type_info(source["type_hint"])

            # Never emit a link we can't stand behind. A relative or malformed
            # href becomes a dead "page not found" in the dashboard, which is
            # worse than no link - fall back to the page we scraped it from.
            link = (r.get("url") or "").strip()
            if not link.startswith(("http://", "https://")):
                link = url

            events.append(Event(
                commission=code,
                commission_name=spec["name"],
                state=spec["state"],
                tz=tzname,
                title=title,
                start=r["start"],
                end=r.get("end"),
                all_day=bool(r.get("all_day")),
                location=r.get("location", ""),
                description=desc,
                url=link,
                source_url=url,
                dockets=extract_dockets(title, desc),
                event_type=etype,
                event_type_label=elabel,
                relevance=relevance,
                weight=weight,
                scraped_at=now.isoformat(),
            ))

        if events:
            all_events.extend(events)
            used_strategies.append(f"{used}({len(events)})")
            used_urls.append(url)
        else:
            errors.append(f"{url} [{used}] -> parsed 0 usable events")

    if all_events:
        merged = dedupe(all_events)
        return ScrapeResult(
            commission=code, commission_name=spec["name"], tier=spec.get("tier", "full"),
            ok=True, events=merged, strategy_used=" + ".join(used_strategies),
            source_url=used_urls[0],
            error="; ".join(errors[:2]),   # partial failures stay visible
            duration_s=time.monotonic() - started,
        )

    return ScrapeResult(
        commission=code, commission_name=spec["name"], tier=spec.get("tier", "full"),
        ok=False, error=" ;; ".join(errors[:3]) or "no sources configured",
        duration_s=time.monotonic() - started,
    )


def dedupe(events: list[Event]) -> list[Event]:
    """Collapse the same meeting scraped from two sources, preferring the
    richer record (more dockets, longer description, a real URL)."""
    best: dict[tuple, Event] = {}
    for e in events:
        k = e.dedupe_key
        cur = best.get(k)
        if cur is None:
            best[k] = e
            continue
        # A record with a real clock time beats a date-only one - "9:30 AM"
        # from the scheduler must not be clobbered by a dateline mention
        # (which parses to midnight without carrying the all_day flag).
        # Timedness outranks docket count because dockets are unioned from
        # both records below, while start time comes only from the winner.
        def _timed(x: Event) -> bool:
            return not x.all_day and (x.start.hour, x.start.minute) != (0, 0)
        score_new = (_timed(e), len(e.dockets), len(e.description), bool(e.url))
        score_cur = (_timed(cur), len(cur.dockets), len(cur.description), bool(cur.url))
        if score_new > score_cur:
            e.dockets = sorted(set(e.dockets) | set(cur.dockets))
            best[k] = e
        else:
            cur.dockets = sorted(set(cur.dockets) | set(e.dockets))
    return sorted(best.values(), key=lambda x: (x.start, x.commission))


def run(only: list[str] | None = None, workers: int = 6, no_cache: bool = False) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", stream=sys.stderr
    )
    if no_cache:
        import os
        os.environ["PSCAL_CACHE_TTL"] = "0"

    now = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
    specs = classify.load_commissions()
    if only:
        wanted = {c.upper() for c in only}
        specs = [s for s in specs if s["code"].upper() in wanted]

    log.info("scraping %d commissions with %d workers", len(specs), workers)
    results: list[ScrapeResult] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(scrape_commission, s, now): s for s in specs}
        for fut in as_completed(futures):
            spec = futures[fut]
            try:
                res = fut.result()
            except Exception as e:
                res = ScrapeResult(
                    commission=spec["code"], commission_name=spec["name"],
                    tier=spec.get("tier", "full"), ok=False, error=f"{type(e).__name__}: {e}",
                )
            results.append(res)
            mark = "OK " if res.ok else "FAIL"
            log.info("%-6s %-4s %3d events  [%s]%s", res.commission, mark,
                     len(res.events), res.strategy_used or "-",
                     "" if res.ok else f"  {res.error[:110]}")

    results.sort(key=lambda r: r.commission)
    events = dedupe([e for r in results for e in r.events])

    core = [r for r in results if r.tier == "core"]
    core_ok = sum(1 for r in core if r.ok)
    log.info("-" * 70)
    log.info("commissions OK: %d/%d  (core %d/%d)",
             sum(1 for r in results if r.ok), len(results), core_ok, len(core))
    log.info("events: %d total", len(events))

    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "feeds").mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at": now.isoformat(),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "event_count": len(events),
        "commissions": [r.to_dict() for r in results],
        "events": [e.to_dict() for e in events],
    }
    (DOCS / "events.json").write_text(json.dumps(payload, indent=1))
    emit_ics.write_all(events, DOCS / "feeds", now)
    emit_site.write_site(payload, DOCS, now)

    log.info("wrote %s", DOCS)

    # Exit non-zero only if core coverage collapses, so a single flaky state
    # doesn't turn the daily run red.
    if core and core_ok / len(core) < 0.5:
        log.error("core commission success rate below 50%% - failing the run")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the utility regulatory calendar.")
    ap.add_argument("--only", nargs="*", help="Limit to these commission codes (e.g. OH TX CA)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()
    return run(only=a.only, workers=a.workers, no_cache=a.no_cache)


if __name__ == "__main__":
    raise SystemExit(main())
