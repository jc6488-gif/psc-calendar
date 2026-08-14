# CLAUDE.md

Context for Claude sessions working in this repo.

## What this is

A daily-refreshed calendar of US utility regulatory dates, built for **equity
research on the utilities sector**. It scrapes the public meeting and hearing
calendars of all 50 state utility commissions (plus FERC, DC and the New Orleans
City Council), attributes each date to a covered ticker where it can, and
publishes a filterable dashboard, subscribable `.ics` feeds, and `events.json`.

**The user is an equity research analyst covering 26 utility names.** A date
matters if it moves an estimate: order dates, testimony deadlines, statutory
decision deadlines, rate-effective dates, and public hearings that signal
intervenor pressure. A commissioner's ribbon-cutting does not.

## The two prime directives

### 1. Completeness — a missing date is the worst failure this tool can produce

Worse than a duplicate, worse than a mis-tagged ticker, worse than an ugly title.
If she plans around a calendar that silently omits a hearing, the tool has harmed
her. Therefore:

- **Scrape every configured source and merge — never stop at the first hit.** An
  earlier version stopped at the first source that returned anything, which capped
  Texas at two items from a thin RSS summary while the month-grid calendar beside
  it listed the open meetings and hearings on the merits she needed. Commissions
  routinely split open meetings, evidentiary hearings and local public hearings
  across three or four pages.
- **More sources is strictly better.** Overlap is free — duplicates collapse and
  repeat fetches hit the cache.
- **Never let a zero read as "quiet."** A covered name with no dates whose
  commission failed to scrape is *unknown*, not calm. The roster strip shows all
  26 tickers always and marks such zeros red with ⚠.
- **Never emit a link you can't stand behind.** Relative or malformed hrefs fall
  back to the page the event was scraped from.

### 2. Verification — never add a URL you have not fetched

This one was learned expensively. The first registry was built from prior
knowledge without checking a single address, and it *looked* authoritative: 119
plausible URLs, sensible paths, real domains. When it was finally run against all
54 commissions, **28 returned HTTP 404**. Roughly three-quarters of the tool would
have come back red on day one.

A clever extraction chain is worthless pointed at a page that doesn't exist.

**Rule: every entry in `data/commissions.yaml` must be fetched and confirmed to
list actual dated events before it is committed.** Mark confirmed entries
`# VERIFIED WORKING <date>`. When you add a source, fetch it. When you can't
fetch it, say so in the registry `notes` rather than committing a hopeful guess.

## Coverage universe

AEP · ATO · BKH · CNP · CPK · DTE · ED · EIX · ETR · EVRG · HE · LNT · MGEE · NI ·
NWE · OGE · OGS · PCG · PEG · PNW · POR · SO · SR · SRE · SWX · WEC

Defined in `data/coverage.yaml`. All 26 appear in the dashboard at all times.
**Do not filter the roster to names that happen to have events** — that bug made
the tool look like it covered 13 names when it covered 26.

## How commissions actually publish calendars

There is **no standard and no "PUC API."** Nothing like a common schema exists.
What you find instead, verified across all 54:

| Pattern | Examples | How to handle |
|---|---|---|
| **RSS / iCal feed** | TX (RSS) | Best case. Rare. |
| **Server-rendered HTML list or table** | NY, GA, MO, NC, ME, SC, DC, WA, IA, NE | The chain handles these. Most common good case. |
| **Municipal meeting platform** | MN (Legistar), NOLA (Granicus), SD (statewide boards portal) | Legistar and Granicus have predictable URLs and sometimes APIs. |
| **Statewide public-notice portal** | UT (`utah.gov/pmn`), ID (`townhall.idaho.gov`), DE, SD | **Often better than the commission's own site.** Always check whether the state runs one. |
| **Google Calendar iframe embed** | CO, IA, WY, MN, UT | Page HTML is empty. Find the sibling agendas/minutes index instead — that's how CO, IA and WY were solved. |
| **PDF-only agenda** | MT (`_docs/Current-Agenda.pdf`) | Needs PDF text extraction — **not yet implemented**. |
| **JavaScript SPA** | WI (Angular), KY, CPUC events page | Fetching returns an empty shell. Needs a headless browser — **not yet implemented**. |
| **Portal-encoded CDATA** | OH (WebSphere) | Body is base64/CDATA. No HTML parser will touch it. |
| **WAF / 403 block** | WV (whole domain), NH (path-level) | Server refuses automated clients. Not a method problem. |
| **robots.txt disallow** | IL, VA, NV, FL, KS, AR | A *permission* question, not a technical one. See below. |

**Permission and method are different axes.** robots.txt and 403 are a site
telling you not to come. JavaScript rendering is a site happy to serve you but not
in a form a simple fetch can read. They need different responses — the first is a
policy call for the user, the second is an engineering fix.

## Current status (verified 2026-08-14)

- **54 commissions, 158 source URLs**, 31 commissions repaired with confirmed URLs.
- **Confirmed serving real dates:** FERC, NY, CA, MO, GA, NJ, TX, IN, PA, RI, AL,
  NOLA, plus the 31 repaired (CO, LA, MS, MN, MD, NE, SD, OR, TX-RRC, AZ, OK, TN,
  IA, WY, MT, HI, MI, AK, DE, UT, NM, NC, VT, ID, ME, ND, SC, DC, MA, WA, KY).
- **Still blocked, with the specific reason:**

| Code | Blocker | What would fix it |
|---|---|---|
| OH | WebSphere portal CDATA on every events URL; `dis.puc.state.oh.us` WAF-rejects | Headless browser, or the GovDelivery bulletin feed if one can be found |
| WI | Angular SPA; detail routes like `/HearingDetails/36` render server-side but the list route wasn't found | Headless browser, or discover the SPA's XHR endpoint |
| WV | Entire `psc.state.wv.us` domain returns 403 | Contact the commission; no technical workaround |
| NH | Path-level 403 on every calendar candidate | Same |
| CT | `dpuc.state.ct.us` (the official calendar) unreachable; `egov.ct.gov/PMC` is JS-only | Headless browser against egov.ct.gov |
| IL VA NV FL KS AR | robots.txt disallowed | **User's call** — see below |

## robots.txt

Six commissions disallow automated access. **The scraper does not currently parse
robots.txt**; it identifies itself honestly via `PSCAL_USER_AGENT` and throttles to
one request per host per second.

**Surface this to the user rather than deciding for her.** The options are
respecting it and losing those states, or contacting the commissions for access.
If she wants strict compliance, add a robots check to `fetch.py` and report
disallowed sources in the health panel. Do not quietly ignore it, and do not
quietly drop the state.

## Architecture

```
data/commissions.yaml   registry: 54 commissions, 158 verified-where-possible URLs
data/coverage.yaml      ticker → subsidiary → commission map, event-type rules,
                        rate-case keyword signals
src/pscal/
  fetch.py              HTTP: retries, per-host throttle, on-disk cache
  extract.py            the extraction chain
  classify.py           ticker attribution, event typing, rate-case detection
  models.py             Event dataclass, docket-number regexes
  pipeline.py           scrape all sources → merge → dedupe → emit
  emit_ics.py           RFC 5545 feeds
  emit_site.py          the dashboard (one self-contained HTML file)
tools/probe.py          diagnose one commission — reach for this first
tools/demo.py           build from synthetic fixtures, no network
tools/build_preview.py  build from real captured data
tests/                  52 tests, all offline
```

### The extraction chain

```
ics → rss → JSON-LD schema.org/Event → Tribe/Drupal JSON API
    → HTML cards → HTML tables → date-regex over page text
```

`auto` also sniffs for a linked `.ics`/RSS feed and follows it. `date_regex` is the
floor, not a goal — if a commission lands there its events will be noisy, so hunt
for a better URL first.

**Two strategies are missing and are the highest-value next work:**

1. **PDF extraction** — Montana publishes only `Current-Agenda.pdf`; Colorado,
   Wyoming, Louisiana, Mississippi and Iowa all link dated agenda PDFs. `pdfplumber`
   plus the existing `date_regex` logic would unlock these.
2. **Headless browser** — Playwright/Chromium for the SPA cases (WI, KY, CT, OH,
   CPUC events). Chromium is already available in most CI images. Add it as a
   `strategy: browser` that renders then hands HTML to the existing parsers.

## Working on this repo

```bash
pip install -r requirements.txt
python3 -m pytest tests/ -q                    # 52 tests, no network
python3 tools/probe.py TX --raw                # diagnose one commission
python3 -m src.pscal.pipeline --only TX CA OH  # live scrape, a few states
python3 -m src.pscal.pipeline                  # full run → docs/
```

`docs-demo/` is synthetic fixtures — its links are invented and will 404. It exists
to check layout offline. **Never send it to the user as if it were real output.**
`docs-preview/` is real captured data.

## Auditing a state for missing dates

The highest-value maintenance task, and what to do when she says "you're missing
dates."

1. Open the commission's site. Find **every** page that lists dates: the main
   calendar, the open-meeting/agenda page, the hearing schedule, the public-notice
   or press-release feed, and the homepage "upcoming events" widget. These are
   usually four different pages with four different subsets.
2. **Check whether the state runs a central public-notice portal** — several do,
   and it is often more scrapeable than the commission's own site.
3. `python3 tools/probe.py XX` and compare against what you see.
4. Add every missing page, **after fetching it**. Prefer a month or list view with
   a date-range parameter over a "next 3 events" widget.
5. Re-probe and confirm the count went up.

**Texas is the worked example.** PUCT's RSS summarises rather than lists, so it
alone drops public hearings. Five TX sources are now configured.

## Maintenance traps

- **Year-hardcoded URLs.** South Dakota (`/agendas/2026/default.aspx`) and Oklahoma
  (`/2026-commission-meetings.html`) embed the year and will silently go stale each
  January. Roll them over, or add both current and next year as sources.
- **A 200 response is not success.** Several pages load fine and contain zero
  events (Google Calendar embeds, SPA shells, landing hubs). Verification means
  confirming *dated events are visible*, not that the URL resolves.
- **Site redesigns.** A broken source shows red in the health panel and opens a
  `scraper-health` GitHub issue. `python3 tools/probe.py OH --raw` first.

## Domain rules that are easy to get wrong

- **Entergy New Orleans is regulated by the New Orleans City Council, not the
  Louisiana PSC.** ETR has five retail jurisdictions plus FERC. There is a test.
- **Texas gas rates sit at the Railroad Commission (`TX-RRC`), not the PUCT.** Atmos
  Mid-Tex, Texas Gas Service and CenterPoint's Texas gas cases go there — and the
  RRC's dated conferences live at `/general-counsel/open-meetings/`, not on any
  page called "calendar." Texas gas LDCs also negotiate with ~440 city rate
  jurisdictions whose dates are not published centrally. Say so.
- **PUCT "Hearing on the Merits"** is the evidentiary phase — the date that moves a
  contested rate case. Don't let it classify as a generic open meeting.
- **Statutory clocks often matter more than the posted calendar.** Missouri 11
  months, Michigan 10, Oregon 10, Pennsylvania 9. The order deadline is implied by
  statute at filing, before anything is scheduled.
- **Nebraska is a public-power state** — only gas LDCs are rate-regulated there.
- **Wisconsin** runs biennial rate cases on a fixed cycle; **Alabama** uses RSE;
  **Hawaii** runs PBR. "No rate case scheduled" means different things by state.
- **CenterPoint divested its Louisiana and Mississippi gas LDCs in 2025.**
- **A failing scraper is not an empty docket.**

## Conventions

- **Python 3.12**, stdlib plus six deps. No framework, no database — static output.
- **Tests run offline.** Fixtures mirror real markup. Never add a network test.
- **Event UIDs must stay stable across runs.** Change the basis and every
  subscriber gets duplicates.
- **Be polite.** One request per host per second, contactable User-Agent. These are
  public-sector sites on small budgets.
- **The dashboard is one self-contained HTML file.** No build step, no CDN, no
  localStorage.
- **Chart/color changes** keep passing the dataviz palette validator: categorical
  slots in validated fixed order, week chart a single sequential hue with no
  legend, event-type color always paired with its text label. No second y-axis.

## Honest limitations — state these plainly

- **Attribution is the weakest link.** Commission calendars usually don't name the
  utility in the title — "Open Meeting," "Administrative Session," "Agenda
  Meeting." The company is in the agenda PDF or the linked detail page. On real
  captured data only 4 of 26 names attributed. **Following each event's detail link
  and parsing parties from the agenda is the single highest-value improvement
  available**, and would do more for her workflow than any additional state.
- **Docket-level procedural schedules** (testimony deadlines, briefing dates) live
  inside procedural orders, not calendars. Caught only when posted as calendar
  entries.
- **City-jurisdiction gas rate cases** (Texas especially) are not covered anywhere.
- **Extraction accuracy per state is still only proven for the 12 commissions in
  `docs-preview/`.** The other 42 have verified URLs but unproven parsing — the
  chain has not been run against them end to end from an unrestricted network.
