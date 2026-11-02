/* RedZone Everything — sports graphics: NFL field, MLB diamond, NHL rink, period progress,
   win-probability bar + sparkline, linescore. All inline SVG/HTML, sized by the container. */

import { attr, esc, num, clamp, get, arr, ordinal, raw, pairInks, teamBy } from "./rz-core.js?v=22";
import { logo, icon } from "./rz-ui.js?v=22";

const REG = { nfl: 4, nba: 4, nhl: 3, mlb: 9 };
const PERIOD_MIN = { nfl: 15, nba: 12, nhl: 20 };

/** League-appropriate live situation graphic, or "" when there is nothing to draw. */
export function situation(g, { dark = true } = {}) {
  if (!g || g.phase !== "live") return "";
  const lg = g.league;
  if (lg === "nfl") return nflField(g) || progress(g);
  if (lg === "mlb") return diamond(g);
  if (lg === "nhl") return rink(g) + progress(g);
  if (lg === "nba") return court(g);
  return "";
}

/* ---------- NFL: offense always drives left → right ---------- */
function nflField(g) {
  const s = g.situation || {};
  const ytg = num(s.yards_to_goal, NaN);
  const off = teamBy(g, s.possession);
  if (!off || !Number.isFinite(ytg) || ytg <= 0 || ytg > 99) return "";
  const def = off === g.home ? g.away : g.home;
  const [offInk] = pairInks(off, def, true);
  const W = 320, EZ = 24, YD = (W - 2 * EZ) / 100;
  const los = EZ + (100 - ytg) * YD;
  const down = num(s.down, 0), dist = num(s.distance, 0);
  const goal = !!s.goal_to_go || (dist && dist >= ytg);
  const fd = !goal && dist ? Math.min(W - EZ, los + dist * YD) : null;
  const lines = [];
  for (let y = 5; y < 100; y += 5) {
    const x = EZ + y * YD;
    lines.push(`<line x1="${x.toFixed(1)}" y1="10" x2="${x.toFixed(1)}" y2="62" stroke="rgba(255,255,255,${y % 10 ? 0.1 : y === 50 ? 0.42 : 0.24})" stroke-width="${y % 10 ? 0.8 : 1.2}"/>`);
  }
  const nums = [10, 20, 30, 40, 50, 40, 30, 20, 10].map((n, i) =>
    `<text x="${(EZ + (i + 1) * 10 * YD).toFixed(1)}" y="58" text-anchor="middle" font-size="8.5" font-weight="800" fill="rgba(255,255,255,.34)" font-family="Barlow Condensed,sans-serif">${n}</text>`).join("");
  const spot = ytg === 50 ? "50" : ytg < 50 ? `${esc(def.abbr)} ${ytg}` : `${esc(off.abbr)} ${100 - ytg}`;
  const ddText = down ? `${ordinal(down)} & ${goal ? "Goal" : dist || "—"}` : "";
  const pct = ((los - 7) / W) * 100;
  const tos = timeouts(g);
  return `<div class="sit sit--nfl">
    <div class="sit-head">${ddText ? `<span class="dd ${goal ? "goal" : ""}">${esc(ddText)}</span>` : `<span class="dd">${esc(off.abbr)} ball</span>`}
      <span class="sit-spot"><b>${esc(off.abbr)}</b> ball on <b>${spot}</b></span></div>
    <div style="position:relative">
      <svg class="field" viewBox="0 0 ${W} 72" role="img" aria-label="${attr(`${off.abbr} ball, ${ddText} at ${spot}`)}">
        <defs><linearGradient id="turf-${attr(g.id)}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#16472c"/><stop offset="1" stop-color="#0c2d1c"/></linearGradient>
        <clipPath id="fc-${attr(g.id)}"><rect x="0" y="10" width="${W}" height="52" rx="8"/></clipPath></defs>
        <g clip-path="url(#fc-${attr(g.id)})">
          <rect x="0" y="10" width="${W}" height="52" fill="url(#turf-${attr(g.id)})"/>
          ${[0, 2, 4, 6, 8].map((i) => `<rect x="${(EZ + i * 10 * YD).toFixed(1)}" y="10" width="${(10 * YD).toFixed(1)}" height="52" fill="rgba(255,255,255,.028)"/>`).join("")}
          <rect x="0" y="10" width="${EZ}" height="52" fill="${raw(off)}"/>
          <rect x="${W - EZ}" y="10" width="${EZ}" height="52" fill="${raw(def)}"/>
          <rect x="${(W - EZ - 20 * YD).toFixed(1)}" y="10" width="${(20 * YD).toFixed(1)}" height="52" fill="rgba(255,59,71,.16)"/>
          ${lines.join("")}${nums}
          <text transform="translate(${EZ / 2 + 4} 36) rotate(-90)" text-anchor="middle" font-size="11" font-weight="800" letter-spacing="2" fill="rgba(255,255,255,.92)" font-family="Barlow Condensed,sans-serif">${esc(off.abbr)}</text>
          <text transform="translate(${W - EZ / 2 - 4} 36) rotate(90)" text-anchor="middle" font-size="11" font-weight="800" letter-spacing="2" fill="rgba(255,255,255,.92)" font-family="Barlow Condensed,sans-serif">${esc(def.abbr)}</text>
          <rect x="${(los - 1.4).toFixed(1)}" y="10" width="2.8" height="52" fill="#4da3ff"/>
          ${fd ? `<rect x="${(fd - 1.4).toFixed(1)}" y="10" width="2.8" height="52" fill="#ffd400"/>` : ""}
          ${goal ? `<rect x="${W - EZ - 1.4}" y="10" width="2.8" height="52" fill="#ffd400" opacity=".9"/>` : ""}
        </g>
        <g transform="translate(${(los - 7).toFixed(1)} 36)"><g style="animation:ball-bob 1.6s ease-in-out infinite">
          <ellipse rx="7" ry="4.2" fill="#8a4b1f" stroke="#fff" stroke-width=".8"/><path d="M-3 0h6M-1.5-1.3v2.6M0-1.3v2.6M1.5-1.3v2.6" stroke="#fff" stroke-width=".7"/>
        </g></g>
        <path d="M${(los + 4).toFixed(1)} 30l7 6-7 6z" fill="${offInk}" opacity=".95"/>
      </svg>
      <span class="ball-pin" style="left:${pct.toFixed(2)}%">${logo(off, { size: 22, dark: true })}</span>
    </div>
    ${tos ? `<div class="sit-row">${tos}</div>` : ""}
  </div>`;
}

function timeouts(g) {
  const s = g.situation || {};
  const max = g.league === "nba" ? 7 : 3;
  const one = (t, n) => (n === null || n === undefined ? "" : `<span class="row" style="gap:6px">${esc(t.abbr)} <span class="tos">${Array.from({ length: max }, (_, i) => `<i class="${i < n ? "" : "used"}"></i>`).join("")}</span></span>`);
  const a = one(g.away || {}, s.timeouts_away), h = one(g.home || {}, s.timeouts_home);
  return a || h ? `${a || "<span></span>"}<span>Timeouts</span>${h || "<span></span>"}` : "";
}

/* ---------- MLB: diamond, outs, count ---------- */
function diamond(g) {
  const s = g.situation || {};
  const run = arr(s.runners);
  const on = (i) => !!run[i];
  const outs = clamp(num(s.outs, 0), 0, 3);
  const half = String(s.half || "");
  const up = /^top/i.test(half);
  const base = (x, y, lit) => `<rect x="-8" y="-8" width="16" height="16" rx="2.5" transform="translate(${x} ${y}) rotate(45)" fill="${lit ? "#ffc83d" : "rgba(255,255,255,.08)"}" stroke="${lit ? "#fff3c4" : "rgba(255,255,255,.45)"}" stroke-width="1.5"${lit ? ' style="filter:drop-shadow(0 0 6px rgba(255,200,61,.8))"' : ""}/>`;
  const balls = s.balls ?? null, strikes = s.strikes ?? null;
  const batting = up ? g.away : g.home;
  const desc = `${up ? "Top" : "Bottom"} ${ordinal(g.period)}, ${outs} out, runners ${["first", "second", "third"].filter((_, i) => on(i)).join(" and ") || "none"}`;
  return `<div class="sit sit--mlb"><div class="diamond-wrap">
    <svg class="diamond" viewBox="0 0 96 82" role="img" aria-label="${attr(desc)}">
      <path d="M48 12 80 44 48 74 16 44z" fill="rgba(52,211,153,.07)" stroke="rgba(255,255,255,.16)" stroke-width="1.5"/>
      ${base(48, 12, on(1))}${base(80, 44, on(0))}${base(16, 44, on(2))}
      <path d="M42 70h12v4l-6 5-6-5z" fill="rgba(255,255,255,.85)"/>
    </svg>
    <div class="inning">
      <div class="inn"><svg viewBox="0 0 10 10">${up ? '<path d="M5 1 9.5 9h-9z"/>' : '<path d="M5 9 .5 1h9z"/>'}</svg>${esc(ordinal(g.period))}</div>
      <div class="outs">${[0, 1, 2].map((i) => `<i class="${i < outs ? "on" : ""}"></i>`).join("")}<span>${outs} out${outs === 1 ? "" : "s"}</span></div>
      ${balls !== null && strikes !== null ? `<div class="count">${num(balls)}-${num(strikes)} count</div>` : ""}
      ${batting ? `<div class="row" style="gap:6px">${logo(batting, { size: 18, dark: true })}<span class="count">${esc(batting.abbr)} batting</span></div>` : ""}
    </div></div></div>`;
}

/* ---------- NHL: rink strip with skater strength ---------- */
function rink(g) {
  const s = g.situation || {};
  const pp = teamBy(g, s.power_play);
  const en = teamBy(g, s.empty_net);
  const m = String(s.strength || "").match(/(\d)\s*v\s*(\d)/i);
  let nA = 5, nH = 5;
  if (m && pp) { const big = +m[1], small = +m[2]; if (pp === g.away) { nA = big; nH = small; } else { nH = big; nA = small; } }
  if (en === g.away) nA = Math.max(nA, 6); if (en === g.home) nH = Math.max(nH, 6);
  const [aInk, hInk] = pairInks(g.away, g.home, true);
  const dots = (n, x0, dir, c) => Array.from({ length: n }, (_, i) => {
    const col = i % 3, row = Math.floor(i / 3);
    return `<circle cx="${x0 + dir * (col * 16 + row * 8)}" cy="${26 + row * 18 + (col === 1 ? 4 : 0)}" r="5.5" fill="${c}" stroke="rgba(0,0,0,.35)" stroke-width="1"/>`;
  }).join("");
  const rid = `rk-${attr(g.id)}`;
  const glow = pp ? `<rect x="${pp === g.away ? 4 : 160}" y="6" width="156" height="58" fill="${pp === g.away ? aInk : hInk}" opacity=".14" clip-path="url(#${rid})"/>` : "";
  return `<div class="sit sit--nhl">
    <div class="sit-head">${pp ? `<span class="pp" style="--pp:${raw(pp)}">${esc(pp.abbr)} power play${s.strength ? ` · ${esc(s.strength)}` : ""}</span>` : `<span class="pp" style="--pp:#3a4152">Even strength${s.strength ? ` · ${esc(s.strength)}` : ""}</span>`}
      ${en ? `<span class="en">${esc(en.abbr)} empty net</span>` : ""}</div>
    <svg class="rink" viewBox="0 0 320 70" role="img" aria-label="${attr(pp ? `${pp.abbr} power play ${s.strength || ""}` : "Even strength")}">
      <defs><clipPath id="${rid}"><rect x="4" y="6" width="312" height="58" rx="26"/></clipPath></defs>
      <rect x="4" y="6" width="312" height="58" rx="26" fill="rgba(210,228,245,.07)" stroke="rgba(255,255,255,.4)" stroke-width="1.5"/>
      ${glow}
      <line x1="26" y1="8" x2="26" y2="62" stroke="#ff3b47" stroke-width="1" opacity=".7"/><line x1="294" y1="8" x2="294" y2="62" stroke="#ff3b47" stroke-width="1" opacity=".7"/>
      <path d="M26 28a7 7 0 0 1 0 14z" fill="rgba(77,163,255,.35)"/><path d="M294 28a7 7 0 0 0 0 14z" fill="rgba(77,163,255,.35)"/>
      <line x1="112" y1="6" x2="112" y2="64" stroke="#4da3ff" stroke-width="2.5" opacity=".85"/><line x1="208" y1="6" x2="208" y2="64" stroke="#4da3ff" stroke-width="2.5" opacity=".85"/>
      <line x1="160" y1="6" x2="160" y2="64" stroke="#ff3b47" stroke-width="2.5" opacity=".85"/>
      <circle cx="160" cy="35" r="12" fill="none" stroke="#4da3ff" stroke-width="1.2" opacity=".8"/>
      ${dots(nA, 60, 1, aInk)}${dots(nH, 260, -1, hInk)}
    </svg>
    <div class="sit-row"><span class="row" style="gap:6px">${logo(g.away, { size: 18, dark: true })}${esc(get(g, "away.abbr"))} · ${nA}</span><span>Skaters</span><span class="row" style="gap:6px">${nH} · ${esc(get(g, "home.abbr"))}${logo(g.home, { size: 18, dark: true })}</span></div>
  </div>`;
}

/* ---------- NBA: period progress + possession/timeouts when the feed has them ---------- */
function court(g) {
  const s = g.situation || {};
  const pos = teamBy(g, s.possession);
  const tos = timeouts(g);
  const p = progress(g);
  if (!p && !pos && !tos) return "";
  return `<div class="sit sit--nba">${p}${tos || pos ? `<div class="sit-row">${tos || `<span></span><span>${pos ? `${esc(pos.abbr)} ball` : ""}</span><span></span>`}</div>` : ""}</div>`;
}

/** Game clock as a segmented bar: one segment per regulation period, filled by elapsed time. */
export function progress(g) {
  const lg = g && g.league;
  const n = REG[lg];
  const per = num(g && g.period, 0);
  if (!PERIOD_MIN[lg] || !per) return "";
  const m = String((g && g.clock) || "").match(/^(\d+):(\d\d)/);
  const left = m ? +m[1] * 60 + +m[2] : null;
  const len = PERIOD_MIN[lg] * 60;
  const segs = [];
  const total = Math.max(n, per);
  for (let i = 1; i <= total; i++) {
    const f = i < per ? 1 : i > per ? 0 : left === null ? 0.5 : clamp(1 - left / len, 0, 1);
    segs.push(`<i style="--f:${f.toFixed(3)}"></i>`);
  }
  const labels = Array.from({ length: total }, (_, i) => {
    const k = i + 1;
    const t = lg === "nhl" ? (k <= 3 ? ordinal(k) : "OT") : k <= 4 ? `Q${k}` : "OT";
    return `<span class="${k === per ? "on" : ""}">${t}</span>`;
  }).join("");
  return `<div class="sit sit--prog"><div class="prog" style="--n:${total}">${segs.join("")}</div><div class="prog-labels" style="--n:${total}">${labels}</div></div>`;
}

/* ---------- win probability ---------- */
export function wpBar(g) {
  const wp = num(g && g.wp, NaN);
  if (!Number.isFinite(wp) || g.phase !== "live") return "";
  const h = Math.round(clamp(wp, 0, 1) * 100), a = 100 - h;
  return `<div class="wp" aria-label="${attr(`Win probability: ${get(g, "away.abbr")} ${a}%, ${get(g, "home.abbr")} ${h}%`)}">
    <div class="wp-labels"><span><b>${a}%</b> ${esc(get(g, "away.abbr"))}</span><span class="mid">Win probability</span><span>${esc(get(g, "home.abbr"))} <b>${h}%</b></span></div>
    <div class="wp-bar" style="--wa:${a}"><i></i></div></div>`;
}

/** Home win-probability history as an area chart: above 50% fills in home color, below in away color. */
export function sparkline(series, g, { id = "wp" } = {}) {
  const xs = arr(series).map((v) => clamp(num(v, 0.5), 0, 1));
  if (xs.length < 2) return "";
  const W = 300, H = 110, mid = H / 2;
  const pts = xs.map((v, i) => [(i / (xs.length - 1)) * W, (1 - v) * (H - 8) + 4]);
  const line = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join("");
  const area = `${line}L${W} ${mid}L0 ${mid}Z`;
  const [aInk, hInk] = pairInks(g && g.away, g && g.home, true);
  const last = xs[xs.length - 1];
  const sid = attr(id);
  return `<div class="spark-wrap" style="position:relative">
    <svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Win probability over the game">
      <defs><clipPath id="t-${sid}"><rect x="0" y="0" width="${W}" height="${mid}"/></clipPath><clipPath id="b-${sid}"><rect x="0" y="${mid}" width="${W}" height="${mid}"/></clipPath>
      <linearGradient id="gt-${sid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${hInk}" stop-opacity=".55"/><stop offset="1" stop-color="${hInk}" stop-opacity=".05"/></linearGradient>
      <linearGradient id="gb-${sid}" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="${aInk}" stop-opacity=".55"/><stop offset="1" stop-color="${aInk}" stop-opacity=".05"/></linearGradient></defs>
      <line x1="0" y1="${mid}" x2="${W}" y2="${mid}" stroke="rgba(255,255,255,.25)" stroke-dasharray="3 4" vector-effect="non-scaling-stroke"/>
      <path d="${area}" fill="url(#gt-${sid})" clip-path="url(#t-${sid})"/>
      <path d="${area}" fill="url(#gb-${sid})" clip-path="url(#b-${sid})"/>
      <path d="${line}" fill="none" stroke="#fff" stroke-width="2" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>
    </svg>
    <span style="position:absolute;right:-4px;top:calc(${(((1 - last) * (H - 8) + 4) / H) * 100}% - 5px);width:10px;height:10px;border-radius:50%;background:#fff;box-shadow:0 0 0 4px rgba(255,255,255,.18)"></span>
  </div>`;
}

/* ---------- linescore ---------- */
export function linescore(ls, g) {
  const labels = arr(ls && ls.labels);
  if (!labels.length || !g) return "";
  const n = labels.length;
  const show = g.league === "mlb" ? Math.max(9, n) : Math.max(REG[g.league] || n, n);
  const cols = Array.from({ length: show }, (_, i) => labels[i] || (g.league === "mlb" ? String(i + 1) : i < (REG[g.league] || 0) ? String(i + 1) : "OT"));
  const w = g.phase === "post" ? (num(get(g, "away.score")) > num(get(g, "home.score")) ? "a" : "h") : null;
  const row = (side) => {
    const t = side === "a" ? g.away : g.home;
    const vals = arr(ls && ls[side === "a" ? "away" : "home"]);
    return `<tr class="${w ? (w === side ? "win" : "lose") : ""}"><td><span class="tm">${logo(t, { size: 24 })}${esc(t && t.abbr)}</span></td>${cols.map((_, i) => `<td>${esc(vals[i], i < vals.length ? "0" : "")}</td>`).join("")}<td class="tot">${esc(t && t.score)}</td></tr>`;
  };
  return `<div style="overflow-x:auto;margin:0 -2px"><table class="ls"><thead><tr><th></th>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}<th>${g.league === "mlb" ? "R" : "T"}</th></tr></thead><tbody>${row("a")}${row("h")}</tbody></table></div>`;
}

export { icon };
