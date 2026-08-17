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
        et, el, w, rel = classify.classify_type(blob)
        out.append(Event(
            commission=commission, commission_name="Test Commission", state="XX", tz=TZ,
            title=r["title"], start=r["start"], end=r.get("end"),
            all_day=bool(r.get("all_day")), location=r.get("location", ""),
            description=r.get("description", ""), url=r.get("url", ""),
            dockets=extract_dockets(r.get("title"), r.get("description")),
            event_type=et, event_type_label=el, relevance=rel,
            weight=w,
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


@pytest.mark.parametrize("text,expected,relevance", [
    ("Commission Open Meeting", "open_meeting", "High"),
    ("Regular Open Meeting", "open_meeting", "High"),
    ("Evidentiary Hearing on the merits", "evidentiary_hearing", "High"),
    ("Prehearing Conference", "procedural", "Medium"),
    ("Direct testimony due", "procedural", "Medium"),
    ("Local Public Hearing", "public_comment", "Medium"),
    ("Stakeholder Workshop on interconnection", "workshop", "Low"),
    ("Public Service Commission Session", "open_meeting", "High"),
    ("Order on Rehearing in Docket 12345", "decision_order", "High"),
    # bare "Hearing (25-035-61...)" was vanishing into Other
    ("Hearing (25-035-61, Utah Fire Fund)", "evidentiary_hearing", "High"),
    ("Hearing on the Merits/Open Meeting", "evidentiary_hearing", "High"),
])
def test_event_typing(text, expected, relevance):
    tid, _, _, rel = classify.classify_type(text)
    assert tid == expected
    assert rel == relevance


def test_type_hint_rescues_keywordless_titles():
    """MA's hearings API serves docket rows like '26-50 - Boston Gas - Rates'
    with no type words; the source-level hint keeps them out of Other."""
    assert classify.classify_type("26-50 - Boston Gas Company d/b/a National Grid - Rates")[0] == "other"
    assert classify.type_info("evidentiary_hearing")[0] == "evidentiary_hearing"
    assert classify.type_info("evidentiary_hearing")[3] == "High"


def test_noise_filter():
    assert classify.is_noise("Next")
    assert classify.is_noise("12")
    assert not classify.is_noise("Evidentiary Hearing - Cause No. 46150")


# ------------------------------------------------------------------ end to end

def test_full_normalisation_from_ics():
    evs = _mk(extract.from_ics(F.ICS_TRUMBA, ZoneInfo(TZ), NOW, "u"), "IN")
    nip = evs[0]
    assert nip.event_type == "evidentiary_hearing"
    assert "46150" in " ".join(nip.dockets)
    assert nip.weight >= 3
    assert nip.uid.endswith("@psc-calendar")


def test_uid_is_stable_across_runs():
    a = _mk(extract.from_ics(F.ICS_TRUMBA, ZoneInfo(TZ), NOW, "u"), "IN")
    b = _mk(extract.from_ics(F.ICS_TRUMBA, ZoneInfo(TZ), NOW, "u"), "IN")
    assert [e.uid for e in a] == [e.uid for e in b]


def test_dedupe_merges_and_keeps_union_of_dockets():
    base = _mk(extract.from_ics(F.ICS_TRUMBA, ZoneInfo(TZ), NOW, "u"), "IN")
    dup = _mk(extract.from_ics(F.ICS_TRUMBA, ZoneInfo(TZ), NOW, "u"), "IN")
    dup[0].dockets = ["99999"]
    dup[0].description = "x"          # poorer record
    merged = dedupe(base + dup)
    assert len(merged) == 3
    first = [e for e in merged if "46150" in " ".join(e.dockets)][0]
    assert "99999" in first.dockets


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
    assert "[IN]" in str(vevents[0].get("SUMMARY"))


def test_ics_all_day_event_has_date_valued_dtstart():
    from icalendar import Calendar
    evs = _mk(extract.from_ics(F.ICS_TRUMBA, ZoneInfo(TZ), NOW, "u"), "IN")
    cal = Calendar.from_ical(_build([e for e in evs if e.all_day], "T", "T", NOW))
    ve = list(cal.walk("VEVENT"))[0]
    assert not isinstance(ve["DTSTART"].dt, datetime)   # a plain date, not datetime


def test_write_all_produces_expected_feeds(tmp_path):
    evs = _mk(extract.from_ics(F.ICS_TRUMBA, ZoneInfo(TZ), NOW, "u"), "IN")
    counts = write_all(evs, tmp_path, NOW)
    for f in ("all.ics", "high-priority.ics"):
        assert (tmp_path / f).exists()
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
    assert classify.classify_type(e["title"] + " sunshine act")[0] == "open_meeting"


def test_telerik_scheduler_extractor():
    """PUCT's Telerik widget hides dates from the rendered HTML; the init
    JSON carries them. Cancelled appointments drop; en-dash survives."""
    evs = extract.from_telerik_scheduler(F.HTML_TELERIK, ZoneInfo("America/Chicago"), NOW, "u")
    assert len(evs) == 2
    om = next(e for e in evs if e["title"] == "Open Meeting")
    assert (om["start"].month, om["start"].day, om["start"].hour) == (8, 14, 9)
    assert om["location"] == "Commissioners Hearing Room"
    assert om["url"].startswith("https://ftp.puc.texas.gov/")
    assert any("–" in e["title"] for e in evs)      # – decoded, not mangled
    assert not any("Cancelled Meeting" in e["title"] for e in evs)


def test_dedupe_prefers_timed_record_over_dateline():
    """A 9:30 AM record from a scheduler must not be clobbered by a
    date-only mention of the same meeting that parses to midnight."""
    from datetime import datetime as dt
    base = dict(commission="TX", commission_name="T", state="TX", tz="America/Chicago",
                title="Open Meeting", event_type="decision", event_type_label="Decision / Order",
                weight=3)
    midnight = Event(start=dt(2026, 8, 14, 0, 0, tzinfo=ZoneInfo("America/Chicago")),
                     description="long dateline description with docket 12345",
                     dockets=["12345"], **base)
    timed = Event(start=dt(2026, 8, 14, 9, 30, tzinfo=ZoneInfo("America/Chicago")),
                  location="Commissioners Hearing Room", **base)
    merged = dedupe([midnight, timed])
    assert len(merged) == 1
    assert merged[0].start.hour == 9 and merged[0].start.minute == 30
    assert "12345" in merged[0].dockets      # docket unioned from the loser


@pytest.mark.parametrize("raw,cleaned", [
    ("Agenda (122.64 KB) .pdf (Amended)", "Agenda (Amended)"),
    ("Agenda (posted )", "Agenda (posted)"),
    ("Hearing Regarding CASD No. 2025-V-00759", "Hearing Regarding CASD No. 2025-V-00759"),
])
def test_clean_title(raw, cleaned):
    assert classify.clean_title(raw) == cleaned


@pytest.mark.parametrize("title,uninformative", [
    ("8/17/26 to 8/21/26", True),
    ("25 Minutes", True),
    ("(Tuesday)", True),
    ("Agenda Watch Live", True),
    ("Final Agenda", True),
    ("Open Meeting", False),
    ("Agenda of Commission Meeting", False),
    ("Docket # 55973 City of Cartersville v. Georgia Power", False),
])
def test_uninformative_titles(title, uninformative):
    assert classify.is_uninformative(title) is uninformative


def test_generated_on_is_noise():
    """A page's own render timestamp is not a meeting."""
    assert classify.is_noise("Generated On Aug 14, 2026 2:48 PM")


def test_fullcalendar_extractor(monkeypatch):
    """Maryland's FullCalendar feed: ISO datetimes parse with times, date-only
    entries become all-day, out-of-window events drop."""
    monkeypatch.setattr(extract, "get", lambda url: (F.FULLCALENDAR_JSON, "application/json"))
    evs = extract.from_fullcalendar_json("https://x/ajax", ZoneInfo(TZ), NOW)
    assert len(evs) == 3          # ancient one dropped
    hearing = next(e for e in evs if "9866" in e["title"])
    assert hearing["start"].hour == 10 and not hearing["all_day"]
    deadline = next(e for e in evs if "Deadline" in e["title"])
    assert deadline["all_day"] is True


@pytest.mark.parametrize("text,expected", [
    ("UE-260208 PacifiCorp & PGE Settlement Conference", "UE-260208"),
    ("GU-2026-0225 Spire - Evidentiary Hearing", "GU-2026-0225"),
    ("Hearing on Settlement Stipulation (24-035-61)", "24-035-61"),
    ("Hearing: U-37463", "U-37463"),
    ("DPU 25-1555 public comment session", "25-1555"),
])
def test_docket_patterns_state_formats(text, expected):
    assert expected in extract_dockets(text)


def test_docket_no_prefix_not_mangled():
    """'Docket Nos. 24-035-61' must not yield a phantom 'NO24-035'."""
    got = extract_dockets("Public Witness Hearing Docket Nos. 24-035-61 and 23-035-01")
    assert all(not d.startswith("NO") for d in got)


def test_only_three_types_are_published():
    """The desk narrowed the calendar 2026-08-17. Types are still fully
    classified (so we know what a thing is) but only these three emit."""
    assert classify.is_published("evidentiary_hearing")
    assert classify.is_published("open_meeting")
    assert classify.is_published("decision_order")
    for tid in ("procedural", "public_comment", "workshop", "other"):
        assert not classify.is_published(tid), tid


@pytest.mark.parametrize("title,expected", [
    # Colorado abbreviates Hearing as HRG - 42 real hearings sat in Other
    ("HRG: 26F-0122EG Stanley Wagon V. Public Service Co.", "evidentiary_hearing"),
    ("Remote HRG: Pro. No. 25AL-0538G, Public Service - Tariff 6", "evidentiary_hearing"),
    # FERC notational orders really are orders
    ("August 2026: 27 Notational Orders", "decision_order"),
    # the New Orleans committee that regulates Entergy New Orleans
    ("Joint Utility, Cable, Telecommunications and Technology Committee", "open_meeting"),
    # PHC is a prehearing conference - procedural, therefore NOT published
    ("PHC: 26F-0246EG Formal Complaint", "procedural"),
])
def test_rescued_abbreviations(title, expected):
    assert classify.classify_type(title)[0] == expected


def test_vacated_prefix_marks_cancellation():
    """Colorado publishes cancellations as 'VACATED:' - it must never read
    as a live hearing."""
    assert classify.clean_title("VACATED: HRG: 25AL-0538G Tariff 6").startswith("[CANCELED]")


@pytest.mark.parametrize("title", [
    "Regular Agenda/15-26",                    # NV agenda meeting numbering
    "Scheduled Commission Utility Agendas",    # NV index
    "Notice of Meeting",                       # MS commission meeting
])
def test_state_specific_meeting_wording(title):
    """These wordings silenced NV and MS entirely when the three-type filter
    landed - they are commission meetings, not 'Other'."""
    tid = classify.classify_type(title)[0]
    assert tid == "open_meeting", (title, tid)
    assert classify.is_published(tid)
