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
]


def is_noise(title: str) -> bool:
    if len(title.strip()) < 8:
        return True
    return any(p.search(title) for p in NOISE)


_SIZE_DECOR = re.compile(r"\(\s*\d+(?:\.\d+)?\s*[KMG]?B\b[^)]*\)", re.I)
_FILE_DECOR = re.compile(r"\.(?:pdf|docx?|xlsx?)\b", re.I)


_VACATED = re.compile(r"^\s*VACATED\s*:\s*", re.I)


def clean_title(title: str) -> str:
    """Strip link decorations that leak into scraped titles -
    "Agenda (122.64 KB) .pdf (Amended)" -> "Agenda (Amended)"."""
    t = _VACATED.sub("[CANCELED] ", title or "")
    t = _SIZE_DECOR.sub(" ", t)
    t = _FILE_DECOR.sub(" ", t)
    t = re.sub(r"\(\s*\)", " ", t)
    t = re.sub(r"\s+\)", ")", t)
    return re.sub(r"\s+", " ", t).strip(" -\u2013|,")


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
    r"moving compan|\bbus\b|pilotage|rideshare|\bTNC\b", re.I)


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
