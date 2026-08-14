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
    re.compile(r"^\s*(?:copyright|privacy|accessibility|contact us|site map|back to top)", re.I),
    re.compile(r"^\s*(?:previous|next|page \d+|view all|read more|learn more)\s*$", re.I),
    re.compile(r"^\s*\d{1,2}\s*$"),
]


def is_noise(title: str) -> bool:
    if len(title.strip()) < 8:
        return True
    return any(p.search(title) for p in NOISE)
