"""Diagnose one or more commissions: what strategy wins, and what it actually found.

This is the tool to reach for when a source shows red on the dashboard.

    python3 tools/probe.py OH              # one commission, verbose
    python3 tools/probe.py --tier core     # every core state
    python3 tools/probe.py OH --raw        # also dump what each strategy returns
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.pscal import classify, extract
from src.pscal.fetch import get_text
from src.pscal.models import extract_dockets

NOW = datetime.now(ZoneInfo("America/New_York"))
G, R, Y, D = "\033[32m", "\033[31m", "\033[33m", "\033[0m"


def probe(spec: dict, raw: bool = False) -> bool:
    code, tzname = spec["code"], spec.get("timezone", "America/New_York")
    print(f"\n{'=' * 78}\n{code}  {spec['name']}  [{spec.get('tier')}]\n{'=' * 78}")

    any_ok = False
    for src in spec.get("sources", []):
        url, strat = src["url"], src.get("strategy", "auto")
        print(f"\n  {url}\n  strategy: {strat}")
        try:
            evs, used = extract.extract(url, strat, tzname, NOW)
        except Exception as e:
            print(f"  {R}FAIL{D}  {type(e).__name__}: {str(e)[:400]}")
            if raw:
                try:
                    html, ct = get_text(url)
                    print(f"  content-type: {ct}  length: {len(html)}")
                    feeds = extract.discover_feeds(html, url)
                    print(f"  linked feeds: {feeds or 'none'}")
                    print(f"  first 400 chars: {html[:400]!r}")
                except Exception as e2:
                    print(f"  could not fetch at all: {e2}")
            continue

        any_ok = True
        print(f"  {G}OK{D}  via {used}: {len(evs)} events")
        for e in evs[:8]:
            title = e.get("title", "")[:88]
            blob = f"{title} {e.get('description','')}"
            tk, _ = classify.match_companies(blob, code)
            et, _, _ = classify.classify_type(blob)
            dk = extract_dockets(title, e.get("description"))
            tag = f"{Y}{','.join(tk)}{D}" if tk else "-"
            print(f"     {e['start']:%Y-%m-%d %H:%M}  {tag:<18} {et:<20} {title}")
            if dk:
                print(f"       dockets: {', '.join(dk)}")
        if len(evs) > 8:
            print(f"     … {len(evs) - 8} more")
        break   # the pipeline stops at the first working source too

    if not any_ok:
        print(f"\n  {R}All sources failed for {code}.{D}")
        print("  Next steps: open the URL in a browser. If it moved, update")
        print("  data/commissions.yaml. If it renders via JavaScript, look for the")
        print("  XHR endpoint in devtools Network and point a source at that instead.")
    return any_ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="*", help="Commission codes, e.g. OH TX CA")
    ap.add_argument("--tier", choices=["core", "full"], help="Probe a whole tier")
    ap.add_argument("--raw", action="store_true", help="Dump diagnostics on failure")
    a = ap.parse_args()

    specs = classify.load_commissions()
    if a.tier:
        specs = [s for s in specs if s.get("tier") == a.tier]
    elif a.codes:
        want = {c.upper() for c in a.codes}
        specs = [s for s in specs if s["code"].upper() in want]
    else:
        ap.error("give commission codes or --tier")

    ok = sum(probe(s, a.raw) for s in specs)
    print(f"\n{'=' * 78}\n{ok}/{len(specs)} commissions reporting\n")
    return 0 if ok == len(specs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
