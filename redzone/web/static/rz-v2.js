/* RedZone Everything — phone v2 (flag phone.v2, off by default; server routes in redzone/web/phone_v2.py).
   Why each switch happened (reason chips), 👍/👎 on that one switch, "I missed that", and the Noggin
   Priority L2 sliders. When the flag is off every export here renders "" and nothing else changes. */

import { app, ACTIONS, CHANGES, INPUTS, hooks, attr, esc, num, arr, fmtTime, fmtDur } from "./rz-core.js?v=41";
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
  return `<div class="v2-bar" data-key="v2-bar"><button class="btn v2-missed" data-act="v2-missed">${icon("eye")}I missed that</button>
    <button class="btn btn--ghost" data-act="v2-night">${icon("replay")}Whole night</button></div>`;
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

/* ---------- Night replay viewer: what was on, every switch, hold and tap, with playbook ids ---------- */
const PAL = 8;   // segment colors cycle through .v2-c0 .. .v2-c7 (app.css)
const night = { day: "", data: null, loading: false, filter: "all" };

function colorOf(matchup, order) {
  let i = order.indexOf(matchup);
  if (i < 0) { order.push(matchup); i = order.length - 1; }
  return i % PAL;
}

async function loadNight(day = "") {
  night.loading = true;
  try { night.data = await api(`/api/v2/night${day ? `?day=${encodeURIComponent(day)}` : ""}`); night.day = night.data.day || day; }
  catch (e) { night.data = { error: e.message }; }
  finally { night.loading = false; const s = sheetById("v2-night"); if (s) s.update(); }
}

const EV_IC = { switch: "swap", decision: "hourglass", tap: "thumb-up" };
const TAP_IC = { up: "thumb-up", down: "thumb-down", missed: "eye", clear: "x" };
const TAP_TXT = { up: "You rated it good", down: "You rated it bad", missed: "You missed a play", clear: "Rating cleared" };

function nightSheet() {
  const d = night.data;
  if (!d) return `<h3>The whole night</h3><p class="lead">Loading…</p>`;
  if (d.error) return `<h3>The whole night</h3><p class="lead">${esc(d.error)}</p>`;
  const sm = d.summary || {};
  const taps = sm.taps || {};
  const order = arr(sm.on_air).map((r) => r.matchup);
  const segs = arr(d.segments);
  const days = arr(d.nights).map((x) => `<button class="v2-day" data-act="v2-night-day" data-day="${attr(x)}" aria-pressed="${x === d.day}">${esc(
    new Date(x + "T12:00:00").toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" }))}</button>`).join("");
  let ribbon = `<div class="t2" style="margin:10px 0">No switches this night.</div>`;
  if (segs.length) {
    const a = segs[0].t0, b = Math.max(segs[segs.length - 1].t1, a + 60), span = b - a;
    const ticks = [];
    const first = Math.ceil(a / 3600) * 3600;
    for (let t = first; t < b; t += 3600) ticks.push(`<i style="left:${(((t - a) / span) * 100).toFixed(2)}%"><span>${esc(fmtTime(t).replace(":00", ""))}</span></i>`);
    ribbon = `<div class="v2-ribbon" role="img" aria-label="What was on the TV, ${esc(fmtTime(a))} to ${esc(fmtTime(b))}">${segs.map((sg) =>
      `<b class="v2-c${colorOf(sg.matchup, order)}" style="left:${(((sg.t0 - a) / span) * 100).toFixed(2)}%;width:${Math.max(0.4, ((sg.t1 - sg.t0) / span) * 100).toFixed(2)}%" title="${attr(sg.matchup)} ${attr(fmtTime(sg.t0))}"></b>`).join("")}</div>
      <div class="v2-ticks">${ticks.join("")}</div>
      <div class="v2-legend">${arr(sm.on_air).map((r) => `<span><i class="v2-c${colorOf(r.matchup, order)}"></i>${esc(r.matchup)} <small>${esc(fmtDur(r.s))}</small></span>`).join("")}</div>`;
  }
  const pb = Object.entries(sm.playbooks || {});
  const F = [["all", "All"], ["switch", "Switches"], ["decision", "Holds"], ["tap", "Your taps"]];
  const evs = arr(d.events).filter((e) => night.filter === "all" || e.type === night.filter).slice().reverse();
  const row = (e) => {
    const ic = e.type === "tap" ? TAP_IC[e.kind] || "thumb-up" : EV_IC[e.type] || "tv";
    const title = e.type === "tap" ? TAP_TXT[e.kind] || "Tap" : e.type === "decision" ? `${e.kind === "wait" ? "Waited" : "Held"}${e.matchup ? " on " + e.matchup : ""}` : e.matchup;
    const tags = [
      ...arr(e.chips).map((c) => `<span class="v2-chip v2-chip--${attr(c.key)}">${esc(c.label)}</span>`),
      ...arr(e.playbooks).map((p) => `<span class="tag tag--kind">${esc(p)}</span>`),
      e.type === "switch" && e.dry_run ? `<span class="tag tag--would">Would switch</span>` : "",
      e.type === "switch" && !e.ok && !e.dry_run ? `<span class="tag tag--fail">Failed</span>` : "",
    ].join("");
    return `<div class="v2-ev v2-ev--${attr(e.type)}"><span class="v2-ev-ic">${icon(ic)}</span><div style="min-width:0">
      <div class="v2-ev-top"><span class="m ell">${esc(title)}</span><span class="t">${esc(fmtTime(e.ts))}</span></div>
      ${e.reason ? `<div class="v2-ev-why">${esc(String(e.reason).replace(/\s*playbook=\S+/g, ""))}</div>` : ""}${tags ? `<div class="v2-why">${tags}</div>` : ""}</div></div>`;
  };
  return `<h3>The whole night</h3>
    <div class="v2-days">${days}</div>
    <div class="v2-tiles"><div class="stile"><b>${num(sm.switches)}</b><span>Switches</span></div>
      <div class="stile"><b>${num(sm.games)}</b><span>Games shown</span></div>
      <div class="stile"><b>${num(taps.up)}/${num(taps.down)}</b><span>👍 / 👎</span></div>
      <div class="stile"><b>${num(taps.missed)}</b><span>Missed</span></div></div>
    <div class="sheet-sec"><h4>What was on</h4>${sm.failed ? `<small>${num(sm.failed)} failed</small>` : ""}</div>${ribbon}
    ${pb.length ? `<div class="sheet-sec"><h4>Playbooks used</h4></div><div class="v2-why">${pb.map(([k, n]) => `<span class="tag tag--kind">${esc(k)} × ${num(n)}</span>`).join("")}</div>` : ""}
    <div class="sheet-sec"><h4>Timeline</h4><small>newest first</small></div>
    <div class="v2-days">${F.map(([k, l]) => `<button class="v2-day" data-act="v2-night-filter" data-f="${k}" aria-pressed="${night.filter === k}">${l}</button>`).join("")}</div>
    <div class="v2-evs">${evs.length ? evs.map(row).join("") : `<div class="t2">Nothing here.</div>`}</div>`;
}

ACTIONS["v2-night"] = () => { night.data = null; openSheet({ id: "v2-night", label: "The whole night", full: true, render: nightSheet }); loadNight(""); };
ACTIONS["v2-night-day"] = (el) => { night.data = null; const s = sheetById("v2-night"); if (s) s.update(); loadNight(el.dataset.day); };
ACTIONS["v2-night-filter"] = (el) => { night.filter = el.dataset.f; const s = sheetById("v2-night"); if (s) s.update(); };
