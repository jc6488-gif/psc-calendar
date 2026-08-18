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


@pytest.mark.parametrize("title,desc,expected", [
    # NY: the description mentions comments due, and `procedural` is the first
    # rule in coverage.yaml, so matching title+description as one blob buried a
    # real evidentiary hearing in an unpublished type.
    ("Commencement of evidentiary hearing in the Universal Service Fund proceeding",
     "Comments due on the joint proposal; briefs due 30 days after.",
     "evidentiary_hearing"),
    # An open meeting whose agenda text lists filing deadlines is still a meeting.
    ("August 2026 Commission Meeting",
     "Agenda includes the intervention deadline and testimony due dates.",
     "open_meeting"),
    # A genuine comment deadline stays procedural - the title says what it is.
    ("Comments due on RG&E petition for approval of sales tax refund",
     "Evidentiary hearing in this docket concluded in June.",
     "procedural"),
])
def test_title_outranks_description(title, desc, expected):
    """The title states WHAT an event is; the description is context that
    routinely names other, unrelated dates. A hearing dropped because its
    description mentioned a deadline is the worst failure this tool can make."""
    assert classify.classify_event(title, desc)[0] == expected


def test_description_still_types_uninformative_titles():
    """Title-first must not throw away the description - many sources title
    events 'Notice' and put the substance in the body."""
    tid = classify.classify_event("Notice", "Evidentiary hearing in Docket 26-001")[0]
    assert tid == "evidentiary_hearing"


@pytest.mark.parametrize("raw,want", [
    # Connecticut's XPages calendar appends the join link and its blurb
    ("REGULAR MEETING (https://www.youtube.com/@ConnecticutPURA/streams When available)",
     "REGULAR MEETING"),
    ("26-03-15 CWC Rate Case Evidentiary Hearing (Hearing Room 1)",
     "26-03-15 CWC Rate Case Evidentiary Hearing (Hearing Room 1)"),
    ("Open Meeting https://example.gov/watch", "Open Meeting"),
    # The label that introduced the URL goes with it
    ("Commission Business Meeting Url: https://www.scetv.org/live/psc",
     "Commission Business Meeting"),
    ("Hearing - Webcast: https://example.gov/v", "Hearing"),
    # ...but those are ordinary words when no URL was there. Stripping one
    # unconditionally truncated Maine's every deliberation.
    ("Deliberations Audio/Video", "Deliberations Audio/Video"),
    ("Commission Meeting Video", "Commission Meeting Video"),
])
def test_urls_stripped_from_titles(raw, want):
    """A join link is not part of the event's name, and a real location
    parenthetical must survive."""
    assert classify.clean_title(raw) == want


@pytest.mark.parametrize("raw,title,hhmm", [
    ("09:00 am - REGULAR MEETING", "REGULAR MEETING", (9, 0)),
    ("02:30 pm - 26-06-07 NetSpeed Hearing", "26-06-07 NetSpeed Hearing", (14, 30)),
    ("12:00 pm - Commission Meeting", "Commission Meeting", (12, 0)),
    ("Evidentiary Hearing", "Evidentiary Hearing", None),
    # Nothing but a time is not a title - leave it alone for the
    # uninformative-title fallback to handle.
    ("09:00 am - ", "09:00 am - ", None),
])
def test_leading_time_becomes_the_start_time(raw, title, hhmm):
    """PURA prints the hour in the title and gives the row no machine time.
    Showing a 9am hearing as all-day misleads anyone planning a day."""
    assert classify.split_leading_time(raw) == (title, hhmm)


def test_no_meeting_notice_marks_cancellation():
    """Maryland posts the weeks it is NOT sitting as "NO Administrative
    Meeting", in the meeting's usual slot. Every type pattern reads that as a
    meeting, so it would publish as a real one and put a hold on a morning the
    commission has explicitly cleared."""
    assert classify.clean_title("NO Administrative Meeting") == \
        "[CANCELED] Administrative Meeting"


@pytest.mark.parametrize("title", [
    "Notice of Meeting",                     # MS - not a cancellation
    "November Commission Meeting",
    "No. 12345 Evidentiary Hearing",         # docket number
    "Nortel Networks tariff hearing",
])
def test_no_prefix_does_not_overreach(title):
    """Guard: only a shouted "NO" marks a cancellation. Mississippi's entire
    calendar is titled "Notice of Meeting"."""
    assert not classify.clean_title(title).startswith("[CANCELED]")


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


@pytest.mark.parametrize("title,out", [
    ("Expert Witness Hearing: Joint Application of Aqua North Carolina", True),
    ("HRG: 26C-0259TO, Supreme Towing, ALJ Farley", True),
    ("CLEC Hearing Notice in Docket 26-00045", True),
    ("Docket 26-0000116 - Amerimex wireless request", True),
    # mixed matters stay: an oil-and-gas produced-water docket is energy
    ("*SOLARIS WATER MIDSTREAM, LLC-DOCKET NO. OG-25-00028376", False),
    ("Water and Electric Utility rate hearing", False),
    ("Evidentiary Hearing - Spire Missouri gas rate case", False),
])
def test_out_of_sector_gate(title, out):
    assert classify.is_out_of_sector(title, "evidentiary_hearing") is out


def test_unseparable_open_meetings_survive_sector_gate():
    """A generic commission meeting handles electric, water and telecom
    together - it names no sector, so it never trips the gate. Superseded
    the earlier blanket open-meeting exemption, which was letting an
    explicitly water-only meeting through (NM 'Small Water Utilities')."""
    for t in ("Open Meeting", "Commission Meeting", "Administrative Session",
              "Regular Agenda/17-26", "Agenda Meeting - all dockets"):
        assert not classify.is_out_of_sector(t, "open_meeting"), t


@pytest.mark.parametrize("title", [
    "BLUE STREAM COMMUNICATIONS, LLC, D/B/A BLUE STREAM FIBER",
    "Virtual Hearing (26-2661-02, Ziply Fiber's ETC Application)",
    "HRG: 26G-0225CP - CPAN - EPX Rideshare, ALJ Garvey",
    "Special Open Meeting | Small Water Utilities",     # sector-specific meeting
])
def test_audit_found_out_of_sector(title):
    """Found by an independent audit using broader patterns than the filter."""
    assert classify.is_out_of_sector(title)


def test_nola_utility_committee_survives_sector_gate():
    """Its name lists cable and telecoms, but it is Entergy New Orleans'
    regulator - dropping it would lose ETR's venue."""
    assert not classify.is_out_of_sector(
        "Joint Utility, Cable, Telecommunications and Technology and Public Works")


def test_generic_open_meeting_survives_sector_gate():
    assert not classify.is_out_of_sector("Open Meeting", "open_meeting")
    assert not classify.is_out_of_sector("Regular Agenda/17-26", "open_meeting")


def test_toll_roads_are_out_of_sector():
    """Virginia's SCC regulates toll roads too - "Toll Road Investors
    Partnership II" was reaching the calendar as a utility hearing."""
    assert classify.is_out_of_sector(
        "PUR-2025-00191 - Application of Toll Road Investors Partnership II, "
        "L.P. for authority to increase toll rates", "evidentiary_hearing")


def test_alabama_transport_docket_prefix_is_out_of_sector():
    """Alabama numbers motor-carrier dockets TR#######. The carrier's trade
    name gives no keyword to catch - "RIDES ALL KINDS LLC" was showing up as
    one of only two Alabama dates on the dashboard."""
    assert classify.is_out_of_sector(
        "TR2641219 HEARING POSTPONEMENT for RIDES ALL KINDS LLC, D/B/A "
        "RIDES ALL KINDS", "evidentiary_hearing")
    # A real Alabama utility docket is untouched
    assert not classify.is_out_of_sector("ALABAMA POWER COMPANY", "evidentiary_hearing")


def test_railroad_commission_is_not_out_of_sector():
    """Guard on the rule above: the Texas Railroad Commission regulates GAS
    and is the single largest source in the registry. A 'railroad' term in
    the sector filter would silently empty it."""
    for t in ("RRC open meeting", "Railroad Commission of Texas Conference",
              "Atmos Mid-Tex Statement of Intent"):
        assert not classify.is_out_of_sector(t, "open_meeting"), t


def test_public_witness_signup_boilerplate_is_noise():
    """Virginia prints sign-up instructions under each hearing; the deadline
    inside them was being scraped as an event, inventing a hearing next to a
    real one."""
    assert classify.is_noise(
        "To register to speak as a public witness in the above proceeding, "
        "please submit the online Public Witness Form. Deadline to sign up "
        "is Oct. 15.")


def test_public_participation_is_a_comment_hearing():
    """VA heads its public-witness sessions 'Public Participation'. Without
    this the source's hearing type hint promoted them to Evidentiary
    Hearing, which the desk publishes."""
    assert classify.classify_event("Public Participation: Sept. 1, 2026")[0] == "public_comment"


@pytest.mark.parametrize("title", [
    "View Details of Open Meeting",                 # WI link label
    "Open Meetings for the Week of",                # WI index header
    "Hearing Schedule as of",                       # MD page header
    "01:00 pm CDT Other Not a PSC Hosted Meeting Lignite Energy Council "
    "Quarterly Meeting",                            # ND third-party event
    "Join Zoom Meeting",                            # WA join label
    "Join Microsoft Teams Meeting",
])
def test_listing_furniture_is_not_a_meeting(title):
    """These name a LISTING, not an event, and each one reads as a real
    meeting the moment the type patterns see 'open meeting' or 'hearing'
    inside it."""
    assert classify.is_noise(title)


@pytest.mark.parametrize("title", [
    "Open Meeting",
    "Commission Meeting - Week of August 17 agenda",
    "Evidentiary Hearing on the Schedule as of right filings",
])
def test_furniture_filter_spares_real_meetings(title):
    """Guard on the rule above: it must anchor at the end of the title, so a
    meeting that merely contains the words survives."""
    assert not classify.is_noise(title)


@pytest.mark.parametrize("url,machine", [
    ("https://psc.maryland.gov/wp-admin/admin-ajax.php?action=dgtlnk_events_calendar_ajax", True),
    ("https://calendar.google.com/calendar/ical/state.co.us_x%40group.calendar.google.com/public/basic.ics", True),
    ("https://outlook.office365.com/owa/calendar/abc@rrc.texas.gov/123/calendar.ics", True),
    ("https://minnesotapuc.legistar.com/View.ashx?M=IC&ID=1428564", True),
    ("https://lpscpubvalence.lpsc.louisiana.gov/portal/PSC/ReadScheduledEvents", True),
    # real pages a reader can use
    ("https://psc.maryland.gov/news-events/calendars/", False),
    ("https://www.rrc.texas.gov/general-counsel/open-meetings/", False),
    ("https://minnesotapuc.legistar.com/Calendar.aspx", False),
    ("https://iuc.iowa.gov/commission-activity/hearing-meeting-calendar", False),
])
def test_machine_links_are_recognised(url, machine):
    """A feed is the right thing to scrape and the wrong thing to click -
    it downloads an .ics or dumps raw JSON. 264 of 721 events pointed at
    one because they carried no link of their own."""
    assert classify.is_machine_link(url) is machine


def test_month_grid_day_numbers_are_not_a_meeting():
    """Minnesota's Legistar calendar produced one "event" that was the month
    grid's day numbers: "Aug, 2026: 27 28 29 30 31 3 4 5 6 7 10 11 ..." """
    assert classify.is_noise(
        "Aug, 2026: 27 28 29 30 31 3 4 5 6 7 10 11 12 13 14 17 18 19 20 21 "
        "24 25 26 27 28 31 1 Sep 2 3 4")
    # A real title carrying a couple of numbers survives
    assert not classify.is_noise("Docket Nos. E-21292 & E-22550 Hearing")
    assert not classify.is_noise("PUC Agenda Meeting 8/27/2026 at 10:00")


@pytest.mark.parametrize("url,placeholder", [
    ("https://www.psc.nd.gov/webdocs/case/NoDocs.html", True),
    ("https://x.gov/case/no-documents", True),
    ("https://x.gov/pagenotfound", True),
    ("https://www.psc.nd.gov/webdocs/case/26-0219/008-010.pdf", False),
    ("https://psc.maryland.gov/news-events/calendars/", False),
])
def test_placeholder_links_are_recognised(url, placeholder):
    """North Dakota hangs "No Documents / At this time, there are no documents
    available for this event" on 10 events. A destination that announces its
    own emptiness reads as though the hearing is not real."""
    assert classify.is_placeholder_link(url) is placeholder


def test_leading_date_fragment_is_noise():
    """Indiana's archived notice arrived as a fragment of a longer sentence.
    Caught structurally - a title that OPENS mid-date is the tail of
    something else, whatever year it names."""
    assert classify.is_noise("23, 2015 – Executive Session Meeting "
                             "(Cybersecurity Briefing) Public Notice posted on 10.20.15")
    assert not classify.is_noise("Evidentiary Hearing on the 2015 Fuel Clause")


@pytest.mark.parametrize("title,year", [
    # Every one of these was DELETED by a year-based staleness rule before it
    # was withdrawn. In this domain a year is usually part of a name.
    ("HRG: 26AL-0137E, Public Service Company - AL 2018 - Tariff 8 - Large Load", 2026),
    ("CDM: 24A-0442E Public Service Company - 2024 JTS, C3", 2026),
    ("Hearing (26-035-01, RMP's 2026 EBA)", 2027),
    ("PUR-2026-00076 - Application of Dominion Energy Virginia", 2027),
    ("U.S. Small Business Administration January 22-27, 2026 Severe Storm", 2027),
])
def test_year_in_a_name_never_deletes_a_hearing(title, year):
    """Tariff years, program years, storm years and docket filing years all
    read as "past" while describing a live proceeding. 19 real hearings went
    missing before this was caught - see states_a_past_year's docstring."""
    assert classify.states_a_past_year(title, year) is False


def test_trailing_timezone_goes_with_the_time():
    """North Dakota writes "9:00 AM CDT Formal Hearing ..."; taking only the
    clock left every ND title starting "CDT "."""
    assert classify.split_leading_time("9:00 AM CDT Formal Hearing - Case PU-26-82") == \
        ("Formal Hearing - Case PU-26-82", (9, 0))
    assert classify.split_leading_time("10:00 AM CST Regular Meeting") == \
        ("Regular Meeting", (10, 0))


def test_room_bookings_are_not_meetings():
    """Missouri books its hearing rooms on the calendar it publishes meetings
    on, and the word "hearing" made every booking an event."""
    assert classify.is_noise("Hearing Room 305 Reserved")
    assert classify.is_noise("Room 310 Reserved")
    assert not classify.is_noise("Evidentiary Hearing in Hearing Room 305")


def test_navigation_chrome_is_noise():
    assert classify.is_noise("eDockets Search OPUC Search About Us Contact Us "
                             "Commissioners General Information")


def test_filesize_with_trailing_word_is_stripped():
    assert classify.clean_title("Formal Hearing (380KB pdf) - Case No. P") == \
        "Formal Hearing - Case No. P"


def test_dedupe_collapses_title_variants_of_one_meeting():
    """Michigan publishes one Commission Meeting three ways across two pages."""
    from datetime import datetime as dt
    base = dict(commission="MI", commission_name="M", state="MI", tz="America/Detroit",
                event_type="open_meeting", event_type_label="Open Meeting / Commission Meeting",
                relevance="High", weight=3)
    day = dt(2026, 8, 27, tzinfo=ZoneInfo("America/Detroit"))
    evs = [Event(title="Commission Meeting", start=day, **base),
           Event(title="August 27, 2026 Commission Meeting", start=day, **base),
           Event(title="Open meeting", start=day, **base)]
    out = dedupe(evs)
    assert len(out) == 1, [e.title for e in out]


def test_dedupe_collapses_missouri_per_commissioner_rows():
    """psc.mo.gov publishes a calendar PER COMMISSIONER, so one meeting
    arrives up to five times under different initials. Missouri showed 16
    rows for 6 real meetings."""
    from datetime import datetime as dt
    base = dict(commission="MO", commission_name="M", state="MO", tz="America/Chicago",
                event_type="open_meeting", event_type_label="Open Meeting / Commission Meeting",
                relevance="High", weight=3)
    day = dt(2026, 8, 19, 9, 30, tzinfo=ZoneInfo("America/Chicago"))
    titles = ["HK 9:30am Public Meeting with SPP & MISO ( Via WebEx Only)",
              "CM 9:30am Public Meeting with SPP & MISO ( Via WebEx Only)",
              "KG 9:30am Public Meeting with SPP & MISO ( Via WebEx Only)",
              "MJ 9:30am Public Meeting with SPP & MISO ( Via WebEx Only)",
              "Adj 9:30am Public Meeting with SPP & MISO ( Via WebEx Only)"]
    evs = []
    for t in titles:
        clean, _tod = classify.split_leading_time(classify.clean_title(t))
        evs.append(Event(title=clean, start=day, **base))
    out = dedupe(evs)
    assert len(out) == 1, [e.title for e in out]
    assert out[0].title == "Public Meeting with SPP & MISO ( Via WebEx Only)"


def test_dedupe_collapses_one_meeting_described_by_two_rooms():
    """Where a meeting is held says nothing about WHICH meeting it is.
    Missouri prints the room inside the title, so one Agenda Meeting read as
    two."""
    from datetime import datetime as dt
    base = dict(commission="MO", commission_name="M", state="MO", tz="America/Chicago",
                event_type="open_meeting", event_type_label="Open Meeting / Commission Meeting",
                relevance="High", weight=3)
    day = dt(2026, 8, 12, 11, 0, tzinfo=ZoneInfo("America/Chicago"))
    evs = [Event(title="Agenda Meeting ( 310)", start=day, **base),
           Event(title="Agenda Meeting ( Hearing Room 310 and via WebEx)", start=day, **base)]
    assert len(dedupe(evs)) == 1


def test_docket_numbers_keep_same_day_hearings_apart():
    """The guard on both rules above. Louisiana runs a dozen hearings at
    09:30 that differ only by docket, and Illinois names cases the same way.
    Collapsing these would delete real proceedings."""
    from datetime import datetime as dt
    base = dict(commission="LA", commission_name="L", state="LA", tz="America/Chicago",
                event_type="evidentiary_hearing", event_type_label="Evidentiary Hearing",
                relevance="High", weight=3)
    day = dt(2026, 8, 26, 9, 30, tzinfo=ZoneInfo("America/Chicago"))
    evs = [Event(title=f"Hearing: T-379{n}", start=day, **base) for n in (77, 78, 80, 81)]
    assert len(dedupe(evs)) == 4


def test_dedupe_keeps_genuinely_different_events_same_day():
    from datetime import datetime as dt
    base = dict(commission="CO", commission_name="C", state="CO", tz="America/Denver",
                event_type="evidentiary_hearing", event_type_label="Evidentiary Hearing",
                relevance="High", weight=3)
    day = dt(2026, 9, 9, 9, 0, tzinfo=ZoneInfo("America/Denver"))
    evs = [Event(title="HRG: 26F-0122EG Stanley Wagon v Public Service", start=day, **base),
           Event(title="HRG: 26A-0173E Black Hills Colorado Electric", start=day, **base)]
    assert len(dedupe(evs)) == 2


def test_pdf_block_parser_recovers_hearings():
    """Indiana's weekly list: date / CAUSE NO. / TIME / ROOM / caption."""
    evs = extract._pdf_blocks(F.PDF_BLOCK_TEXT, ZoneInfo(TZ), NOW, "u")
    assert len(evs) == 2
    first = evs[0]
    assert first["start"].hour == 9 and first["start"].minute == 30
    assert "WESTFIELD GAS" in first["title"]
    assert "37389-GCA147" in first["title"]        # cause number carried
    assert first["location"].startswith("PNC")
    im = evs[1]
    assert im["start"].hour == 13                  # 1:00 P.M. -> 13:00
    assert "INDIANA MICHIGAN POWER" in im["title"]


@pytest.mark.parametrize("line,is_event", [
    ("Commission Business Meeting: August 18, 2026, 1:30 PM", True),
    ("Commission Scheduling Meeting: August 18, 2026, 1:00 PM", True),
    ("Date Published: August 13, 2026", False),
    ("For Week Commencing: August 17, 2026", False),
    ("Approval of the Commission Business Meeting Minutes for the week of", False),
    ("Internal Document Subject to Revision Revision Date", False),
])
def test_pdf_line_filter(line, is_event):
    """Montana's agenda mixes real meeting declarations with publication
    stamps and back-references to a past week's minutes."""
    assert (extract._PDF_NOT_AN_EVENT.search(line) is None) is is_event


def test_granicus_reads_the_time_florida_hides():
    """Florida's hearings carry a clock time in exactly one place: the
    Granicus table behind its events page. Generic parsing lost the time to
    non-breaking spaces and read " - 09:30 AM" as the tail of a range."""
    evs = extract.from_granicus(F.GRANICUS_HTML, ZoneInfo(TZ), NOW, "u")
    got = {e["title"]: e["start"] for e in evs}

    a = got["Service Hearing: 20260026-GU (Virtual)"]
    assert (a.hour, a.minute) == (9, 30) and a.day == 17
    b = got["Service Hearing: 20260026-GU (Virtual) - Hearing immediately following"]
    assert (b.hour, b.minute) == (13, 30)
    c = got["Hearing: 20260087-EM (Day:1)"]
    assert (c.hour, c.minute) == (11, 15) and c.day == 18

    # The archive table's player boilerplate is not an event
    assert not any("Windows Media Player" in t for t in got)
    # The agenda link is carried when the row has one
    assert any("AgendaViewer" in (e["url"] or "") for e in evs)


def test_granicus_epoch_is_pacific_wall_clock():
    """Granicus stores its hidden epoch as PACIFIC wall-clock. Reading it as
    the commission's own timezone would put every Florida hearing 3 hours
    late, so the displayed text is the authority and this is only a note on
    the fallback."""
    from datetime import datetime as dt
    assert dt.fromtimestamp(1786984200, ZoneInfo("America/Los_Angeles")).strftime("%H:%M") == "09:30"
    assert dt.fromtimestamp(1786984200, ZoneInfo("America/New_York")).strftime("%H:%M") == "12:30"


def test_same_day_different_times_are_different_sessions():
    """One title containing another is normally one meeting described twice -
    but not when the two disagree about the clock. Florida runs a 09:30
    service hearing and a 13:30 "Hearing immediately following"; merging on
    containment deleted the morning one."""
    from datetime import datetime as dt
    base = dict(commission="FL", commission_name="F", state="FL", tz="America/New_York",
                event_type="evidentiary_hearing", event_type_label="Evidentiary Hearing",
                relevance="High", weight=3)
    day = dt(2026, 8, 17, tzinfo=ZoneInfo("America/New_York"))
    evs = [Event(title="Service Hearing: 20260026-GU (Virtual)",
                 start=day.replace(hour=9, minute=30), **base),
           Event(title="Service Hearing: 20260026-GU (Virtual) - Hearing immediately following",
                 start=day.replace(hour=13, minute=30), **base)]
    assert len(dedupe(evs)) == 2


def test_timeless_docket_record_folds_into_the_timed_one():
    """Florida publishes each hearing twice: a schedule row with the docket
    and subject but no time, and a Granicus row with the time and session
    name. Same docket, same day - so the vague one goes and its subject rides
    along on every session that day."""
    from datetime import datetime as dt
    base = dict(commission="FL", commission_name="F", state="FL", tz="America/New_York",
                event_type="evidentiary_hearing", event_type_label="Evidentiary Hearing",
                relevance="High", weight=3)
    day = dt(2026, 8, 17, tzinfo=ZoneInfo("America/New_York"))
    vague = Event(title="Docket 20260026: Application for rate increase by Florida City Gas",
                  start=day, all_day=True, dockets=["20260026"], **base)
    timed = Event(title="Service Hearing: 20260026-GU (Virtual)",
                  start=day.replace(hour=9, minute=30), dockets=["20260026-GU"], **base)
    out = dedupe([vague, timed])
    assert len(out) == 1
    assert out[0].start.hour == 9
    assert "Application for rate increase by Florida City Gas" in out[0].title


def test_one_link_shared_by_every_row_is_navigation():
    """Florida's schedule page hangs the same "watch-archive" link on all 14
    of its rows. Followed, it lands on a page that never names the docket -
    while the page it came from spells the matter out in full. A single href
    repeated across a whole page is site chrome, not a per-event link."""
    from src.pscal.pipeline import _collapse_shared_links
    from datetime import datetime as dt
    base = dict(commission="FL", commission_name="F", state="FL", tz="America/New_York",
                event_type="evidentiary_hearing", event_type_label="Evidentiary Hearing",
                relevance="High", weight=3)
    day = dt(2026, 8, 17, tzinfo=ZoneInfo("America/New_York"))
    nav = "https://www.floridapsc.com/watch-archive-psc-events"
    src = "https://www.floridapsc.com/schedule-of-events"
    evs = [Event(title=f"Docket 2026006{n}: Something", start=day, url=nav, **base)
           for n in range(3)]
    _collapse_shared_links(evs, src, {})
    assert {e.url for e in evs} == {src}


def test_shared_link_survives_when_the_source_is_the_machine_one():
    """Massachusetts scrapes an API endpoint whose rows all carry the
    fileroom's own "#/hearings" page. That shared link is the HUMAN view and
    the source is the machine one - collapsing there hands the reader JSON."""
    from src.pscal.pipeline import _collapse_shared_links
    from datetime import datetime as dt
    base = dict(commission="MA", commission_name="M", state="MA", tz="America/New_York",
                event_type="evidentiary_hearing", event_type_label="Evidentiary Hearing",
                relevance="High", weight=3)
    day = dt(2026, 8, 17, tzinfo=ZoneInfo("America/New_York"))
    human = "https://eeaonline.eea.state.ma.us/dpu/fileroom/#/hearings"
    api = "https://eeaonline.eea.state.ma.us/dpu/fileroom/api/search/hearings/"
    evs = [Event(title=f"26-5{n} - Boston Gas", start=day, url=human, **base)
           for n in range(3)]
    _collapse_shared_links(evs, api, {})
    assert {e.url for e in evs} == {human}


def test_genuinely_per_event_links_are_kept():
    """Guard: only collapse when EVERY row agrees on one href."""
    from src.pscal.pipeline import _collapse_shared_links
    from datetime import datetime as dt
    base = dict(commission="MN", commission_name="M", state="MN", tz="America/Chicago",
                event_type="open_meeting", event_type_label="Open Meeting / Commission Meeting",
                relevance="High", weight=3)
    day = dt(2026, 8, 17, tzinfo=ZoneInfo("America/Chicago"))
    evs = [Event(title=f"PUC Agenda Meeting {n}", start=day,
                 url=f"https://x.gov/MeetingDetail.aspx?ID={n}", **base) for n in range(3)]
    before = [e.url for e in evs]
    _collapse_shared_links(evs, "https://x.gov/Calendar.aspx", {})
    assert [e.url for e in evs] == before


def test_field_style_titles_are_tidied():
    assert classify.clean_title(
        "Docket No : 20260026 ; Title: Application for rate increase by Florida City Gas."
    ) == "Docket 20260026: Application for rate increase by Florida City Gas"


def test_pdf_schedule_reads_year_from_header():
    """NJ's meeting notice states the year once and then lists bare dates,
    two to a line. Both other PDF shapes see nothing here."""
    evs = extract._pdf_schedule(F.PDF_SCHEDULE_TEXT, ZoneInfo(TZ), NOW, "u")
    got = {(e["start"].date().isoformat(), e["title"]) for e in evs}

    assert ("2026-09-23", "Regular Board Agenda Meetings") in got
    assert ("2026-12-16", "Regular Board Agenda Meetings") in got
    # Second column of a two-date line is not lost
    assert ("2026-11-20", "Regular Board Agenda Meetings") in got
    # Second section keeps its own heading; "Friday,June 12" has no space
    assert ("2026-09-18", "Quarterly Meetings") in got

    # The time sentence under each heading supplies the hour
    assert all(e["start"].hour == 10 for e in evs)
    # Past dates and the signature line are not events
    assert all(e["start"].year == 2026 for e in evs)
    assert not any("Dated" in e["title"] or "Lewis" in e["title"] for e in evs)


def test_pdf_schedule_needs_a_real_schedule():
    """Strict on purpose: prose that merely mentions a month must not
    become a calendar."""
    prose = ("2026 ANNUAL REPORT MEETING\nThe Commission met in August 4 to "
             "review the record.\n")
    assert extract._pdf_schedule(prose, ZoneInfo(TZ), NOW, "u") == []


def test_pdf_schedule_confidence_ignores_the_date_window():
    """Whether the shape matched and whether a date is still ahead are
    different questions. Late in a year almost every date on the notice has
    passed; scoring the shape on the survivors would drop the meetings that
    are still to come."""
    mostly_past = """2026 REGULAR BOARD AGENDA MEETINGS
The Board meetings will be held at 10:00 a.m.
Wednesday, January 14 Wednesday, February 18
Wednesday, March 4
Wednesday, September 9
"""
    evs = extract._pdf_schedule(mostly_past, ZoneInfo(TZ), NOW, "u")
    assert [e["start"].date().isoformat() for e in evs] == ["2026-09-09"]


def test_pdf_month_only_titles_rejected():
    """A line whose only words are month names is a date range, not an event."""
    assert "august" in extract._MONTH_WORDS


@pytest.mark.parametrize("etype,code,title,dropped", [
    # the desk's 2026-08-17 open-meeting filters
    ("open_meeting", "MD", "[CANCELED] Administrative Meeting", True),
    ("open_meeting", "MN", "Cancelled - Consent Items Only", True),
    ("open_meeting", "PA", "FY2027 Budget Hearing", True),
    ("open_meeting", "CO", "Agenda Unavailable", True),
    ("open_meeting", "UT", "Commission Meeting", True),      # Utah open meetings
    # ... but Utah HEARINGS still publish, and the words only bite open meetings
    ("evidentiary_hearing", "UT", "Hearing (26-035-01, RMP's 2026 EBA)", False),
    ("evidentiary_hearing", "CO", "[CANCELED] HRG: 26F-0246EG Formal Complaint", False),
    ("open_meeting", "MD", "Administrative Meeting", False),
    ("open_meeting", "PA", "Public Meeting", False),
])
def test_desk_publish_filters(etype, code, title, dropped):
    """Policy applied after typing: these events are correctly identified,
    the desk simply does not want them. Configured in coverage.yaml so the
    word list can change without touching code."""
    assert bool(classify.is_filtered_by_desk(etype, code, title)) is dropped


def test_desk_filter_reports_why():
    """The health panel shows the reason, so a silent drop is never a
    mystery."""
    why = classify.is_filtered_by_desk("open_meeting", "UT", "Commission Meeting")
    assert "UT" in why
    why = classify.is_filtered_by_desk("open_meeting", "MD", "Budget Meeting")
    assert "budget" in why.lower()


# ------------------------------------------------------------ desk exclusions

def _ex_events():
    from datetime import datetime as dt
    base = dict(commission_name="N", state="ND", tz="America/Chicago",
                event_type="open_meeting", event_type_label="Open Meeting / Commission Meeting",
                relevance="High", weight=3)
    day = dt(2026, 8, 26, 10, 0, tzinfo=ZoneInfo("America/Chicago"))
    return [
        Event(commission="ND", title="Regular Meeting - Internet Broadcast", start=day, **base),
        Event(commission="ND", title="Regular Meeting - Internet Broadcast",
              start=day.replace(month=9, day=9), **base),
        Event(commission="ND", title="Formal Hearing Case No. PU-26-164", start=day, **base),
        Event(commission="MD", title="Regular Meeting - Internet Broadcast", start=day, **base),
    ]


def test_exclusion_by_single_event(monkeypatch):
    from src.pscal import exclusions
    monkeypatch.setattr(exclusions, "_raw", lambda: {"events": [
        {"commission": "ND", "date": "2026-08-26",
         "title": "  formal HEARING   case no. PU-26-164 "},   # case/space insensitive
    ]})
    kept, rules, n = exclusions.apply(_ex_events())
    assert n == 1
    assert not any("PU-26-164" in e.title for e in kept)
    assert any(e.commission == "MD" for e in kept)


def test_exclusion_recurring_covers_every_occurrence(monkeypatch):
    """The reason recurring rules exist: dropping one instance of a monthly
    meeting is a decision you would have to make again next month."""
    from src.pscal import exclusions
    monkeypatch.setattr(exclusions, "_raw", lambda: {"recurring": [
        {"commission": "ND", "title_contains": "Regular Meeting - Internet Broadcast"},
    ]})
    kept, rules, n = exclusions.apply(_ex_events())
    assert n == 2                                   # August AND September
    assert [e.commission for e in kept] == ["ND", "MD"]
    assert rules[0].hits == 2


def test_exclusion_is_scoped_to_its_commission(monkeypatch):
    from src.pscal import exclusions
    monkeypatch.setattr(exclusions, "_raw", lambda: {"recurring": [
        {"commission": "MD", "title_contains": "Regular Meeting"},
    ]})
    kept, _r, n = exclusions.apply(_ex_events())
    assert n == 1 and all(e.commission == "ND" for e in kept)


def test_stale_exclusion_is_reported(monkeypatch):
    """A rule that matches nothing usually means the commission retitled the
    event - so what the desk hid is back, and that must not be silent."""
    from src.pscal import exclusions
    monkeypatch.setattr(exclusions, "_raw", lambda: {
        "recurring": [{"commission": "ND", "title_contains": "nothing matches this"}],
        "events": [
            {"commission": "ND", "date": "2020-01-01", "title": "long gone"},
            {"commission": "ND", "date": "2027-01-01", "title": "not seen yet"},
        ],
    })
    _kept, rules, _n = exclusions.apply(_ex_events())
    labels = " ".join(r.label() for r in exclusions.stale(rules, "2026-08-18"))
    assert "nothing matches this" in labels     # recurring that never fires
    assert "not seen yet" in labels             # future date that should have matched
    assert "long gone" not in labels            # simply spent, not broken
    assert [r.label() for r in exclusions.spent(rules, "2026-08-18")] == \
        ["ND 2020-01-01 long gone"]


def test_no_exclusions_file_publishes_everything(monkeypatch):
    """Exclusion, never selection: the default is that everything publishes."""
    from src.pscal import exclusions
    monkeypatch.setattr(exclusions, "_raw", lambda: {})
    evs = _ex_events()
    kept, rules, n = exclusions.apply(evs)
    assert kept == evs and rules == [] and n == 0


@pytest.mark.parametrize("template,expected", [
    ("https://puc.sd.gov/agendas/{year}/default.aspx",
     "https://puc.sd.gov/agendas/2027/default.aspx"),
    ("https://oklahoma.gov/occ/public-meetings/{year}-commission-meetings.html",
     "https://oklahoma.gov/occ/public-meetings/2027-commission-meetings.html"),
    ("https://x.gov/?from={today}", "https://x.gov/?from=01/05/2027"),
    ("https://x.gov/plain", "https://x.gov/plain"),
])
def test_year_baked_urls_roll_over_on_their_own(template, expected):
    """Six commissions publish at a path with the year in it. Hard-coding it
    means those sources silently go stale every January - the scrape keeps
    "working", it just stops finding this year's dates."""
    from datetime import datetime as dt
    from src.pscal.fetch import expand_url
    assert expand_url(template, dt(2027, 1, 5)) == expected
