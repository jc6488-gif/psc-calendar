"""Extraction strategies, tried in order of reliability.

The chain is deliberate: structured data is parsed as structured data, and the
fuzzy text scraper is the last resort. A commission that publishes an iCal feed
should never fall through to regex.

    ics  >  rss/atom  >  JSON-LD schema.org/Event  >  Tribe/Drupal JSON API
         >  HTML cards/tables  >  date-regex over page text
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, time as dtime
from typing import Iterable, Iterator, Optional
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import feedparser
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from dateutil.relativedelta import relativedelta

from .fetch import get, get_text, FetchError

log = logging.getLogger(__name__)

# Accept events from 30 days back (recently-passed dates still matter for
# "what did I miss") through 18 months forward.
WINDOW_BACK = timedelta(days=30)
WINDOW_FWD = timedelta(days=550)


class RawEvent(dict):
    """Loose intermediate representation before normalisation into Event."""


# --------------------------------------------------------------------------- utils

def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("America/New_York")


def _aware(dt: datetime, tz: ZoneInfo) -> datetime:
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt


def in_window(dt: datetime, now: datetime) -> bool:
    return (now - WINDOW_BACK) <= dt <= (now + WINDOW_FWD)


def _soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def _parse_dt(value, tz: ZoneInfo, default_year: Optional[int] = None) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _aware(value, tz)
    s = str(value).strip()
    if not s:
        return None
    # dateutil chokes on ordinals and stray weekday/ranges
    s = re.sub(r"\b(\d{1,2})(st|nd|rd|th)\b", r"\1", s, flags=re.I)
    s = re.sub(r"\s*(?:-|–|—|to|through)\s*\d{1,2}:\d{2}\s*(?:am|pm)?\s*$", "", s, flags=re.I)
    s = re.sub(r"\s+at\s+", " ", s, flags=re.I)
    s = s.replace("Noon", "12:00 PM").replace("noon", "12:00 PM")
    default = datetime(default_year or datetime.now().year, 1, 1)
    try:
        dt = dateparser.parse(s, fuzzy=True, default=default)
    except (ValueError, OverflowError, TypeError):
        return None
    if dt is None:
        return None
    return _aware(dt, tz)


# --------------------------------------------------------------------------- ICS

def from_ics(body: bytes, tz: ZoneInfo, now: datetime, source_url: str) -> list[RawEvent]:
    from icalendar import Calendar

    try:
        cal = Calendar.from_ical(body)
    except Exception as e:
        raise ValueError(f"not a parseable iCalendar: {e}") from e

    out: list[RawEvent] = []
    for comp in cal.walk("VEVENT"):
        raw_start = comp.get("DTSTART")
        if raw_start is None:
            continue
        val = raw_start.dt
        all_day = not isinstance(val, datetime)
        start = (
            datetime.combine(val, dtime(0, 0))
            if all_day
            else val
        )
        start = _aware(start, tz)
        if not in_window(start, now):
            continue

        end = None
        raw_end = comp.get("DTEND")
        if raw_end is not None:
            ev = raw_end.dt
            end = _aware(ev if isinstance(ev, datetime) else datetime.combine(ev, dtime(0, 0)), tz)

        out.append(RawEvent(
            title=str(comp.get("SUMMARY") or "").strip(),
            start=start,
            end=end,
            all_day=all_day,
            location=str(comp.get("LOCATION") or "").strip(),
            description=str(comp.get("DESCRIPTION") or "").strip(),
            url=str(comp.get("URL") or "").strip() or source_url,
        ))
    return out


# --------------------------------------------------------------------------- RSS

def from_rss(body: bytes, tz: ZoneInfo, now: datetime, source_url: str) -> list[RawEvent]:
    feed = feedparser.parse(body)
    if feed.bozo and not feed.entries:
        raise ValueError("not a parseable feed")
    if not feed.entries:
        raise ValueError("feed has no entries")

    out: list[RawEvent] = []
    for e in feed.entries:
        title = (e.get("title") or "").strip()
        summary = re.sub(r"<[^>]+>", " ", e.get("summary", "") or "")
        start = None

        # A calendar RSS usually puts the event date in the title or summary,
        # NOT in pubDate (which is when the notice was posted).
        for candidate in (title, summary):
            m = re.search(
                r"((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s+)?"
                r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}"
                r"(?:,?\s+\d{4})?(?:\s*,?\s*\d{1,2}:\d{2}\s*[APap]\.?[Mm]\.?)?",
                candidate,
            )
            if m:
                start = _parse_dt(m.group(0), tz, now.year)
                if start:
                    break
            m2 = re.search(r"\b\d{1,2}/\d{1,2}/\d{2,4}(?:\s+\d{1,2}:\d{2}\s*[APap][Mm])?", candidate)
            if m2:
                start = _parse_dt(m2.group(0), tz, now.year)
                if start:
                    break

        if start is None:
            for key in ("published_parsed", "updated_parsed"):
                if e.get(key):
                    start = _aware(datetime(*e[key][:6]), tz)
                    break
        if start is None or not in_window(start, now):
            continue

        out.append(RawEvent(
            title=title,
            start=start,
            description=summary.strip(),
            url=(e.get("link") or source_url).strip(),
        ))
    return out


# ------------------------------------------------------------------ JSON-LD Events

def from_jsonld(html: str, tz: ZoneInfo, now: datetime, source_url: str) -> list[RawEvent]:
    """schema.org/Event blocks. Common on Drupal + modern .gov templates."""
    soup = _soup(html)
    out: list[RawEvent] = []

    def walk(node):
        if isinstance(node, list):
            for n in node:
                walk(n)
            return
        if not isinstance(node, dict):
            return
        for key in ("@graph", "itemListElement", "subEvent"):
            if key in node:
                walk(node[key])
        t = node.get("@type", "")
        types = t if isinstance(t, list) else [t]
        if not any("event" in str(x).lower() for x in types):
            return
        start = _parse_dt(node.get("startDate"), tz, now.year)
        if not start or not in_window(start, now):
            return
        loc = node.get("location")
        if isinstance(loc, dict):
            loc = loc.get("name") or (loc.get("address") if isinstance(loc.get("address"), str) else "")
        elif isinstance(loc, list) and loc:
            loc = loc[0].get("name", "") if isinstance(loc[0], dict) else str(loc[0])
        out.append(RawEvent(
            title=str(node.get("name") or "").strip(),
            start=start,
            end=_parse_dt(node.get("endDate"), tz, now.year),
            location=str(loc or "").strip(),
            description=re.sub(r"<[^>]+>", " ", str(node.get("description") or "")).strip(),
            url=str(node.get("url") or "").strip() or source_url,
        ))

    for tag in soup.find_all("script", type="application/ld+json"):
        txt = tag.string or tag.get_text() or ""
        try:
            walk(json.loads(txt))
        except (json.JSONDecodeError, TypeError):
            # Some sites concatenate multiple objects; try line-wise recovery.
            for chunk in re.findall(r"\{.*?\}(?=\s*[\{\]]|\s*$)", txt, re.S)[:20]:
                try:
                    walk(json.loads(chunk))
                except Exception:
                    pass
    return out


# ------------------------------------------------------------------ JSON APIs

def from_tribe_api(base_url: str, tz: ZoneInfo, now: datetime) -> list[RawEvent]:
    """WordPress 'The Events Calendar' REST API."""
    p = urlparse(base_url)
    root = f"{p.scheme}://{p.netloc}"
    api = f"{root}/wp-json/tribe/events/v1/events?per_page=50&start_date={(now - WINDOW_BACK):%Y-%m-%d}"
    body, _ = get(api)
    data = json.loads(body)
    out = []
    for e in data.get("events", []):
        start = _parse_dt(e.get("start_date"), tz, now.year)
        if not start or not in_window(start, now):
            continue
        venue = e.get("venue") or {}
        out.append(RawEvent(
            title=(e.get("title") or "").strip(),
            start=start,
            end=_parse_dt(e.get("end_date"), tz, now.year),
            all_day=bool(e.get("all_day")),
            location=str(venue.get("venue", "")).strip(),
            description=re.sub(r"<[^>]+>", " ", e.get("description", "") or "").strip(),
            url=e.get("url", ""),
        ))
    if not out:
        raise ValueError("tribe API returned no usable events")
    return out


def from_drupal_json(base_url: str, tz: ZoneInfo, now: datetime) -> list[RawEvent]:
    """Drupal JSON:API node--event collection."""
    p = urlparse(base_url)
    root = f"{p.scheme}://{p.netloc}"
    body, _ = get(f"{root}/jsonapi/node/event?page[limit]=50&sort=-field_date")
    data = json.loads(body)
    out = []
    for node in data.get("data", []):
        attrs = node.get("attributes", {})
        raw = None
        for k, v in attrs.items():
            if "date" in k.lower() and v:
                raw = v.get("value") if isinstance(v, dict) else v
                if raw:
                    break
        start = _parse_dt(raw, tz, now.year)
        if not start or not in_window(start, now):
            continue
        out.append(RawEvent(
            title=(attrs.get("title") or "").strip(),
            start=start,
            description=re.sub(r"<[^>]+>", " ", str((attrs.get("body") or {}).get("value", ""))).strip(),
            url=urljoin(root, (attrs.get("path") or {}).get("alias", "") or ""),
        ))
    if not out:
        raise ValueError("drupal JSON:API returned no usable events")
    return out


# ------------------------------------------------------------------ HTML parsing

CARD_SELECTORS = [
    "[class*='event-item']", "[class*='event-card']", "[class*='eventItem']",
    "[class*='calendar-item']", "[class*='meeting-item']", "li[class*='event']",
    "div[class*='views-row']", "article[class*='event']", "[class*='event-listing']",
    "[class*='listing-item']", "tr[class*='event']", "[typeof*='Event']",
]

DATE_ATTR_SELECTORS = ["time[datetime]", "[data-date]", "[data-start]", "[datetime]"]


def _text(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""


def from_federal_register(url: str, tz: ZoneInfo, now: datetime) -> list[RawEvent]:
    """Federal Register API search results (www.federalregister.gov/api/v1).

    FERC's own site WAF-blocks all automated clients, but its Sunshine Act
    meeting notices are published in the Federal Register, whose API is
    public and returns the actual meeting date/time in the `dates` field.
    Notices appear ~2 days before each meeting, so this yields the next
    meeting, not a long-horizon calendar.
    """
    import json as _json

    body, _ = get(url)
    data = _json.loads(body)
    out: list[RawEvent] = []
    for r in data.get("results", []):
        start = _parse_dt(r.get("dates"), tz)
        if not start or not in_window(start, now):
            continue
        out.append(RawEvent(
            title=r.get("title") or "Sunshine Act Meeting",
            start=start,
            description=(r.get("abstract") or "")[:800],
            url=r.get("html_url") or url,
        ))
    if not out:
        raise ValueError("no in-window documents with parseable dates")
    return out


def from_fullcalendar_json(url: str, tz: ZoneInfo, now: datetime) -> list[RawEvent]:
    """A FullCalendar events feed: a JSON array of {title, start, end, url}.

    Maryland's calendar (psc.maryland.gov) renders with FullCalendar and
    serves the complete schedule - exact datetimes, case numbers in titles -
    from a WordPress admin-ajax action that also answers GET.
    """
    import json as _json

    body, _ = get(url)
    data = _json.loads(body)
    if not isinstance(data, list):
        raise ValueError("not a FullCalendar event array")
    out: list[RawEvent] = []
    for ev in data:
        if not isinstance(ev, dict) or "start" not in ev:
            continue
        start = _parse_dt(ev.get("start"), tz)
        if not start or not in_window(start, now):
            continue
        link = (ev.get("url") or "").strip()
        out.append(RawEvent(
            title=(ev.get("title") or "").strip() or "Meeting",
            start=start,
            end=_parse_dt(ev.get("end"), tz),
            all_day=bool(ev.get("allDay")) or "T" not in str(ev.get("start")),
            description="",
            url=link if link.startswith(("http://", "https://")) else "",
        ))
    if not out:
        raise ValueError("FullCalendar feed had no in-window events")
    return out


_TELERIK_APPTS = re.compile(r'"appointments":"((?:[^"\\]|\\.)*)"')


def from_telerik_scheduler(html: str, tz: ZoneInfo, now: datetime, source_url: str) -> list[RawEvent]:
    """Telerik RadScheduler (ASP.NET) calendars - PUCT uses one.

    The rendered HTML positions events in a grid with no per-event date, so
    HTML parsers see subjects but not when they happen (the "Open Meeting ...
    delete" garbage of 2026-08-14). But the widget's init script embeds every
    appointment as JSON with exact start/end, location, a Cancelled flag and
    a NavigateUrl. Parse that instead.
    """
    import json as _json

    m = _TELERIK_APPTS.search(html)
    if not m:
        raise ValueError("no RadScheduler appointment data in page")
    # The appointments value is a JSON string inside JSON - unescape it as
    # JSON, not unicode_escape, or multi-byte characters get mangled.
    appts = _json.loads(_json.loads(f'"{m.group(1)}"'))
    out: list[RawEvent] = []
    for a in appts:
        attrs = {}
        for r in a.get("resources", []):
            attrs.update(r.get("attributes") or {})
        if str(attrs.get("Cancelled", "")).lower() == "true":
            continue
        start = _parse_dt(a.get("start"), tz)
        if not start or not in_window(start, now):
            continue
        end = _parse_dt(a.get("end"), tz)
        url = (attrs.get("NavigateUrl") or "").strip()
        out.append(RawEvent(
            title=(a.get("subject") or "").strip() or "Meeting",
            start=start,
            end=end,
            location=(attrs.get("Location") or "").strip(),
            description=(a.get("description") or "").strip(),
            url=url if url.startswith(("http://", "https://")) else source_url,
        ))
    if not out:
        raise ValueError("RadScheduler data had no in-window appointments")
    return out


def from_html_cards(html: str, tz: ZoneInfo, now: datetime, source_url: str) -> list[RawEvent]:
    soup = _soup(html)
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    best: list[RawEvent] = []
    for sel in CARD_SELECTORS:
        try:
            cards = soup.select(sel)
        except Exception:
            continue
        if not (1 <= len(cards) <= 400):
            continue

        found: list[RawEvent] = []
        for card in cards:
            start = None
            # 1. machine-readable datetime attribute
            for dsel in DATE_ATTR_SELECTORS:
                node = card.select_one(dsel)
                if node:
                    val = node.get("datetime") or node.get("data-date") or node.get("data-start")
                    start = _parse_dt(val, tz, now.year)
                    if start:
                        break
            # 2. visible date text
            if not start:
                txt = _text(card)
                start = _first_date_in(txt, tz, now)
            if not start or not in_window(start, now):
                continue

            link = card.find("a", href=True)
            heading = card.find(["h1", "h2", "h3", "h4", "h5"])
            title = _text(heading) or (_text(link) if link else "") or _text(card)[:200]
            title = re.sub(r"^\s*(?:LEARN MORE|READ MORE|DETAILS)\s*", "", title, flags=re.I).strip()
            if len(title) < 4:
                continue

            found.append(RawEvent(
                title=title,
                start=start,
                location="",
                description=_text(card)[:800],
                url=urljoin(source_url, link["href"]) if link else source_url,
            ))
        if len(found) > len(best):
            best = found
    if not best:
        raise ValueError("no event cards matched")
    return best


def _table_context(table) -> str:
    """Caption or nearest preceding heading - names the table and often
    carries the year that schedule-style tables omit from every row
    (e.g. AZ's "2026 Open Meeting Schedule" over rows like "January 12-13")."""
    cap = table.find("caption")
    if cap and _text(cap):
        return _text(cap)
    # Accordion/tab labels ("2026 Open Meeting Dates") are often plain text
    # nodes, not headings - prefer a short year-bearing label when one exists.
    s = table.find_previous(string=re.compile(r"\b20\d\d\b"))
    if s:
        st = re.sub(r"\s+", " ", str(s)).strip()
        if 0 < len(st) <= 80:
            return st
    prev = table.find_previous(["h1", "h2", "h3", "h4", "h5"])
    return _text(prev) if prev else ""


def from_html_table(html: str, tz: ZoneInfo, now: datetime, source_url: str) -> list[RawEvent]:
    soup = _soup(html)
    out: list[RawEvent] = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        context = _table_context(table)
        ctx_year_m = re.search(r"\b(20\d\d)\b", context)
        ctx_year = int(ctx_year_m.group(1)) if ctx_year_m else None
        headers = [_text(th).lower() for th in rows[0].find_all(["th", "td"])]
        date_col = next((i for i, h in enumerate(headers) if "date" in h or "when" in h), None)
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if not cells:
                continue
            texts = [_text(c) for c in cells]
            start = None
            date_text = ""
            if date_col is not None and date_col < len(texts):
                start = _first_date_in(texts[date_col], tz, now)
                date_text = texts[date_col]
            if not start:
                for t in texts[:3]:
                    start = _first_date_in(t, tz, now)
                    if start:
                        date_text = t
                        break
            joined_cells = False
            if not start and len(texts) >= 2:
                # Some schedule tables split the date across cells
                # ("January" | "14") - no single cell parses alone.
                candidate = " ".join(texts[:2])
                start = _first_date_in(candidate, tz, now)
                if start:
                    date_text = candidate
                    joined_cells = True
            if not start:
                continue
            # A yearless date under a "2027 ... Schedule" heading belongs to
            # that year, not to the roll-forward guess.
            if ctx_year and not re.search(r"\d{4}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}", date_text):
                start = start.replace(year=ctx_year)
            if not in_window(start, now):
                continue
            link = row.find("a", href=True)
            rest = [t for t in texts if t and not _first_date_in(t, tz, now)]
            title = max(rest, key=len) if rest else " ".join(texts)[:200]
            if joined_cells and context:
                # The row's remaining cells are date fragments, not a title -
                # the table heading is the only real name for the event.
                title = f"{context}: {date_text}"
            if len(title) < 8 and context:
                # Schedule tables often have no per-row title at all - the
                # meaning lives in the heading ("2026 Open Meeting Schedule").
                title = f"{context}: {date_text}".strip(": ")
            if len(title) < 4:
                continue
            out.append(RawEvent(
                title=title,
                start=start,
                description=" | ".join(texts)[:800],
                url=urljoin(source_url, link["href"]) if link else source_url,
            ))
    if not out:
        raise ValueError("no parseable table rows")
    return out


DATE_RE = re.compile(
    r"(?:(?:Mon|Tues?|Wed(?:nes)?|Thur?s?|Fri|Sat(?:ur)?|Sun)[a-z]*\.?,?\s+)?"
    r"(?:"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:t|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2}"
    r"(?:st|nd|rd|th)?(?:,?\s+\d{4})?"
    r"|\d{1,2}/\d{1,2}/\d{2,4}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|(?<!\d)(?<!\d-)\d{1,2}-\d{1,2}-\d{4}(?!\d)"  # 8-4-2026 (GA titles)
    r")"
    r"(?:\s*,?\s*(?:at\s+)?\d{1,2}(?::\d{2})?\s*[APap]\.?[Mm]\.?)?",
    re.IGNORECASE,
)


def _first_date_in(text: str, tz: ZoneInfo, now: datetime) -> Optional[datetime]:
    if not text:
        return None
    m = DATE_RE.search(text)
    if not m:
        return None
    dt = _parse_dt(m.group(0), tz, now.year)
    if dt is None:
        return None
    # A bare "March 4" with no year defaults to the current year; if that lands
    # well in the past on a page of upcoming events, it almost certainly means
    # next year. A two-digit slash year ("4/6/26") IS a year - rolling those
    # forward silently shifted real dates into the wrong year.
    has_year = re.search(r"\d{4}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}", m.group(0))
    if not has_year and dt < now - timedelta(days=120):
        dt = dt + relativedelta(years=1)
    return dt


def from_date_regex(html: str, tz: ZoneInfo, now: datetime, source_url: str) -> list[RawEvent]:
    """Last resort: sweep the page text for dates and keep the surrounding line
    as the title. Noisy by design - the classifier filters most of it out."""
    soup = _soup(html)
    for tag in soup(["script", "style", "nav", "footer", "header", "select", "option"]):
        tag.decompose()

    seen: set[tuple] = set()
    out: list[RawEvent] = []
    for block in soup.find_all(["p", "li", "td", "div", "h2", "h3", "h4", "a", "span"]):
        if block.find(["p", "li", "td", "div"]):
            continue  # only leaf-ish blocks, avoids duplicating whole page
        txt = _text(block)
        if not (10 <= len(txt) <= 400):
            continue
        dt = _first_date_in(txt, tz, now)
        if not dt or not in_window(dt, now):
            continue
        # Stripping the date out of running prose leaves fragments
        # ("...is scheduled for"), so only strip it when it sits at an edge;
        # otherwise keep the sentence intact and let the Date column repeat it.
        stripped = DATE_RE.sub("", txt).strip(" -–—:|,.•")
        edge = DATE_RE.match(txt) or DATE_RE.search(txt[-40:])
        title = re.sub(r"\s{2,}", " ", stripped) if edge else txt
        title = title.strip(" -–—:|,.•")
        if len(title) < 8:
            continue
        key = (dt.date(), title.lower()[:60])
        if key in seen:
            continue
        seen.add(key)
        link = block.find("a", href=True) or (block if block.name == "a" and block.get("href") else None)
        out.append(RawEvent(
            title=title[:250],
            start=dt,
            description=txt[:600],
            url=urljoin(source_url, link["href"]) if link else source_url,
        ))
    if not out:
        raise ValueError("no dates found in page text")
    return out[:250]


# ------------------------------------------------------------------ discovery

def discover_feeds(html: str, source_url: str) -> list[tuple[str, str]]:
    """Find linked .ics / RSS feeds. Returns [(kind, url)]."""
    soup = _soup(html)
    found: list[tuple[str, str]] = []

    for link in soup.find_all("link", rel=True):
        rel = " ".join(link.get("rel", [])).lower()
        typ = (link.get("type") or "").lower()
        href = link.get("href")
        if not href or "alternate" not in rel:
            continue
        if "calendar" in typ:
            found.append(("ics", urljoin(source_url, href)))
        elif "rss" in typ or "atom" in typ or "xml" in typ:
            found.append(("rss", urljoin(source_url, href)))

    for a in soup.find_all("a", href=True):
        href = a["href"]
        low = href.lower()
        label = _text(a).lower()
        full = urljoin(source_url, href)
        if low.endswith(".ics") or "format=ical" in low or "ical=1" in low or "/ical" in low:
            found.append(("ics", full))
        elif low.endswith((".rss", ".xml")) or "format=rss" in low or "feed=rss" in low:
            found.append(("rss", full))
        elif "webcal://" in low:
            found.append(("ics", full.replace("webcal://", "https://")))
        elif any(w in label for w in ("subscribe to calendar", "add to calendar", "ical", "rss feed")):
            found.append(("ics" if "ical" in label else "rss", full))

    seen: set[str] = set()
    uniq = []
    for kind, url in found:
        if url not in seen:
            seen.add(url)
            uniq.append((kind, url))
    return uniq[:8]


STRATEGY_ORDER = ["ics", "rss", "jsonld", "tribe", "drupal", "html_cards", "html_table", "date_regex"]


def extract_auto(url: str, tz_name: str, now: datetime) -> tuple[list[RawEvent], str]:
    """Run the full chain against a URL. Returns (events, strategy_name)."""
    tz = _tz(tz_name)
    body, ctype = get(url)

    # Content-type shortcuts
    if "calendar" in ctype or body[:80].lstrip().startswith(b"BEGIN:VCALENDAR"):
        return from_ics(body, tz, now, url), "ics"
    if "xml" in ctype or body[:200].lstrip().startswith((b"<?xml", b"<rss", b"<feed")):
        return from_rss(body, tz, now, url), "rss"
    if "json" in ctype:
        try:
            return from_drupal_json(url, tz, now), "drupal"
        except Exception:
            pass

    html = body.decode("utf-8", errors="replace")

    # Follow a linked feed if the page advertises one.
    for kind, feed_url in discover_feeds(html, url):
        try:
            fbody, _ = get(feed_url)
            evs = from_ics(fbody, tz, now, feed_url) if kind == "ics" else from_rss(fbody, tz, now, feed_url)
            if evs:
                return evs, f"{kind}:discovered"
        except Exception as e:
            log.debug("discovered feed %s failed: %s", feed_url, e)

    attempts = [
        ("telerik", lambda: from_telerik_scheduler(html, tz, now, url)),
        ("jsonld", lambda: from_jsonld(html, tz, now, url)),
        ("tribe", lambda: from_tribe_api(url, tz, now)),
        ("html_cards", lambda: from_html_cards(html, tz, now, url)),
        ("html_table", lambda: from_html_table(html, tz, now, url)),
        ("date_regex", lambda: from_date_regex(html, tz, now, url)),
    ]
    errors = []
    for name, fn in attempts:
        try:
            evs = fn()
            if evs:
                return evs, name
        except Exception as e:
            errors.append(f"{name}: {e}")
            continue
    raise ValueError("all strategies failed -> " + " | ".join(errors[:4]))


def extract(url: str, strategy: str, tz_name: str, now: datetime) -> tuple[list[RawEvent], str]:
    tz = _tz(tz_name)
    if strategy in ("auto", "", None):
        return extract_auto(url, tz_name, now)
    if strategy == "ics":
        body, _ = get(url)
        return from_ics(body, tz, now, url), "ics"
    if strategy == "rss":
        body, _ = get(url)
        return from_rss(body, tz, now, url), "rss"
    if strategy == "tribe":
        return from_tribe_api(url, tz, now), "tribe"
    if strategy == "drupal":
        return from_drupal_json(url, tz, now), "drupal"
    if strategy == "federal_register":
        return from_federal_register(url, tz, now), "federal_register"
    if strategy == "fullcalendar":
        return from_fullcalendar_json(url, tz, now), "fullcalendar"
    html, _ = get_text(url)
    if strategy == "jsonld":
        return from_jsonld(html, tz, now, url), "jsonld"
    if strategy == "html":
        try:
            return from_html_cards(html, tz, now, url), "html_cards"
        except Exception:
            return from_html_table(html, tz, now, url), "html_table"
    if strategy == "date_regex":
        return from_date_regex(html, tz, now, url), "date_regex"
    return extract_auto(url, tz_name, now)
