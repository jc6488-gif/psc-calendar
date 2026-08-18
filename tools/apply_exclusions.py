"""Merge the removals requested in a GitHub issue into data/exclusions.yaml.

Driven by .github/workflows/apply-exclusions.yml. The desk crosses events off
on the dashboard, presses "Remove these", and submits the issue that opens
pre-filled; this reads the ```yaml block out of it and merges.

Deliberately strict about what it will accept. This runs with write access on
a public repo, so it only ever reads the two keys it knows, only ever ADDS
entries, and never executes anything from the issue body.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "data" / "exclusions.yaml"

_BLOCK = re.compile(r"```(?:yaml|yml)?\s*\n(.*?)```", re.S | re.I)
_CODE = re.compile(r"^[A-Z][A-Z0-9-]{1,7}$")


def out(key: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as fh:
            fh.write(f"{key}={value}\n")
    print(f"{key}={value}")


def fail(msg: str) -> None:
    out("error", msg.replace("\n", " ")[:300])
    out("added", "0")
    sys.exit(0)          # reported on the issue, not a red workflow


def clean_event(e: dict) -> dict | None:
    code = str(e.get("commission", "")).strip().upper()
    date = str(e.get("date", "")).strip()
    title = str(e.get("title", "")).strip()
    if not (_CODE.match(code) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) and title):
        return None
    return {"commission": code, "date": date, "title": title[:300]}


def clean_recurring(e: dict) -> dict | None:
    code = str(e.get("commission", "")).strip().upper()
    frag = str(e.get("title_contains", "")).strip()
    # A 2-character fragment would silently wipe out a whole commission.
    if not (_CODE.match(code) and len(frag) >= 6):
        return None
    return {"commission": code, "title_contains": frag[:300]}


def main() -> None:
    body = os.environ.get("ISSUE_BODY") or ""
    who = os.environ.get("ISSUE_USER") or "dashboard"
    num = os.environ.get("ISSUE_NUM") or "?"

    blocks = _BLOCK.findall(body)
    if not blocks:
        fail("no ```yaml block found in the issue")

    wanted_events: list[dict] = []
    wanted_recurring: list[dict] = []
    for raw in blocks:
        try:
            parsed = yaml.safe_load(raw)
        except yaml.YAMLError as e:
            fail(f"that YAML block does not parse: {e}")
        if not isinstance(parsed, dict):
            continue
        for e in parsed.get("events") or []:
            if isinstance(e, dict) and (c := clean_event(e)):
                wanted_events.append(c)
        for e in parsed.get("recurring") or []:
            if isinstance(e, dict) and (c := clean_recurring(e)):
                wanted_recurring.append(c)

    if not wanted_events and not wanted_recurring:
        fail("no usable entries - each needs commission + date + title, "
             "or commission + title_contains (6+ chars)")

    doc = yaml.safe_load(FILE.read_text()) if FILE.exists() else {}
    doc = doc if isinstance(doc, dict) else {}
    events = list(doc.get("events") or [])
    recurring = list(doc.get("recurring") or [])

    def key_e(e):
        return (str(e.get("commission", "")).upper(), str(e.get("date", "")),
                re.sub(r"\s+", " ", str(e.get("title", ""))).strip().lower())

    def key_r(e):
        return (str(e.get("commission", "")).upper(),
                re.sub(r"\s+", " ", str(e.get("title_contains", ""))).strip().lower())

    have_e = {key_e(e) for e in events}
    have_r = {key_r(e) for e in recurring}
    added = 0
    for e in wanted_events:
        if key_e(e) not in have_e:
            e["reason"] = f"desk review (issue #{num})"
            e["by"] = who
            events.append(e)
            have_e.add(key_e(e))
            added += 1
    for e in wanted_recurring:
        if key_r(e) not in have_r:
            e["reason"] = f"desk review (issue #{num})"
            e["by"] = who
            recurring.append(e)
            have_r.add(key_r(e))
            added += 1

    if not added:
        out("added", "0")
        out("error", "")
        return

    # Keep the file's comment header. Split on a line that STARTS with the
    # key, not on the word anywhere - "events:" appears inside the header
    # comments and splitting there shredded them.
    text = FILE.read_text() if FILE.exists() else ""
    m = re.search(r"^(events|recurring):", text, re.M)
    header = text[:m.start()] if m else (text + "\n" if text else "")
    FILE.write_text(header + yaml.safe_dump(
        {"events": events, "recurring": recurring},
        sort_keys=False, allow_unicode=True, width=100))
    out("added", str(added))
    out("error", "")


if __name__ == "__main__":
    main()
