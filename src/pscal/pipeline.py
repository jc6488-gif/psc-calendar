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
    dropped_types: dict[str, int] = {}
    all_events: list[Event] = []
    used_strategies: list[str] = []
    used_urls: list[str] = []

    for source in spec.get("sources", []):
        url = source["url"]
        strategy = source.get("strategy", "auto")
        try:
            raws, used = extract(url, strategy, tzname, now, source.get("wait_for"))
        except Exception as e:
            errors.append(f"{url} [{strategy}] -> {type(e).__name__}: {e}")
            log.debug("%s: %s failed: %s", code, url, e)
            continue

        label = source.get("label", "")
        events: list[Event] = []
        for r in raws:
            title = classify.clean_title((r.get("title") or "").strip())
            # A title like "09:00 am - REGULAR MEETING" on a row that carried
            # no machine-readable time is the only copy of that time we get.
            # Trust it only when the event really has none of its own.
            title, tod = classify.split_leading_time(title)
            timeless = bool(r.get("all_day")) or (
                r["start"].hour == 0 and r["start"].minute == 0)
            if tod and timeless:
                r["start"] = r["start"].replace(hour=tod[0], minute=tod[1])
                r["all_day"] = False
                r["end"] = None
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

            etype, elabel, weight, relevance = classify.classify_event(title, desc)
            if etype == "other" and source.get("type_hint"):
                # The source itself knows what it serves (MA's API is all
                # hearings; RRC's ICS is the hearings calendar) even when
                # titles carry no type words.
                etype, elabel, weight, relevance = classify.type_info(source["type_hint"])
            if not classify.is_published(etype):
                dropped_types[elabel] = dropped_types.get(elabel, 0) + 1
                continue
            if classify.is_out_of_sector(title, etype):
                dropped_types["Out of sector (water/telecom/transport)"] = \
                    dropped_types.get("Out of sector (water/telecom/transport)", 0) + 1
                continue

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
            if dropped_types:
                errors.append(f"{url} [{used}] -> all {sum(dropped_types.values())} "
                              f"events excluded by current type/sector settings")
            else:
                errors.append(f"{url} [{used}] -> parsed 0 usable events")

    if all_events:
        merged = dedupe(all_events)
        return ScrapeResult(
            commission=code, commission_name=spec["name"], tier=spec.get("tier", "full"),
            ok=True, events=merged, strategy_used=" + ".join(used_strategies),
            dropped=dict(dropped_types),
            source_url=used_urls[0],
            error="; ".join(errors[:2]),   # partial failures stay visible
            duration_s=time.monotonic() - started,
        )

    # A commission whose pages scraped cleanly but whose every event was
    # excluded by the desk's type/sector settings is NOT a broken scraper.
    # Showing it the same red as a 403 would teach her to distrust the panel.
    hard_errors = [e for e in errors if "excluded by current" not in e]
    filtered_only = bool(dropped_types) and not hard_errors
    if dropped_types:
        # Lead with the filtering fact - it is the dominant reason this
        # commission is empty, and it is a settings choice, not a breakage.
        msg = (f"scraped fine - all {sum(dropped_types.values())} events are types "
               f"you excluded ({', '.join(sorted(dropped_types))})")
        if hard_errors:
            msg += f" ;; ALSO {len(hard_errors)} source(s) failed: " + hard_errors[0][:160]
    else:
        msg = " ;; ".join(errors[:3]) or "no sources configured"
    return ScrapeResult(
        commission=code, commission_name=spec["name"], tier=spec.get("tier", "full"),
        ok=False, filtered_only=filtered_only, error=msg,
        dropped=dict(dropped_types),
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
    merged = sorted(best.values(), key=lambda x: (x.start, x.commission))

    # A generic fallback title ("Open meeting") adds nothing on a day when the
    # same commission already has a specific event - it is the same meeting
    # under a poorer name.
    GENERIC = {"open meeting", "commission meeting", "meeting", "hearing",
               "administrative session", "agenda meeting", "regular meeting",
               "conference", "commission conference", "open meeting conference",
               "all commissioners"}
    by_day: dict[tuple, list[Event]] = {}
    for e in merged:
        by_day.setdefault((e.commission, e.start.date()), []).append(e)

    def _rank(x: Event) -> tuple:
        timed = not x.all_day and (x.start.hour, x.start.minute) != (0, 0)
        return (timed, len(x.dockets), len(x.description), bool(x.url), len(x.title))

    drop: set[int] = set()
    for group in by_day.values():
        if len(group) < 2:
            continue
        generic = [e for e in group if e.title.strip().lower() in GENERIC]
        if not generic:
            continue
        if len(generic) < len(group):
            # a specific event exists that day - the generic ones are the
            # same meeting under a poorer name
            drop.update(id(e) for e in generic)
        else:
            # every variant is generic: they are one meeting, keep the richest
            keep = max(generic, key=_rank)
            drop.update(id(e) for e in generic if e is not keep)
    # --- second pass: the same meeting described two ways -------------------
    # Two sources rarely word a meeting identically. Tennessee posts "TPUC
    # Commission Conference" and "Notice and Agenda for Commission
    # Conference"; the RRC's ICS says "CONFERENCE" where its web page says
    # "RRC open meeting (conference)". After stripping filler, one title
    # contains the other - that is one meeting, not two.
    #
    # NOTE: we deliberately do NOT merge on (commission, date, time) alone.
    # 120 events currently share an exact commission+datetime and most are
    # genuinely distinct - Indiana runs four different hearings at 09:30.
    _FILLER = re.compile(r"\b(?:notice|notices|agenda|agendas|and|for|of|the|a|an|"
                         r"remote|virtual|in|re|no|nos|pro|url|location|summary|notes)\b")

    def _norm(t: str) -> str:
        t = t.lower()
        t = re.split(r"\burl\s*:", t)[0]           # SC appends a livestream URL
        t = _FILLER.sub(" ", t)
        t = re.sub(r"[^a-z0-9]+", " ", t)
        return " ".join(t.split())

    for group in by_day.values():
        alive = [e for e in group if id(e) not in drop]
        for i in range(len(alive)):
            a = alive[i]
            if id(a) in drop:
                continue
            na = _norm(a.title)
            if not na:
                continue
            for j in range(i + 1, len(alive)):
                b = alive[j]
                if id(b) in drop:
                    continue
                nb = _norm(b.title)
                if not nb:
                    continue
                if na == nb or na in nb or nb in na:
                    loser = a if _rank(a) < _rank(b) else b
                    winner = b if loser is a else a
                    winner.dockets = sorted(set(winner.dockets) | set(loser.dockets))
                    drop.add(id(loser))
                    if loser is a:
                        break
    return [e for e in merged if id(e) not in drop]


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

    # Static jurisdiction map: which covered names each commission regulates.
    # This is corporate geography, not attribution - an MO open meeting can
    # only ever touch the MO names. Zero maintenance until M&A.
    coverage_map: dict[str, list[str]] = {}
    for c in classify.load_coverage()["companies"]:
        for sub in c.get("subsidiaries", []) or []:
            for code_ in sub.get("commissions", []) or []:
                coverage_map.setdefault(code_, [])
                if c["ticker"] not in coverage_map[code_]:
                    coverage_map[code_].append(c["ticker"])
    coverage_map = {k: sorted(v) for k, v in coverage_map.items()}

    payload = {
        "coverage_map": coverage_map,
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
