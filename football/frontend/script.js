/*
 * Sovereign Risk Lab — phase two (football) dashboard rendering.
 *
 * This file ONLY renders football/data/processed/*.json produced by
 * football/scripts/process.py. No business logic, no computed metrics live
 * here — same architecture rule as phase one (raw -> Python -> JSON ->
 * presentation). If a fetch fails (pipeline hasn't been run yet), the page
 * shows an explicit "no data" state rather than falling back to invented
 * numbers.
 *
 * Generic chart/table helpers below are intentionally duplicated from
 * phase one's script.js rather than shared via a module — each dashboard
 * stays a single self-contained <script src> with no build step. The shared
 * design system (colors, layout, chart chrome) lives once in
 * frontend/style.css, which this page also loads — see index.html.
 */

const DATA_DIR = "../data/processed/";

const FEFI_COUNTRIES = {
  NGA: { name: "Nigeria", color: "var(--series-nga)" },
  GHA: { name: "Ghana", color: "var(--series-gha)" },
  SEN: { name: "Senegal", color: "var(--series-sen)" },
  CIV: { name: "Cote d'Ivoire", color: "var(--series-civ)" },
  CMR: { name: "Cameroon", color: "var(--series-cmr)" },
};

const COMPARATOR_COUNTRIES = {
  ZWE: { name: "Zimbabwe", color: "var(--series-zwe)" },
  KEN: { name: "Kenya", color: "var(--series-ken)" },
  MWI: { name: "Malawi", color: "var(--series-mwi)" },
};
const COMPARATOR_ORDER = ["ZWE", "KEN", "MWI"];

// ---------------------------------------------------------------------
// Data loading
// ---------------------------------------------------------------------

async function loadJSON(filename) {
  const resp = await fetch(DATA_DIR + filename, { cache: "no-store" });
  if (!resp.ok) throw new Error(`${filename}: HTTP ${resp.status}`);
  return resp.json();
}

async function loadAllData() {
  const files = [
    "meta.json",
    "fefi.json",
    "nigeria_case_study.json",
    "case_study_transfers.json",
    "value_capture_context.json",
    "comparator_context.json",
  ];
  const results = {};
  const missing = [];
  for (const f of files) {
    try {
      results[f.replace(".json", "")] = await loadJSON(f);
    } catch (err) {
      missing.push(f);
    }
  }
  return { data: results, missing };
}

// ---------------------------------------------------------------------
// Small DOM / formatting helpers (see phase one's script.js — duplicated
// deliberately, not shared; both files stay self-contained)
// ---------------------------------------------------------------------

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else node.setAttribute(k, v);
  }
  for (const child of [].concat(children)) {
    if (child == null) continue;
    node.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function svgEl(tag, attrs = {}) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

function fmtNum(v, digits = 1) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return Number(v).toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

function fmtPct(v, digits = 1) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${fmtNum(v, digits)}%`;
}

function fmtUSD(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  // World Bank figures arrive as raw USD; abbreviate for readability.
  const abs = Math.abs(v);
  if (abs >= 1e9) return `$${fmtNum(v / 1e9, 1)}B`;
  if (abs >= 1e6) return `$${fmtNum(v / 1e6, 1)}M`;
  return `$${fmtNum(v, 0)}`;
}

function fmtEUR(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const abs = Math.abs(v);
  if (abs >= 1e6) return `€${fmtNum(v / 1e6, 2)}M`;
  if (abs >= 1e3) return `€${fmtNum(v / 1e3, 0)}k`;
  return `€${fmtNum(v, 0)}`;
}

// ---------------------------------------------------------------------
// Header / banner
// ---------------------------------------------------------------------

function renderHeaderMeta(meta) {
  const container = document.getElementById("header-meta");
  container.innerHTML = "";
  const badgeRow = el("div", { class: "badge-row" });
  if (meta) badgeRow.appendChild(el("span", { class: "badge badge-live" }, "Live pipeline output"));
  container.appendChild(badgeRow);
  if (meta) {
    const finished = new Date(meta.run_finished_at_utc);
    container.appendChild(el("div", {}, `Last refreshed ${finished.toLocaleString()} UTC`));
  }
}

function renderDataStateBanner(missing) {
  const banner = document.getElementById("data-state-banner");
  if (missing.length === 0) {
    banner.hidden = true;
    return;
  }
  banner.hidden = false;
  banner.innerHTML = "";
  banner.appendChild(el("strong", {}, "No processed data found. "));
  banner.appendChild(document.createTextNode(
    `This page reads from football/data/processed/, generated by running ` +
    `football/scripts/process.py — it is not checked into the repo, since it ` +
    `depends on a live World Bank pull at the time you run it. Missing files: ${missing.join(", ")}.`
  ));
}

// ---------------------------------------------------------------------
// Generic SVG chart engine (bar + line), tooltip, table builder
// ---------------------------------------------------------------------

function makeChartSVG(container, { width = 640, height = 320, margin = { top: 16, right: 20, bottom: 32, left: 48 } }) {
  container.innerHTML = "";
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, role: "img" });
  container.appendChild(svg);
  return { svg, width, height, margin };
}

function scaleLinear(domain, range) {
  const [d0, d1] = domain, [r0, r1] = range;
  const span = d1 - d0 || 1;
  return (v) => r0 + ((v - d0) / span) * (r1 - r0);
}

function niceLinearTicks(domain, count = 4) {
  const [lo, hi] = domain;
  const span = hi - lo || 1;
  const step = span / count;
  const ticks = [];
  for (let i = 0; i <= count; i++) ticks.push(lo + step * i);
  return ticks;
}

let tooltipEl = null;
function getTooltip() {
  if (!tooltipEl) {
    tooltipEl = el("div", { class: "viz-tooltip" });
    tooltipEl.style.display = "none";
    document.body.appendChild(tooltipEl);
  }
  return tooltipEl;
}

function showTooltip(x, y, titleText, rows) {
  const tip = getTooltip();
  tip.innerHTML = "";
  tip.appendChild(el("div", { class: "tt-title" }, titleText));
  for (const row of rows) {
    const swatch = el("span", { class: "tt-swatch" });
    swatch.style.background = row.color;
    const keyWrap = el("span", { class: "tt-key" }, [swatch, document.createTextNode(row.label)]);
    tip.appendChild(el("div", { class: "tt-row" }, [keyWrap, el("span", { class: "tt-value" }, row.value)]));
  }
  tip.style.display = "block";
  tip.style.left = `${x + 14}px`;
  tip.style.top = `${y + 14}px`;
}

function hideTooltip() {
  if (tooltipEl) tooltipEl.style.display = "none";
}

function buildDataTable(columns, rows) {
  const table = el("table", { class: "data-table" });
  const thead = el("thead", {}, el("tr", {}, columns.map((c) => el("th", { class: c.num ? "num" : "" }, c.label))));
  const tbody = el("tbody", {}, rows.map((r) => el("tr", {}, columns.map((c) => el("td", { class: c.num ? "num" : "" }, c.render(r))))));
  table.appendChild(thead);
  table.appendChild(tbody);
  return table;
}

function wireTableToggle(key) {
  const btn = document.querySelector(`[data-toggle-table="${key}"]`);
  if (!btn) return;
  btn.addEventListener("click", () => {
    const tableEl = document.getElementById(`${key}-table`);
    const chartEl = document.getElementById(`${key}-chart`);
    const showing = !tableEl.hidden;
    tableEl.hidden = showing;
    if (chartEl) chartEl.hidden = !showing;
    btn.setAttribute("aria-expanded", String(!showing));
    btn.textContent = showing ? "View as table" : "View as chart";
  });
}

function renderBarChart(containerId, { bars, valueFormatter = (v) => fmtNum(v), domain = null, unitLabel = "" }) {
  const container = document.getElementById(containerId);
  const rowH = 44;
  const margin = { top: 12, right: 70, bottom: 12, left: 120 };
  const width = 640;
  const height = margin.top + margin.bottom + bars.length * rowH;
  const { svg } = makeChartSVG(container, { width, height, margin });
  const plotW = width - margin.left - margin.right;

  const values = bars.map((b) => b.value).filter((v) => v !== null && !Number.isNaN(v));
  const dom = domain || [0, Math.max(1, ...values)];
  const xScale = scaleLinear(dom, [0, plotW]);

  bars.forEach((b, i) => {
    const y = margin.top + i * rowH;
    const barH = 20;
    const barY = y + (rowH - barH) / 2;

    const label = svgEl("text", { class: "axis-label", x: margin.left - 10, y: barY + barH / 2 + 4, "text-anchor": "end", "font-weight": 600 });
    label.textContent = b.name;
    svg.appendChild(label);

    svg.appendChild(svgEl("line", { class: "gridline", x1: margin.left, x2: margin.left + plotW, y1: barY + barH / 2, y2: barY + barH / 2 }));

    if (b.value === null || Number.isNaN(b.value)) {
      const naLabel = svgEl("text", { class: "axis-label", x: margin.left + 8, y: barY + barH / 2 + 4 });
      naLabel.textContent = "No data";
      svg.appendChild(naLabel);
      return;
    }

    const barW = Math.max(2, xScale(b.value) - xScale(dom[0]));
    const barX = margin.left + xScale(dom[0]);
    const rect = svgEl("rect", { x: barX, y: barY, width: barW, height: barH, rx: 4, fill: b.color });
    rect.style.cursor = "pointer";
    rect.addEventListener("pointermove", (evt) => {
      showTooltip(evt.pageX, evt.pageY, b.name, [{ color: b.color, label: unitLabel || "Value", value: valueFormatter(b.value) }]);
    });
    rect.addEventListener("pointerleave", hideTooltip);
    svg.appendChild(rect);

    const valueLabel = svgEl("text", { class: "axis-label", x: barX + barW + 8, y: barY + barH / 2 + 4, "font-weight": 600 });
    valueLabel.setAttribute("fill", "var(--text-primary)");
    valueLabel.textContent = valueFormatter(b.value);
    svg.appendChild(valueLabel);
  });
}

function renderLineChart(containerId, { points, color, name, valueFormatter = (v) => fmtNum(v) }) {
  const container = document.getElementById(containerId);
  const { svg, width, height, margin } = makeChartSVG(container, {
    width: 640, height: 260, margin: { top: 16, right: 20, bottom: 32, left: 48 },
  });
  const plotW = width - margin.left - margin.right;
  const plotH = height - margin.top - margin.bottom;

  const pts = points.filter((p) => p.value !== null && !Number.isNaN(p.value)).sort((a, b) => a.year - b.year);
  if (pts.length === 0) {
    container.appendChild(el("p", { class: "section-note" }, "No data available."));
    return;
  }

  const years = pts.map((p) => p.year);
  const values = pts.map((p) => p.value);
  const xScale = scaleLinear([years[0], years[years.length - 1]], [margin.left, margin.left + plotW]);
  const yDomain = [Math.min(0, ...values), Math.max(...values) * 1.15];
  const yScale = scaleLinear(yDomain, [margin.top + plotH, margin.top]);
  const yTicks = niceLinearTicks(yDomain);

  const plot = svgEl("g", {});
  svg.appendChild(plot);

  for (const t of yTicks) {
    const y = yScale(t);
    plot.appendChild(svgEl("line", { class: "gridline", x1: margin.left, x2: margin.left + plotW, y1: y, y2: y }));
    const label = svgEl("text", { class: "axis-label", x: margin.left - 8, y: y + 4, "text-anchor": "end" });
    label.textContent = valueFormatter(t);
    plot.appendChild(label);
  }
  years.forEach((yr, i) => {
    const x = xScale(yr);
    const label = svgEl("text", { class: "axis-label", x, y: margin.top + plotH + 18, "text-anchor": "middle" });
    label.textContent = String(yr);
    plot.appendChild(label);
  });
  plot.appendChild(svgEl("line", { class: "axis-line", x1: margin.left, x2: margin.left + plotW, y1: margin.top + plotH, y2: margin.top + plotH }));

  const d = pts.map((p, i) => `${i === 0 ? "M" : "L"} ${xScale(p.year)} ${yScale(p.value)}`).join(" ");
  plot.appendChild(svgEl("path", { d, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));

  pts.forEach((p) => {
    const cx = xScale(p.year), cy = yScale(p.value);
    plot.appendChild(svgEl("circle", { cx, cy, r: 4, fill: color, stroke: "var(--surface-1)", "stroke-width": 2 }));
    const hit = svgEl("circle", { cx, cy, r: 12, fill: "transparent" });
    hit.style.cursor = "pointer";
    hit.addEventListener("pointermove", (evt) => {
      showTooltip(evt.pageX, evt.pageY, `${p.year}`, [{ color, label: name, value: valueFormatter(p.value) }]);
    });
    hit.addEventListener("pointerleave", hideTooltip);
    plot.appendChild(hit);
  });
}

// ---------------------------------------------------------------------
// FEFI
// ---------------------------------------------------------------------

function renderFEFI(fefi) {
  if (!fefi) return;
  const bars = fefi.ranking_highest_footprint_first.map((iso3) => ({
    name: (FEFI_COUNTRIES[iso3] || {}).name || iso3,
    value: fefi.fefi_composite_0_100[iso3],
    color: (FEFI_COUNTRIES[iso3] || {}).color || "var(--series-nga)",
  }));
  renderBarChart("fefi-chart", { bars, domain: [0, 100], unitLabel: "FEFI (0-100)" });

  const tableContainer = document.getElementById("fefi-table");
  tableContainer.innerHTML = "";
  tableContainer.appendChild(buildDataTable(
    [
      { label: "Country", render: (r) => r.name },
      { label: "Raw expatriates, 2020-2025", num: true, render: (r) => fmtNum(fefi.raw_expatriate_totals_2020_2025[r.iso3], 0) },
      { label: "FEFI (0-100)", num: true, render: (r) => fmtNum(r.value) },
    ],
    fefi.ranking_highest_footprint_first.map((iso3) => ({ iso3, name: (FEFI_COUNTRIES[iso3] || {}).name || iso3, value: fefi.fefi_composite_0_100[iso3] })),
  ));
  wireTableToggle("fefi");
}

// ---------------------------------------------------------------------
// Nigeria case study
// ---------------------------------------------------------------------

function renderNigeriaCaseStudy(nigeria) {
  const noteEl = document.getElementById("nigeria-big5-note");
  noteEl.innerHTML = "";
  if (!nigeria) return;

  renderLineChart("nigeria-trend-chart", {
    points: nigeria.yearly_trend_2020_2025.map((r) => ({ year: r.year, value: r.expatriate_count })),
    color: "var(--series-nga)",
    name: "Nigeria",
    valueFormatter: (v) => fmtNum(v, 0),
  });

  const destBars = nigeria.top_5_destinations_2020_2025.map((d) => ({
    name: d.destination_country,
    value: d.expatriate_count,
    color: d.is_big5_league_country ? "var(--status-good)" : "var(--series-nga)",
  }));
  renderBarChart("nigeria-dest-chart", { bars: destBars, unitLabel: "Players" });

  const pct = nigeria.top_5_destinations_big5_league_share_pct;
  noteEl.appendChild(el("strong", {}, pct === null || pct === undefined ? "Big-5 league share unavailable this run. " : `${fmtPct(pct)} in a "Big 5" league. `));
  noteEl.appendChild(document.createTextNode(nigeria.note));
}

// ---------------------------------------------------------------------
// Case-study transfers (ZWE/KEN/MWI)
// ---------------------------------------------------------------------

function renderCaseStudyTransfers(transfers) {
  const warningEl = document.getElementById("case-study-warning");
  const cardsEl = document.getElementById("case-study-cards");
  warningEl.innerHTML = "";
  cardsEl.innerHTML = "";
  if (!transfers) return;

  warningEl.appendChild(el("strong", {}, "Illustrative, not statistical. "));
  warningEl.appendChild(document.createTextNode(transfers.generalization_warning));

  for (const iso3 of COMPARATOR_ORDER) {
    const info = COMPARATOR_COUNTRIES[iso3];
    const players = transfers.players_by_country[iso3] || [];
    const col = el("div", { class: "case-study-country" }, [
      el("div", { class: "case-study-country-title" }, [
        el("span", { class: "kpi-dot", style: `background:${info.color}` }),
        `${info.name} (${players.length})`,
      ]),
    ]);
    for (const p of players) {
      const feeClass = p.fee_confirmed ? "confirmed" : "unconfirmed";
      const feeText = p.fee_confirmed && p.transfer_fee_eur !== null
        ? `Transfer fee: ${fmtEUR(p.transfer_fee_eur)} (confirmed)`
        : "Fee not publicly disclosed";
      const metaParts = [p.position, p.current_club, p.current_league].filter(Boolean).join(" · ");
      col.appendChild(el("div", { class: "player-card" }, [
        el("div", { class: "player-name" }, p.player_name),
        metaParts ? el("div", { class: "player-meta" }, metaParts) : null,
        p.previous_club ? el("div", { class: "player-meta" }, `Previously: ${p.previous_club}`) : null,
        el("div", { class: `player-fee ${feeClass}` }, feeText),
        p.notes ? el("div", { class: "player-notes" }, p.notes) : null,
        el("div", { class: "player-notes" }, [el("a", { href: p.source_url, target: "_blank", rel: "noopener" }, "source")]),
      ]));
    }
    cardsEl.appendChild(col);
  }
}

// ---------------------------------------------------------------------
// Comparator context (GDP / remittances)
// ---------------------------------------------------------------------

function renderComparatorContext(comparator) {
  const container = document.getElementById("comparator-cards");
  container.innerHTML = "";
  if (!comparator) return;

  for (const iso3 of COMPARATOR_ORDER) {
    const info = COMPARATOR_COUNTRIES[iso3];
    const latest = comparator.latest_available[iso3] || {};
    const card = el("div", { class: "comparator-card", style: `border-left:4px solid ${info.color}` }, [
      el("div", { class: "comp-country" }, [el("span", { class: "kpi-dot", style: `background:${info.color}` }), info.name]),
    ]);
    if (latest.gdp_usd === null && latest.remittances_usd === null) {
      card.appendChild(el("div", { class: "comp-unavailable" }, "World Bank data unavailable this run — not fabricated."));
    } else {
      card.appendChild(el("div", { class: "comp-stat" }, "GDP"));
      card.appendChild(el("div", { class: "comp-value" }, fmtUSD(latest.gdp_usd)));
      card.appendChild(el("div", { class: "comp-stat" }, "Personal remittances received"));
      card.appendChild(el("div", { class: "comp-value" }, fmtUSD(latest.remittances_usd)));
      card.appendChild(el("div", { class: "comp-stat" }, `As of ${latest.year ?? "—"}`));
    }
    container.appendChild(card);
  }
}

// ---------------------------------------------------------------------
// Value capture context (narrative, never scored)
// ---------------------------------------------------------------------

function renderValueCaptureContext(context) {
  const container = document.getElementById("value-capture-list");
  container.innerHTML = "";
  if (!context) return;
  for (const entry of context.entries) {
    container.appendChild(el("div", { class: "value-capture-entry" }, [
      el("div", { class: "vc-metric" }, entry.metric),
      el("div", { class: "vc-value" }, `${fmtNum(entry.value, entry.value < 10 ? 4 : 1)} ${entry.unit} (${entry.scope}, ${entry.year})`),
      entry.caveat ? el("div", { class: "vc-caveat" }, entry.caveat) : null,
      el("div", { class: "vc-source" }, [entry.source + " · ", el("a", { href: entry.source_url, target: "_blank", rel: "noopener" }, "source")]),
    ]));
  }
}

// ---------------------------------------------------------------------
// Methodology
// ---------------------------------------------------------------------

function renderMethodology(fefi, meta) {
  const container = document.getElementById("methodology-body");
  container.innerHTML = "";
  if (!fefi || !meta) return;

  container.appendChild(el("h3", {}, "FEFI scope & weights"));
  container.appendChild(el("p", {}, fefi.scope_note));
  container.appendChild(buildDataTable(
    [{ label: "Component", render: (r) => r[0] }, { label: "Weight", num: true, render: (r) => fmtPct(r[1] * 100) }],
    Object.entries(fefi.weights),
  ));

  if (meta.sanity_check_warnings && meta.sanity_check_warnings.length) {
    container.appendChild(el("h3", {}, "Sanity check log from this run"));
    container.appendChild(el("ul", {}, meta.sanity_check_warnings.map((w) => el("li", {}, w))));
  }

  container.appendChild(el("h3", {}, "Sources"));
  const sourceList = el("ul", {});
  for (const [k, v] of Object.entries(meta.sources || {})) {
    sourceList.appendChild(el("li", {}, `${k}: ${v}`));
  }
  container.appendChild(sourceList);
}

// ---------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------

async function main() {
  const { data, missing } = await loadAllData();
  renderDataStateBanner(missing);
  renderHeaderMeta(data.meta);
  renderFEFI(data.fefi);
  renderNigeriaCaseStudy(data.nigeria_case_study);
  renderCaseStudyTransfers(data.case_study_transfers);
  renderComparatorContext(data.comparator_context);
  renderValueCaptureContext(data.value_capture_context);
  renderMethodology(data.fefi, data.meta);
}

main();
