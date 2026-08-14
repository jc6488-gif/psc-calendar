"""Verification suite. Run: python3 -m pytest tests/ -q"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.pscal import classify, extract
from src.pscal.emit_ics import _build, write_all
from src.pscal.models import Event, extract_dockets
from src.pscal.pipeline import dedupe
from tests import fixtures as F

TZ = "America/New_York"
NOW = datetime(2026, 8, 14, tzinfo=ZoneInfo(TZ))


def _mk(raws, commission="XX"):
    """Normalise RawEvents the way the pipeline does, so we can assert on Events."""
    out = []
    for r in raws:
        blob = f"{r.get('title','')} {r.get('description','')}"
        tk, subs = classify.match_companies(blob, commission)
        et, el, w = classify.classify_type(blob)
        rc, sig = classify.detect_rate_case(blob)
        out.append(Event(
            commission=commission, commission_name="Test Commission", state="XX", tz=TZ,
            title=r["title"], start=r["start"], end=r.get("end"),
            all_day=bool(r.get("all_day")), location=r.get("location", ""),
            description=r.get("description", ""), url=r.get("url", ""),
            dockets=extract_dockets(r.get("title"), r.get("description")),
            tickers=tk, subsidiaries=subs, event_type=et, event_type_label=el,
            weight=w + (1 if tk else 0) + (1 if rc else 0),
            rate_case=rc, rate_case_signals=sig,
        ))
    return out


# ------------------------------------------------------------------ extractors

def test_ics():
    evs = extract.from_ics(F.ICS_TRUMBA, ZoneInfo(TZ), NOW, "u")
    assert len(evs) == 3
    e = evs[0]
    assert "NIPSCO" in e["title"] or "Northern Indiana" in e["title"]
    assert e["start"].month == 9 and e["start"].day == 3
    assert e["start"].hour == 9 and e["start"].minute == 30
    assert e["location"].startswith("PNC Center")
    assert evs[2]["all_day"] is True


def test_rss_uses_event_date_not_pubdate():
    """The event is Sept 10; pubDate is Sept 1. Must pick the event date."""
    evs = extract.from_rss(F.RSS_PUCT, ZoneInfo(TZ), NOW, "u")
    assert len(evs) == 2
    assert evs[0]["start"].month == 9 and evs[0]["start"].day == 10
    assert evs[0]["start"].hour == 9
    assert evs[1]["start"].day == 24


def test_html_cards():
    evs = extract.from_html_cards(F.HTML_DRUPAL, ZoneInfo(TZ), NOW, "https://dps.ny.gov/calendar")
    assert len(evs) == 3
    titles = " ".join(e["title"] for e in evs)
    assert "Public Service Commission Session" in titles
    sept = [e for e in evs if e["start"].month == 9][0]
    assert sept["start"].hour == 10 and sept["start"].minute == 30
    assert evs[0]["url"].startswith("https://dps.ny.gov/")


def test_jsonld():
    evs = extract.from_jsonld(F.HTML_JSONLD, ZoneInfo("America/Los_Angeles"), NOW, "u")
    assert len(evs) == 2
    assert evs[0]["title"] == "Commission Voting Meeting"
    assert "CPUC Auditorium" in evs[0]["location"]
    assert evs[0]["end"] is not None


def test_html_table():
    evs = extract.from_html_table(F.HTML_ASPX_TBL, ZoneInfo("America/Chicago"), NOW, "u")
    assert len(evs) == 3
    assert evs[0]["start"].month == 9 and evs[0]["start"].day == 15
    assert any("Evergy" in e["description"] for e in evs)


def test_date_regex_fallback_and_noise():
    evs = extract.from_date_regex(F.HTML_LOOSE, ZoneInfo(TZ), NOW, "u")
    assert len(evs) >= 3
    titles = [e["title"] for e in evs]
    # the copyright line has no date, so it must not appear
    assert not any("Copyright" in t for t in titles)
    assert any("Appalachian Power" in t for t in titles)


def test_feed_discovery():
    found = extract.discover_feeds(F.HTML_WITH_ICS_LINK, "https://x.gov/cal")
    assert ("ics", "https://x.gov/calendar/export.ics") in found


def test_window_filtering():
    """Events far outside the window are dropped."""
    old = b"""BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\nBEGIN:VEVENT\r
UID:x\r\nDTSTART:20200101T100000Z\r\nSUMMARY:Ancient hearing\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"""
    assert extract.from_ics(old, ZoneInfo(TZ), NOW, "u") == []


# ------------------------------------------------------------------ classification

def test_docket_extraction():
    assert "46150" in " ".join(extract_dockets("Cause No. 46150 hearing"))
    assert any("ER-2026-0087" in d for d in extract_dockets("Case ER-2026-0087"))
    assert any("A.25-05-011" in d for d in extract_dockets("Application A.25-05-011 of PG&E"))
    assert any("24-E-0165" in d for d in extract_dockets("Case 24-E-0165 Con Edison"))


@pytest.mark.parametrize("text,commission,expected", [
    ("Northern Indiana Public Service Company rate case", "IN", "NI"),
    ("NIPSCO base rate increase", "IN", "NI"),
    ("Consolidated Edison Company of New York electric service", "NY", "ED"),
    ("Orange and Rockland Utilities gas rates", "NY", "ED"),
    ("Rockland Electric Company annual review", "NJ", "ED"),
    ("Pacific Gas and Electric Company general rate case", "CA", "PCG"),
    ("Southern California Edison cost of capital", "CA", "EIX"),
    ("San Diego Gas and Electric revenue requirement", "CA", "SRE"),
    ("Oncor Electric Delivery Company rate change", "TX", "SRE"),
    ("Entergy New Orleans formula rate plan", "NOLA", "ETR"),
    ("Georgia Power Company IRP", "GA", "SO"),
    ("Nicor Gas rate case", "IL", "SO"),
    ("Peoples Gas Light and Coke Company", "IL", "WEC"),
    ("Evergy Missouri West rate increase", "MO", "EVRG"),
    ("Spire Missouri Inc. general rate increase", "MO", "SR"),
    ("Appalachian Power Company base rates", "WV", "AEP"),
    ("Madison Gas and Electric biennial rate review", "WI", "MGEE"),
    ("Arizona Public Service Company rate case", "AZ", "PNW"),
    ("Southwest Gas Corporation general rate case", "AZ", "SWX"),
    ("Black Hills Energy Nebraska Gas", "NE", "BKH"),
])
def test_company_matching(text, commission, expected):
    tickers, _ = classify.match_companies(text, commission)
    assert expected in tickers, f"{expected} not in {tickers} for {text!r}"


@pytest.mark.parametrize("text", [
    "The deadline lapses on Monday",          # must not match "APS "
    "A scenic overview of the grid",          # must not match "SCE "
    "General discussion of southern states",  # must not match "Southern Company"
    "Spirent test equipment procurement",     # must not match "Spire"
])
def test_no_false_positive_matches(text):
    tickers, _ = classify.match_companies(text, "XX")
    assert tickers == [], f"false positive: {tickers} for {text!r}"


def test_two_digit_slash_year_not_rolled_forward():
    """'4/6/26' carries a real year. The roll-forward heuristic for yearless
    dates ("March 4") must not fire on it - it shifted IURC weekly-hearing
    rows into 2027 on the first live run."""
    dt = extract._first_date_in("4/6/26 to 4/10/26", ZoneInfo(TZ), NOW)
    assert dt is not None and (dt.year, dt.month, dt.day) == (2026, 4, 6)


def test_bare_date_in_past_still_rolls_forward():
    """A truly yearless date well in the past on an upcoming-events page
    still means next year."""
    dt = extract._first_date_in("Hearing scheduled for March 4", ZoneInfo(TZ), NOW)
    assert dt is not None and (dt.year, dt.month, dt.day) == (2027, 3, 4)


def test_description_only_match_needs_matching_jurisdiction():
    """'Pinnacle West' in the body of an Indiana community event is a venue,
    not the Arizona utility. Description-only evidence counts only on a
    commission the company actually appears before."""
    title = "Indy Vet To Vet Terrific Tuesday"
    blob = f"{title} Come be part of our family in the ballroom at Pinnacle West."
    tickers, _ = classify.match_companies(blob, "IN", title=title)
    assert tickers == []
    # Same text on the company's own commission still attributes.
    tickers, _ = classify.match_companies(blob, "AZ", title=title)
    assert tickers == ["PNW"]


def test_title_match_attributes_across_jurisdictions():
    """A title naming the utility is strong evidence anywhere - the
    Georgia-Power-on-the-FERC-calendar rule."""
    title = "Arizona Public Service Company transmission formula rate"
    tickers, _ = classify.match_companies(title, "XX", title=title)
    assert tickers == ["PNW"]


def test_entergy_new_orleans_routes_to_nola():
    """Entergy New Orleans is regulated by the City Council, not the LPSC.
    Getting this wrong is the classic utility-coverage mistake."""
    tickers, subs = classify.match_companies("Entergy New Orleans rate case", "NOLA")
    assert tickers == ["ETR"]
    assert any("New Orleans" in s for s in subs)


@pytest.mark.parametrize("text,expected", [
    ("Commission Open Meeting", "decision"),
    ("Evidentiary Hearing on the merits", "evidentiary_hearing"),
    ("Prehearing Conference", "procedural"),
    ("Direct testimony due", "procedural"),
    ("Local Public Hearing", "public_comment"),
    ("Stakeholder Workshop on interconnection", "workshop"),
    ("Public Service Commission Session", "decision"),
])
def test_event_typing(text, expected):
    assert classify.classify_type(text)[0] == expected


def test_rate_case_detection():
    assert classify.detect_rate_case("application for a general rate increase")[0]
    assert classify.detect_rate_case("integrated resource plan filing")[0]
    assert not classify.detect_rate_case("annual staff picnic")[0]


def test_noise_filter():
    assert classify.is_noise("Next")
    assert classify.is_noise("12")
    assert not classify.is_noise("Evidentiary Hearing - Cause No. 46150")


# ------------------------------------------------------------------ end to end

def test_full_normalisation_from_ics():
    evs = _mk(extract.from_ics(F.ICS_TRUMBA, ZoneInfo(TZ), NOW, "u"), "IN")
    nip = evs[0]
    assert nip.tickers == ["NI"]
    assert nip.rate_case is True
    assert nip.event_type == "evidentiary_hearing"
    assert "46150" in " ".join(nip.dockets)
    assert nip.weight >= 4          # base 3 + ticker + rate case, capped by design
    assert nip.uid.endswith("@psc-calendar")


def test_uid_is_stable_across_runs():
    a = _mk(extract.from_ics(F.ICS_TRUMBA, ZoneInfo(TZ), NOW, "u"), "IN")
    b = _mk(extract.from_ics(F.ICS_TRUMBA, ZoneInfo(TZ), NOW, "u"), "IN")
    assert [e.uid for e in a] == [e.uid for e in b]


def test_dedupe_merges_and_keeps_union_of_attribution():
    base = _mk(extract.from_ics(F.ICS_TRUMBA, ZoneInfo(TZ), NOW, "u"), "IN")
    dup = _mk(extract.from_ics(F.ICS_TRUMBA, ZoneInfo(TZ), NOW, "u"), "IN")
    dup[0].tickers = ["AEP"]
    dup[0].dockets = ["99999"]
    dup[0].description = "x"          # poorer record
    merged = dedupe(base + dup)
    assert len(merged) == 3
    first = [e for e in merged if "46150" in " ".join(e.dockets)][0]
    assert set(first.tickers) >= {"NI", "AEP"}


def test_dedupe_sorted_by_start():
    evs = _mk(extract.from_ics(F.ICS_TRUMBA, ZoneInfo(TZ), NOW, "u"), "IN")
    out = dedupe(evs)
    assert [e.start for e in out] == sorted(e.start for e in out)


# ------------------------------------------------------------------ ICS output

def test_ics_output_is_valid_and_roundtrips():
    from icalendar import Calendar

    evs = _mk(extract.from_ics(F.ICS_TRUMBA, ZoneInfo(TZ), NOW, "u"), "IN")
    blob = _build(evs, "Test", "Test feed", NOW)

    assert blob.startswith(b"BEGIN:VCALENDAR")
    assert blob.rstrip().endswith(b"END:VCALENDAR")
    # RFC 5545 requires CRLF line endings.
    assert b"\r\n" in blob
    for line in blob.split(b"\r\n"):
        assert len(line) <= 75, f"line exceeds 75 octets: {line[:90]!r}"

    cal = Calendar.from_ical(blob)
    vevents = list(cal.walk("VEVENT"))
    assert len(vevents) == 3
    for ve in vevents:
        assert ve.get("UID")
        assert ve.get("DTSTART")
        assert ve.get("DTEND")          # every event needs an end for Outlook
        assert ve.get("SUMMARY")
    assert "[NI]" in str(vevents[0].get("SUMMARY"))


def test_ics_all_day_event_has_date_valued_dtstart():
    from icalendar import Calendar
    evs = _mk(extract.from_ics(F.ICS_TRUMBA, ZoneInfo(TZ), NOW, "u"), "IN")
    cal = Calendar.from_ical(_build([e for e in evs if e.all_day], "T", "T", NOW))
    ve = list(cal.walk("VEVENT"))[0]
    assert not isinstance(ve["DTSTART"].dt, datetime)   # a plain date, not datetime


def test_write_all_produces_expected_feeds(tmp_path):
    evs = _mk(extract.from_ics(F.ICS_TRUMBA, ZoneInfo(TZ), NOW, "u"), "IN")
    counts = write_all(evs, tmp_path, NOW)
    for f in ("all.ics", "coverage.ics", "rate-cases.ics", "high-priority.ics"):
        assert (tmp_path / f).exists()
    assert (tmp_path / "ticker-NI.ics").exists()
    assert (tmp_path / "commission-IN.ics").exists()
    assert counts["all.ics"] == 3


def test_registry_is_well_formed():
    comms = classify.load_commissions()
    codes = [c["code"] for c in comms]
    assert len(codes) == len(set(codes)), "duplicate commission codes"
    states = {c["state"] for c in comms if c["type"] == "state_puc"}
    assert len(states) >= 50, f"expected all 50 states, got {len(states)}"
    for c in comms:
        assert c.get("sources"), f"{c['code']} has no sources"
        assert c.get("timezone")
        ZoneInfo(c["timezone"])          # raises if the tz name is bogus
        for s in c["sources"]:
            assert s["url"].startswith("http")


def test_every_ticker_maps_to_a_real_commission():
    comms = {c["code"] for c in classify.load_commissions()}
    cov = classify.load_coverage()["companies"]
    tickers = {c["ticker"] for c in cov}
    expected = {"AEP","ATO","BKH","CNP","CPK","DTE","ED","EIX","ETR","EVRG","HE","LNT",
                "MGEE","NI","NWE","OGE","OGS","PCG","PEG","PNW","POR","SO","SR","SRE",
                "SWX","WEC"}
    assert tickers == expected, f"missing {expected - tickers}, extra {tickers - expected}"
    for c in cov:
        assert c.get("match"), f"{c['ticker']} has no match strings"
        for sub in c["subsidiaries"]:
            for code in sub["commissions"]:
                assert code in comms, f"{c['ticker']}/{sub['name']} -> unknown commission {code}"


def test_federal_register_extractor(monkeypatch):
    """FERC's site blocks all scrapers; Sunshine Act notices in the Federal
    Register API are the working source. The meeting datetime comes from the
    `dates` field, not the publication date, and out-of-window past meetings
    drop."""
    monkeypatch.setattr(extract, "get", lambda url: (F.FR_API_JSON, "application/json"))
    evs = extract.from_federal_register("https://api.example/fr", ZoneInfo(TZ), NOW)
    assert len(evs) == 1  # June meeting is >30 days past -> dropped
    e = evs[0]
    assert e["start"].month == 9 and e["start"].day == 17 and e["start"].hour == 10
    assert "Sunshine Act" in e["title"]
    assert e["url"].startswith("https://www.federalregister.gov/")
    assert classify.classify_type(e["title"] + " sunshine act")[0] == "decision"
