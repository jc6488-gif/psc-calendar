"""Audit every link the dashboard offers: does it resolve, and is the event on it?

A link that 404s is obvious. The failure that actually cost trust was subtler -
a live, plausible page that simply does not contain the hearing it is attached
to (Louisiana's docket hearings pointed at a monthly-sessions page). So this
checks BOTH:

    reachable  - the URL answers, in a plain fetch or a real browser
    relevant   - the destination mentions the event's docket, or enough of its
                 title, to be worth clicking

Some commissions (MI, FERC, NC) 403 every scripted client but serve browsers
fine, so a plain-fetch failure is retried with Chromium before being called
dead. Run after a build:

    python3 tools/audit_links.py                # audit docs/events.json
    python3 tools/audit_links.py --live         # audit the published site
    python3 tools/audit_links.py --only FL LA   # a few commissions
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.pscal import classify, extract, fetch  # noqa: E402

LIVE_URL = "https://stellacc888.github.io/psc-calendar/events.json"
G, R, Y, B, D = "\033[32m", "\033[31m", "\033[33m", "\033[34m", "\033[0m"

# Words that say nothing about which event a page is about.
_STOP = {
    "the", "and", "for", "of", "a", "an", "in", "on", "to", "by", "with", "at",
    "hearing", "hearings", "meeting", "meetings", "commission", "conference",
    "docket", "case", "no", "nos", "open", "public", "session", "sessions",
    "agenda", "notice", "service", "day", "virtual", "immediately", "following",
    "canceled", "cancelled", "utility", "utilities", "company", "llc", "inc",
    # Dates say nothing about WHICH page is right - every calendar names them.
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "june", "july", "august",
    "september", "october", "november", "december",
    "remote", "internet", "broadcast", "formal", "regular", "special",
}


def _keywords(title: str) -> list[str]:
    words = re.findall(r"[A-Za-z]{4,}", (title or "").lower())
    return [w for w in words if w not in _STOP]


def load_events(args) -> list[dict]:
    if args.live:
        with urllib.request.urlopen(LIVE_URL, timeout=60) as r:
            d = json.load(r)
    else:
        d = json.loads((ROOT / "docs" / "events.json").read_text())
    ev = d["events"]
    if args.only:
        ev = [e for e in ev if e["commission"] in args.only]
    return ev


def body_of(url: str) -> tuple[str, str]:
    """Return (text, how). Falls back to a real browser on a scripted refusal."""
    try:
        raw, ct = fetch.get(url, use_cache=False, timeout=30)
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"[:90]
        try:
            html = extract.render_with_browser(url)
            return html, "browser"
        except Exception:
            return "", f"DEAD {msg}"
    if "pdf" in (ct or "") or url.lower().endswith(".pdf"):
        try:
            text = extract.pdf_text(raw)
        except Exception:
            return "", "DEAD unreadable pdf"
        # A scanned agenda has no text layer. The PDF opens and a person can
        # read it; we cannot, and saying "wrong link" on that basis would be
        # the audit lying. North Dakota publishes all of its agendas this way.
        if not text.strip():
            return "", "SCAN"
        return text, "pdf"
    text = raw.decode("utf-8", "replace")
    # An SPA shell has markup but no content; a real browser will fill it in.
    if len(re.sub(r"<[^>]+>", " ", text).split()) < 60:
        try:
            return extract.render_with_browser(url), "browser"
        except Exception:
            return text, "fetch"
    return text, "fetch"


def audit(events: list[dict]) -> list[dict]:
    by_url: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        by_url[e["url"]].append(e)

    def _checkable(e: dict) -> bool:
        """Whether this event gives us anything to look for. "Open Meeting"
        on a commission's own event page yields no docket and no distinctive
        word - the check would be vacuous, and reporting it as a bad link
        would be the audit lying rather than the link being wrong."""
        return bool(e.get("dockets")) or bool(_keywords(e["title"]))

    def _misses(text: str, events: list[dict]) -> list[dict]:
        flat = re.sub(r"<[^>]+>", " ", text).lower()
        flat = re.sub(r"\s+", " ", flat)
        out = []
        for e in events:
            if not _checkable(e):
                continue
            dockets = [d.lower() for d in e.get("dockets") or []]
            bases = {d.split("-")[0] for d in dockets} | set(dockets)
            if bases and any(b in flat for b in bases if len(b) >= 4):
                continue
            kw = _keywords(e["title"])
            if kw and sum(1 for w in kw if w in flat) >= max(1, len(kw) // 3):
                continue
            out.append(e)
        return out

    def check(url: str) -> dict:
        events_here = by_url[url]
        text, how = body_of(url)
        row = {"url": url, "how": how, "events": len(events_here)}
        if how.startswith("DEAD"):
            row["verdict"] = "DEAD"
            row["misses"] = events_here
            return row
        if how == "SCAN":
            row["verdict"] = "NOCHECK"
            row["misses"] = []
            row["note"] = "PDF opens but has no text layer (scanned) - unreadable here"
            return row

        misses = _misses(text, events_here)
        # Escalate before accusing. Most commission calendars build themselves
        # with JavaScript, so a plain fetch sees navigation chrome and none of
        # the events - which looks exactly like a wrong page.
        if misses and how != "browser":
            try:
                rendered = extract.render_with_browser(url)
                better = _misses(rendered, events_here)
                if len(better) < len(misses):
                    misses, text, how = better, rendered, "browser"
            except Exception:
                pass

        # A calendar embedded from another origin (Google Calendar, Granicus)
        # renders in an iframe whose text never joins the parent document. The
        # page is right; we simply cannot read it from here.
        if misses and re.search(r"<iframe[^>]+(?:calendar\.google|granicus|"
                                r"trumba|legistar)", text, re.I):
            row["verdict"] = "IFRAME"
            row["misses"] = []
            row["note"] = f"{len(misses)} event(s) live inside a cross-origin calendar embed"
            return row

        checkable = [e for e in events_here if _checkable(e)]
        if not checkable:
            row["verdict"] = "NOCHECK"
            row["misses"] = []
            row["note"] = "page loads; titles carry nothing specific to look for"
            return row
        row["verdict"] = "OK" if not misses else (
            "MIXED" if len(misses) < len(checkable) else "UNRELATED")
        row["misses"] = misses
        return row

    with ThreadPoolExecutor(max_workers=6) as ex:
        return list(ex.map(check, sorted(by_url)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true", help="audit the published site")
    ap.add_argument("--only", nargs="*", help="limit to these commission codes")
    ap.add_argument("--quiet", action="store_true", help="only show problems")
    args = ap.parse_args()

    events = load_events(args)
    rows = audit(events)
    rows.sort(key=lambda r: (r["verdict"] == "OK", -r["events"]))

    dead = [r for r in rows if r["verdict"] == "DEAD"]
    unrel = [r for r in rows if r["verdict"] == "UNRELATED"]
    mixed = [r for r in rows if r["verdict"] == "MIXED"]
    iframe = [r for r in rows if r["verdict"] == "IFRAME"]
    nochk = [r for r in rows if r["verdict"] == "NOCHECK"]
    ok = [r for r in rows if r["verdict"] == "OK"]

    for r in rows:
        if args.quiet and r["verdict"] in ("OK", "IFRAME", "NOCHECK"):
            continue
        colour = {"DEAD": R, "UNRELATED": R, "MIXED": Y, "IFRAME": B, "NOCHECK": B, "OK": G}[r["verdict"]]
        print(f"{colour}{r['verdict']:9}{D} {r['events']:4d} ev  [{r['how'][:7]:7}] {r['url'][:96]}")
        if r.get("note"):
            print(f"              {B}{r['note']}{D}")
        for e in (r.get("misses") or [])[:4]:
            print(f"              {B}not found on page:{D} {e['commission']} "
                  f"{e['start'][:10]} {e['title'][:66]}")

    n = len(rows)
    bad_events = sum(len(r.get("misses") or []) for r in rows) + \
        sum(r["events"] for r in dead)
    print(f"\n{n} distinct links | OK {len(ok)} · IFRAME {len(iframe)} · "
          f"NOCHECK {len(nochk)} · MIXED {len(mixed)} · UNRELATED {len(unrel)} · "
          f"DEAD {len(dead)}")
    print(f"{bad_events} of {len(events)} events have a link that cannot be verified")
    return 1 if (dead or unrel) else 0


if __name__ == "__main__":
    raise SystemExit(main())
