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
def _type_rules() -> list[tuple[str, str, int, list[re.Pattern]]]:
    rules = []
    for t in load_coverage()["event_types"]:
        pats = [re.compile(re.escape(p).replace(r"\ ", r"\s+"), re.IGNORECASE)
                for p in t.get("patterns", [])]
        rules.append((t["id"], t["label"], t.get("weight", 1), pats))
    return rules


def classify_type(text: str) -> tuple[str, str, int]:
    for tid, label, weight, pats in _type_rules():
        if any(p.search(text) for p in pats):
            return tid, label, weight
    return "other", "Other", 1


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


def clean_title(title: str) -> str:
    """Strip link decorations that leak into scraped titles -
    "Agenda (122.64 KB) .pdf (Amended)" -> "Agenda (Amended)"."""
    t = _SIZE_DECOR.sub(" ", title or "")
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
