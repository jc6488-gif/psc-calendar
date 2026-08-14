"""Build the dashboard from REAL commission data captured 2026-08-14.

Unlike tools/demo.py (synthetic fixtures), every event here was read off the
commission's own published calendar. Events flow through the same
classify/dedupe/emit path the live pipeline uses, so what you see is what the
scraper produces - only the fetch step differs.

Sources that failed are recorded with their real errors, including the four
commissions that disallow automated access via robots.txt. Those are not
worked around.

    python3 tools/build_preview.py && open docs-preview/index.html
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.pscal import classify, emit_ics, emit_site
from src.pscal.models import Event, ScrapeResult, extract_dockets
from src.pscal.pipeline import dedupe

CAPTURED = datetime(2026, 8, 14, 13, 0, tzinfo=ZoneInfo("America/New_York"))

# ---------------------------------------------------------------- real captures
# format: DATE|TIME|TITLE|DOCKET
LIVE = {
    "NY": ("New York Public Service Commission", "NY", "America/New_York",
           "https://dps.ny.gov/calendar", """
2026-08-13|10:30|AUGUST 2026 Public Service Commission Session|
2026-08-17|16:30|Comments due on petition filed by the Rochester Gas and Electric|
2026-08-17|16:30|Comments due joint petition between Reserve Gas Co., Inc. (RGC)|
2026-08-18|09:00|Commencement of evidentiary hearing in the Universal Service Fund|
2026-08-19|16:30|Comments sought on NYSERDA petition proposing modifications|
2026-08-24|16:30|PSC seeks comments for UTEN Pilot Proposal filings by NFG|
2026-08-24|16:30|PSC seeks comments for Tracy Solar Energy Center, LLC petition|
2026-08-24|16:30|PSC seeks comments for National Grid petition requesting approval|
2026-08-24|16:30|PSC seeks comments for National Grid proposal to extend existing|
2026-08-26|18:00|Bliss Wind Repowering Project Public Comment Hearing|
2026-08-28|17:00|Bliss Wind Repowering Project Comments Due|
2026-09-09|18:00|Cayuga Lake Solar Public Comment Hearing|
2026-09-17|10:30|SEPTEMBER 2026 Public Service Commission Session|
"""),
    "CA": ("California Public Utilities Commission", "CA", "America/Los_Angeles",
           "https://www.cpuc.ca.gov/events-and-meetings", """
2026-08-17|10:00|CPUC to Hold In-Person Public Workshop on Video Franchise Rules|
2026-08-17|15:00|Low Income Energy Assistance Programs Subcommittee Meeting|
2026-08-20|09:00|Interagency Public Briefing on Electric Utilities' Safety Culture and Public Safety Power Shutoff Updates|
2026-08-21|13:00|DACAG Meeting 8-21-26|
2026-08-25|18:00|Golden State Water Company Proposed Acquisition of City of Norwalk's North and South Artesia System|
2026-09-03|11:00|CPUC Voting Meeting 09-03-26|
2026-09-16|09:00|Low Income Oversight Board Quarterly Meeting|
2026-09-17|11:00|CPUC Voting Meeting 09-17-26|
2026-09-17|12:00|CPUC Energy Division Open House|
2026-09-30|09:00|CPUC Small and Diverse Business Expo|
"""),
    "MO": ("Missouri Public Service Commission", "MO", "America/Chicago",
           "https://psc.mo.gov/Calendars.aspx?Selected=Hearings", """
2026-08-19|09:00|Agenda Meeting|
2026-08-19|09:30|Public Meeting with SPP & MISO|
2026-08-20|09:00|Ameren Missouri - Prehearing Conference|EA-2026-0226
2026-08-20|10:00|Rulemaking Hearing - Chapter 4 and Communications Rulemaking|OX-2026-0335
2026-08-26|11:00|Agenda Meeting|
2026-08-26|18:00|Ameren Missouri - Local Public Hearing|EA-2026-0183
2026-08-27|09:00|Ginger v. Spire - Evidentiary Hearing|GC-2026-0250
"""),
    "GA": ("Georgia Public Service Commission", "GA", "America/New_York",
           "https://psc.ga.gov/calendar/", """
2026-08-18|09:30|Administrative Session|
2026-08-18|09:35|Docket No. 56181 GEORGIA POWER COMPANY'S APPLICATION FOR THE CERTIFICATION OF THE CARES 2023 UTILITY SCALE RENEWABLE POWER PURCHASE AGREEMENTS|56181
2026-08-19|10:00|GUFPA Hearing|
2026-08-27|09:30|Facilities Protection/Telecommunication/Energy And Administrative Affairs Committees|
2026-09-01|09:30|Administrative Session|
2026-09-01|09:35|Docket No. 57171 RTP Revenue Credit and Allocation Methodology Hearing|57171
2026-09-10|09:30|Facilities Protection/Telecommunication/Energy And Administrative Affairs Committees|
2026-09-10|09:35|Docket # 57062 Hearing for the Liberty Utilities 2026-2027 Gas Supply Plan|57062
"""),
    "NJ": ("New Jersey Board of Public Utilities", "NJ", "America/New_York",
           "https://www.nj.gov/bpu/newsroom/public/", """
2026-08-19|10:00|In The Matter of Executive Order 1 Modernization of the Traditional Electric Distribution Utility Business Model Study|
2026-09-10|10:00|In The Matter of Executive Order 1 Modernization of the Traditional Electric Distribution Utility Business Model Study|
2026-09-22|10:00|IN THE MATTER OF THE PROVISION OF BASIC GENERATION SERVICE (BGS) FOR THE PERIOD BEGINNING JUNE 1, 2027|
"""),
    # Read off the PUCT "Upcoming Events" panel on 2026-08-14.
    "TX": ("Public Utility Commission of Texas", "TX", "America/Chicago",
           "https://puc.texas.gov/agency/calendar/calendar.aspx", """
2026-08-14|09:30|Open Meeting|
2026-08-20|09:30|Hearing on the Merits / Open Meeting|
2026-08-21|09:30|Open Meeting|
2026-08-28|09:30|Open Meeting|
"""),
    "FERC": ("Federal Energy Regulatory Commission", "US", "America/New_York",
           "https://www.ferc.gov/news-events/events", """
2026-08-19|19:00|Virtual Public Scoping Session for the Rio Grande LNG Expansion Project|CP26-532-000
2026-08-25|17:30|Public Scoping Session for the CP2 LNG Expansion Project|CP26-530-000
2026-08-26|17:30|Public Scoping Session for the CP2 LNG Expansion Project|CP26-533-000
2026-09-01|21:30|Evening Scoping Session for the Terror Lake Hydroelectric Project|P-2743-112
2026-09-02|14:00|Daytime Scoping Session for the Terror Lake Hydroelectric Project|P-2743-112
"""),
    "IN": ("Indiana Utility Regulatory Commission", "IN", "America/Indiana/Indianapolis",
           "https://www.in.gov/iurc/docketed-cases/find-a-docketed-case/weekly-hearings", """
2026-08-14|10:00|Verified Petition of Southern Indiana Gas and Electric Company d/b/a CenterPoint Energy Indiana South|45894-TDSIC2
2026-08-17|09:30|Application of Westfield Gas LLC d/b/a Citizens Gas of Westfield for a Change in its Gas Cost Adjustment Charge|37389-GCA147
2026-08-17|10:00|Petition of the Board of Directors for Utilities of the Dept. of Public Utilities of the City of Indianapolis for Approval of Gas Cost Adjustments|37399-GCA171
2026-08-17|13:00|Verified Petition of Indiana Michigan Power Company for CPCN for Acquisition of the 918 MW Sycamore Riverside Energy Center|46389
"""),
    "PA": ("Pennsylvania Public Utility Commission", "PA", "America/New_York",
           "https://www.puc.pa.gov/about-the-puc/public-meetings/public-meeting-schedule/", """
2026-08-27|10:00|Public Meeting, Hearing Room 1, Commonwealth Keystone Building|
2026-09-09|09:00|2026 Safety Conference, State College|
2026-09-17|09:00|Be Utility Wise Event, Erie|
2026-10-07|09:00|Be Utility Wise Event, Pittsburgh|
2026-10-28|09:00|Be Utility Wise Event, Berks|
"""),
    "RI": ("Rhode Island Public Utilities Commission", "RI", "America/New_York",
           "https://ripuc.ri.gov/events", """
2026-08-18|10:00|Notice of Open Meetings (continued)|25-45-GE
"""),
    "AL": ("Alabama Public Service Commission", "AL", "America/Chicago",
           "https://psc.alabama.gov/category/events/", """
2026-09-01|10:00|Commission Meeting, RSA Union Building, Montgomery|
2026-10-06|10:00|Tentative Commission Meeting|
"""),
    "NOLA": ("New Orleans City Council Utility Committee", "LA", "America/Chicago",
           "https://council.nola.gov/meetings/", """
2026-08-20|10:00|Regular Meeting|
2026-08-25|10:00|Economic Development & Special Development Projects|
2026-08-26|13:00|Climate Change and Sustainability Committee|
2026-08-27|10:00|Transportation Committee|
2026-08-27|13:00|Budget/Audit/Board of Review Committee Meeting|
2026-08-31|10:00|Criminal Justice Committee|
2026-08-31|13:00|Quality of Life Committee|
2026-09-03|10:00|Regular Meeting|
"""),
}

# Sources that genuinely did not yield data, with the real reason.
FAILED = [
    # --- disallowed by robots.txt: NOT worked around ---
    ("IL", "Illinois Commerce Commission", "core", "robots.txt disallowed — not worked around"),
    ("VA", "Virginia State Corporation Commission", "core", "robots.txt disallowed — not worked around"),
    ("NV", "Public Utilities Commission of Nevada", "core", "robots.txt disallowed — not worked around"),
    ("FL", "Florida Public Service Commission", "core", "robots.txt fetch failed (ConnectTimeout/SSL) — treated as disallowed"),
    ("KS", "Kansas Corporation Commission", "core", "robots.txt fetch failed (SSL cert verify) — treated as disallowed"),
    ("AR", "Arkansas Public Service Commission", "core", "robots.txt fetch failed (DNS) — treated as disallowed"),
    # --- registry URL is wrong / stale: needs a verification pass ---
    ("CO", "Colorado Public Utilities Commission", "core", "HTTP 404 — registry URL unverified"),
    ("LA", "Louisiana Public Service Commission", "core", "HTTP 404 — registry URL unverified"),
    ("MS", "Mississippi Public Service Commission", "core", "HTTP 404 — registry URL unverified"),
    ("MN", "Minnesota Public Utilities Commission", "core", "HTTP 404 — registry URL unverified"),
    ("MD", "Maryland Public Service Commission", "core", "HTTP 404 — registry URL unverified"),
    ("NE", "Nebraska Public Service Commission", "core", "HTTP 404 — registry URL unverified"),
    ("SD", "South Dakota Public Utilities Commission", "core", "HTTP 404 — registry URL unverified"),
    ("OR", "Oregon Public Utility Commission", "core", "HTTP 404 — registry URL unverified"),
    ("TX-RRC", "Railroad Commission of Texas", "core", "HTTP 404 — registry URL unverified"),
    ("AZ", "Arizona Corporation Commission", "core", "HTTP 404 — registry URL unverified"),
    ("OK", "Oklahoma Corporation Commission", "core", "HTTP 404 — registry URL unverified"),
    ("TN", "Tennessee Public Utility Commission", "core", "HTTP 404 — registry URL unverified"),
    ("IA", "Iowa Utilities Commission", "core", "HTTP 404 — registry URL unverified"),
    ("WY", "Wyoming Public Service Commission", "core", "HTTP 404 — registry URL unverified"),
    ("UT", "Utah Public Service Commission", "full", "HTTP 404 — registry URL unverified"),
    ("NM", "New Mexico Public Regulation Commission", "full", "HTTP 404 — registry URL unverified"),
    ("NC", "North Carolina Utilities Commission", "full", "HTTP 404 — registry URL unverified"),
    ("VT", "Vermont Public Utility Commission", "full", "HTTP 404 — registry URL unverified"),
    ("ID", "Idaho Public Utilities Commission", "full", "HTTP 404 — registry URL unverified"),
    ("CT", "Connecticut Public Utilities Regulatory Authority", "full", "HTTP 404 — registry URL unverified"),
    ("ME", "Maine Public Utilities Commission", "full", "HTTP 404 — registry URL unverified"),
    ("MT", "Montana Public Service Commission", "core", "HTTP 404 — registry URL unverified"),
    ("ND", "North Dakota Public Service Commission", "full", "HTTP 404 — registry URL unverified"),
    ("SC", "South Carolina Public Service Commission", "full", "HTTP 404 — registry URL unverified"),
    ("DC", "District of Columbia Public Service Commission", "full", "HTTP 404 — registry URL unverified"),
    ("MA", "Massachusetts Department of Public Utilities", "full", "HTTP 404 — registry URL unverified"),
    ("WA", "Washington UTC", "full", "HTTP 404 — registry URL unverified"),
    ("DE", "Delaware Public Service Commission", "core", "events page empty; statewide portal 500 error"),
    ("AK", "Regulatory Commission of Alaska", "full", "RCA 'Page Not Found'"),
    ("NH", "New Hampshire Public Utilities Commission", "full", "HTTP 403"),
    ("WV", "West Virginia Public Service Commission", "core", "HTTP 403 on both URLs"),
    ("KY", "Kentucky Public Service Commission", "core", "site error page, no calendar content"),
    ("HI", "Hawaii Public Utilities Commission", "core", "HTTP 404 on both URLs"),
    ("OH", "Public Utilities Commission of Ohio", "core",
     "puco.ohio.gov/events returns portal-encoded CDATA; hearing-schedule + agenda URLs resolve to homepage"),
    ("MI", "Michigan Public Service Commission", "core",
     "all-events and events show 'No Results Found'; /consumer/public-hearings 404"),
    ("WI", "Public Service Commission of Wisconsin", "core",
     "apps.psc.wi.gov calendar returned no parseable rows"),
]


def parse(block: str, tzname: str) -> list[tuple]:
    out = []
    for line in block.strip().splitlines():
        parts = line.split("|")
        if len(parts) < 3:
            continue
        d, t, title = parts[0].strip(), parts[1].strip(), parts[2].strip()
        docket = parts[3].strip() if len(parts) > 3 else ""
        hh, mm = (t.split(":") + ["0"])[:2] if ":" in t else ("0", "0")
        dt = datetime(*map(int, d.split("-")), int(hh), int(mm), tzinfo=ZoneInfo(tzname))
        out.append((dt, title, docket, t == "00:00"))
    return out


def main() -> int:
    results, events = [], []
    for code, (name, state, tzname, url, block) in LIVE.items():
        evs = []
        for dt, title, docket, allday in parse(block, tzname):
            tk, subs = classify.match_companies(title, code)
            et, el, w = classify.classify_type(title)
            dockets = extract_dockets(title) or ([docket] if docket else [])
            evs.append(Event(
                commission=code, commission_name=name, state=state, tz=tzname,
                title=title, start=dt, all_day=allday, location="",
                description="", url=url, source_url=url, dockets=dockets,
                tickers=tk, subsidiaries=subs, event_type=et, event_type_label=el,
                weight=w + (1 if tk else 0),
                scraped_at=CAPTURED.isoformat(),
            ))
        events += evs
        results.append(ScrapeResult(code, name, "core", True, evs, "webfetch", url, "", 1.0))

    for code, name, tier, err in FAILED:
        results.append(ScrapeResult(code, name, tier, False, error=err))

    events = dedupe(events)
    results.sort(key=lambda r: r.commission)

    roster = [
        {"ticker": c["ticker"], "name": c["name"], "sector": c.get("sector", ""),
         "commissions": sorted({k for s in c.get("subsidiaries", [])
                                for k in s.get("commissions", [])})}
        for c in classify.load_coverage()["companies"]
    ]
    payload = {
        "roster": roster,
        "generated_at": CAPTURED.isoformat(),
        "generated_at_utc": CAPTURED.isoformat(),
        "event_count": len(events),
        "covered_event_count": sum(1 for e in events if e.tickers),
        "commissions": [r.to_dict() for r in results],
        "events": [e.to_dict() for e in events],
    }
    out = ROOT / "docs-preview"
    emit_site.write_site(payload, out, CAPTURED)
    emit_ics.write_all(events, out / "feeds", CAPTURED)
    print(f"{len(events)} real events from {len(LIVE)} commissions -> {out/'index.html'}")
    print(f"  attributed: {sorted({t for e in events for t in e.tickers})}")
    print(f"  failing sources recorded: {len(FAILED)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
