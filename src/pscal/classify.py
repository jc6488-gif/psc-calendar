"""Classify what kind of regulatory date an event is."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

DATA = Path(__file__).resolve().parents[2] / "data"


@lru_cache(maxsize=1)
def load_coverage() -> dict:
    return yaml.safe_load((DATA / "coverage.yaml").read_text())


@lru_cache(maxsize=1)
def load_commissions() -> list[dict]:
    return yaml.safe_load((DATA / "commissions.yaml").read_text())["commissions"]


@lru_cache(maxsize=1)
def _type_rules() -> list[tuple[str, str, int, str, list[re.Pattern]]]:
    rules = []
    for t in load_coverage()["event_types"]:
        pats = [re.compile(re.escape(p).replace(r"\ ", r"\s+"), re.IGNORECASE)
                for p in t.get("patterns", [])]
        rules.append((t["id"], t["label"], t.get("weight", 1),
                      t.get("relevance", "Low"), pats, t.get("publish", True)))
    return rules


def classify_type(text: str) -> tuple[str, str, int, str]:
    """Returns (type_id, label, weight, relevance). The type says WHAT the
    event is; relevance says how much the desk should care - the old scheme
    conflated the two by filing open meetings under Decision / Order."""
    for tid, label, weight, relevance, pats, _pub in _type_rules():
        if any(p.search(text) for p in pats):
            return tid, label, weight, relevance
    return "other", "Other", 1, "Low"


def classify_event(title: str, desc: str = "") -> tuple[str, str, int, str]:
    """Classify an event from its title, falling back to the description.

    The title states WHAT an event is; the description is supporting context
    that routinely mentions other, unrelated dates. Matching one blob of
    title+description let whichever rule sits earliest in coverage.yaml win,
    and `procedural` sits first - so NY's "Commencement of evidentiary hearing
    in the Universal Service Fund proceeding" was filed as a Procedural
    Milestone (unpublished) because its description mentioned comments due,
    and vanished from the calendar. A dropped hearing is the worst failure
    this tool can produce, so the title decides whenever it says anything at
    all; the description only breaks ties for genuinely uninformative titles
    ("Notice", "Hearing Room 2E").
    """
    tid, label, weight, relevance = classify_type(title)
    if tid != "other":
        return tid, label, weight, relevance
    return classify_type(f"{title} {desc}")


def is_published(tid: str) -> bool:
    """Whether this event type is emitted at all. The desk narrowed the
    calendar to Evidentiary Hearing / Open Meeting / Decision-Order on
    2026-08-17; everything else is classified (so we know what it is) and
    then dropped. Anything IMPORTANT landing in an unpublished type is a
    classifier bug to fix, not an acceptable loss."""
    for t, _l, _w, _r, _p, pub in _type_rules():
        if t == tid:
            return bool(pub)
    return False


def type_info(tid: str) -> tuple[str, str, int, str]:
    """Look up a type id directly - used when a source knows what its events
    are (the MA hearings API serves only hearings) but titles lack keywords."""
    for t, label, weight, relevance, _pats, _pub in _type_rules():
        if t == tid:
            return t, label, weight, relevance
    return "other", "Other", 1, "Low"


NOISE = [
    re.compile(r"^\s*(?:no |there are no )", re.I),
    re.compile(r"^\s*(?:generated on|page generated|last updated|page last updated)\b", re.I),
    re.compile(r"^\s*(?:copyright|privacy|accessibility|contact us|site map|back to top)", re.I),
    re.compile(r"^\s*(?:previous|next|page \d+|view all|read more|learn more)\s*$", re.I),
    re.compile(r"^\s*\d{1,2}\s*$"),
    re.compile(r"^\s*location\s*:", re.I),
    # Site navigation swept up as an "event" - Oregon's eDockets header did
    # this. Two or more nav labels in one title is never a real meeting.
    re.compile(r"(?:About Us|Contact Us|Site ?map|Skip to (?:main|content)|"
               r"Home\s+About|Search\b.*\bSearch\b).*(?:About Us|Contact Us|"
               r"General Information|Commissioners|Privacy)", re.I),
    # Sign-up instructions printed under each hearing on Virginia's webcast
    # schedule. The "Deadline to sign up is Oct. 15" inside them was being
    # scraped as an event of its own, inventing a hearing that does not exist
    # on a date next to one that does.
    re.compile(r"to (?:register|sign up) to speak|public witness form|"
               r"deadline to sign up", re.I),
    # Index furniture that names a LISTING rather than an event: Wisconsin's
    # "View Details of Open Meeting" link label, and headers whose date was
    # stripped out of the title ("Open Meetings for the Week of",
    # "Hearing Schedule as of"). These read as real meetings once the type
    # patterns see "open meeting" or "hearing" in them.
    re.compile(r"^\s*view details\b", re.I),
    # A conferencing join label, not a meeting. Washington prints one beside
    # the hearing it belongs to, so this is a duplicate of a real event.
    re.compile(r"^\s*join\s+(?:the\s+)?(?:a\s+)?"
               r"(?:zoom|microsoft ?teams|teams|webex|google ?meet|skype)\b", re.I),
    re.compile(r"\b(?:week of|as of)\s*$", re.I),
    # North Dakota lists third-party events on its calendar and marks them
    # plainly. A trade association's quarterly meeting is not a regulatory
    # date, whoever attends it.
    re.compile(r"\bnot a .{0,12}hosted (?:meeting|event)", re.I),
    # A room held open is not a proceeding. Missouri books its hearing rooms
    # on the same calendar it publishes meetings on ("Hearing Room 305
    # Reserved"), and "hearing" in the title made every booking an event.
    re.compile(r"\broom\b[^,;]{0,24}\breserved\b", re.I),
]


# A month-grid's day numbers, scraped as one event: Minnesota's Legistar
# calendar produced "Aug, 2026: 27 28 29 30 31 3 4 5 6 7 10 11 12 13 14 ...".
# Any title carrying a long run of bare day numbers is the calendar's
# furniture, not a meeting on it.
_DAY_RUN = re.compile(r"(?:\b\d{1,2}\b[\s,]+){5,}\b\d{1,2}\b")


def is_noise(title: str) -> bool:
    if len(title.strip()) < 8:
        return True
    if _DAY_RUN.search(title or ""):
        return True
    return any(p.search(title) for p in NOISE)


_SIZE_DECOR = re.compile(r"\(\s*\d+(?:\.\d+)?\s*[KMG]?B\b[^)]*\)", re.I)
_FILE_DECOR = re.compile(r"\.(?:pdf|docx?|xlsx?)\b", re.I)


_VACATED = re.compile(r"^\s*VACATED\s*:\s*", re.I)

# Maryland posts the weeks it is NOT sitting as "NO Administrative Meeting",
# on the calendar, at the meeting's usual slot. That reads as a meeting to
# every type pattern we have, so it would publish as a real one - telling her
# to hold a morning the commission has explicitly cleared. Case-sensitive:
# only a shouted "NO" is the cancellation marker, so "Notice", "November" and
# a docket "No. 12345" are untouched.
_NO_MEETING = re.compile(r"^\s*NO\s+(?![.\d])")

# A bare URL and everything trailing it inside the same parenthetical.
# Connecticut's XPages calendar renders each entry as
# "REGULAR MEETING (https://www.youtube.com/@ConnecticutPURA/streams When
# available ...)" - the join link and its blurb, not part of the event name.
_URL_DECOR = re.compile(r"\(\s*https?://\S+[^)]*\)|\s*https?://\S+")

# The label that introduced the URL, left dangling once the URL is gone:
# "Commission Business Meeting Url:" -> "Commission Business Meeting".
# Only ever applied when a URL was actually removed - these are ordinary
# words otherwise, and stripping one unconditionally turned Maine's
# "Deliberations Audio/Video" into "Deliberations Audio/".
_URL_LABEL = re.compile(
    r"\s*[-–|]?\s*\b(?:url|link|stream|webcast|video|register|join)\s*:?\s*$", re.I)

# "09:00 am - 26-03-15 CWC Rate Case Evidentiary Hearing" - the time PURA
# prints ahead of every title. Captured so it can set the real start time
# rather than being deleted or left to read as an all-day event.
# The optional initials group is Missouri: psc.mo.gov publishes a calendar
# PER COMMISSIONER, so one meeting appears up to five times as "HK 9:30am
# Public Meeting...", "CM 9:30am Public Meeting...", "Adj 9:30am ...". Once
# the owner's initials and the repeated time come off the front, the rows are
# identical and dedupe collapses them to the single meeting they describe.
LEADING_TIME = re.compile(
    r"^\s*(?:[A-Z][A-Za-z]{0,3}\s+)?"
    r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?\s*(?:[-–:]\s*|(?=[A-Z]))", re.I)


def clean_title(title: str) -> str:
    """Strip link decorations that leak into scraped titles -
    "Agenda (122.64 KB) .pdf (Amended)" -> "Agenda (Amended)"."""
    t = _VACATED.sub("[CANCELED] ", title or "")
    t = _NO_MEETING.sub("[CANCELED] ", t)
    t = _SIZE_DECOR.sub(" ", t)
    t, had_url = _URL_DECOR.subn(" ", t)
    if had_url:
        t = _URL_LABEL.sub("", t.rstrip())
    t = _FILE_DECOR.sub(" ", t)
    t = re.sub(r"\(\s*\)", " ", t)
    t = re.sub(r"\s+\)", ")", t)
    t = re.sub(r"\s+", " ", t).strip(" -\u2013|,")
    return tidy_field_title(t)


# Florida's schedule page emits its own field names into the title:
# "Docket No : 20260026 ; Title: Application for rate increase by Florida City
# Gas." Keep the docket - it is what ties this record to the timed one on the
# Granicus feed - but say it the way a person would.
_FIELD_TITLE = re.compile(
    r"^\s*Docket\s*No\.?\s*:?\s*([\w-]+)\s*;\s*Title\s*:\s*(.+)$", re.I)


def tidy_field_title(title: str) -> str:
    m = _FIELD_TITLE.match(title or "")
    if not m:
        return title
    return f"Docket {m.group(1)}: {m.group(2).strip().rstrip('.')}"


def split_leading_time(title: str) -> tuple[str, tuple[int, int] | None]:
    """Pull a "09:00 am - " prefix off a title, returning (title, (hh, mm)).

    Some calendars print the hour in the title and give the row no machine
    time, so the event arrives looking all-day. Showing a 9am hearing as
    all-day misleads anyone planning a day around it, and deleting the prefix
    would lose the only copy of the time there is.
    """
    m = LEADING_TIME.match(title or "")
    if not m:
        return title, None
    hh = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0)
    rest = title[m.end():].strip(" -\u2013:|,")
    if not rest:
        return title, None
    return rest, (hh, int(m.group(2) or 0))


# Words that carry no meaning on their own in a calendar-entry title. A title
# made ONLY of these (plus digits/dates) tells the reader nothing.
_STOP = {
    "agenda", "agendas", "final", "amended", "pdf", "minutes", "minute",
    "recording", "recordding", "watch", "live", "posted", "view", "download",
    "all", "commissioners", "commissioner",
    "attachments", "attachment", "date", "time", "times",
    "to", "through", "of", "the", "and", "a", "an", "for", "am", "pm", "pt",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri", "sat", "sun",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec",
}


def is_uninformative(title: str) -> bool:
    """True when the title is only dates and filler ("8/17/26 to 8/21/26",
    "25 Minutes", "(Tuesday)", "Agenda Watch Live") - the department wants
    titles that say what the thing IS, with "Open meeting" as the honest
    fallback when the page tells us nothing more."""
    tokens = re.findall(r"[A-Za-z]{2,}", (title or "").lower())
    return all(t in _STOP for t in tokens)


# ---------------------------------------------------------------- link hygiene
# Feeds and data endpoints. They are the best thing to SCRAPE and the worst
# thing to hand a reader: clicking one downloads an .ics, or dumps raw JSON
# with escaped markup in it. 264 of 721 events pointed at one of these -
# Maryland's WordPress admin-ajax handler, Colorado's Google Calendar .ics,
# the Railroad Commission's Outlook feed - because the event carried no link
# of its own and fell back to the page it was scraped from.
_MACHINE_LINK = re.compile(
    r"\.ics(?:$|[?#])|/ical/|admin-ajax\.php|"
    r"outlook\.office365\.com/owa/calendar|"
    r"legistar\.com/View\.ashx|webapi\.legistar\.com|"
    r"/ReadScheduledEvents\b|"
    # A path segment literally named api. Massachusetts scrapes
    # .../dpu/fileroom/api/search/hearings/ while its readable page is
    # .../dpu/fileroom/#/hearings.
    r"/api/",
    re.I)


def is_machine_link(url: str) -> bool:
    """True when a URL serves data rather than a page a person can read."""
    return bool(_MACHINE_LINK.search(url or ""))


# --------------------------------------------------------------- sector gate
# The desk covers electric and gas names only. State commissions also regulate
# water, telecom and (in CO) tow trucks and passenger carriers - those matters
# are dropped. An OPEN MEETING is never dropped: one commission meeting
# disposes of every kind of case it handles, so it is inherently unseparable
# and may well contain the electric/gas item that matters.
_ENERGY = re.compile(
    r"\belectric|\bgas\b|natural gas|\bLNG\b|\bpower\b|energy|\bsolar\b|\bwind\b|"
    r"transmission|generation|\bIRP\b|resource plan|\bkwh\b|fuel|coal|nuclear|"
    r"pipeline|propane|rate case|\bOG-\d", re.I)
_NON_ENERGY = re.compile(
    r"\bwater\b|wastewater|\bsewer|sewage|\baqua\b|artesian|tidewater|"
    r"\bCLEC\b|telecom|telephone|broadband|\bVoIP\b|\bcable\b|\b911\b|E-?911|"
    r"universal service|\bwireless\b|\bfiber\b|\bILEC\b|"
    r"motor carrier|\btowing\b|\btow\b|taxi|limousine|household goods|"
    r"moving compan|\bbus\b|pilotage|rideshare|\bTNC\b|"
    # Virginia's SCC also regulates toll roads - "Toll Road Investors
    # Partnership II" was reaching the calendar as a utility hearing.
    # NOT "railroad": the Texas Railroad Commission is a GAS regulator and
    # the single largest source in the registry.
    r"toll road|turnpike|"
    # Alabama numbers its motor-carrier dockets TR#######, which is a far
    # more reliable marker than the carrier's trade name ("RIDES ALL KINDS
    # LLC" names no sector a keyword list would catch).
    r"\bTR\d{6,}\b", re.I)


# The New Orleans City Council committee whose remit INCLUDES Entergy New
# Orleans - its name lists cable and telecoms, but dropping it would lose
# ETR's regulator.
_SECTOR_EXEMPT = re.compile(r"utility,\s*cable,\s*telecommunications", re.I)


def is_out_of_sector(title: str, event_type: str = "") -> bool:
    """True when the TITLE clearly marks a non-electric/gas matter.

    Title only - descriptions carry venue boilerplate that false-positives.
    Requires a non-energy signal AND no energy signal, so mixed matters
    (an oil-and-gas produced-water docket, a combined water/electric
    utility) are kept. When in doubt, keep: a wrongly dropped hearing is
    the worst failure this tool can produce.

    Open meetings are NOT blanket-exempt. A generic "Open Meeting" names no
    sector at all, so it never trips this test and is kept - which is the
    unseparable case the desk wanted preserved. But a meeting explicitly
    titled for one non-energy sector ("Special Open Meeting | Small Water
    Utilities") IS separable, and goes.
    """
    t = title or ""
    if _SECTOR_EXEMPT.search(t):
        return False
    return bool(_NON_ENERGY.search(t)) and not _ENERGY.search(t)
