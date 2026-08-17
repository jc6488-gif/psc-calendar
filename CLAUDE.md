# CLAUDE.md

Context for Claude sessions working in this repo.

## What this is

A daily-refreshed calendar of US utility regulatory dates, built for **equity
research on the utilities sector**. It scrapes the public meeting and hearing
calendars of all 50 state utility commissions (plus FERC, DC and the New Orleans
City Council) and publishes a filterable dashboard, subscribable `.ics` feeds,
and `events.json`.

**Scope decision (2026-08-14): open meetings/hearings/schedules ONLY.** The
user's coworker maintains a separate ticker-tagged calendar of rate-case docket
milestones; this tool is the complementary layer. Ticker attribution and
rate-case detection were removed at her direction - do not reintroduce them.

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
- **Never let silence read as "quiet."** A commission that failed to scrape is
  *unknown*, not calm. The dashboard's health panel shows every commission's
  scrape status; a red cell means dates may be missing, never that nothing is
  scheduled.
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

## Coverage universe (reference only)

AEP · ATO · BKH · CNP · CPK · DTE · ED · EIX · ETR · EVRG · HE · LNT · MGEE · NI ·
NWE · OGE · OGS · PCG · PEG · PNW · POR · SO · SR · SRE · SWX · WEC

The 26 names sit before 39 of the 54 commissions. The `companies` section of
`data/coverage.yaml` is kept as REFERENCE (it informs which commissions are
`tier: core` and which blocked states matter most) but is NOT used at runtime -
attribution was removed 2026-08-14. Only `event_types` is read by the code.

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

## Current status (2026-08-17)

**Live at https://stellacc888.github.io/psc-calendar/** (renamed from jc6488-gif
on 2026-08-17 - the old URL is dead). Outlook feed:
`https://stellacc888.github.io/psc-calendar/feeds/all.ics`. Backup of the
pre-narrowing version: tag `v1.0` / branch `backup-v1.0`.

- **694 published events; 40 of 54 commissions reporting,
  3 scraped-fine-but-fully-filtered.** The raw scrape is much larger -
  see the scope decisions below, which cut it deliberately.
- **`browser` strategy (headless Chromium via Playwright)** unlocked MI, FL
  and OH. It identifies honestly as psc-calendar - a real browser engine
  telling the truth about itself, NOT impersonation. MI's CDN 403s any UA
  naming a tool but serves a real browser. OH's WebSphere portal renders
  nothing server-side but exposes its featured hearing through
  add-to-calendar links (`addtocalendar` extractor reads
  `data:text/calendar` and Google Calendar TEMPLATE hrefs); PUCO features
  ONE hearing at a time, so OH is a rolling next-hearing feed. CI installs
  Chromium with `playwright install --with-deps chromium`.
- **`pdf` / `pdf_links` strategies (pdfplumber)** unlocked MT (it published
  ONLY a PDF) and turned IN's placeholder rows into ~30 real hearings incl.
  Indiana Michigan Power (AEP) and CenterPoint Indiana (CNP). Two document
  shapes, tried in order: labelled blocks (date / CAUSE NO. / TIME / ROOM /
  caption - line scanning shreds these) then dated prose lines. Publication
  stamps, date-range headers and past-week minutes references are filtered.

### Scope decisions the desk made 2026-08-17 - do not undo without asking

1. **Only three event types are published**: Evidentiary Hearing, Open
   Meeting / Commission Meeting, Decision / Order. Public Comment Hearing,
   Workshop / Conference, Procedural Milestone and Other are still
   *classified* (so we know what a thing is) but carry `publish: false` in
   `data/coverage.yaml` and are dropped. Reversing one is a one-word edit.
   **Anything important landing in an unpublished type is a CLASSIFIER BUG** -
   fix the patterns, do not accept the loss. This is how the HRG/Regular
   Agenda/Notice of Meeting rescues were found.
2. **Electric and gas only.** `classify.is_out_of_sector` drops water,
   telecom and transport matters whose TITLE says so and carries no energy
   signal. A generic "Open Meeting" names no sector so it is never dropped -
   that is the unseparable case she explicitly wanted kept. The New Orleans
   "Utility, Cable, Telecommunications" committee is exempt: it regulates
   Entergy New Orleans.
3. **The relevance filter and column were removed** - all three published
   types are High, so it had become constant. The field survives in
   events.json and revives if a type is re-enabled.
4. **Date filter caps at 3 months**: Next week / 2 weeks / 30 days /
   3 months / Past 30 days. The .ics feed still carries the full horizon.
5. **"Could touch" column** = static commission -> ticker map from
   coverage.yaml (corporate geography, zero maintenance). It is NOT
   per-event attribution and must not be presented as such.

### Dedupe rules (learned from real failures)

- Key strips dates embedded in titles (MI publishes one meeting as
  "Commission Meeting", "August 27, 2026 Commission Meeting" and a generic
  fallback).
- Generic titles are dropped when a specific event exists the same day; when
  every variant is generic, the richest survives.
- A second pass merges titles where one *contains* the other after filler is
  stripped (TN "TPUC Commission Conference" = "Notice and Agenda for
  Commission Conference"; TX-RRC ICS "CONFERENCE" = web "RRC open meeting").
- **NEVER merge on (commission, date, time) alone.** 120 events share an
  exact commission+datetime and most are distinct - IN runs four different
  hearings at 09:30.

### Health panel has THREE states, not two

`✓` reporting, `–` scraped fine but every event was a type/sector the desk
excluded (grey - DE, HI, VT), `✕`/`!` the scrape actually failed. Conflating
the middle case with failure teaches distrust of the one instrument that
reports blindness.

- **Still failing, with the specific reason:**

| Code | Blocker | What would fix it |
|---|---|---|
| VA KY CT | JS apps the browser strategy did not crack (VA's schedules are inside DocketSearch) | Deeper per-app work |
| KS | Calendar moved to a Salesforce app that errors headless (their TLS bug is fixed) | Deeper per-app work |
| WI | Works from a residential IP; 403s GitHub's runners. Its 2 "events" are page furniture, not meetings | Proxy, self-hosted runner, or contact |
| WV NH AK | 403 to every automated client incl. real browsers | Contact the commission |
| AR | DNS dead (their outage) | Retry daily (automatic) |
| FERC | ferc.gov 403s everyone; the Federal Register carries a Sunshine Act notice only ~2 days before each meeting | Nothing - it appears and disappears monthly |


## robots.txt

Six commissions disallow automated access (IL VA NV FL KS AR). The scraper does
not parse robots.txt; it identifies itself honestly and throttles to one request
per host per second. **Decided by the user 2026-08-14: keep scraping despite
robots.txt.** IL delivers events under this policy. If she ever reverses this,
add a robots check to `fetch.py` and report disallowed sources in the health
panel rather than quietly dropping states.

## Architecture

```
data/commissions.yaml   registry: 54 commissions, 158 verified-where-possible URLs
data/coverage.yaml      ticker → subsidiary → commission map, event-type rules,
                        rate-case keyword signals
src/pscal/
  fetch.py              HTTP: retries, per-host throttle, on-disk cache
  extract.py            the extraction chain
  classify.py           event typing, noise filtering
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

**`strategy: browser` (2026-08-17) renders JS-built calendars** with headless
Chromium, identifying honestly as psc-calendar, then hands the HTML to the
ordinary chain. It unlocked MI (its CDN 403s tool-shaped UAs but serves a real
browser engine), FL (schedule pages with docketed hearings) and OH (whose
WebSphere portal exposes its featured hearing only through add-to-calendar
links - parsed by the new `addtocalendar` extractor). CI installs Chromium via
`playwright install --with-deps chromium`. VA and KS resisted: VA's schedules
live inside the DocketSearch SPA and KS moved to a Salesforce app that errors
headless.

**`strategy: pdf` / `pdf_links` (2026-08-17) read PDF agendas** with pdfplumber.
Two shapes, tried in order: *labelled blocks* (Indiana's weekly hearing list -
date / CAUSE NO. / TIME / ROOM / caption - which line scanning shreds into
fragments) and then *dated lines* (Montana's prose agenda). `pdf_links` follows
the PDFs a page links to, newest first. MT 0 -> 3 (it published ONLY a PDF);
IN placeholder rows -> ~30 real hearings incl. Indiana Michigan Power (AEP) and
CenterPoint Indiana (CNP). Publication stamps, date-range headers and
past-week minutes references are filtered - a line whose only words are month
names is not an event.

**Remaining engineering gaps:**

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

- **No ticker attribution, by design.** Removed 2026-08-14; the coworker's
  rate-case calendar carries company-level dates. This tool answers "what is
  each commission doing and when," not "which ticker does it touch."
- **Docket-level procedural schedules** (testimony deadlines, briefing dates) live
  inside procedural orders, not calendars. Caught only when posted as calendar
  entries - and rate-case tracking is out of scope here anyway.
- **City-jurisdiction gas rate cases** (Texas especially) are not covered anywhere.
- **Extraction accuracy per state is still only proven for the 12 commissions in
  `docs-preview/`.** The other 42 have verified URLs but unproven parsing — the
  chain has not been run against them end to end from an unrestricted network.
