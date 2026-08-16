"""Core data model for a regulatory calendar event."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from typing import Optional


DOCKET_PATTERNS = [
    # Ordered most-specific first.
    r"\b\d{2}-\d{4,5}-[A-Z]{2,4}-[A-Z]{2,4}\b",      # KS: 25-EKCE-315-RTS
    r"\b[A-Z]{1,3}-\d{2}-\d{2,4}\b",                  # OR: UE-24-101
    r"\b\d{2}-[A-Z]{1,4}-\d{2,5}\b",                  # NY: 24-E-0123
    r"\b\d{4,5}-[A-Z]{2}-\d{3}\b",                    # WI: 6690-UR-129
    r"\b[A-Z]{2}\d{2}-\d{2,4}\b",                     # MS: EC-24-101
    # Keyword-led forms. "Cause" is Indiana, "Application"/"Proceeding" are CA/NY.
    r"\b(?:Docket|Case|Cause|Proceeding|Application|Matter)\s+"
    r"(?:Nos?\.?\s*)?([A-Z]{0,4}[\-\.]?\d[A-Z0-9\-\./]{2,24})",
    r"\bER\d{2}-\d{3,5}(?:-\d{3})?\b",                # FERC electric
    r"\bRP\d{2}-\d{3,5}(?:-\d{3})?\b",                # FERC gas
    r"\bEL\d{2}-\d{2,5}(?:-\d{3})?\b",                # FERC complaint
    r"\bA\.\d{2}-\d{2}-\d{3}\b",                      # CPUC application
    r"\bR\.\d{2}-\d{2}-\d{3}\b",                      # CPUC rulemaking
    r"\b\d{5}-U\b",                                   # GA
    r"\b\d{2}-\d{4}-E[A-Z]{2,4}\b",                   # WV
    r"\bPUE-\d{4}-\d{5}\b",                           # VA
    r"\bU-\d{4,5}\b",                                 # MI / LA
    r"\b\d{2}-\d{3,5}-[A-Z]{2,4}\b",                  # generic dashed
]

_WS = re.compile(r"\s+")


def _clean(s: Optional[str]) -> str:
    if not s:
        return ""
    return _WS.sub(" ", str(s).replace("\xa0", " ")).strip()


def extract_dockets(*texts: Optional[str]) -> list[str]:
    """Pull docket/case identifiers out of free text."""
    blob = " ".join(_clean(t) for t in texts if t)
    found: list[str] = []
    for pat in DOCKET_PATTERNS:
        for m in re.finditer(pat, blob, flags=re.IGNORECASE):
            val = (m.group(1) if m.groups() else m.group(0)).strip(" .,;:")
            if 4 <= len(val) <= 30 and any(c.isdigit() for c in val):
                up = val.upper()
                if up not in found:
                    found.append(up)
    return found[:6]


@dataclass
class Event:
    """One dated item on a commission's calendar."""

    commission: str                 # registry code, e.g. "OH"
    commission_name: str
    state: str
    tz: str                         # IANA zone the hearing is actually held in
    title: str
    start: datetime                 # tz-aware
    end: Optional[datetime] = None
    all_day: bool = False
    location: str = ""
    description: str = ""
    url: str = ""
    source_url: str = ""
    dockets: list[str] = field(default_factory=list)
    event_type: str = "other"
    event_type_label: str = "Other"
    relevance: str = "Low"
    weight: int = 1
    scraped_at: str = ""

    def __post_init__(self) -> None:
        self.title = _clean(self.title)[:300]
        self.location = _clean(self.location)[:200]
        self.description = _clean(self.description)[:1200]

    @property
    def uid(self) -> str:
        """Stable identifier. Same event across runs -> same UID, so calendar
        subscribers get updates rather than duplicates."""
        basis = f"{self.commission}|{self.start.date().isoformat()}|{self.title.lower()[:120]}"
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:20] + "@psc-calendar"

    @property
    def dedupe_key(self) -> tuple:
        return (self.commission, self.start.date(), self.title.lower()[:80])

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start"] = self.start.isoformat()
        d["end"] = self.end.isoformat() if self.end else None
        d["uid"] = self.uid
        d["start_date"] = self.start.date().isoformat()
        return d


@dataclass
class ScrapeResult:
    """Per-commission outcome, so the dashboard can show scraper health."""

    commission: str
    commission_name: str
    tier: str
    ok: bool
    events: list[Event] = field(default_factory=list)
    strategy_used: str = ""
    source_url: str = ""
    error: str = ""
    duration_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "commission": self.commission,
            "commission_name": self.commission_name,
            "tier": self.tier,
            "ok": self.ok,
            "event_count": len(self.events),
            "strategy_used": self.strategy_used,
            "source_url": self.source_url,
            "error": self.error[:500],
            "duration_s": round(self.duration_s, 2),
        }
