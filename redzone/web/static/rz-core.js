/* RedZone Everything — core: shared state, formatting, team colors, images, game helpers.
   Every helper is defensive: missing fields render as "—" and nothing here throws. */

export const Q = new URLSearchParams(location.search);
export const MOCK = Q.has("mock");
export const LEAGUES = ["nfl", "nba", "nhl", "mlb"];
export const LEAGUE_NAME = { nfl: "NFL", nba: "NBA", nhl: "NHL", mlb: "MLB" };

/* ---------- shared app state ---------- */
export const app = {
  S: null,            // latest snapshot from /api/state or SSE
  prev: null,         // previous snapshot (for diffs → animations)
  skew: 0,            // server clock minus phone clock, seconds
  tab: "now",
  teams: null,        // GET /api/teams
  settings: null,     // GET /api/settings
  channels: null,     // GET /api/channels
  lab: null,          // GET /api/lab
  details: new Map(), // game id -> { data, at }
  rosters: new Map(), // "lg:id" -> { data, at }
  ui: {},             // per-screen UI state (filters, segments, forms)
};

/* Delegated event registries: modules register handlers by name (data-act / data-change / data-input). */
export const ACTIONS = Object.create(null);
export const CHANGES = Object.create(null);
export const INPUTS = Object.create(null);

/* Render hooks registered by app.js so feature modules can ask for a redraw without import cycles. */
export const hooks = { render: () => {}, renderScreen: () => {}, go: () => {} };

/* ---------- storage that never throws ---------- */
export const store = {
  get(k, d = null) {
    try { const v = localStorage.getItem("rz:" + k); return v === null ? d : JSON.parse(v); } catch { return d; }
  },
  set(k, v) { try { localStorage.setItem("rz:" + k, JSON.stringify(v)); } catch { /* private mode */ } },
};

/* ---------- small utils ---------- */
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export function esc(v, fallback = "—") {
  if (v === null || v === undefined || v === "") return fallback;
  return String(v).replace(/[&<>"']/g, (c) => ESC[c]);
}
export const attr = (v) => esc(v, "");
export function num(v, d = 0) { const n = Number(v); return v !== null && v !== "" && Number.isFinite(n) ? n : d; }
export const clamp = (x, lo, hi) => Math.min(hi, Math.max(lo, x));
export function get(o, path, d) {
  try {
    const v = path.split(".").reduce((a, k) => (a == null ? undefined : a[k]), o);
    return v === undefined || v === null ? d : v;
  } catch { return d; }
}
export const arr = (v) => (Array.isArray(v) ? v.filter((x) => x !== null && x !== undefined) : []);
export const reduceMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
export const isDark = () => !matchMedia("(prefers-color-scheme: light)").matches;
export function vibrate(ms = 10) { try { navigator.vibrate && navigator.vibrate(ms); } catch { /* iOS */ } }
export const SPRING = (() => {
  try {
    if (CSS.supports("transition-timing-function", "linear(0, 1)")) {
      return "linear(0, .07 3%, .25 7%, .5 12%, .72 17%, .87 22%, .96 27%, 1.01 32%, 1.03 38%, 1.03 44%, 1.015 52%, 1.005 62%, 1 75%, 1)";
    }
  } catch { /* old engine */ }
  return "cubic-bezier(.25, 1.25, .4, 1)";
})();
export const EASE_OUT = "cubic-bezier(.22, 1, .36, 1)";
export function initials(name) {
  const parts = String(name || "").replace(/\./g, " ").split(/\s+/).filter(Boolean);
  if (!parts.length) return "";
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}

/* ---------- time ---------- */
export const nowS = () => Date.now() / 1000 + app.skew;
export function fmtDur(s) {
  s = Math.max(0, Math.floor(num(s)));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`;
}
export function fmtAgo(ts) {
  if (!ts) return "—";
  const d = nowS() - num(ts);
  if (d < 15) return "just now";
  if (d < 60) return `${Math.floor(d)}s ago`;
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}
function local(ts) { return new Date((num(ts) - app.skew) * 1000); }
export function fmtTime(ts) {
  if (!ts) return "—";
  try { return local(ts).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); } catch { return "—"; }
}
export function fmtStart(ts) {
  if (!ts) return "";
  const d = local(ts), t = new Date();
  const time = fmtTime(ts);
  if (d.toDateString() === t.toDateString()) return time;
  if (new Date(t.getTime() + 86400000).toDateString() === d.toDateString()) return `Tomorrow ${time}`;
  if (d < t) return time;
  return `${d.toLocaleDateString([], { weekday: "short" })} ${time}`;
}
export function fmtCountdown(ts) {
  const s = num(ts) - nowS();
  if (s <= 0) return "now";
  if (s < 60) return `${Math.ceil(s)}s`;
  const h = Math.floor(s / 3600), m = Math.ceil((s % 3600) / 60);
  return h ? `${h}h ${String(m === 60 ? 0 : m).padStart(2, "0")}m` : `${m}m`;
}
export function fmtClockCountdown(ts) { return fmtDur(num(ts) - nowS()); }
export function ordinal(n) {
  n = num(n, 0);
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}
export function todayLabel() {
  try { return new Date().toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" }); } catch { return ""; }
}

/* ---------- colors ---------- */
export function hex(c) {
  const s = String(c || "").trim().replace(/^#/, "");
  if (/^[0-9a-f]{6}$/i.test(s)) return "#" + s.toLowerCase();
  if (/^[0-9a-f]{3}$/i.test(s)) return "#" + s.split("").map((x) => x + x).join("").toLowerCase();
  return null;
}
function rgb(h) { const n = parseInt(h.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
function toHex(c) { return "#" + c.map((x) => Math.round(clamp(x, 0, 255)).toString(16).padStart(2, "0")).join(""); }
export function lum(h) {
  if (!h) return 0;
  const [r, g, b] = rgb(h).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
export function mixHex(a, b, t) { const x = rgb(a), y = rgb(b); return toHex(x.map((v, i) => v + (y[i] - v) * t)); }
const contrast = (l1, l2) => (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
function dist(a, b) { const x = rgb(a), y = rgb(b); return Math.hypot(x[0] - y[0], x[1] - y[1], x[2] - y[2]); }
const BG_DARK = 0.0035, BG_LIGHT = 0.88;

/** A version of the team's color that reads as an accent on the current background. */
export function ink(team, dark = isDark()) {
  const p = hex(team && team.color), a = hex(team && team.alt_color);
  const bg = dark ? BG_DARK : BG_LIGHT;
  const ok = (c) => c && contrast(lum(c), bg) >= 2.6 && !(dark ? lum(c) > 0.85 : lum(c) < 0.02);
  if (ok(p)) return p;
  if (ok(a)) return a;
  const base = p || a || "#6f778a";
  for (let t = 0.15; t <= 1; t += 0.1) {
    const c = mixHex(base, dark ? "#ffffff" : "#000000", t);
    if (contrast(lum(c), bg) >= 2.6) return c;
  }
  return dark ? "#c9ced8" : "#4a5266";
}
/** Two accents for a matchup that stay distinguishable from each other. */
export function pairInks(away, home, dark = isDark()) {
  const ai = ink(away, dark);
  let hi = ink(home, dark);
  if (dist(ai, hi) < 70) {
    const alt = ink({ color: home && home.alt_color, alt_color: home && home.color }, dark);
    hi = dist(ai, alt) >= 70 ? alt : mixHex(hi, dark ? "#ffffff" : "#000000", 0.45);
  }
  return [ai, hi];
}
export const raw = (t) => hex(t && t.color) || "#3a4152";
export const onColor = (c) => (lum(c) > 0.42 ? "#0b0d12" : "#ffffff");
export function teamVars(away, home, dark = isDark()) {
  const [ai, hi] = pairInks(away, home, dark);
  return `--a:${raw(away)};--h:${raw(home)};--a-ink:${ai};--h-ink:${hi}`;
}
export function heatColor(p) {
  p = clamp(num(p), 0, 100);
  const stops = ["--heat-0", "--heat-1", "--heat-2", "--heat-3", "--heat-4", "--heat-5"];
  return `var(${stops[Math.min(5, Math.floor(p / 17))]})`;
}

/* ---------- images: every a.espncdn.com picture goes through the Mac's /img cache ---------- */
let proxyOn = !MOCK && store.get("imgproxy", true) !== false;
let proxyGood = false, proxyFails = 0;
export function imgSrc(url) {
  const u = String(url || "").trim();
  if (!u) return { src: "", orig: "" };
  const m = u.match(/^https?:\/\/a\.espncdn\.com\/(.+)$/i);
  if (!m || !proxyOn) return { src: u, orig: u };
  return { src: "/img/" + m[1], orig: u };
}
export function imgTag(url, { cls = "", alt = "", lazy = true, eager = false } = {}) {
  const { src, orig } = imgSrc(url);
  if (!src) return "";
  return `<img${cls ? ` class="${cls}"` : ""} src="${attr(src)}" data-src="${attr(src)}" data-orig="${attr(orig)}" alt="${attr(alt)}"${lazy && !eager ? ' loading="lazy"' : ""} decoding="async"${eager ? ' fetchpriority="high"' : ""}>`;
}
export function installImages() {
  document.addEventListener("load", (e) => {
    const t = e.target;
    if (!t || t.tagName !== "IMG") return;
    t.setAttribute("data-rt-ok", "");
    if ((t.getAttribute("src") || "").startsWith("/img/")) proxyGood = true;
  }, true);
  document.addEventListener("error", (e) => {
    const t = e.target;
    if (!t || t.tagName !== "IMG") return;
    const orig = t.getAttribute("data-orig");
    if (orig && t.getAttribute("src") !== orig && !t.hasAttribute("data-rt-tried")) {
      t.setAttribute("data-rt-tried", "");
      if (++proxyFails >= 4 && !proxyGood) { proxyOn = false; store.set("imgproxy", false); }
      t.setAttribute("src", orig);
      return;
    }
    t.removeAttribute("data-rt-ok");
    t.setAttribute("data-rt-fail", "");
  }, true);
  if (!MOCK && store.get("imgproxy", true) === false) {
    // Re-test the proxy in the background; the backend may have gained /img since last time.
    const probe = new Image();
    probe.onload = () => { store.set("imgproxy", true); };
    probe.src = "/img/i/teamlogos/leagues/500/nfl.png?probe=" + Date.now();
  }
}
export function settleImages(root) {
  for (const im of root.querySelectorAll("img:not([data-rt-ok]):not([data-rt-fail])")) {
    if (im.complete && im.naturalWidth > 0) im.setAttribute("data-rt-ok", "");
  }
}
export const teamLogoUrl = (t, dark = isDark()) => (t ? (dark ? t.logo_dark || t.logo : t.logo || t.logo_dark) : "") || "";
export const leagueLogoUrl = (lg, dark = isDark()) =>
  LEAGUES.includes(lg) ? `https://a.espncdn.com/i/teamlogos/leagues/${dark ? "500-dark" : "500"}/${lg}.png` : "";

/* ---------- game helpers ---------- */
export const games = () => arr(app.S && app.S.games);
export const gameById = (id) => (id ? games().find((g) => g.id === id) || null : null);
export const phase = (g) => (g && ["live", "pre", "post"].includes(g.phase) ? g.phase : "unknown");
export function teamName(t) { return (t && (t.name || t.abbr)) || "—"; }

const SPECIAL = /half|end of|intermission|delay|postpon|suspend|cancel|shootout/i;
export function periodLabel(g) {
  const p = num(g && g.period, 0);
  const lg = g && g.league;
  if (!p) return "";
  if (lg === "mlb") {
    const half = get(g, "situation.half", "") || "";
    const h = /^bot/i.test(half) ? "Bot" : /^top/i.test(half) ? "Top" : /^mid/i.test(half) ? "Mid" : /^end/i.test(half) ? "End" : "";
    return `${h} ${ordinal(p)}`.trim();
  }
  if (lg === "nhl") return p <= 3 ? ordinal(p) : p === 4 ? "OT" : "SO";
  if (p <= 4) return `Q${p}`;
  return p === 5 ? "OT" : `${p - 4}OT`;
}
/** {per, clk, text, kind} for scoreboards. kind: live | break | pre | final | other */
export function status(g) {
  const ph = phase(g);
  const st = String((g && g.status_text) || "");
  if (ph === "pre") {
    const t = g && g.start_time ? fmtStart(g.start_time) : st.replace(/^\d+\/\d+\s*-\s*/, "").replace(/\s+E[SD]T$/, "");
    return { per: "", clk: t, text: t || "Upcoming", kind: "pre" };
  }
  if (ph === "post") return { per: "", clk: "", text: st || "Final", kind: "final" };
  if (ph !== "live") return { per: "", clk: "", text: st || "—", kind: "other" };
  if (SPECIAL.test(st)) return { per: "", clk: st, text: st, kind: g && g.is_break ? "break" : "live" };
  const per = periodLabel(g), clk = (g && g.clock) || "";
  const text = per && clk ? `${per} · ${clk}` : per || st || "Live";
  return { per, clk, text, kind: g && g.is_break ? "break" : "live" };
}
export function leaderLabel(l) {
  const s = String((l && l.label) || "");
  const map = { "Passing Yards": "PASS", "Rushing Yards": "RUSH", "Receiving Yards": "REC", Points: "PTS", Rebounds: "REB",
    Assists: "AST", Goals: "G", "Points Per Game": "PPG", "Rebounds Per Game": "RPG", "Assists Per Game": "APG",
    "Batting Average": "AVG", "Home Runs": "HR", "Runs Batted In": "RBI", Hits: "H" };
  return map[s] || (s.length <= 5 ? s.toUpperCase() : s.split(/\s+/).map((w) => w[0]).join("").toUpperCase());
}
export function winner(g) {
  const a = num(get(g, "away.score"), NaN), h = num(get(g, "home.score"), NaN);
  if (!Number.isFinite(a) || !Number.isFinite(h) || a === h) return null;
  return a > h ? "a" : "h";
}
export function teamBy(g, abbr) {
  if (!g || !abbr) return null;
  return get(g, "home.abbr") === abbr ? g.home : get(g, "away.abbr") === abbr ? g.away : null;
}
export function matchupWords(g) {
  if (!g) return "";
  const a = teamName(g.away), h = teamName(g.home);
  return a !== "—" && h !== "—" ? `${a} at ${h}` : g.matchup || "";
}
export function whereLabel(g) {
  const w = g && g.where;
  if (typeof w === "string") return w;
  const l = (w && w.label) || "";
  return /^unknown/i.test(l) ? "" : l;
}
export function whereUnknown(g) {
  const w = g && g.where;
  if (!w || typeof w === "string") return !w;
  return !w.target || w.confidence === "unknown" || /^unknown/i.test(w.label || "");
}
