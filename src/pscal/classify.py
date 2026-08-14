"""Attribute events to covered tickers and classify what kind of date they are."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import yaml

DATA = Path(__file__).resolve().parents[2] / "data"


@lru_cache(maxsize=1)
def load_coverage() -> dict:
    return yaml.safe_load((DATA / "coverage.yaml").read_text())


@lru_cache(maxsize=1)
def load_commissions() -> list[dict]:
    return yaml.safe_load((DATA / "commissions.yaml").read_text())["commissions"]


def _boundary_pattern(term: str) -> re.Pattern:
    """Word-boundary match so 'APS ' doesn't hit 'lapse' and 'SCE ' doesn't hit 'scene'."""
    esc = re.escape(term.strip())
    esc = esc.replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![A-Za-z0-9]){esc}(?![A-Za-z0-9])", re.IGNORECASE)


@lru_cache(maxsize=1)
def _company_matchers() -> list[tuple[str, list[re.Pattern], set[str], list[dict]]]:
    out = []
    for c in load_coverage()["companies"]:
        pats = [_boundary_pattern(m) for m in c.get("match", [])]
        subs = c.get("subsidiaries", []) or []
        comms = {code for s in subs for code in s.get("commissions", [])}
        out.append((c["ticker"], pats, comms, subs))
    return out


@lru_cache(maxsize=1)
def _type_rules() -> list[tuple[str, str, int, list[re.Pattern]]]:
    rules = []
    for t in load_coverage()["event_types"]:
        pats = [re.compile(re.escape(p).replace(r"\ ", r"\s+"), re.IGNORECASE)
                for p in t.get("patterns", [])]
        rules.append((t["id"], t["label"], t.get("weight", 1), pats))
    return rules


@lru_cache(maxsize=1)
def _rate_signals() -> list[tuple[str, re.Pattern]]:
    return [(s, re.compile(re.escape(s).replace(r"\ ", r"\s+"), re.IGNORECASE))
            for s in load_coverage()["rate_case_signals"]]


def match_companies(text: str, commission_code: str) -> tuple[list[str], list[str]]:
    """Return (tickers, subsidiary_names) mentioned in the text.

    The commission code is used as a tiebreaker, not a filter: a Georgia Power
    item on the FERC calendar should still tag SO.
    """
    tickers: list[str] = []
    subs: list[str] = []
    for ticker, pats, comms, sub_defs in _company_matchers():
        hit = any(p.search(text) for p in pats)
        if not hit:
            continue
        tickers.append(ticker)
        for s in sub_defs:
            name = s.get("name", "")
            abbr = s.get("abbr")
            if commission_code in (s.get("commissions") or []):
                if _boundary_pattern(name).search(text) or (
                    abbr and _boundary_pattern(abbr).search(text)
                ):
                    subs.append(name)
        if not subs:
            for s in sub_defs:
                if commission_code in (s.get("commissions") or []):
                    subs.append(s["name"])
                    break
    return tickers, subs


def classify_type(text: str) -> tuple[str, str, int]:
    for tid, label, weight, pats in _type_rules():
        if any(p.search(text) for p in pats):
            return tid, label, weight
    return "other", "Other", 1


def detect_rate_case(text: str) -> tuple[bool, list[str]]:
    hits = [s for s, p in _rate_signals() if p.search(text)]
    return bool(hits), hits[:6]


NOISE = [
    re.compile(r"^\s*(?:no |there are no )", re.I),
    re.compile(r"^\s*(?:copyright|privacy|accessibility|contact us|site map|back to top)", re.I),
    re.compile(r"^\s*(?:previous|next|page \d+|view all|read more|learn more)\s*$", re.I),
    re.compile(r"^\s*\d{1,2}\s*$"),
]


def is_noise(title: str) -> bool:
    if len(title.strip()) < 8:
        return True
    return any(p.search(title) for p in NOISE)
