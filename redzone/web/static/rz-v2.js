/* RedZone Everything — phone v2 (flag phone.v2, off by default; server routes in redzone/web/phone_v2.py).
   Why each switch happened (reason chips), 👍/👎 on that one switch, "I missed that", and the Noggin
   Priority L2 sliders. When the flag is off every export here renders "" and nothing else changes. */

import { app, ACTIONS, CHANGES, INPUTS, hooks, attr, esc, num, arr } from "./rz-core.js?v=41";
import { icon, sec } from "./rz-ui.js?v=40";
import { api } from "./rz-api.js?v=43";
import { toast, openSheet, closeSheet, sheetById } from "./rz-sheet.js?v=40";

const v2 = { on: false, checked: false, rows: new Map(), head: "", loading: false, prio: null, missed: null };

async function check() {
  if (v2.checked) return;
  v2.checked = true;
  try { v2.on = !!(await api("/api/v2/status")).enabled; } catch { v2.on = false; }
  if (v2.on) hooks.render();
}
check();

/** Refetch chips + votes when the newest switch changes. */
function sync(S) {
  const log = arr(S && S.log);
  const head = log.length ? `${log[0].ts}|${log[0].to}` : "";
  if (!v2.on || v2.loading || head === v2.head) return;
  v2.loading = true;
  api("/api/v2/switches").then((r) => {
    v2.rows = new Map(arr(r && r.switches).map((x) => [`${x.ts}|${x.to}`, x]));
    v2.head = head;
  }).catch(() => {}).finally(() => { v2.loading = false; hooks.renderScreen("now"); });
}

export const v2On = () => v2.on;

/** Extra line under one "Recent switches" row: reason chips + 👍/👎. */
export function switchExtras(l) {
  if (!v2.on) return "";
  sync(app.S);
  const r = v2.rows.get(`${l.ts}|${l.to}`);
  if (!r) return "";
  const vb = (v, ic, label) => `<button class="v2-vote ${r.vote === v ? "on" : ""}" data-act="v2-vote" data-key="${attr(r.key)}" data-v="${r.vote === v ? 0 : v}"
    aria-pressed="${r.vote === v}" aria-label="${attr(label)}">${icon(ic)}</button>`;
  return `<div class="v2-why">${arr(r.chips).map((c) => `<span class="v2-chip v2-chip--${attr(c.key)}">${esc(c.label)}</span>`).join("")}
    <span class="grow"></span>${vb(1, "thumb-up", "Good switch")}${vb(-1, "thumb-down", "Bad switch")}</div>`;
}

ACTIONS["v2-vote"] = async (el) => {
  const key = el.dataset.key, vote = num(el.dataset.v);
  try {
    const r = await api("/api/v2/vote", { body: { key, vote } });
    if (r.ok) { for (const x of v2.rows.values()) if (x.key === key) x.vote = vote; }
    toast(r.message, r.ok ? "ok" : "warn");
  } catch (err) { toast(err.message || "That didn't work", "error"); }
  hooks.renderScreen("now");
};

/** The "I missed that" bar above Recent switches. */
export function missedBar() {
  if (!v2.on) return "";
  return `<button class="btn btn--block v2-missed" data-act="v2-missed" data-key="v2-missed">${icon("eye")}I missed that</button>`;
}

function missedSheet() {
  const m = v2.missed || {};
  const c = arr(m.candidates);
  return `<h3>Which play?</h3><p class="lead">${esc(m.message, "")}</p>
    <div class="pick-list" style="margin-top:12px">${c.map((x) => `<button class="pick" data-act="v2-missed-pick" data-gid="${attr(x.game_id)}" aria-pressed="${!!m.guess && m.guess.game_id === x.game_id}">
      <span class="grow"><span class="t1" style="display:block">${esc(x.matchup)}</span><span class="t2 ell" style="display:block">${esc(x.text || x.kind)} · ${Math.round(num(x.ago_s) / 60)} min ago</span></span>
      <span class="tick">${icon("check")}</span></button>`).join("")}</div>`;
}

ACTIONS["v2-missed"] = async (el) => {
  el.setAttribute("aria-busy", "true");
  try {
    const r = await api("/api/v2/missed", { body: {} });
    v2.missed = r;
    if (arr(r.candidates).length > 1) openSheet({ id: "v2-missed", label: "Which play", render: missedSheet });
    else toast(r.message, "ok");
  } catch (err) { toast(err.message || "That didn't work", "error"); }
  finally { el.removeAttribute("aria-busy"); }
};
ACTIONS["v2-missed-pick"] = async (el) => {
  try {
    const r = await api("/api/v2/missed", { body: { game_id: el.dataset.gid } });
    toast(r.message, "ok");
  } catch (err) { toast(err.message || "That didn't work", "error"); }
  closeSheet(sheetById("v2-missed"));
};

/* ---------- Settings: Noggin Priority L2 ---------- */
const SLIDERS = [
  ["bets_weight", "Your bets", "How much a live bet pulls the TV toward its game", 0.1],
  ["rivalry_points", "Rivalries", "Points a rivalry game gets on top of its live value", 1],
  ["nhl_pp_ot_boost", "NHL power plays + OT", "Extra pull for power plays and overtime", 0.1],
  ["switch_frequency", "Switch how often", "Calmer stays put longer; busier chases more", 0.1],
];

function loadPrio() {
  if (v2.prio || !v2.on) return;
  v2.prio = { loading: true };
  api("/api/v2/priority").then((r) => { v2.prio = r; }).catch((e) => { v2.prio = { error: e.message }; })
    .finally(() => hooks.renderScreen("settings"));
}

export function prioritySection() {
  if (!v2.on) return "";
  loadPrio();
  const p = v2.prio || {};
  const head = sec("Your priorities", { key: "s-v2prio", aside: "Never beats your hard rules" });
  if (!p.prefs) return `${head}<div class="card card-pad" data-key="v2prio"><div class="t2">${p.error ? esc(p.error) : "Loading…"}</div></div>`;
  const rows = SLIDERS.map(([k, label, help, step]) => {
    const [lo, hi] = arr(p.ranges && p.ranges[k]).length ? p.ranges[k] : [0, 2];
    const v = num(p.prefs[k], lo);
    const pct = ((v - lo) / Math.max(1e-9, hi - lo)) * 100;
    return `<div class="slider"><div class="slider-top"><span class="t1">${esc(label)}</span><span class="val tnum" data-v2-val="${attr(k)}">${esc(v)}</span></div>
      <div class="t2">${esc(help)}</div>
      <input class="range" type="range" min="${lo}" max="${hi}" step="${step}" value="${v}" style="--p:${pct.toFixed(1)}" data-sync data-input="v2-prio" data-k="${attr(k)}" aria-label="${attr(label)}"></div>`;
  }).join("");
  const reels = `<div class="set-r"><div class="grow"><div class="t1">Catch-up reels</div><div class="t2">Short reels of plays you missed, only in dead time</div></div>
    <label class="switch"><input type="checkbox" data-change="v2-reels" ${p.prefs.reels ? "checked" : ""} aria-label="Catch-up reels"><span></span></label></div>`;
  const sugg = arr(p.suggestions);
  const learned = `<div class="t2" style="margin-top:10px">${sugg.length
    ? `From your ${p.votes} ratings: ${sugg.map((s) => `${esc(s.suggest === "less" ? "less" : "more")} <b>${esc(s.term)}</b>`).join(", ")}. Suggestions only; nothing changes until you move a slider.`
    : `Your sliders are the only way these change; the Noggin teaches itself and never learns from your ratings.`}</div>`;
  return `${head}<div class="card set" data-key="v2prio">${rows}${reels}<div class="card-pad">${learned}</div></div>`;
}

async function savePrio(changes) {
  try {
    const r = await api("/api/v2/priority", { body: changes });
    if (r.ok && v2.prio && v2.prio.prefs) Object.assign(v2.prio.prefs, r.prefs);
    toast(r.message, r.ok ? "ok" : "warn");
  } catch (err) { toast(err.message || "That didn't work", "error"); }
}

INPUTS["v2-prio"] = (el) => {
  const v = Number(el.value), k = el.dataset.k;
  const lbl = document.querySelector(`[data-v2-val="${CSS.escape(k)}"]`);
  if (lbl) lbl.textContent = v;
  el.style.setProperty("--p", (((v - +el.min) / Math.max(1e-9, +el.max - +el.min)) * 100).toFixed(1));
  clearTimeout(INPUTS["v2-prio"].t);
  INPUTS["v2-prio"].t = setTimeout(() => savePrio({ [k]: v }), 400);
};

CHANGES["v2-reels"] = (el) => savePrio({ reels: !!el.checked });

/* ---------- Button states: what happened to the tap you just made (lane 3's id/ack/status API) ----------
   GO NOW / Watch this: "Switching to X…" until GET /api/action/{id} says the TV shows it ("On your TV"),
   or "Waiting for a safe moment" while it holds off mid-play. A tap whose answer was lost shows
   "Not delivered yet, retrying" while rz-api retries with the same id, then "Not delivered" if it gives up.
   A dropped live connection for 8 s shows a banner: taps may not reach the TV. Display only. */
const GAME_TAPS = new Set(["hold", "watch", "redzone", "focus"]);
const POLL_MS = 1000, POLL_FOR_MS = 25000;
const tap = { el: null, timer: null, hide: null };

function tapBar() {
  if (!tap.el) {
    tap.el = document.createElement("div");
    tap.el.className = "v2-tap";
    tap.el.setAttribute("role", "status");
    tap.el.setAttribute("aria-live", "polite");
    document.body.appendChild(tap.el);
  }
  return tap.el;
}

function showTap(kind, ic, text, ms = 0) {
  const el = tapBar();
  clearTimeout(tap.hide);
  el.dataset.kind = kind;
  el.innerHTML = `${icon(ic)}<span>${esc(text)}</span>`;
  el.hidden = false;
  if (ms) tap.hide = setTimeout(() => { el.hidden = true; }, ms);
}

function gameName(gid) {
  const g = arr(app.S && app.S.games).find((x) => x.id === gid);
  return (g && g.matchup) || "that game";
}

function follow(id, gid, t0 = Date.now()) {
  clearTimeout(tap.timer);
  tap.timer = setTimeout(async () => {
    let st = null;
    try { st = await api(`/api/action/${encodeURIComponent(id)}`); } catch { st = null; }
    if (st && st.on_tv) { showTap("ok", "check", `On your TV: ${gameName(gid)}`, 3000); return; }
    if (st && st.state === "failed") { showTap("bad", "alert", st.message || "That didn't work", 5000); return; }
    if (Date.now() - t0 > POLL_FOR_MS) { showTap("wait", "hourglass", `Still waiting for a safe moment in ${gameName(gid)}`, 6000); return; }
    follow(id, gid, t0);
  }, POLL_MS);
}

export function onTap(d) {
  if (!v2.on || !d) return;
  const action = d.body && d.body.action;
  if (d.phase === "retry") { showTap("wait", "wifi", `Not delivered yet, retrying (${d.attempt})…`); return; }
  if (d.phase === "lost") { showTap("bad", "alert", "Not delivered: the app didn't answer. Tap again.", 6000); return; }
  if (d.phase !== "answer" || !d.res) return;
  const r = d.res, gid = d.body.game_id;
  if (!GAME_TAPS.has(action) || !gid || r.ok === false) { if (tap.el && tap.el.dataset.kind === "wait") tap.el.hidden = true; return; }
  if (r.on_tv) { showTap("ok", "check", `On your TV: ${gameName(gid)}`, 3000); return; }
  showTap("wait", "swap", d.body.value === "force" ? `Switching to ${gameName(gid)} now…` : `Switching to ${gameName(gid)} at the next safe moment…`);
  follow(r.id || d.id, gid);
}
try { window.addEventListener("rz:tap", (e) => onTap(e.detail)); } catch { /* no window */ }

/* Stale connection: the #conn pill (app.js) says "Reconnecting" for 8 s -> one banner until it's back. */
const STALE_MS = 8000;
function watchConn() {
  const c = document.getElementById("conn");
  if (!c || typeof MutationObserver === "undefined") return;
  let since = null, t = null;
  const check = () => {
    const bad = c.dataset.state === "wait";
    clearTimeout(t);
    if (!bad) { since = null; if (tap.el && tap.el.dataset.kind === "stale") tap.el.hidden = true; return; }
    since = since || Date.now();
    t = setTimeout(() => { if (v2.on && c.dataset.state === "wait") showTap("stale", "wifi", "Lost the connection to the Mac. Taps may not reach the TV; reconnecting…"); }, Math.max(0, STALE_MS - (Date.now() - since)));
  };
  new MutationObserver(check).observe(c, { attributes: true, attributeFilter: ["data-state"] });
  check();
}
try { if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", watchConn); else watchConn(); } catch { /* no DOM */ }
