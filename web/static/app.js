/* Alpha Engine dashboard frontend.
   Read-only: fetches JSON from /api/dashboard and /api/asset/<SYMBOL> and
   renders it. No framework, no build step — plain DOM + inline SVG charts. */

"use strict";

// ---- helpers -------------------------------------------------------------

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);

const fmtPct = (v) => (v == null ? "-" : (v * 100).toFixed(1) + "%");
const fmtNum = (v) => (v == null ? "-" : Number(v).toFixed(2));
const fmtDate = (v) =>
  v
    ? new Date(v).toLocaleDateString("en-US", {
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
      })
    : "-";

const pill = (text, cls) => `<span class="pill ${cls}">${esc(text)}</span>`;
const dirClass = (d) => (d === "bullish" ? "bull" : d === "bearish" ? "bear" : "neutral");

// Fixed categorical assignment: the color follows the market, never its rank,
// so a filter or a new market never repaints the survivors.
const MARKET_COLORS = {
  crypto: "#3987e5",
  us_equity: "#199e70",
  in_equity: "#c98500",
  in_fno: "#9085e9",
};
const marketColor = (m) => MARKET_COLORS[m] || "#64748b";

// ---- tooltip -------------------------------------------------------------

const tooltip = document.getElementById("tooltip");

function showTooltip(html, x, y) {
  tooltip.innerHTML = html;
  tooltip.hidden = false;
  const pad = 12;
  const rect = tooltip.getBoundingClientRect();
  let left = x + pad;
  if (left + rect.width > window.innerWidth - pad) left = x - rect.width - pad;
  tooltip.style.left = left + "px";
  tooltip.style.top = Math.max(pad, y - rect.height - pad) + "px";
}

function hideTooltip() {
  tooltip.hidden = true;
}

// ---- charts ----------------------------------------------------------------

function renderMarkets(byMarket) {
  const el = document.getElementById("markets-chart");
  const entries = Object.entries(byMarket || {});
  if (entries.length === 0) {
    el.innerHTML = '<div class="chart-empty">No signals recorded yet</div>';
    return;
  }
  const max = Math.max(...entries.map(([, c]) => c));
  el.innerHTML = entries
    .map(([market, count]) => {
      const w = ((count / max) * 100).toFixed(1);
      return `<div class="bar-row">
        <div class="bar-name">${esc(market)}</div>
        <div class="bar-track"><div class="bar-fill" style="width:${w}%;background:${marketColor(market)};"></div></div>
        <div class="bar-value">${count}</div>
      </div>`;
    })
    .join("");
}

function renderCalibration(cal) {
  const el = document.getElementById("calibration-chart");
  const bins = (cal || []).filter((b) => b.count > 0);
  if (bins.length === 0) {
    el.innerHTML = '<div class="chart-empty">Awaiting resolved signals</div>';
    return;
  }

  const W = 400, H = 190, pad = { t: 26, r: 12, b: 30, l: 38 };
  const plotW = W - pad.l - pad.r;
  const plotH = H - pad.t - pad.b;
  const cw = plotW / bins.length;

  let svg = `<svg viewBox="0 0 ${W} ${H}" style="width:100%;" role="img" aria-label="Calibration: hit rate per confidence bucket">`;
  for (let i = 0; i <= 4; i++) {
    const y = pad.t + plotH * (1 - i / 4);
    svg += `<line x1="${pad.l}" y1="${y}" x2="${W - pad.r}" y2="${y}" stroke="rgba(51,65,85,0.3)" stroke-dasharray="3,3"/>`;
    svg += `<text x="${pad.l - 6}" y="${y + 3}" text-anchor="end" fill="#64748b" font-size="9" font-family="monospace">${i * 25}%</text>`;
  }

  bins.forEach((b, i) => {
    const x = pad.l + i * cw + cw * 0.2;
    const bw = Math.min(cw * 0.6, 40);
    const hr = b.hit_rate ?? 0;
    const bh = Math.max(hr * plotH, 1);
    const by = H - pad.b - bh;
    const color = hr >= 0.5 ? "#34d399" : "#fb7185";
    const r = Math.min(4, bw / 2, bh);
    // Bar with rounded data-end only; the baseline end stays square.
    svg += `<path d="M${x},${H - pad.b} v${-(bh - r)} q0,${-r} ${r},${-r} h${bw - 2 * r} q${r},0 ${r},${r} v${bh - r} z"
      fill="${color}" opacity="0.9" class="cal-bar" data-lo="${b.lo}" data-hi="${b.hi}" data-count="${b.count}" data-hits="${b.hits}" data-hr="${b.hit_rate ?? ""}"/>`;
    svg += `<text x="${x + bw / 2}" y="${H - pad.b + 14}" text-anchor="middle" fill="#64748b" font-size="8" font-family="monospace">${(b.lo * 100).toFixed(0)}–${(b.hi * 100).toFixed(0)}</text>`;
    if (b.hit_rate != null) {
      svg += `<text x="${x + bw / 2}" y="${by - 5}" text-anchor="middle" fill="#f1f5f9" font-size="9" font-family="monospace">${(hr * 100).toFixed(0)}%</text>`;
    }
  });
  // Diagonal = perfect calibration (stated confidence equals realized hit rate).
  svg += `<line x1="${pad.l}" y1="${H - pad.b}" x2="${W - pad.r}" y2="${pad.t}" stroke="rgba(96,165,250,0.3)" stroke-dasharray="4,4"/>`;
  svg += `<text x="${W - pad.r}" y="${pad.t - 6}" text-anchor="end" fill="#64748b" font-size="8" font-family="monospace">perfect calibration</text>`;
  svg += `</svg>`;
  el.innerHTML = svg;

  el.querySelectorAll(".cal-bar").forEach((bar) => {
    bar.addEventListener("mousemove", (e) => {
      const d = bar.dataset;
      const hr = d.hr === "" ? "-" : (Number(d.hr) * 100).toFixed(0) + "%";
      showTooltip(
        `confidence ${(d.lo * 100).toFixed(0)}–${(d.hi * 100).toFixed(0)}%<br/>` +
          `${d.hits}/${d.count} hits &rarr; ${hr}`,
        e.clientX, e.clientY
      );
    });
    bar.addEventListener("mouseleave", hideTooltip);
  });
}

function renderOutcomes(o) {
  const el = document.getElementById("outcome-bars");
  if (!o || o.total === 0) {
    el.innerHTML = '<div class="chart-empty">No recorded outcomes yet</div>';
    return;
  }
  const segs = [
    { label: "Hits", count: o.hits || 0, color: "#34d399" },
    { label: "Misses", count: (o.resolved || 0) - (o.hits || 0), color: "#fb7185" },
    { label: "Pending", count: o.pending || 0, color: "#fbbf24" },
    { label: "N/A", count: o.not_applicable || 0, color: "#64748b" },
  ].filter((s) => s.count > 0);
  const total = o.total || 1;

  let html = '<div style="display:flex;gap:16px;align-items:center;margin-bottom:12px;">';
  html += `<div style="font-size:36px;font-weight:700;font-family:var(--mono);letter-spacing:-0.04em;">${fmtPct(o.hit_rate)}</div>`;
  html += `<div style="font-size:12px;color:#64748b;">hit rate<br/>${o.resolved} resolved of ${o.total}</div>`;
  html += "</div>";
  html += '<div style="display:flex;flex-direction:column;gap:10px;">';
  segs.forEach((s) => {
    const pct = ((s.count / total) * 100).toFixed(1);
    html += `<div>
      <div style="display:flex;justify-content:space-between;margin-bottom:3px;">
        <span style="color:${s.color};font-size:12px;font-weight:500;">${s.label}</span>
        <span style="color:#64748b;font-size:12px;font-family:var(--mono);">${s.count} (${pct}%)</span>
      </div>
      <div class="bar-track"><div class="bar-fill" style="width:${pct}%;background:${s.color};"></div></div>
    </div>`;
  });
  html += "</div>";
  el.innerHTML = html;
}

// ---- portfolio view ---------------------------------------------------------

function corrColor(v) {
  // Diverging encoding: blue for positive co-movement, red for negative,
  // neutral surface near zero. Values are always printed in the cell, so
  // color never carries the number alone.
  if (v == null) return "transparent";
  const alpha = Math.min(Math.abs(v), 1) * 0.55;
  return v >= 0 ? `rgba(57,135,229,${alpha})` : `rgba(230,103,103,${alpha})`;
}

function renderPortfolio(p) {
  const el = document.getElementById("portfolio");
  if (!p || p.signal_count === 0) {
    el.innerHTML = '<div class="chart-empty">No signals recorded yet</div>';
    return;
  }

  const dirCls = dirClass(p.direction);
  let html = '<div class="portfolio-grid">';

  // Left column: positioning numbers
  html += '<div>';
  html += `<div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">
    ${pill(p.direction, dirCls)}
    <span style="font-family:var(--mono);font-size:22px;font-weight:700;">${(p.net_bias * 100).toFixed(0)}%</span>
    <span style="color:var(--muted);font-size:12px;">net bias (confidence-weighted)</span>
  </div>`;
  if (p.diversification_score != null) {
    html += `<div style="margin-bottom:10px;">
      <span style="font-family:var(--mono);font-size:16px;font-weight:600;">${(p.diversification_score * 100).toFixed(0)}%</span>
      <span style="color:var(--muted);font-size:12px;"> diversification (100% = uncorrelated wiggles)</span>
    </div>`;
  }
  const weights = Object.entries(p.conviction_weights || {});
  if (weights.length) {
    html += '<div class="label" style="margin:10px 0 6px;">Conviction share</div>';
    weights.sort((a, b) => b[1] - a[1]).forEach(([asset, w]) => {
      html += `<div class="bar-row">
        <div class="bar-name">${esc(asset)}</div>
        <div class="bar-track"><div class="bar-fill" style="width:${(w * 100).toFixed(1)}%;background:#3987e5;"></div></div>
        <div class="bar-value">${(w * 100).toFixed(0)}%</div>
      </div>`;
    });
  }
  (p.concentration_flags || []).forEach((f) => {
    html += `<div class="alert">&#9888; ${esc(f)}</div>`;
  });
  html += "</div>";

  // Right column: correlation matrix
  const m = p.correlations;
  if (m && m.assets && m.assets.length >= 2) {
    html += '<div><div class="label" style="margin-bottom:6px;">Return correlation (' + m.window + "d)</div>";
    html += '<div class="table-wrap"><table class="corr-table"><thead><tr><th></th>';
    m.assets.forEach((a) => (html += `<th>${esc(a)}</th>`));
    html += "</tr></thead><tbody>";
    m.assets.forEach((a, i) => {
      html += `<tr><th>${esc(a)}</th>`;
      m.matrix[i].forEach((v) => {
        const txt = v == null ? "–" : v.toFixed(2);
        html += `<td style="background:${corrColor(v)};">${txt}</td>`;
      });
      html += "</tr>";
    });
    html += "</tbody></table></div></div>";
  }

  html += "</div>";
  el.innerHTML = html;
}

// ---- signal feed -----------------------------------------------------------

function renderSignals(signals) {
  const tbody = document.querySelector("#signals-table tbody");
  tbody.innerHTML = (signals || [])
    .map((r) => {
      const inv = r.invalidation_level != null ? fmtNum(r.invalidation_level) : "-";
      const sources = (r.sources || [])
        .map((s) => `<span class="source-tag">${esc(s.name)} ${esc(s.direction)}</span>`)
        .join("");
      return `<tr data-asset="${esc(r.asset)}" title="Show ${esc(r.asset)} history">
        <td class="asset">${esc(r.asset)}</td>
        <td>${pill(r.market, "market")}</td>
        <td>${pill(r.direction, dirClass(r.direction))}</td>
        <td class="mono">${fmtPct(r.confidence)}</td>
        <td class="mono muted">${inv}</td>
        <td><div class="source-list">${sources}</div></td>
        <td class="muted" style="white-space:nowrap;">${fmtDate(r.recorded_at)}</td>
        <td><pre class="thesis">${esc(r.thesis)}</pre></td>
      </tr>`;
    })
    .join("");

  tbody.querySelectorAll("tr[data-asset]").forEach((row) => {
    row.addEventListener("click", () => loadAssetHistory(row.dataset.asset));
  });
}

// ---- per-asset detail --------------------------------------------------------

function outcomeCell(outcome) {
  if (!outcome) return pill("no data", "neutral");
  if (outcome.status === "pending") return pill("pending", "neutral");
  if (outcome.status === "not_applicable") return '<span class="muted">n/a</span>';
  return outcome.hit
    ? pill("✓ hit", "bull")
    : pill("✗ miss", "bear");
}

async function loadAssetHistory(asset) {
  const panel = document.getElementById("asset-detail");
  const tbody = document.querySelector("#history-table tbody");
  try {
    const resp = await fetch(`/api/asset/${encodeURIComponent(asset)}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    document.getElementById("detail-asset").textContent =
      `${data.asset} · ${data.count} recorded signal${data.count === 1 ? "" : "s"}`;
    tbody.innerHTML = (data.history || [])
      .map((r) => {
        const ret = r.outcome?.realized_return;
        const retCls = ret == null ? "muted" : ret >= 0 ? "bull" : "bear";
        return `<tr>
          <td class="muted" style="white-space:nowrap;">${fmtDate(r.recorded_at)}</td>
          <td>${pill(r.direction, dirClass(r.direction))}</td>
          <td class="mono">${fmtPct(r.confidence)}</td>
          <td class="mono muted">${r.entry_price != null ? fmtNum(r.entry_price) : "-"}</td>
          <td class="mono muted">${r.invalidation_level != null ? fmtNum(r.invalidation_level) : "-"}</td>
          <td>${outcomeCell(r.outcome)}</td>
          <td class="mono" style="color:var(--${retCls === "muted" ? "muted" : retCls});">${ret == null ? "-" : (ret * 100).toFixed(2) + "%"}</td>
          <td><pre class="thesis">${esc(r.thesis)}</pre></td>
        </tr>`;
      })
      .join("");
    panel.hidden = false;
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="8" class="muted">Failed to load history: ${esc(err.message)}</td></tr>`;
    panel.hidden = false;
  }
}

document.getElementById("detail-close").addEventListener("click", () => {
  document.getElementById("asset-detail").hidden = true;
});

// ---- main render loop ----------------------------------------------------------

function render(payload) {
  document.getElementById("updated").textContent = new Date().toLocaleString();
  document.getElementById("total-records").textContent = payload.total_records ?? 0;
  document.getElementById("latest-count").textContent = payload.latest_count ?? 0;

  const o = payload.outcomes || {};
  const hrEl = document.getElementById("hit-rate");
  hrEl.textContent = fmtPct(o.hit_rate);
  hrEl.className = "metric" + (o.hit_rate != null ? " " + (o.hit_rate >= 0.5 ? "bull" : "bear") : "");
  document.getElementById("hit-rate-sub").textContent =
    o.resolved ? `${o.hits}/${o.resolved} resolved` : "";

  const arEl = document.getElementById("avg-return");
  arEl.textContent = o.avg_realized_return == null ? "-" : (o.avg_realized_return * 100).toFixed(2) + "%";
  arEl.className = "metric" + (o.avg_realized_return != null ? " " + (o.avg_realized_return >= 0 ? "bull" : "bear") : "");
  document.getElementById("avg-return-sub").textContent =
    o.avg_realized_return != null ? "per resolved signal" : "";

  renderMarkets(payload.assets_by_market);
  renderCalibration(o.calibration);
  renderOutcomes(o);
  renderPortfolio(payload.portfolio);
  renderSignals(payload.latest_signals);
}

async function refresh() {
  try {
    const resp = await fetch("/api/dashboard");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    render(await resp.json());
  } catch (err) {
    document.getElementById("updated").textContent = `load failed: ${err.message}`;
  }
}

refresh();
setInterval(refresh, 60_000);
