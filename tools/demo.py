"""Build the dashboard from test fixtures - no network required.

Useful for previewing layout changes, and for confirming the site renders
before you trust a live scrape. Writes to docs-demo/.

    python3 tools/demo.py && open docs-demo/index.html
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.pscal import classify, emit_ics, emit_site, extract
from src.pscal.models import Event, ScrapeResult, extract_dockets
from src.pscal.pipeline import dedupe
from tests import fixtures as F

TZ = ZoneInfo("America/New_York")
NOW = datetime.now(TZ)

SOURCES = [
    ("IN", "Indiana Utility Regulatory Commission", "IN", "America/Indiana/Indianapolis",
     lambda tz: extract.from_ics(F.ICS_TRUMBA, tz, NOW, "https://calendar.in.gov/site/iurc"), "ics"),
    ("TX", "Public Utility Commission of Texas", "TX", "America/Chicago",
     lambda tz: extract.from_rss(F.RSS_PUCT, tz, NOW, "https://www.puc.texas.gov/"), "rss"),
    ("NY", "New York Public Service Commission", "NY", "America/New_York",
     lambda tz: extract.from_html_cards(F.HTML_DRUPAL, tz, NOW, "https://dps.ny.gov/calendar"), "html_cards"),
    ("CA", "California Public Utilities Commission", "CA", "America/Los_Angeles",
     lambda tz: extract.from_jsonld(F.HTML_JSONLD, tz, NOW, "https://www.cpuc.ca.gov/"), "jsonld"),
    ("MO", "Missouri Public Service Commission", "MO", "America/Chicago",
     lambda tz: extract.from_html_table(F.HTML_ASPX_TBL, tz, NOW, "https://psc.mo.gov/"), "html_table"),
    ("WV", "West Virginia Public Service Commission", "WV", "America/New_York",
     lambda tz: extract.from_date_regex(F.HTML_LOOSE, tz, NOW, "https://www.psc.state.wv.us/"), "date_regex"),
]


def shift(dt: datetime) -> datetime:
    """Fixtures are pinned to autumn 2026; nudge them into the live window so the
    12-week chart and the default 90-day filter have something to show."""
    target = NOW + timedelta(days=(dt.timetuple().tm_yday % 70) + 3)
    return target.replace(hour=dt.hour or 10, minute=dt.minute, second=0, microsecond=0)


def main() -> int:
    results, events = [], []
    for code, name, state, tzname, fn, strat in SOURCES:
        raws = fn(ZoneInfo(tzname))
        evs = []
        for r in raws:
            blob = f"{r.get('title','')} {r.get('description','')}"
            tk, subs = classify.match_companies(blob, code)
            et, el, w = classify.classify_type(blob)
            rc, sig = classify.detect_rate_case(blob)
            evs.append(Event(
                commission=code, commission_name=name, state=state, tz=tzname,
                title=r["title"], start=shift(r["start"]), all_day=bool(r.get("all_day")),
                location=r.get("location", ""), description=r.get("description", ""),
                url=r.get("url", ""), source_url="",
                dockets=extract_dockets(r.get("title"), r.get("description")),
                tickers=tk, subsidiaries=subs, event_type=et, event_type_label=el,
                weight=w + (1 if tk else 0) + (1 if rc else 0),
                rate_case=rc, rate_case_signals=sig, scraped_at=NOW.isoformat(),
            ))
        events += evs
        results.append(ScrapeResult(code, name, "core", True, evs, strat, "fixture", "", 0.4))

    # a couple of failures so the health panel shows all three states
    results.append(ScrapeResult("AK", "Regulatory Commission of Alaska", "full", False,
                                error="HTTP 403"))
    results.append(ScrapeResult("HI", "Hawaii Public Utilities Commission", "core", False,
                                error="all strategies failed"))

    events = dedupe(events)
    results.sort(key=lambda r: r.commission)
    roster = [
        {"ticker": c["ticker"], "name": c["name"], "sector": c.get("sector", ""),
         "commissions": sorted({k for sub in c.get("subsidiaries", [])
                                for k in sub.get("commissions", [])})}
        for c in classify.load_coverage()["companies"]
    ]
    payload = {
        "roster": roster,
        "generated_at": NOW.isoformat(),
        "generated_at_utc": NOW.isoformat(),
        "event_count": len(events),
        "covered_event_count": sum(1 for e in events if e.tickers),
        "commissions": [r.to_dict() for r in results],
        "events": [e.to_dict() for e in events],
    }
    out = ROOT / "docs-demo"
    emit_site.write_site(payload, out, NOW)
    emit_ics.write_all(events, out / "feeds", NOW)
    print(f"{len(events)} demo events -> {out/'index.html'}")
    print(f"  attributed: {sorted({t for e in events for t in e.tickers})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
