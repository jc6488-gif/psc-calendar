"""Events the desk reviewed and does not want on the calendar.

This is the one place where a human overrules the scrape. It is a DELETION
mechanism, so it is built to fail loudly rather than quietly:

  * exclusion, never selection - an event nobody has reviewed still publishes,
    so a hearing cannot go missing because the team has not got to it yet;
  * every drop is counted and shown on the dashboard, so an empty week never
    reads as "nothing scheduled" when it means "we hid it";
  * entries that stop matching anything are reported as STALE, because a
    commission retitling an event would otherwise bring it silently back.

Two kinds of entry, because most of what a desk drops recurs:

    events:                       one specific date
      - commission: ND
        date: 2026-08-26
        title: Regular Meeting - Internet Broadcast
    recurring:                    every time it appears
      - commission: ND
        title_contains: Regular Meeting - Internet Broadcast

Dropping a single instance of a monthly meeting is a decision you have to make
again every month; `recurring` makes it once.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

DATA = Path(__file__).resolve().parents[2] / "data"
FILE = DATA / "exclusions.yaml"


def _norm(s: str) -> str:
    """Compare titles the way a person would - case and spacing are noise."""
    return re.sub(r"\s+", " ", (s or "").strip().lower())


@dataclass
class Rule:
    kind: str            # "event" | "recurring"
    commission: str
    date: str = ""       # YYYY-MM-DD, event rules only
    title: str = ""
    reason: str = ""
    hits: int = 0

    def label(self) -> str:
        where = f"{self.commission} {self.date}".strip()
        return f"{where} {self.title}".strip()


@lru_cache(maxsize=1)
def _raw() -> dict:
    if not FILE.exists():
        return {}
    return yaml.safe_load(FILE.read_text()) or {}


def load_rules() -> list[Rule]:
    d = _raw()
    out: list[Rule] = []
    for e in d.get("events") or []:
        out.append(Rule("event", str(e.get("commission", "")).upper(),
                        str(e.get("date", "")), _norm(e.get("title", "")),
                        str(e.get("reason", ""))))
    for e in d.get("recurring") or []:
        out.append(Rule("recurring", str(e.get("commission", "")).upper(),
                        "", _norm(e.get("title_contains", "")),
                        str(e.get("reason", ""))))
    return out


def apply(events: list) -> tuple[list, list[Rule], int]:
    """Split events into (kept, rules, dropped_count), tallying hits per rule."""
    rules = load_rules()
    if not rules:
        return events, [], 0

    by_comm: dict[str, list[Rule]] = {}
    for r in rules:
        by_comm.setdefault(r.commission, []).append(r)

    kept = []
    dropped = 0
    for ev in events:
        title = _norm(ev.title)
        day = ev.start.date().isoformat()
        hit = None
        for r in by_comm.get(ev.commission, ()):
            if r.kind == "event":
                if r.date == day and r.title == title:
                    hit = r
                    break
            elif r.title and r.title in title:
                hit = r
                break
        if hit is not None:
            hit.hits += 1
            dropped += 1
            continue
        kept.append(ev)
    return kept, rules, dropped


def stale(rules: list[Rule], today: str) -> list[Rule]:
    """Rules that matched nothing and are not simply in the past.

    A dated rule whose day has gone is spent, not broken - it should be pruned
    for tidiness but says nothing is wrong. A rule for a FUTURE date, or any
    recurring rule, that matches nothing means the event it named has been
    retitled or removed, and the thing the desk hid may well be back.
    """
    out = []
    for r in rules:
        if r.hits:
            continue
        if r.kind == "event" and r.date and r.date < today:
            continue
        out.append(r)
    return out


def spent(rules: list[Rule], today: str) -> list[Rule]:
    """Dated rules whose date has passed - safe to delete from the file."""
    return [r for r in rules
            if r.kind == "event" and r.date and r.date < today]
