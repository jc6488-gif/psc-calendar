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
| **PDF-only agenda** | MT (`_docs/Current-Agenda.pdf`), NJ (meeting-schedule notice) | `strategy: pdf` / `pdf_links`, three shapes. |
| **JavaScript SPA** | CT, VA, MI, FL, OH; still WI (Angular), KY | `strategy: browser`. When the calendar resists, try the **webcast** page. |
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

- **721 published events live; 42 of 54 commissions reporting,
  2 scraped-fine-but-fully-filtered.** The raw scrape is much larger -
  see the scope decisions below, which cut it deliberately.
  A local run gets 733 / 43 because **CT works from a residential IP and
  is refused from CI** - see the WI/CT note in the failure table.
- **VA and NJ went from silent to reporting (2026-08-17)** and NY from
  1 event to 4. See "Classification order" below for the bug that was
  hiding hearings everywhere, not just in these three.
- **CT is solved as a method and blocked as an origin.** The browser
  strategy renders its calendar correctly (20 events, 14 published) from
  a residential IP; from GitHub's runners `dpuc.state.ct.us` closes the
  connection on both HTTP (`ERR_EMPTY_RESPONSE`) and HTTPS
  (`ERR_CONNECTION_CLOSED`). **Do not spend another session on the
  extraction - it works.** This needs a different egress IP.
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

### Classification order: the title decides (2026-08-17)

`classify_event(title, desc)` types the TITLE first and only falls back to
title+description when the title yields "other".

The bug this fixed: classification ran on one `title + description` blob, and
the first matching rule in `coverage.yaml` wins - where `procedural` is listed
first. So NY's *"Commencement of evidentiary hearing in the Universal Service
Fund proceeding"* was filed as a Procedural Milestone, which the desk does not
publish, **because its description mentioned comments due**. Any hearing whose
description named a deadline disappeared, in every state. Vermont's only
electric/gas date was lost the same way.

The title states what an event IS; the description is context that routinely
names other, unrelated dates. Keep the fallback - many sources title an event
"Notice" and put the substance in the body.

**Do not "simplify" this back into one blob.** Reordering `coverage.yaml` is
not a fix either: whichever type sits first would then swallow the others.

### Links: scrape the feed, never link to it (2026-08-17)

`classify.is_machine_link()` catches URLs that serve data rather than a page:
`.ics`, `/ical/`, `admin-ajax.php`, the Outlook `owa/calendar` feed, Legistar
`View.ashx`, LPSC's `ReadScheduledEvents`. When an event carries no link of
its own it falls back to the page it was scraped from - and for a feed-based
source that fallback was the feed. **264 of 721 events linked to one**: the
user clicked a Maryland date and got raw JSON with escaped markup in it.

A source may now declare `public_url:` - the human page showing the same
calendar. The pipeline substitutes it, and falls back to the commission's
`home` if the registry has not named one, so a raw endpoint can never reach a
link she is invited to follow. Seven are configured (CO, IA, LA, MD, MN,
TX-RRC, UT).

**`public_url` must be the human VIEW OF THAT FEED, not a related page.**
This was got wrong on the first attempt and she caught it within the hour:
Louisiana's docket hearings were pointed at `/Agenda`, which lists only the
monthly Business & Executive Sessions and none of the T-/U- hearings; the
Railroad Commission's docketed hearings were pointed at the open-meetings
page, which lists the Commissioners' conference dates instead. Both pages
were real, live and topically adjacent - and neither contained the event.
The test is not "does this page load" but **"is the event on it?"** Confirm
by finding the calendar's own embed: CO's page carries the Google Calendar
id we scrape (base64 in the iframe src), UT's is on `psc.utah.gov` rather
than the state notice board, RRC's Hearings Calendar embeds the Outlook feed.

### Never publish a time nobody stated

An event whose source gave a DATE and no hour arrives at midnight. Rendering
that as "12:00 AM" asserts an hour nobody published, and it reads as a real
midnight meeting - **191 of 726 events were doing this.** A midnight start is
now marked `all_day`, so the dashboard and the `.ics` say "all day". No
commission sits at midnight; if one ever does, it will be wrong in the
harmless direction.

Times that ARE shown come straight from the source and are exact - spot-check
before assuming otherwise (the RRC ICS gives `20260827T090000` for the two
Aug 27 OG dockets, the LPSC portal 09:30 for its T- hearings).

**When a link is bad, fix the link.** Deleting the event loses a real hearing
to cure a cosmetic fault - the exact trade the first prime directive forbids.

**"Unreachable from a script" is not "broken".** 19 links fail our fetcher;
almost all are Michigan, FERC and NC returning 403 to non-browser clients.
They open fine when she clicks them. Check a failing link in a browser before
concluding anything about it.

### Dedupe rules (learned from real failures)

- **Missouri publishes a calendar PER COMMISSIONER.** One meeting arrives up
  to five times, each prefixed with the owner's initials and the repeated time
  ("HK 9:30am Public Meeting...", "CM 9:30am ...", "Adj 9:30am ..."). MO
  showed 16 rows for 6 real meetings. `LEADING_TIME` strips an optional short
  initials token ahead of the time, after which the rows are identical.
- **Venue words are filler in the dedupe comparison.** Where a meeting is held
  says nothing about which meeting it is, and MO prints the room in the title,
  so one Agenda Meeting read as two ("( 310)" vs "( Hearing Room 310 and via
  WebEx)").
- **A room booking is not a proceeding.** MO reserves hearing rooms on the
  same calendar, and "hearing" in "Hearing Room 305 Reserved" made each one an
  event.
- **Docket numbers are what keep same-day hearings apart.** Louisiana runs a
  dozen at 09:30 differing only by `T-379xx`, Illinois and Tennessee the same.
  Any similarity measure that ignores digits will call these duplicates and
  delete real proceedings - there is a test.

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

### The daily refresh, and what can quietly stop it

`.github/workflows/refresh.yml` runs at **10:00 UTC daily** (6am ET) with
`PSCAL_CACHE_TTL: 0`, so every run is a fresh scrape; it also runs on any push
touching `src/`, `data/` or the workflow. It commits the rebuilt `docs/` back
to main and deploys Pages, so the live site and the `.ics` feeds refresh
without anyone doing anything.

Two failure modes that do NOT announce themselves:

1. **GitHub disables a scheduled workflow after 60 days with no repository
   activity.** The calendar would silently freeze at its last good run while
   the site still looks fine. Any commit resets the clock; if this repo ever
   goes quiet for two months, check the Actions tab first.
2. **A red run does not always mean bad data, and it used to mean nothing at
   all.** The alerting step ran after the scrape, commit and deploy; a
   transient GitHub API 500 there marked a perfectly good refresh as failed
   (2026-08-17). It is now `continue-on-error: true`.

The health alert counts only genuinely failing core commissions -
`!ok && !filtered_only && tier === 'core'`. It previously counted the grey
`filtered_only` states too, so it listed DE, HI and WY as broken every single
day. **An alarm that is always on is one nobody reads**, which would have cost
the alert its whole purpose the first time a real source broke.

### Health panel has THREE states, not two

`✓` reporting, `–` scraped fine but every event was a type/sector the desk
excluded (grey - DE, HI), `✕`/`!` the scrape actually failed. Conflating
the middle case with failure teaches distrust of the one instrument that
reports blindness.

- **Still failing, with the specific reason:**

| Code | Blocker | What would fix it |
|---|---|---|
| KY | JS app the browser strategy did not crack | Deeper per-app work |
| KS | Calendar moved to a Salesforce app that errors headless (their TLS bug is fixed) | Deeper per-app work |
| WI CT | **Work from a residential IP, refused from GitHub's runners.** WI 403s; CT closes the connection outright on both HTTP and HTTPS. Both extract fine locally - CT yields 14 published events - so there is nothing left to fix in the parser | Proxy, self-hosted runner, or contact. This is the one blocker class where the code is already correct |
| ID WY | Scrape fine but every event is a type the desk excludes, AND one source fails, so they show red rather than grey | Replace the dead source; the grey/red split only reads cleanly when every other source works |
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
tests/                  142 tests, all offline
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
`playwright install --with-deps chromium`. Extended 2026-08-17 to **CT**
(the `dpuc.state.ct.us` XPages calendar - PURA's weekly Regular Meeting plus
docketed evidentiary hearings; renders correctly but the host refuses CI's
IP, so it is dark on the live site) and **VA**. KS still resists: it moved
to a Salesforce app that errors headless.

**Rendering and reaching are separate problems.** A `browser` strategy that
works on your laptop can still fail in CI, because the block is on the egress
IP rather than the client. When a source regresses only in CI, read the error
before touching the parser: `ERR_CONNECTION_CLOSED` / `ERR_EMPTY_RESPONSE` /
403 are the network refusing you, not the extraction failing.

**Virginia is not in DocketSearch after all.** The earlier note said VA's
schedules were locked inside that SPA. They are also on
`/case-information/webcasting/` - the hearing WEBCAST schedule, which is a
hearing calendar under another name, JS-rendered. It carries Appalachian
Power's base rate increase (AEP) and Columbia Gas of Virginia (NI). Worth
remembering as a pattern: **when the calendar is unreachable, look for the
webcast/streaming page** - a commission that streams its hearings has to
publish when they are.

**`strategy: pdf` / `pdf_links` (2026-08-17) read PDF agendas** with pdfplumber.
Three shapes, tried in order: *labelled blocks* (Indiana's weekly hearing list -
date / CAUSE NO. / TIME / ROOM / caption - which line scanning shreds into
fragments), then *schedule notices*, then *dated lines* (Montana's prose
agenda). `pdf_links` follows
the PDFs a page links to, newest first. MT 0 -> 3 (it published ONLY a PDF);
IN placeholder rows -> ~30 real hearings incl. Indiana Michigan Power (AEP) and
CenterPoint Indiana (CNP). Publication stamps, date-range headers and
past-week minutes references are filtered - a line whose only words are month
names is not an event.

*Schedule notices* (added 2026-08-17 for NJ) are the Open-Public-Meetings-Act
shape: the year appears once in prose, a heading names the meeting series, and
the dates carry **no year at all**, set two to a line because the PDF lays them
in columns. Neither other shape sees anything, so NJ published zero Board
Agenda Meetings - its decision dates for PSE&G. The heading supplies the title
and the nearest preceding time sentence the hour. It is deliberately strict
(a meeting heading plus 3+ bare dates) so it cannot fire on prose that mentions
a month. **Its confidence check counts dates matched, not events emitted** - by
December most of a year's schedule is past, and scoring the shape on the
survivors would throw away the meetings still to come.

**Remaining engineering gaps:**

1. **More schedule PDFs** — Colorado, Wyoming, Louisiana, Mississippi and Iowa
   all link dated agenda PDFs that are not yet configured as sources. The three
   PDF shapes now cover most layouts; this is registry work, not parser work.
2. **Headless browser holdouts** — KY (JS app) and KS (Salesforce app that
   errors headless) still resist `strategy: browser`.
3. **WI** — its two "events" were page furniture and are now filtered as such,
   so WI reads as failing, which is honest. Still needs a non-datacenter IP.

## Working on this repo

```bash
pip install -r requirements.txt
python3 -m pytest tests/ -q                    # 142 tests, no network
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
2b. **Check the webcast / livestream page.** A commission that streams its
   hearings must publish when they are, so its streaming schedule is a hearing
   calendar under another name — and it is often a plain page while the real
   calendar is a locked-down app. This is how VA was solved after its calendar
   URLs 404ed and its dockets proved to be inside an SPA.
2c. **Check for a meeting-schedule NOTICE.** Open-meetings statutes make
   commissions publish the whole year's meeting dates in advance, usually as a
   PDF. That single document beats any calendar for horizon — NJ's carries
   every Board Agenda Meeting through December.
3. `python3 tools/probe.py XX` and compare against what you see.
4. Add every missing page, **after fetching it**. Prefer a month or list view with
   a date-range parameter over a "next 3 events" widget.
5. Re-probe and confirm the count went up.

**Texas is the worked example.** PUCT's RSS summarises rather than lists, so it
alone drops public hearings. Five TX sources are now configured.

## Maintenance traps

- **Year-hardcoded URLs.** South Dakota (`/agendas/2026/default.aspx`), Oklahoma
  (`/2026-commission-meetings.html`) and New Jersey
  (`Notice Agenda and Quarterly Meeting dates-2026-SL.pdf`) embed the year and
  will silently go stale each January. Roll them over, or add both current and
  next year as sources. NJ is the one that bites hardest: that single PDF is
  the only source for all its Board Agenda Meetings.
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
- **A "NO meeting" notice is not a meeting.** Maryland posts the weeks it is
  not sitting as `NO Administrative Meeting`, in the meeting's usual slot, and
  every open-meeting pattern reads that as a meeting. `clean_title` rewrites a
  shouted leading `NO ` to `[CANCELED]`, same as Colorado's `VACATED:`. The
  case sensitivity is load-bearing: Mississippi's entire calendar is titled
  "Notice of Meeting". **A phantom date is as harmful as a missing one** - it
  puts a hold on a morning the commission has explicitly cleared.
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
