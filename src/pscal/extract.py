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
from datetime import datetime, timedelta, timezone, time as dtime
from typing import Iterable, Iterator, Optional
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import html as _html_mod

import feedparser
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from dateutil.relativedelta import relativedelta

from .fetch import get, get_text, post, FetchError

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
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
    # Struck-through content is a correction, not information - AZ crosses
    # out rescheduled open-meeting dates, and parsing them published the
    # WRONG date for the meeting.
    for tag in soup(["s", "del", "strike"]):
        tag.decompose()
    return soup


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
        # A yearless "Oct 21" in a 2024 archive entry is October 2024, not
        # October of the current year - default the year from the entry's own
        # pubDate so archives fall out of the window instead of becoming
        # phantom future events (the New Orleans feed goes back to 2019).
        default_year = now.year
        for key in ("published_parsed", "updated_parsed"):
            if e.get(key):
                default_year = e[key][0]
                break

        # A calendar RSS usually puts the event date in the title or summary,
        # NOT in pubDate (which is when the notice was posted).
        for candidate in (title, summary):
            m = re.search(
                r"((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s+)?"
                r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}"
                r"(?!\d)(?:,?\s+\d{4})?(?:\s*,?\s*\d{1,2}:\d{2}\s*[APap]\.?[Mm]\.?)?",
                candidate,
            )
            if m:
                start = _parse_dt(m.group(0), tz, default_year)
                if start:
                    break
            m2 = re.search(r"\b\d{1,2}/\d{1,2}/\d{2,4}(?:\s+\d{1,2}:\d{2}\s*[APap][Mm])?", candidate)
            if m2:
                start = _parse_dt(m2.group(0), tz, default_year)
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


def from_epoch_links(html: str, tz: ZoneInfo, now: datetime, source_url: str) -> list[RawEvent]:
    """Anchors whose href carries the event's exact start as a unix epoch
    (?date=1787059800). Georgia's calendar page lists its full forward
    schedule this way while its visible month grid holds only one month
    (and its archive fragments were parsing into phantom dates)."""
    soup = _soup(html)
    out: list[RawEvent] = []
    seen: set[tuple] = set()
    for a in soup.find_all("a", href=True):
        m = re.search(r"[?&]date=(\d{10})(?!\d)", a["href"])
        if not m:
            continue
        try:
            start = datetime.fromtimestamp(int(m.group(1)), tz=timezone.utc).astimezone(tz)
        except (OverflowError, OSError, ValueError):
            continue
        title = _text(a)
        if len(title) < 8 or not in_window(start, now):
            continue
        key = (start, title[:60])
        if key in seen:
            continue
        seen.add(key)
        out.append(RawEvent(title=title, start=start, description="",
                            url=urljoin(source_url, a["href"])))
    if not out:
        raise ValueError("no epoch-dated links")
    return out


def from_legistar_api(url: str, tz: ZoneInfo, now: datetime) -> list[RawEvent]:
    """Legistar Web API events (webapi.legistar.com) - Minnesota's agenda
    meetings. EventDate is date-only; EventTime carries the clock."""
    import json as _json

    body, _ = get(url, headers={"Accept": "application/json"})
    data = _json.loads(body)
    out: list[RawEvent] = []
    for ev in data if isinstance(data, list) else []:
        start = _parse_dt(ev.get("EventDate"), tz)
        if not start:
            continue
        tm = re.search(r"(\d{1,2}):(\d{2})\s*([AP])M", ev.get("EventTime") or "", re.I)
        if tm:
            hh = int(tm.group(1)) % 12 + (12 if tm.group(3).upper() == "P" else 0)
            start = start.replace(hour=hh, minute=int(tm.group(2)))
        if not in_window(start, now):
            continue
        out.append(RawEvent(
            title=(ev.get("EventBodyName") or "Meeting").strip(),
            start=start,
            description=(ev.get("EventComment") or "")[:400],
            url=(ev.get("EventInSiteURL") or "").strip(),
        ))
    if not out:
        raise ValueError("no in-window Legistar events")
    return out


def from_ma_fileroom(url: str, tz: ZoneInfo, now: datetime) -> list[RawEvent]:
    """Massachusetts DPU File Room hearings API - POST-only; returns months
    of hearings with docket, petitioner and exact UTC times while the public
    page is an Angular shell."""
    import json as _json

    payload = {"StartDate": now.date().isoformat(),
               "EndDate": (now + timedelta(days=180)).date().isoformat()}
    body, _ = post(url, json=payload)
    data = _json.loads(body)
    rows = data if isinstance(data, list) else data.get("Results") or data.get("Hearings") or []
    out: list[RawEvent] = []
    def _name(v):
        if isinstance(v, dict):
            v = v.get("Name") or ""
        return (v or "").strip()

    for h in rows:
        start = _parse_dt(h.get("StartTime") or h.get("HearingDate"), tz)
        if not start or not in_window(start, now):
            continue
        start = start.astimezone(tz)
        docket = _name(h.get("DocketNumber"))
        pet = _name(h.get("Petitioner"))
        kind = _name(h.get("CaseType")) or _name(h.get("HearingType")) or "Hearing"
        title = " - ".join(x for x in (docket, pet, kind) if x) or "DPU Hearing"
        out.append(RawEvent(
            title=title[:220],
            start=start,
            end=(_parse_dt(h.get("EndTime"), tz).astimezone(tz)
                 if _parse_dt(h.get("EndTime"), tz) else None),
            location=_name(h.get("Location"))[:160],
            description="",
            url="https://eeaonline.eea.state.ma.us/dpu/fileroom/#/hearings",
        ))
    if not out:
        raise ValueError("no in-window DPU hearings")
    return out


def from_la_portal(url: str, tz: ZoneInfo, now: datetime) -> list[RawEvent]:
    """Louisiana PSC portal scheduler (Kendo). The events endpoint answers a
    form POST once the session holds the portal cookie; dates arrive as
    /Date(ms)/ epochs."""
    import json as _json

    prime = "https://lpscpubvalence.lpsc.louisiana.gov/portal/lpsc-web-portal?tab=calendar"
    d1 = now + timedelta(days=180)
    payload = {"sort": "", "group": "", "filter": "",
               "startDate": f"{now.month:02d}/{now.day:02d}/{now.year}",
               "endDate": f"{d1.month:02d}/{d1.day:02d}/{d1.year}"}
    body, _ = post(url, data=payload, prime_url=prime)
    data = _json.loads(body)
    out: list[RawEvent] = []
    for ev in data.get("Data") or []:
        m = re.search(r"/Date\((\d+)\)/", str(ev.get("Start") or ""))
        if not m:
            continue
        start = datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc).astimezone(tz)
        if not in_window(start, now):
            continue
        kind = (ev.get("HearingTypeName") or ev.get("MeetingTypeName") or "").strip()
        title = (ev.get("Title") or kind or "").strip()
        if not kind and not re.search(r"hearing|session|conference|meeting|docket", title, re.I):
            continue   # holidays and office closures share this scheduler
        out.append(RawEvent(title=(title or kind)[:220], start=start, description=kind, url=""))
    if not out:
        raise ValueError("no in-window LPSC portal events")
    return out


def from_mo_modals(html: str, tz: ZoneInfo, now: datetime, source_url: str) -> list[RawEvent]:
    """psc.mo.gov Calendars.aspx: server-rendered Bootstrap day modals -
    modal title "Calendar Events For August 19", items with time, docket
    link, description, location."""
    soup = _soup(html)
    out: list[RawEvent] = []
    for modal in soup.select("[id^=ClickEvent-]"):
        head = modal.find(string=re.compile(r"Calendar Events For", re.I))
        base = _first_date_in(str(head) if head else "", tz, now)
        if not base:
            t = modal.find(["h4", "h5", "div"], string=re.compile(r"[A-Z][a-z]+ \d{1,2}"))
            base = _first_date_in(_text(t), tz, now) if t else None
        if not base:
            continue
        for item in modal.select(".AppointmentItem") or modal.find_all("li"):
            txt = _text(item)
            if len(txt) < 8:
                continue
            tm = re.search(r"(\d{1,2}):(\d{2})\s*([AP])\.?M", txt, re.I)
            start = base
            if tm:
                hh = int(tm.group(1)) % 12 + (12 if tm.group(3).upper() == "P" else 0)
                start = base.replace(hour=hh, minute=int(tm.group(2)))
            if not in_window(start, now):
                continue
            title = re.sub(r"^\s*\d{1,2}:\d{2}\s*[AP]\.?M\.?\s*", "", txt, flags=re.I)[:200]
            link = item.find("a", href=True)
            out.append(RawEvent(
                title=title or "Commission event",
                start=start,
                description=txt[:500],
                url=urljoin(source_url, link["href"]) if link else source_url,
            ))
    if not out:
        raise ValueError("no day-modal events")
    return out


def from_or_hearings(url: str, tz: ZoneInfo, now: datetime) -> list[RawEvent]:
    """Oregon PUC hearings: hcal.asp is only a week index; the real rows live
    at hcallist.asp?StartDate=M/D/YYYY&EndDate=M/D/YYYY. Generate the next
    eight weeks of ranges and parse each server-rendered list."""
    base = url.rsplit("/", 1)[0]
    monday = (now - timedelta(days=(now.weekday()))).date()
    out: list[RawEvent] = []
    for w in range(8):
        s0 = monday + timedelta(days=7 * w)
        s1 = s0 + timedelta(days=6)
        wurl = (f"{base}/hcallist.asp?StartDate={s0.month}/{s0.day}/{s0.year}"
                f"&EndDate={s1.month}/{s1.day}/{s1.year}")
        try:
            html, _ = get_text(wurl)
        except FetchError:
            continue
        try:
            out.extend(from_html_table(html, tz, now, wurl))
        except ValueError:
            try:
                out.extend(from_date_regex(html, tz, now, wurl))
            except ValueError:
                pass
    if not out:
        raise ValueError("no events across hcallist weeks")
    return out


def from_pa_umbraco(url: str, tz: ZoneInfo, now: datetime) -> list[RawEvent]:
    """PA PUC public-meetings page: the search form is GET but requires the
    page's own ufprt routing token. Fetch page, lift token, query meetings
    and hearings date ranges."""
    page, _ = get_text(url)
    m = re.search(r"name=['\"]ufprt['\"][^>]*value=['\"]([^'\"]+)['\"]", page) or \
        re.search(r"value=['\"]([^'\"]+)['\"][^>]*name=['\"]ufprt['\"]", page)
    if not m:
        raise ValueError("no ufprt token on page")
    token = m.group(1)
    d0 = now.date().isoformat()
    d1 = (now + timedelta(days=300)).date().isoformat()
    out: list[RawEvent] = []
    for qs in (f"MeetingType=meeting&MeetingBeginDate={d0}&MeetingEndDate={d1}",
               f"MeetingType=hearing&HearingBeginDate={d0}&HearingEndDate={d1}"):
        try:
            html, _ = get_text(f"{url.split('?')[0]}?{qs}&ufprt={token}")
        except FetchError:
            continue
        for fn in (from_html_table, from_html_cards, from_date_regex):
            try:
                out.extend(fn(html, tz, now, url))
                break
            except ValueError:
                continue
    if not out:
        raise ValueError("umbraco queries returned no events")
    return out


def from_dated_links(html: str, tz: ZoneInfo, now: datetime, source_url: str) -> list[RawEvent]:
    """Pages whose only machine-readable dates are YYYYMMDD in linked file
    names (Idaho: /Agenda/2026/20260818AGE.pdf = the Aug 18 decision
    meeting)."""
    soup = _soup(html)
    out: list[RawEvent] = []
    seen = set()
    for a in soup.find_all("a", href=True):
        m = re.search(r"(20\d{2})(\d{2})(\d{2})[^/]*\.(?:pdf|docx?|html?)\b", a["href"], re.I)
        if not m:
            continue
        try:
            start = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=tz)
        except ValueError:
            continue
        if not in_window(start, now) or start.date() in seen:
            continue
        seen.add(start.date())
        label = _text(a)
        out.append(RawEvent(
            title=label if len(label) >= 8 else "Decision meeting agenda",
            start=start, all_day=True, description="",
            url=urljoin(source_url, a["href"])))
    if not out:
        raise ValueError("no dated file links")
    return out



_TELERIK_APPTS = re.compile(r'"appointments":"((?:[^"\\]|\\.)*)"')


def from_drupal_settings_events(html: str, tz: ZoneInfo, now: datetime, source_url: str) -> list[RawEvent]:
    """Drupal pages that ship their whole FullCalendar dataset inside the
    drupal-settings-json script (WA UTC: 1,300+ events with exact times and
    per-event URLs) while the visible page renders zero rows server-side."""
    import json as _json

    m = re.search(r'<script[^>]*data-drupal-selector="drupal-settings-json"[^>]*>(.*?)</script>',
                  html, re.S)
    if not m:
        raise ValueError("no drupal-settings-json block")
    try:
        settings = _json.loads(m.group(1))
    except ValueError as e:
        raise ValueError(f"drupal settings unparseable: {e}")
    events = []
    def walk(node):
        # Drupal serializes nested option blobs as JSON *strings* - descend
        # into them too (WA stores calendar_options that way).
        if isinstance(node, str) and node[:1] in "[{":
            try:
                walk(_json.loads(node))
            except ValueError:
                pass
            return
        if isinstance(node, dict):
            ev = node.get("events")
            if isinstance(ev, str) and ev[:1] == "[":
                try:
                    ev = _json.loads(ev)
                except ValueError:
                    ev = None
            if isinstance(ev, list) and ev and isinstance(ev[0], dict) and "start" in ev[0]:
                events.extend(ev)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(settings)
    if not events:
        raise ValueError("no events array in drupal settings")
    out: list[RawEvent] = []
    for ev in events:
        start = _parse_dt(ev.get("start"), tz)
        if not start or not in_window(start, now):
            continue
        link = (ev.get("url") or "").strip()
        out.append(RawEvent(
            title=_html_mod.unescape(re.sub(r"\s*\d{4}-\d{2}-\d{2}T[\d:+-]+\s*$", "",
                         (ev.get("title") or "").strip())) or "Event",
            start=start,
            end=_parse_dt(ev.get("end"), tz),
            all_day="T" not in str(ev.get("start")),
            description="",
            url=urljoin(source_url, link) if link else source_url,
        ))
    if not out:
        raise ValueError("no in-window drupal-settings events")
    return out



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
            # 3. month-grid day cell: the date lives on an ancestor's
            #    datetime/title attribute (NE Drupal, ASP.NET grids)
            if not start:
                anc = card
                for _ in range(4):
                    anc = anc.parent
                    if anc is None or anc.name in ("body", "html"):
                        break
                    for attr in ("datetime", "data-date", "data-day", "title", "abbr"):
                        v = anc.get(attr) if hasattr(anc, "get") else None
                        if v:
                            start = _parse_dt(v, tz, now.year) or _first_date_in(str(v), tz, now)
                            if start:
                                break
                    if start:
                        break
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
        # Prefer the column that names the EVENT's date - NJ's notice table has
        # "Date Issued" before "Meeting Date(s)", and taking the first date
        # column silently replaced every hearing date with its posting date.
        date_col = next((i for i, h in enumerate(headers)
                         if re.search(r"(meeting|hearing|event)s?\s*date", h)), None)
        if date_col is None:
            date_col = next((i for i, h in enumerate(headers)
                             if ("date" in h or "when" in h)
                             and not re.search(r"issued|posted|filed|deadline", h)), None)
        if date_col is None:
            date_col = next((i for i, h in enumerate(headers) if "date" in h or "when" in h), None)
        time_col = next((i for i, h in enumerate(headers) if re.fullmatch(r"\s*time\s*", h)), None)
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if not cells:
                continue
            texts = [_text(c) for c in cells]
            start = None
            date_text = ""
            extra_starts: list = []
            if date_col is not None and date_col < len(texts):
                start = _first_date_in(texts[date_col], tz, now)
                date_text = texts[date_col]
                if start:
                    # "August 6, 13, 20" in one cell = three meetings; a second
                    # Date|Time pair in the same row = a second meeting. Only
                    # the first date used to survive.
                    for frag in re.findall(r"(?<![\d/])\b(\d{1,2})\b(?![\d/:])",
                                           texts[date_col][ (texts[date_col].find(date_text) + 0):]):
                        pass
                    more = list(DATE_RE.finditer(texts[date_col]))[1:]
                    for mm in more:
                        d2 = _parse_dt(mm.group(0), tz, start.year)
                        if d2 and in_window(d2, now):
                            extra_starts.append(d2)
                    # bare day numbers after the first full date ("August 6, 13, 20")
                    tail = texts[date_col][texts[date_col].find(date_text):]
                    mday = re.match(r".*?\d{1,2}", tail)
                    for d in re.findall(r",\s*(\d{1,2})(?!\d|[:/])", tail):
                        try:
                            d2 = start.replace(day=int(d))
                        except ValueError:
                            continue
                        if d2 != start and in_window(d2, now):
                            extra_starts.append(d2)
                    for j, h in enumerate(headers):
                        if j != date_col and ("date" in h or "when" in h):
                            if j < len(texts):
                                d2 = _first_date_in(texts[j], tz, now)
                                if d2 and in_window(d2, now):
                                    tm2 = None
                                    if j + 1 < len(texts):
                                        tm2 = re.search(r"(\d{1,2}):(\d{2})\s*([APap])", texts[j + 1])
                                    if tm2:
                                        hh = int(tm2.group(1)) % 12 + (12 if tm2.group(3).lower() == "p" else 0)
                                        d2 = d2.replace(hour=hh, minute=int(tm2.group(2)))
                                    extra_starts.append(d2)
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
            # Join a separate Time column/cell ("10:00 AM") onto a date that
            # parsed at midnight - ME and NC tables split date and time.
            if (start.hour, start.minute) == (0, 0):
                cand = texts[time_col] if (time_col is not None and time_col < len(texts)) else ""
                if not re.search(r"\d{1,2}:\d{2}", cand):
                    cand = next((t for t in texts
                                 if re.fullmatch(r"\s*\d{1,2}:\d{2}\s*[APap]\.?[Mm]\.?\s*", t)), "")
                tm = re.search(r"(\d{1,2}):(\d{2})\s*([APap])", cand)
                if tm:
                    hh = int(tm.group(1)) % 12 + (12 if tm.group(3).lower() == "p" else 0)
                    start = start.replace(hour=hh, minute=int(tm.group(2)))
            if not in_window(start, now):
                continue
            cancelled = any(re.search(r"\bcancell?ed\b", t, re.I) for t in texts)
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
            if cancelled and not title.lower().startswith("[cancel"):
                # Keep the row visible - she may have planned around it - but
                # never let a cancelled hearing masquerade as a live one.
                title = f"[CANCELED] {title}"
            row_url = urljoin(source_url, link["href"]) if link else source_url
            out.append(RawEvent(
                title=title,
                start=start,
                description=" | ".join(texts)[:800],
                url=row_url,
            ))
            for d2 in extra_starts:
                if (d2.hour, d2.minute) == (0, 0) and (start.hour, start.minute) != (0, 0):
                    d2 = d2.replace(hour=start.hour, minute=start.minute)
                out.append(RawEvent(
                    title=title, start=d2,
                    description=" | ".join(texts)[:800], url=row_url,
                ))
    if not out:
        raise ValueError("no parseable table rows")
    return out


DATE_RE = re.compile(
    r"(?:(?:Mon|Tues?|Wed(?:nes)?|Thur?s?|Fri|Sat(?:ur)?|Sun)[a-z]*\.?,?\s+)?"
    r"(?:"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:t|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2}"
    r"(?:st|nd|rd|th)?(?!\d)(?:,?\s+\d{4})?"
    r"|\d{1,2}/\d{1,2}/\d{2,4}"
    r"|\d{4}/\d{1,2}/\d{1,2}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|(?<!\d)(?<!\d-)\d{1,2}-\d{1,2}-\d{4}(?!\d)"  # 8-4-2026 (GA titles)
    r")"
    r"(?:\s*,?\s*(?:at\s+)?\d{1,2}(?::\d{2})?\s*[APap]\.?[Mm]\.?)?",
    re.IGNORECASE,
)


_SCHED_CUE = re.compile(
    r"(?:scheduled\s+for|scheduled\s+to\s+(?:begin|commence)|to\s+be\s+held\s+on|"
    r"will\s+be\s+held\s+on|commenc(?:e|ing)\s+on|hearing\s+on)\s*:?\s*", re.I)


def _first_date_in(text: str, tz: ZoneInfo, now: datetime) -> Optional[datetime]:
    if not text:
        return None
    # A notice blob often opens with its filing date ("FILED DECEMBER 9,
    # 2025") while the meeting date follows "scheduled for ..." - prefer the
    # cued date (Delaware missed a Delmarva rate-case session this way).
    cue = _SCHED_CUE.search(text)
    if cue:
        m = DATE_RE.search(text, cue.end())
        if m and m.start() - cue.end() < 40:
            dt = _parse_dt(m.group(0), tz, now.year)
            if dt is not None:
                has_year = re.search(r"\d{4}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}", m.group(0))
                if not has_year and dt < now - timedelta(days=120):
                    dt = dt + relativedelta(years=1)
                return dt
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
        if len(txt) < 10:
            continue
        if len(txt) > 400:
            # A long notice paragraph is normally skipped, but if it announces
            # a session ("... is scheduled for August 19, 2026 ...") that
            # sentence IS the event - Delaware's Delmarva rate-case comment
            # session hid in one of these.
            cue = _SCHED_CUE.search(txt)
            if not cue or len(txt) > 1500:
                continue
            lo = max(txt.rfind(". ", 0, cue.start()), txt.rfind("– ", 0, cue.start()),
                     txt.rfind("- ", 0, cue.start()))
            hi = txt.find(". ", cue.end())
            txt = txt[lo + 2 if lo > 0 else 0: hi + 1 if hi > 0 else len(txt)].strip()
            if len(txt) > 400:
                txt = txt[:400]
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
        ("epoch_links", lambda: from_epoch_links(html, tz, now, url)),
        ("drupal_settings", lambda: from_drupal_settings_events(html, tz, now, url)),
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
    if strategy == "legistar":
        return from_legistar_api(url, tz, now), "legistar"
    if strategy == "ma_fileroom":
        return from_ma_fileroom(url, tz, now), "ma_fileroom"
    if strategy == "la_portal":
        return from_la_portal(url, tz, now), "la_portal"
    if strategy == "or_hearings":
        return from_or_hearings(url, tz, now), "or_hearings"
    if strategy == "pa_umbraco":
        return from_pa_umbraco(url, tz, now), "pa_umbraco"
    if strategy == "mo_modals":
        html, _ = get_text(url)
        return from_mo_modals(html, tz, now, url), "mo_modals"
    if strategy == "dated_links":
        html, _ = get_text(url)
        return from_dated_links(html, tz, now, url), "dated_links"
    html, _ = get_text(url)
    if strategy == "jsonld":
        return from_jsonld(html, tz, now, url), "jsonld"
    if strategy == "html":
        try:
            return from_html_cards(html, tz, now, url), "html_cards"
        except Exception:
            return from_html_table(html, tz, now, url), "html_table"
    if strategy == "html_table":
        return from_html_table(html, tz, now, url), "html_table"
    if strategy == "html_cards":
        return from_html_cards(html, tz, now, url), "html_cards"
    if strategy == "date_regex":
        return from_date_regex(html, tz, now, url), "date_regex"
    return extract_auto(url, tz_name, now)
