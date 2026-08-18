"""Generate the static dashboard.

Form choices follow the dataviz method:
  - Four headline numbers -> KPI row of stat tiles, not a bar chart.
  - "How busy is each of the next 12 weeks" -> magnitude over time, single
    series -> column chart in ONE sequential hue, no legend (the title names it).
  - 54 commissions x 6 event types all carry meaning -> a filterable TABLE, not more
    colors. Event-type identity is a dot ALWAYS paired with its text label.
  - Scraper health uses the reserved status palette with icon + label.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Utility Regulatory Calendar</title>
<style>
  :root {
    color-scheme: light;
    --surface-1: #fcfcfb;
    --plane: #f9f9f7;
    --text-primary: #0b0b0b;
    --text-secondary: #52514e;
    --muted: #898781;
    --grid: #e1e0d9;
    --baseline: #c3c2b7;
    --border: rgba(11,11,11,0.10);
    --accent: #2a78d6;
    --accent-soft: #cde2fb;
    --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#eda100; --s5:#e87ba4; --s6:#008300;
    --good:#0ca30c; --warning:#fab219; --serious:#ec835a; --critical:#d03b3b;
    --hover: rgba(11,11,11,0.04);
  }
  :root[data-theme="dark"] {
    color-scheme: dark;
    --surface-1: #1a1a19;
    --plane: #0d0d0d;
    --text-primary: #ffffff;
    --text-secondary: #c3c2b7;
    --muted: #898781;
    --grid: #2c2c2a;
    --baseline: #383835;
    --border: rgba(255,255,255,0.10);
    --accent: #3987e5;
    --accent-soft: #184f95;
    --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; --s5:#d55181; --s6:#008300;
    --hover: rgba(255,255,255,0.06);
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      color-scheme: dark;
      --surface-1: #1a1a19; --plane: #0d0d0d; --text-primary: #ffffff;
      --text-secondary: #c3c2b7; --grid: #2c2c2a; --baseline: #383835;
      --border: rgba(255,255,255,0.10); --accent: #3987e5; --accent-soft: #184f95;
      --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; --s5:#d55181; --s6:#008300;
      --hover: rgba(255,255,255,0.06);
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--plane); color: var(--text-primary);
    font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  .wrap { max-width: 1500px; margin: 0 auto; padding: 24px 20px 64px; }
  header { display: flex; align-items: flex-start; gap: 16px; flex-wrap: wrap;
           justify-content: space-between; margin-bottom: 20px; }
  h1 { font-size: 21px; font-weight: 650; margin: 0 0 4px; letter-spacing: -0.01em; }
  .sub { color: var(--text-secondary); font-size: 13px; }
  .card { background: var(--surface-1); border: 1px solid var(--border);
          border-radius: 10px; padding: 16px; }

  /* ---- KPI row: stat tiles, not a chart ---- */
  .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px,1fr));
          gap: 12px; margin-bottom: 12px; }
  .kpi .label { font-size: 11px; text-transform: uppercase; letter-spacing: .07em;
                color: var(--muted); font-weight: 600; }
  .kpi .value { font-size: 34px; font-weight: 640; margin-top: 6px; letter-spacing: -0.02em; }
  .kpi .foot { font-size: 12px; color: var(--text-secondary); margin-top: 2px; }

  /* ---- column chart: one hue, single series, no legend ---- */
  .chartwrap { margin-bottom: 12px; }
  .chart-title { font-size: 13px; font-weight: 620; margin-bottom: 2px; }
  .chart-sub { font-size: 12px; color: var(--text-secondary); margin-bottom: 14px; }
  #weeks { display: block; width: 100%; height: 150px; overflow: visible; }
  #weeks .bar { fill: var(--accent); }
  #weeks .bar:hover { fill: var(--text-primary); }
  #weeks .tick { fill: var(--muted); font-size: 10px; }
  #weeks .vlabel { fill: var(--text-secondary); font-size: 10px; font-weight: 600; }
  #weeks .base { stroke: var(--baseline); stroke-width: 1; }
  .rel { font-size: 11px; font-weight: 650; letter-spacing: .02em; }
  .rel-high { color: var(--critical); }
  .rel-medium { color: var(--text-secondary); }
  .rel-low { color: var(--muted); }

  /* ---- filters: one row above the content ---- */
  .filters { display: flex; gap: 8px; flex-wrap: wrap; align-items: center;
             margin: 16px 0 12px; }
  input[type=search], select {
    font: inherit; padding: 7px 10px; border-radius: 7px; background: var(--surface-1);
    border: 1px solid var(--border); color: var(--text-primary); min-height: 34px;
  }
  input[type=search] { min-width: 240px; flex: 1 1 240px; }
  .chip { font: inherit; font-size: 13px; padding: 7px 12px; border-radius: 7px;
          border: 1px solid var(--border); background: var(--surface-1);
          color: var(--text-secondary); cursor: pointer; min-height: 34px; }
  .chip:hover { background: var(--hover); }
  .chip[aria-pressed="true"] { background: var(--accent); border-color: var(--accent);
                               color: #fff; font-weight: 600; }
  :root[data-theme="dark"] .chip[aria-pressed="true"] { color: #0d0d0d; }

  /* ---- table: the primary view ---- */
  table { width: 100%; border-collapse: collapse; }
  thead th { text-align: left; font-size: 11px; text-transform: uppercase;
             letter-spacing: .06em; color: var(--muted); font-weight: 650;
             padding: 8px 10px; border-bottom: 1px solid var(--baseline);
             position: sticky; top: 0; background: var(--surface-1); z-index: 2;
             cursor: pointer; white-space: nowrap; }
  thead th:hover { color: var(--text-primary); }
  tbody td { padding: 10px; border-bottom: 1px solid var(--grid); vertical-align: top; }
  tbody tr:hover { background: var(--hover); }
  .date { font-variant-numeric: tabular-nums; white-space: nowrap; font-weight: 600; }
  .time { color: var(--muted); font-weight: 400; font-size: 12px; display: block; }
  .soon { color: var(--critical); }
  td.title a { color: var(--text-primary); text-decoration: none; }
  td.title a:hover { text-decoration: underline; text-decoration-color: var(--accent); }
  .meta { color: var(--muted); font-size: 12px; margin-top: 3px; }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
         margin-right: 6px; vertical-align: 1px; }
  .tag { display: inline-block; font-size: 11px; font-weight: 650; padding: 2px 7px;
         border-radius: 5px; border: 1px solid var(--border); margin: 0 4px 3px 0;
         white-space: nowrap; }
  .tag.tk { background: var(--accent-soft); color: var(--text-primary);
            border-color: transparent; }
  .docket { font-variant-numeric: tabular-nums; font-size: 12px; color: var(--text-secondary);
            white-space: nowrap; }
  .type { white-space: nowrap; font-size: 12px; color: var(--text-secondary); }
  .st { font-size: 12px; color: var(--text-secondary); white-space: nowrap; }

  /* ---- health ---- */
  .health { display: grid; grid-template-columns: repeat(auto-fill, minmax(112px,1fr));
            gap: 6px; margin-top: 12px; }
  .hcell { font-size: 11px; padding: 6px 8px; border-radius: 6px;
           border: 1px solid var(--border); display: flex; align-items: center; gap: 5px; }
  .hcell b { font-weight: 650; }
  .hcell .n { margin-left: auto; color: var(--muted); font-variant-numeric: tabular-nums; }

  details.about { margin-top: 20px; }
  summary { cursor: pointer; font-weight: 620; font-size: 13px; padding: 6px 0; }
  .feeds a { color: var(--accent); text-decoration: none; font-size: 13px; }
  .feeds a:hover { text-decoration: underline; }
  .feedrow { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 8px; }
  code { background: var(--hover); padding: 1px 5px; border-radius: 4px; font-size: 12px; }
  .empty { padding: 48px 16px; text-align: center; color: var(--muted); }
  .count { color: var(--text-secondary); font-size: 13px; margin: 4px 2px 10px; }
  @media (max-width: 760px) {
    .hide-sm { display: none; }
    .kpi .value { font-size: 27px; }
  }
  td.pick, th.pick { width: 34px; text-align: center; padding-left: 10px; }
  td.pick input { width: 16px; height: 16px; cursor: pointer; }
  /* Ticking a box has to LOOK like crossing the event off, or the only
     feedback is a counter on a button somewhere else on the page. */
  tr.picked { opacity: .45; }
  tr.picked .title, tr.picked .date { text-decoration: line-through; }
  tr.picked .title a { color: var(--text-secondary); }
  #review[aria-pressed="true"] { background: var(--accent); color: #fff; }
</style>
</head>
<body>
<div class="wrap">

<header>
  <div>
    <h1>Utility Regulatory Calendar</h1>
    <div class="sub">
      State commission meetings, hearings and procedural dates &middot;
      <span id="genstamp"></span>
    </div>
  </div>
  <div style="display:flex;gap:8px">
    <button class="chip" id="review" aria-pressed="false">Review mode</button>
    <button class="chip" id="drop" hidden>Copy exclusions (0)</button>
    <button class="chip" id="csv">Export CSV (current filter)</button>
    <button class="chip" id="theme" aria-pressed="false">Dark</button>
  </div>
</header>

<div class="kpis">
  <div class="card kpi"><div class="label">Tracked dates</div>
    <div class="value" id="k-total">—</div>
    <div class="foot" id="k-total-f"></div></div>
  <div class="card kpi"><div class="label">Next 7 days</div>
    <div class="value" id="k-week">—</div>
    <div class="foot" id="k-week-f"></div></div>
</div>

<div class="card chartwrap">
  <div class="chart-title">Scheduled dates per week</div>
  <div class="chart-sub" id="chart-sub">Next 12 weeks, matching current filters</div>
  <svg id="weeks" role="img" aria-label="Column chart of scheduled dates per week"></svg>
</div>

<div class="filters">
  <input type="search" id="q" placeholder="Search title, docket, company…" aria-label="Search">
  <select id="f-state" aria-label="Filter by commission"><option value="">All commissions</option></select>
  <select id="f-type" aria-label="Filter by event type"><option value="">All event types</option></select>
  <select id="f-range" aria-label="Date range">
    <option value="7">Next week</option>
    <option value="14">Next 2 weeks</option>
    <option value="30">Next 30 days</option>
    <option value="90" selected>Next 3 months</option>
    <option value="past">Past 30 days</option>
  </select>
  <button class="chip" id="reset">Reset</button>
</div>

<div class="count" id="count"></div>

<div class="card" style="padding:0;overflow:hidden">
  <table>
    <thead><tr>
      <th class="pick" hidden></th>
      <th data-sort="start">Date</th>
      <th data-sort="commission">Comm.</th>
      <th data-sort="title">Event</th>
      <th data-sort="event_type" class="hide-sm">Type</th>
      <th class="hide-sm">Could touch</th>
    </tr></thead>
    <tbody id="rows"></tbody>
  </table>
  <div class="empty" id="empty" hidden>No dates match these filters.</div>
  <div id="reviewhelp" class="card" hidden style="margin:10px;padding:10px 12px;font-size:13px;color:var(--text-secondary)">
    Tick the events you don't want, then press <b>Copy exclusions</b> and paste
    the lines <b>directly under the <code>events:</code> line</b> in
    <code>data/exclusions.yaml</code> on GitHub (its web editor — no terminal).
    Adding to the list is safe; replacing it would drop your earlier reviews.
    The site and the Outlook feeds follow a few minutes later.
  </div>
</div>

<details class="about" open>
  <summary>Subscribe in Outlook / Google Calendar</summary>
  <div class="card" style="margin-top:8px">
    <p style="margin:0 0 6px;color:var(--text-secondary)">
      These feeds refresh daily. Subscribe once by URL — do not download and import,
      or updates and cancellations will not reach you.
      In Outlook: <em>Add calendar &rarr; Subscribe from web</em>.
      In Google Calendar: <em>Other calendars &rarr; From URL</em>.
    </p>
    <div class="feedrow feeds">
      <a href="feeds/high-priority.ics">High priority only (.ics)</a>
      <a href="feeds/all.ics">Everything, all 50 states (.ics)</a>
      <a href="events.json">Raw JSON</a>
    </div>
    <p style="margin:10px 0 0;color:var(--muted);font-size:12px">
      Per-commission feeds: <code>feeds/commission-OH.ics</code>
    </p>
  </div>
</details>

<details class="about">
  <summary>Source health — which commissions scraped cleanly</summary>
  <div class="card" style="margin-top:8px">
    <p style="margin:0 0 4px;color:var(--text-secondary);font-size:13px">
      <b>✓</b> reporting &middot; <b>–</b> scraped fine, but every event was one of the
      types/sectors you excluded &middot; <b>✕ / !</b> the scrape failed, so that
      commission's dates are missing from the table below — <i>not</i> that it has none
      scheduled. Check the source site directly before concluding a docket is quiet.
    </p>
    <div class="health" id="health"></div>
  </div>
</details>

</div>

<script id="payload" type="application/json">__PAYLOAD__</script>
<script>
(function () {
  "use strict";
  const DATA = JSON.parse(document.getElementById("payload").textContent);
  const EV = DATA.events;
  const COV_MAP = DATA.coverage_map || {};
  const TYPE_COLOR = { open_meeting:"var(--s1)", evidentiary_hearing:"var(--s2)",
    decision_order:"var(--s3)", public_comment:"var(--s4)", procedural:"var(--s5)",
    workshop:"var(--s6)", other:"var(--muted)" };

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));

  // ---- theme -------------------------------------------------------------
  const themeBtn = $("theme");
  function setTheme(dark) {
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    themeBtn.setAttribute("aria-pressed", String(dark));
    themeBtn.textContent = dark ? "Light" : "Dark";
    drawChart(lastRows);
  }
  themeBtn.onclick = () =>
    setTheme(document.documentElement.getAttribute("data-theme") !== "dark");

  // ---- populate filter options ------------------------------------------
  const comms = [...new Set(EV.map((e) => e.commission))].sort();
  const commName = {};
  EV.forEach((e) => { commName[e.commission] = e.commission_name; });
  const types = [...new Map(EV.map((e) => [e.event_type, e.event_type_label])).entries()];

  comms.forEach((c) => $("f-state").add(new Option(`${c} — ${commName[c]}`, c)));
  types.forEach(([id, label]) => $("f-type").add(new Option(label, id)));

  const state = { q:"", comm:"", type:"", range:"90", sort:"start", dir:1 };

  const startOfToday = new Date(); startOfToday.setHours(0, 0, 0, 0);

  function filtered() {
    const q = state.q.trim().toLowerCase();
    let lo = startOfToday, hi = null;
    if (state.range === "past") {
      lo = new Date(startOfToday.getTime() - 30 * 864e5);
      hi = startOfToday;
    } else {
      // 3 months is the furthest horizon the desk wants to see.
      hi = new Date(startOfToday.getTime() + Number(state.range) * 864e5);
    }
    return EV.filter((e) => {
      const d = new Date(e.start);
      if (d < lo) return false;
      if (hi && d > hi) return false;
      if (state.comm && e.commission !== state.comm) return false;
      if (state.type && e.event_type !== state.type) return false;
      if (q) {
        const hay = (e.title + " " + e.description + " " + e.dockets.join(" ") + " " +
                     e.commission + " " + e.commission_name).toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }

  function sorted(rows) {
    const k = state.sort, dir = state.dir;
    return rows.slice().sort((a, b) => {
      let x = a[k], y = b[k];
      if (Array.isArray(x)) { x = x.join(","); y = y.join(","); }
      if (k === "start") { x = a.start; y = b.start; }
      if (x < y) return -1 * dir;
      if (x > y) return 1 * dir;
      return a.start < b.start ? -1 : 1;
    });
  }

  // Times are rendered in the commission's OWN timezone, not the viewer's.
  // A 9am Honolulu hearing is a 9am Honolulu hearing whether you read this
  // from New York or not; the tz label makes that explicit.
  const _dcache = {}, _tcache = {};
  function dfmt(tz) {
    return _dcache[tz] || (_dcache[tz] = new Intl.DateTimeFormat("en-US",
      { weekday:"short", month:"short", day:"numeric", year:"numeric", timeZone: tz }));
  }
  function tfmt(tz) {
    return _tcache[tz] || (_tcache[tz] = new Intl.DateTimeFormat("en-US",
      { hour:"numeric", minute:"2-digit", timeZone: tz, timeZoneName: "short" }));
  }
  const LOCAL = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const DFMT = new Intl.DateTimeFormat("en-US",
    { weekday:"short", month:"short", day:"numeric", year:"numeric" });
  const TFMT = new Intl.DateTimeFormat("en-US", { hour:"numeric", minute:"2-digit" });

  function render() {
    const rows = sorted(filtered());
    lastRows = rows;

    const wk = rows.filter((e) => {
      const d = new Date(e.start);
      return d >= startOfToday && d <= new Date(startOfToday.getTime() + 7 * 864e5);
    }).length;

    $("k-total").textContent = rows.length;
    $("k-total-f").textContent = `${DATA.commissions.filter((c) => c.ok).length} of ${DATA.commissions.length} sources reporting`;
    $("k-week").textContent = wk;
    $("k-week-f").textContent = wk ? "act on these first" : "nothing imminent";

    $("count").textContent =
      `${rows.length} date${rows.length === 1 ? "" : "s"}`;

    const tb = $("rows");
    tb.innerHTML = rows.slice(0, 1200).map((e) => {
      const d = new Date(e.start);
      const days = Math.round((d - startOfToday) / 864e5);
      const soon = days >= 0 && days <= 7 ? " soon" : "";
      const tz = e.tz || LOCAL;
      const time = e.all_day ? "all day" : tfmt(tz).format(d);
      const link = e.url
        ? `<a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.title)}</a>`
        : esc(e.title);
      const dk = e.dockets.length
        ? `<div class="meta">Docket ${esc(e.dockets.slice(0, 3).join(", "))}</div>` : "";
      return `<tr class="${PICKED.has(e.uid) ? "picked" : ""}">
        <td class="pick" hidden><input type="checkbox" class="x" data-uid="${esc(e.uid)}"${PICKED.has(e.uid) ? " checked" : ""} aria-label="Exclude this event"></td>
        <td class="date${soon}">${dfmt(tz).format(d)}<span class="time">${time}</span></td>
        <td class="st"><span title="${esc(e.commission_name)}">${esc(e.commission)}</span></td>
        <td class="title">${link}${dk}</td>
        <td class="type hide-sm"><span class="dot" style="background:${TYPE_COLOR[e.event_type] || "var(--muted)"}"></span>${esc(e.event_type_label)}</td>
        <td class="hide-sm">${(COV_MAP[e.commission] || []).map((t) => `<span class="tag tk">${esc(t)}</span>`).join("")}</td>
      </tr>`;
    }).join("");

    $("empty").hidden = rows.length > 0;
    drawChart(rows);
  }

  // ---- column chart: single series, one hue, no legend --------------------
  let lastRows = [];
  function drawChart(rows) {
    const svg = $("weeks");
    const W = svg.clientWidth || 900, H = 150;
    const weeks = 12;
    // Narrow viewports: short "8/10" labels on two staggered rows, so every
    // bar stays labeled without collisions.
    const compact = (W - 8) / weeks < 64;
    const padB = compact ? 32 : 22, padT = 18, padL = 4;
    const monday = new Date(startOfToday);
    monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7));

    const buckets = new Array(weeks).fill(0);
    rows.forEach((e) => {
      const i = Math.floor((new Date(e.start) - monday) / (7 * 864e5));
      if (i >= 0 && i < weeks) buckets[i]++;
    });
    const max = Math.max(1, ...buckets);
    const bw = (W - padL * 2) / weeks;
    const barW = Math.max(6, bw - 6);   // 2px+ surface gap between adjacent bars
    const plotH = H - padB - padT;

    // Every bar carries its own week label and its own count - a bar the
    // reader cannot name or quantify is decoration, not information. Empty
    // weeks show an explicit 0 so "quiet" is stated rather than implied.
    const parts = [`<line class="base" x1="0" y1="${H - padB}" x2="${W}" y2="${H - padB}"/>`];
    buckets.forEach((n, i) => {
      const h = Math.max(n > 0 ? 3 : 0, (n / max) * plotH);
      const x = padL + i * bw + (bw - barW) / 2;
      const y = H - padB - h;
      const wd = new Date(monday.getTime() + i * 7 * 864e5);
      const long = wd.toLocaleDateString("en-US", { month: "short", day: "numeric" });
      const label = compact ? `${wd.getMonth() + 1}/${wd.getDate()}` : long;
      if (n > 0) {
        parts.push(`<rect class="bar" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${h.toFixed(1)}" rx="4"><title>Week of ${long}: ${n} date${n === 1 ? "" : "s"}</title></rect>`);
      }
      parts.push(`<text class="vlabel" x="${(x + barW / 2).toFixed(1)}" y="${(y - 5).toFixed(1)}" text-anchor="middle">${n}</text>`);
      const tickY = compact ? (i % 2 ? H - 4 : H - 16) : H - 7;
      parts.push(`<text class="tick" x="${(x + barW / 2).toFixed(1)}" y="${tickY}" text-anchor="middle">${label}</text>`);
    });
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.innerHTML = parts.join("");
  }

  // ---- health ------------------------------------------------------------
  $("health").innerHTML = DATA.commissions.map((c) => {
    // Three states, not two: reporting / excluded-by-your-settings / broken.
    // A commission filtered to zero is working - showing it the same red as
    // a 403 would make the panel untrustworthy.
    const color = c.ok ? "var(--good)"
      : c.filtered_only ? "var(--muted)"
      : (c.tier === "core" ? "var(--critical)" : "var(--warning)");
    const mark = c.ok ? "✓" : c.filtered_only ? "–" : (c.tier === "core" ? "✕" : "!");
    const tip = c.ok ? `${c.strategy_used} · ${c.source_url}` : c.error;
    return `<div class="hcell" title="${esc(tip)}">
      <span style="color:${color};font-weight:700">${mark}</span>
      <b>${esc(c.commission)}</b><span class="n">${c.event_count}</span></div>`;
  }).join("");

  // ---- review mode --------------------------------------------------------
  // Ticking a box saves nothing on its own - this page is a static file with
  // no backend, and the .ics feeds your calendar subscribes to are built
  // server-side. The tick produces YAML you paste into data/exclusions.yaml
  // and commit; the site rebuilds a few minutes later and the event leaves
  // both the dashboard AND the feeds. Browser-side hiding could never reach
  // Outlook, which is the whole point of the exercise.
  // In-progress ticks only - never a source of truth. The published data is
  // always what data/exclusions.yaml says. This exists so a stray reload does
  // not throw away an afternoon of review.
  const PICK_KEY = "pscal.review.picks";
  const loadPicks = () => {
    try { return new Set(JSON.parse(localStorage.getItem(PICK_KEY) || "[]")); }
    catch (e) { return new Set(); }
  };
  const savePicks = () => {
    try { localStorage.setItem(PICK_KEY, JSON.stringify([...PICKED])); }
    catch (e) { /* private mode - ticks simply do not survive reload */ }
  };
  const PICKED = loadPicks();
  const BY_UID = new Map(DATA.events.map((e) => [e.uid, e]));

  function syncDropBtn() {
    const n = PICKED.size;
    $("drop").textContent = `Copy exclusions (${n})`;
    $("drop").hidden = !$("review").getAttribute("aria-pressed") ||
                       $("review").getAttribute("aria-pressed") === "false";
  }

  $("review").onclick = () => {
    const on = $("review").getAttribute("aria-pressed") !== "true";
    $("review").setAttribute("aria-pressed", on ? "true" : "false");
    document.querySelectorAll(".pick").forEach((el) => { el.hidden = !on; });
    $("drop").hidden = !on;
    $("reviewhelp").hidden = !on;
    syncDropBtn();
  };

  document.addEventListener("change", (ev) => {
    const box = ev.target.closest("input.x");
    if (!box) return;
    if (box.checked) PICKED.add(box.dataset.uid);
    else PICKED.delete(box.dataset.uid);
    box.closest("tr").classList.toggle("picked", box.checked);
    savePicks();
    syncDropBtn();
  });

  // Quote anything YAML could misread. A colon is the one that bites:
  // "Hearing: T-38004" unquoted becomes two keys and the file will not parse,
  // and Louisiana alone has dozens of those. When in doubt, quote - a quoted
  // scalar is always valid, an unquoted one is a gamble.
  const yamlStr = (v) => {
    const t = String(v == null ? "" : v);
    return /^[A-Za-z0-9][\w .,'()\/&+-]*$/.test(t) ? t : JSON.stringify(t);
  };

  $("drop").onclick = async () => {
    const picks = [...PICKED].map((u) => BY_UID.get(u)).filter(Boolean);
    if (!picks.length) return;
    picks.sort((a, b) => (a.commission + a.start).localeCompare(b.commission + b.start));
    // Emit ONLY the list items, never a fresh "events:" header. Pasting a
    // second events: block is silently destructive - YAML keeps the last key
    // and the earlier batch vanishes with no error, so a second review would
    // quietly undo the first.
    const lines = ["  # --- added " + new Date().toISOString().slice(0, 10) +
                   " - paste directly under the existing `events:` line ---"];
    for (const e of picks) {
      const ymd = new Intl.DateTimeFormat("en-CA", {
        timeZone: e.tz || LOCAL, year: "numeric", month: "2-digit", day: "2-digit",
      }).format(new Date(e.start));
      lines.push(`  - commission: ${e.commission}`);
      lines.push(`    date: ${ymd}`);
      lines.push(`    title: ${yamlStr(e.title)}`);
    }
    const text = lines.join("\n");
    try {
      await navigator.clipboard.writeText(text);
      $("drop").textContent = `Copied ${picks.length} \u2713`;
    } catch (err) {
      window.prompt("Copy this into data/exclusions.yaml", text);
    }
    setTimeout(() => {
      if (window.confirm(
            `${picks.length} exclusion(s) copied.\n\n` +
            `In data/exclusions.yaml on GitHub, paste them directly UNDER the ` +
            `line that says "events:" - do not replace anything, or the ` +
            `previous batch is lost.\n\nClear the ticks here?`)) {
        PICKED.clear();
        savePicks();
        render();
      }
      syncDropBtn();
    }, 400);
  };

  // ---- CSV ---------------------------------------------------------------
  $("csv").onclick = () => {
    const rows = sorted(filtered());
    const head = ["Date","Time","Commission","CommissionName","State","Title",
                  "EventType","Dockets","Location","URL"];
    const q = (v) => `"${String(v == null ? "" : v).replace(/"/g, '""')}"`;
    const body = rows.map((e) => {
      const d = new Date(e.start);
      const tz = e.tz || LOCAL;
      // en-CA gives ISO-ordered Y-M-D; toISOString would report the UTC date,
      // which is a day late for evening hearings out west.
      const ymd = new Intl.DateTimeFormat("en-CA",
        { year:"numeric", month:"2-digit", day:"2-digit", timeZone: tz }).format(d);
      return [ymd, e.all_day ? "" : tfmt(tz).format(d),
              e.commission, e.commission_name, e.state, e.title, e.event_type_label,
              e.dockets.join(" "), e.location, e.url].map(q).join(",");
    });
    const blob = new Blob([head.join(",") + "\n" + body.join("\n")],
                          { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `regulatory-calendar-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  // ---- wiring ------------------------------------------------------------
  let t;
  $("q").oninput = (e) => { clearTimeout(t); t = setTimeout(() => { state.q = e.target.value; render(); }, 140); };
  $("f-state").onchange = (e) => { state.comm = e.target.value; render(); };
  $("f-type").onchange = (e) => { state.type = e.target.value; render(); };
  $("f-range").onchange = (e) => { state.range = e.target.value; render(); };

  $("reset").onclick = () => {
    Object.assign(state, { q:"", comm:"", type:"", range:"90" });
    $("q").value = ""; $("f-state").value = "";
    $("f-type").value = ""; $("f-range").value = "90";
    render();
  };
  document.querySelectorAll("th[data-sort]").forEach((th) => {
    th.onclick = () => {
      const k = th.dataset.sort;
      state.dir = state.sort === k ? -state.dir : 1;
      state.sort = k;
      render();
    };
  });
  window.addEventListener("resize", () => drawChart(lastRows));

  $("genstamp").textContent = "refreshed " + new Date(DATA.generated_at)
    .toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" })
    // Hidden events are stated, never silent - an empty week must not be
    // mistaken for "nothing scheduled" when it means "we hid it".
    + (DATA.hidden_by_review
        ? ` \u00b7 ${DATA.hidden_by_review} hidden by review`
        : "")
    + (DATA.stale_exclusions && DATA.stale_exclusions.length
        ? ` \u00b7 \u26a0 ${DATA.stale_exclusions.length} exclusion(s) no longer match`
        : "");

  render();
})();
</script>
</body>
</html>
"""


def write_site(payload: dict, outdir: Path, now: datetime) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    # </script> inside JSON would close the tag early.
    blob = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.replace("__PAYLOAD__", blob)
    path = outdir / "index.html"
    path.write_text(html, encoding="utf-8")
    (outdir / ".nojekyll").write_text("")
    return path
