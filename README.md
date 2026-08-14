# Utility Regulatory Calendar

Daily-refreshed calendar of US utility commission meetings, hearings and rate-case
dates, built for equity research on the utilities sector.

Scrapes all 50 state commissions plus FERC and the New Orleans City Council,
attributes dates to a 26-name coverage universe, and publishes a filterable
dashboard and subscribable `.ics` feeds.

---

## Setup — about 10 minutes, once

### 1. Create the repo

On GitHub: **New repository** → name it `psc-calendar` → **Private** is fine →
create it empty (no README).

Then, in this folder:

```bash
git init
git add -A
git commit -m "Utility regulatory calendar"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/psc-calendar.git
git push -u origin main
```

### 2. Turn on Pages

Repo **Settings → Pages → Source: GitHub Actions**. (Not "Deploy from a branch.")

### 3. Allow the workflow to commit

Repo **Settings → Actions → General → Workflow permissions** →
**Read and write permissions** → Save.

### 4. Run it

**Actions** tab → **Refresh regulatory calendar** → **Run workflow**.

First run takes ~5 minutes. When it finishes, the run summary lists exactly which
commissions reported and which failed, and your site is live at:

```
https://YOUR-USERNAME.github.io/psc-calendar/
```

After this it runs itself every morning at 6am ET.

> **Private repos:** GitHub Pages needs a paid plan for private repos. If yours is
> private and on the free plan, either make it public (there's nothing sensitive
> here — it's all public regulatory data) or skip Pages and use the committed
> `docs/index.html` and `docs/feeds/*.ics` files directly from the repo.

---

## Subscribing to the calendar

Subscribe **by URL** — don't download and import, or updates and cancellations
won't reach you.

| Feed | URL |
|---|---|
| Coverage universe | `…github.io/psc-calendar/feeds/coverage.ics` |
| High priority only | `…/feeds/high-priority.ics` |
| Rate cases | `…/feeds/rate-cases.ics` |
| Everything, 50 states | `…/feeds/all.ics` |
| One ticker | `…/feeds/ticker-AEP.ics` |
| One commission | `…/feeds/commission-OH.ics` |

**Outlook:** Add calendar → Subscribe from web → paste URL.
**Google Calendar:** Other calendars → + → From URL → paste URL.
**Apple Calendar:** File → New Calendar Subscription → paste URL.

Google refreshes external feeds on its own schedule (often 12–24h). Outlook is
usually faster. The feeds advertise a 3-hour refresh hint, but clients decide.

---

## Using it

The dashboard filters by ticker, commission, event type and date range, and
everything composes. Sort by any column. **Export CSV** respects the current
filters, so you can pull "everything for AEP in the next 6 months" straight into a
model.

Each row shows the commission, the matched operating subsidiary, the docket number
where one could be parsed, and whether the event looks rate-case relevant.

**Check the source-health panel before concluding a docket is quiet.** A failed
scrape means dates are missing, not that none are scheduled.

---

## Maintenance

Commissions redesign their sites; scrapers break. When a core source fails, the
workflow opens a GitHub issue tagged `scraper-health` and the dashboard shows it
red.

```bash
python3 tools/probe.py OH --raw     # diagnose one commission
python3 tools/demo.py               # preview the dashboard offline
python3 -m pytest tests/ -q         # 52 tests, no network
```

Usually the fix is a changed URL in `data/commissions.yaml`. `CLAUDE.md` has the
full playbook — hand this repo to Claude and ask it to fix the failing state.

---

## What it doesn't cover

- **Docket-level procedural schedules.** Testimony deadlines and briefing dates
  live inside procedural orders, not on calendars. Caught only when a commission
  posts them as calendar entries.
- **Texas city gas jurisdictions.** Atmos and Texas Gas Service negotiate rates
  with ~440 municipalities; those dates aren't centrally published.
- **Attribution is keyword-based** — it can miss an event that cites only a docket
  number, and can over-tag a broad agenda. The matched entity is always shown so
  you can see the reasoning.
