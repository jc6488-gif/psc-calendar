"""RFC 5545 output: one master feed plus per-ticker and per-state feeds."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from icalendar import Calendar, Event as IcsEvent

from .models import Event

PRODID = "-//psc-calendar//Utility Regulatory Calendar//EN"


def _build(events: list[Event], name: str, desc: str, now: datetime) -> bytes:
    cal = Calendar()
    cal.add("prodid", PRODID)
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", name)
    cal.add("x-wr-caldesc", desc)
    cal.add("x-wr-timezone", "America/New_York")
    # Ask subscribers to re-poll every 3 hours rather than the client default.
    cal.add("x-published-ttl", "PT3H")
    cal.add("refresh-interval;value=duration", "PT3H")

    for e in events:
        ie = IcsEvent()
        ie.add("uid", e.uid)
        ie.add("dtstamp", now)

        prefix = f"[{'/'.join(e.tickers)}] " if e.tickers else f"[{e.commission}] "
        ie.add("summary", f"{prefix}{e.title}"[:250])

        if e.all_day:
            ie.add("dtstart", e.start.date())
            ie.add("dtend", (e.end or e.start).date() + timedelta(days=1))
        else:
            ie.add("dtstart", e.start)
            ie.add("dtend", e.end or (e.start + timedelta(hours=2)))

        body = [f"Commission: {e.commission_name} ({e.commission})",
                f"Type: {e.event_type_label}"]
        if e.tickers:
            body.append(f"Tickers: {', '.join(e.tickers)}")
        if e.subsidiaries:
            body.append(f"Entity: {', '.join(e.subsidiaries)}")
        if e.dockets:
            body.append(f"Docket: {', '.join(e.dockets)}")
        if e.rate_case:
            body.append(f"Rate-case relevant: {', '.join(e.rate_case_signals)}")
        if e.description and e.description[:60] not in e.title:
            body.append("")
            body.append(e.description[:600])
        if e.url:
            body += ["", f"Source: {e.url}"]

        ie.add("description", "\n".join(body))
        if e.location:
            ie.add("location", e.location)
        if e.url:
            ie.add("url", e.url)
        ie.add("categories", [e.commission, e.event_type_label] + e.tickers)
        # Priority 1-4 shows as "high" in most clients.
        ie.add("priority", 2 if e.weight >= 4 else (5 if e.weight >= 3 else 9))
        cal.add_component(ie)

    return cal.to_ical()


def write_all(events: list[Event], outdir: Path, now: datetime) -> dict[str, int]:
    outdir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}

    def emit(fname: str, evs: list[Event], name: str, desc: str):
        (outdir / fname).write_bytes(_build(evs, name, desc, now))
        counts[fname] = len(evs)

    emit("all.ics", events, "US Utility Regulatory Calendar",
         "All tracked commission meetings, hearings and procedural dates.")

    covered = [e for e in events if e.tickers]
    emit("coverage.ics", covered, "Utility Coverage — Regulatory Dates",
         "Commission dates attributed to the coverage universe.")

    rate = [e for e in events if e.rate_case]
    emit("rate-cases.ics", rate, "Utility Rate Case Dates",
         "Events flagged as rate-case or resource-plan relevant.")

    high = [e for e in events if e.weight >= 4]
    emit("high-priority.ics", high, "Utility Regulatory — High Priority",
         "Coverage-company decisions, hearings and procedural deadlines.")

    by_ticker: dict[str, list[Event]] = {}
    for e in events:
        for t in e.tickers:
            by_ticker.setdefault(t, []).append(e)
    for t, evs in by_ticker.items():
        emit(f"ticker-{t}.ics", evs, f"{t} — Regulatory Calendar",
             f"Commission dates for {t}.")

    by_comm: dict[str, list[Event]] = {}
    for e in events:
        by_comm.setdefault(e.commission, []).append(e)
    for c, evs in by_comm.items():
        emit(f"commission-{c}.ics", evs, f"{c} — Utility Commission Calendar",
             f"All tracked dates for {evs[0].commission_name}.")

    return counts
