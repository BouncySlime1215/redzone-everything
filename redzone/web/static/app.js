/* RedZone Everything — phone UI entry point.
   Vanilla ES modules, no build step. Live state arrives over SSE every ~10s; every screen renders an HTML
   string that rz-dom.js patch()es into the live DOM by key, so animations, scroll and open sheets survive.
   Cache-busting: index.html and every import carry the same ?v=N — bump them all together when shipping changes. */

import {
  app, ACTIONS, CHANGES, INPUTS, hooks, store, MOCK, Q, esc, num, get, arr, gameById, nowS, fmtDur, fmtCountdown,
  installImages, reduceMotion, pairInks, markMine,
} from "./rz-core.js?v=41";
import { patch, flash } from "./rz-dom.js?v=40";
import { icon } from "./rz-ui.js?v=40";
import { startStream, act } from "./rz-api.js?v=43";
import { updateSheets, topSheet, closeSheet } from "./rz-sheet.js?v=40";
import { renderNow, countdown } from "./rz-now.js?v=68";
import { renderGames } from "./rz-games.js?v=41";
import { renderBets } from "./rz-bets.js?v=40";
import { renderLab, loadLab } from "./rz-lab.js?v=40";
import { renderSettings, announce } from "./rz-settings.js?v=68";
import { loadTeams } from "./rz-detail.js?v=42";

const TABS = ["now", "games", "bets", "lab", "settings"];
const SCREENS = { now: renderNow, games: renderGames, bets: renderBets, lab: renderLab, settings: renderSettings };
const scrollY = {};
const $ = (id) => document.getElementById(id);

/* ---------- rendering ---------- */
function renderScreen(tab) {
  if (tab !== app.tab) return; // hidden screens render when shown
  const root = $(`scr-${tab}`);
  try { SCREENS[tab](root); } catch (e) { console.error(`render ${tab}`, e); }
}
function render() {
  markMine(app.S);
  chrome();
  renderScreen(app.tab);
  updateSheets();
}
hooks.render = render;
hooks.renderScreen = renderScreen;
hooks.go = (t) => go(t);

function chrome() {
  const S = app.S;
  const m = (S && S.mode) || {};
  const dry = $("dry-pill");
  dry.hidden = !S || m.dry_run === false;
  // tab bar: live dot on Now, sweating-bets pip
  const live = !!(S && S.now);
  $("tabbar").querySelector(".tab-live").hidden = !live;
  const pip = $("tabbar").querySelector(".tab-pip");
  const sweat = arr(S && S.bets).filter((b) => num(b.sweat) >= 0.5).length;
  pip.hidden = !sweat;
  pip.textContent = sweat || "";
  banners(S, m);
}

function banners(S, m) {
  const out = [];
  const b = (kind, ic, title, body, btn = "") => `<div class="banner banner--${kind}" data-key="b:${kind}">${icon(ic)}<div class="grow"><b>${esc(title)}</b>${body}</div>${btn}</div>`;
  const resume = `<button class="btn btn--sm" data-act="banner-resume">Resume</button>`;
  if (S) {
    const h = m.hold;
    if (h) {
      const body = h.waiting ? "Never mid-play: it cuts over at the next safe moment. Go now cuts immediately."
        : h.resume_at ? `${esc(h.matchup)} ${esc(h.why)} — autopilot back in <span data-clockdown="${num(h.resume_at)}" data-own>${esc(countdown(h.resume_at))}</span>.`
        : h.auto_resume ? "Autopilot won't switch away. It takes back over when this game ends or turns into a blowout."
        : "Autopilot won't switch away until you tap Resume auto.";
      const keep = h.resume_at ? `<button class="btn btn--sm" data-act="hold-auto" data-v="0">${icon("lock")}Keep it</button>`
        : `<button class="btn btn--sm btn--ghost" data-act="hold-auto" data-v="${h.auto_resume ? 0 : 1}" aria-pressed="${!!h.auto_resume}">${icon(h.auto_resume ? "clock" : "lock")}${h.auto_resume ? "Auto-return on" : "Auto-return off"}</button>`;
      out.push(`<div class="banner banner--hold" data-key="b:hold">${icon(h.waiting ? "hourglass" : "target")}<div class="grow"><b>${h.waiting ? "Switching soon" : "Holding"} · ${esc(h.matchup)}</b>${body}</div>
        <div class="hold-btns">${h.waiting ? `<button class="btn btn--sm" data-act="hold-force" data-gid="${esc(h.game_id)}">${icon("zap")}Go now</button>` : keep}
        <button class="btn btn--primary btn--lg hold-resume" data-act="hold-release">${icon("autopilot")}Resume auto</button></div></div>`);
    }
    if (m.safe) out.push(b("safe", "shield", "Safe mode", "Too many errors in a row, so it stopped switching. Check Health in Settings.", resume));
    else if (m.frozen_until && num(m.frozen_until) > nowS()) out.push(b("frozen", "snow", "Holding still", `It caught itself flip-flopping. Back to normal in <span data-clockdown="${num(m.frozen_until)}" data-own>${esc(countdown(m.frozen_until))}</span>.`, resume));
    else if (m.paused) out.push(b("paused", "pause", "Auto-switching paused", "It'll stay right here until you resume.", resume));
    if (m.control_note) out.push(b("paused", "alert", "Heads up", esc(m.control_note)));
  }
  patch($("banners"), out.join(""));
}

/* ---------- state ---------- */
function applyState(s) {
  if (!s || typeof s !== "object") return;
  s.games = arr(s.games);
  const prev = app.S;
  const st = num(s.server_time || s.ts, 0);
  if (!MOCK && st) { const k = st - Date.now() / 1000; if (Math.abs(k) < 86400) app.skew = k; }
  app.prev = prev;
  app.S = s;
  const events = diff(prev, s);
  render();
  play(events);
}

function diff(a, b) {
  const ev = { scored: [], switched: false };
  if (!a) return ev;
  const old = new Map(arr(a.games).map((g) => [g.id, g]));
  for (const g of b.games) {
    const o = old.get(g.id);
    if (!o || g.phase !== "live") continue;
    for (const side of ["away", "home"]) {
      if (num(get(g, `${side}.score`), 0) > num(get(o, `${side}.score`), 0)) ev.scored.push({ id: g.id, side });
    }
  }
  const pa = a.now ? `${a.now.game_id}|${a.now.kind}` : "", pb = b.now ? `${b.now.game_id}|${b.now.kind}` : "";
  ev.switched = pa !== pb && !!b.now;
  return ev;
}

function play(ev) {
  if (ev.switched) announce(app.S.now);
  if (reduceMotion()) return;
  for (const { id, side } of ev.scored) {
    const g = gameById(id);
    if (!g) continue;
    const [ai, hi] = pairInks(g.away, g.home);
    const color = side === "home" ? hi : ai;
    document.querySelectorAll(`[data-key="g:${CSS.escape(id)}"] > .gc-fx`).forEach((el) => flash(el, color));
    const hero = document.querySelector(`.hero[data-gid="${CSS.escape(id)}"] > .hero-fx`);
    if (hero) flash(hero, color);
  }
}

/* ---------- navigation ---------- */
function go(tab, { initial = false } = {}) {
  if (!TABS.includes(tab)) tab = "now";
  const from = app.tab;
  if (!initial && from === tab) { window.scrollTo({ top: 0, behavior: reduceMotion() ? "auto" : "smooth" }); return; }
  scrollY[from] = window.scrollY;
  app.tab = tab;
  store.set("tab", tab);
  if (location.hash.slice(1) !== tab) history.replaceState(null, "", `${location.pathname}${location.search}#${tab}`);
  const dir = TABS.indexOf(tab) < TABS.indexOf(from) ? "back" : "fwd";
  for (const s of document.querySelectorAll(".screen")) {
    const on = s.dataset.screen === tab;
    if (on) s.dataset.dir = dir;
    s.hidden = !on;
  }
  const bar = $("tabbar");
  bar.style.setProperty("--i", TABS.indexOf(tab));
  for (const b of bar.querySelectorAll(".tab")) b.setAttribute("aria-selected", String(b.dataset.go === tab));
  if (tab === "lab") loadLab();
  renderScreen(tab);
  window.scrollTo({ top: initial ? 0 : scrollY[tab] || 0 });
}

/* ---------- live timers (1s) ---------- */
function tick() {
  for (const el of document.querySelectorAll("[data-since]")) {
    const s = num(el.dataset.since);
    const t = s ? fmtDur(nowS() - s) : "—";
    if (el.textContent !== t) el.textContent = t;
  }
  for (const el of document.querySelectorAll("[data-countdown]")) {
    const t = fmtCountdown(el.dataset.countdown);
    if (el.textContent !== t) el.textContent = t;
  }
  for (const el of document.querySelectorAll("[data-clockdown]")) {
    const t = countdown(el.dataset.clockdown);
    if (el.textContent !== t) el.textContent = t;
  }
}

/* ---------- connection pill ---------- */
// playbook=BTN-06: the phone lost the app. The Mac keeps every setting and goes on switching; what
// the phone shows is the last state it heard, so after OFFLINE_BANNER_MS say so. It resyncs
// (GET /api/state) the moment the stream is back, and taps sent meanwhile retry with their id.
const OFFLINE_BANNER_MS = 10000;
let offlineSince = 0, offlineTimer = null;

function offlineBanner() {
  const host = $("banners");
  if (!host || host.querySelector('[data-key="b:offline"]')) return;
  const at = new Date(offlineSince).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  host.insertAdjacentHTML("afterbegin", `<div class="banner banner--paused" data-key="b:offline">${icon("alert")}<div class="grow"><b>Phone offline</b>Showing what the app said at ${esc(at)}. The TV keeps going on its own; taps retry when the phone reconnects.</div></div>`);
}

function conn(state) {
  const el = $("conn");
  if (el.dataset.state === state) return;
  if (state === "wait") {
    offlineSince = offlineSince || Date.now();
    clearTimeout(offlineTimer);
    offlineTimer = setTimeout(offlineBanner, OFFLINE_BANNER_MS);
  } else {
    offlineSince = 0;
    clearTimeout(offlineTimer);
    const b = document.querySelector('[data-key="b:offline"]');
    if (b) b.remove();
  }
  el.dataset.state = state;
  el.querySelector("span").textContent = { live: "Connected", wait: "Reconnecting", mock: "Preview" }[state] || "Connecting";
  el.title = state === "live" ? "Connected to the RedZone app on your Mac" : "";
}

/* ---------- one delegated listener per event type ---------- */
function installEvents() {
  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-act], [data-go]");
    if (!el || el.disabled || el.getAttribute("aria-disabled") === "true") return;
    if (el.dataset.go) { e.preventDefault(); while (topSheet()) closeSheet(topSheet()); go(el.dataset.go); return; }
    const fn = ACTIONS[el.dataset.act];
    if (!fn) return;
    if (el.tagName === "A") e.preventDefault();
    try { const r = fn(el, e); if (r && r.catch) r.catch((err) => console.error(err)); } catch (err) { console.error(err); }
  });
  document.addEventListener("change", (e) => {
    const el = e.target.closest("[data-change]");
    if (el && CHANGES[el.dataset.change]) CHANGES[el.dataset.change](el, e);
  });
  document.addEventListener("input", (e) => {
    const el = e.target.closest("[data-input]");
    if (el && INPUTS[el.dataset.input]) INPUTS[el.dataset.input](el, e);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && topSheet()) { closeSheet(topSheet()); return; }
    if ((e.key === "Enter" || e.key === " ") && e.target.matches && e.target.matches('[role="button"][data-act]')) { e.preventDefault(); e.target.click(); }
  });
  matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
    for (const t of TABS) { const r = $(`scr-${t}`); if (r) r.innerHTML = ""; }
    render();
  });
  const bar = document.getElementById("appbar");
  window.addEventListener("scroll", () => {
    const sb = document.querySelector(".screen:not([hidden]) .subbar");
    if (sb) sb.classList.toggle("stuck", sb.getBoundingClientRect().top <= bar.getBoundingClientRect().bottom + 1);
  }, { passive: true });
}

ACTIONS["banner-resume"] = (el) => act("resume", {}, el);
ACTIONS["hold-release"] = (el) => act("release", {}, el);
ACTIONS["hold-force"] = (el) => act("hold", { game_id: el.dataset.gid, value: "force" }, el);
ACTIONS["hold-auto"] = (el) => act("hold_auto", { value: el.dataset.v === "1" }, el);

/* ---------- boot ---------- */
installImages();
installEvents();
const first = (location.hash || "").slice(1) || (Q.get("tab") || "") || store.get("tab", "now");
go(TABS.includes(first) ? first : "now", { initial: true });
render();
startStream({ state: applyState, conn });
setTimeout(() => loadTeams().then(() => render()), 1500);
conn(MOCK ? "mock" : "wait");
setInterval(tick, 1000);
window.addEventListener("hashchange", () => { const t = location.hash.slice(1); if (TABS.includes(t) && t !== app.tab) go(t); });
