/* RedZone Everything — phone v2 (flag phone.v2, off by default; server routes in redzone/web/phone_v2.py).
   Why each switch happened (reason chips), 👍/👎 on that one switch, "I missed that", and the Noggin
   Priority L2 sliders. When the flag is off every export here renders "" and nothing else changes. */

import { app, ACTIONS, CHANGES, INPUTS, hooks, attr, esc, num, arr } from "./rz-core.js?v=41";
import { icon, sec } from "./rz-ui.js?v=40";
import { api } from "./rz-api.js?v=40";
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
    : `Rate switches 👍/👎 on the Now screen and suggestions show up here (${num(p.votes)} so far).`}</div>`;
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
