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
]


def is_noise(title: str) -> bool:
    if len(title.strip()) < 8:
        return True
    return any(p.search(title) for p in NOISE)


_SIZE_DECOR = re.compile(r"\(\s*\d+(?:\.\d+)?\s*[KMG]?B\s*\)", re.I)
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
