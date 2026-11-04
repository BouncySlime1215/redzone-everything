/* RedZone Everything — Settings: TV control (dry run), microphone, announcer, teams, leagues, fantasy,
   how it picks (sliders + every weight), YouTube TV + Sunday Ticket channels, health. */

import {
  app, ACTIONS, CHANGES, INPUTS, hooks, store, attr, esc, num, get, arr, LEAGUES, LEAGUE_NAME, gameById, matchupWords, vibrate,
} from "./rz-core.js?v=41";
import { patch } from "./rz-dom.js?v=40";
import { icon, logo, leagueImg, empty, sec, screenHead, skel } from "./rz-ui.js?v=40";
import { confirmSheet, toast } from "./rz-sheet.js?v=40";
import { api, act } from "./rz-api.js?v=42";
import { loadTeams, teamList } from "./rz-detail.js?v=42";
import { showTeams } from "./rz-games.js?v=41";
import { prioritySection } from "./rz-v2.js?v=1";

const ui = app.ui;
ui.pending = ui.pending || {};
ui.announcer = store.get("announcer", false) === true;

let loading = null;
export function loadSettings(force = false) {
  if (loading) return loading;
  if (app.settings && !app.settings.error && !force) return Promise.resolve(app.settings);
  loading = Promise.all([api("/api/settings"), api("/api/channels").catch(() => null)])
    .then(([s, ch]) => { app.settings = s || {}; if (ch) app.channels = ch; ui.pending = {}; })
    .catch((e) => { app.settings = { error: e.message }; })
    .finally(() => { loading = null; hooks.renderScreen("settings"); });
  return loading;
}

const cfg = (path) => (path in ui.pending ? ui.pending[path] : get(app.settings && app.settings.config, path));
function limitFor(path) {
  const L = (app.settings && app.settings.limits) || {};
  let best = null;
  for (const k of Object.keys(L)) if ((path === k || path.startsWith(k + ".")) && (!best || k.length > best.length)) best = k;
  return best ? L[best] : null;
}
const SIMPLE = [
  ["score.personal.my_team", "Your-team boost", "How far your teams jump the line", 0, 60, 1],
  ["score.personal.bet_max", "Bet boost", "Extra priority while a bet is sweating", 0, 60, 1],
  ["decider.switch_margin", "Switch margin", "How much better a game must be to switch", 0, 30, 1],
];
const RETIRED_LIMITS = new Set(["decider.min_dwell_s", "decider.chase_min_dwell_s", "decider.max_switches_per_5min", "decider.min_gap_s", "decider.ping_pong_freeze_s"]);

export function renderSettings(root) {
  if (!app.settings) loadSettings();
  if (!app.teams) loadTeams();
  const S = app.S || {};
  const m = S.mode || {};
  const dry = m.dry_run !== false;
  const head = screenHead("Settings", "Everything saves to the Mac");
  const tv = `${sec("TV control", { key: "s-tv" })}<div class="card set" data-key="tv" id="tv-control">
    <div class="set-r"><span class="ic ic--red">${icon("power")}</span><div class="grow"><div class="t1">Control the TV</div>
      <div class="t2">${dry ? "Dry run: it shows what it <i>would</i> do. The TV isn't touched." : "Live: it changes channels on your Fire TV by itself."}</div></div>
      <label class="switch switch--danger"><input type="checkbox" data-change="tv-control" ${dry ? "" : "checked"} aria-label="Control the TV"><span></span></label></div>
    <div class="set-r"><span class="ic">${icon("tv")}</span><div class="grow"><div class="t1">Status</div><div class="t2">${!arr(get(S, "health.sticks")).length ? "No Fire Stick configured yet. Finish the device setup first." : m.two_stick ? "Two sticks: the hidden one pre-tunes the next game for instant flips." : "One stick configured."}${m.tv_control ? " Connected to the TV." : ""}</div></div>
      <span class="tv-state ${dry ? "tv-state--off" : "tv-state--on"}"><i></i>${dry ? "Dry run" : "On air"}</span></div>
    <div class="set-r"><span class="ic ic--purple">${icon("mic")}</span><div class="grow"><div class="t1">Microphone calibration</div>
      <div class="t2">Crowd noise can suggest a delay for you to check. Audio isn't saved.</div></div>
      <label class="switch"><input type="checkbox" data-change="mic" ${m.mic ? "checked" : ""} aria-label="Microphone calibration"><span></span></label></div>
    <div class="set-r"><span class="ic">${icon("sound")}</span><div class="grow"><div class="t1">Announcer</div>
      <div class="t2">A short spoken callout on this phone whenever the TV switches.</div></div>
      ${ui.announcer ? `<button class="btn btn--sm btn--good" data-act="announcer-test">${icon("sound")}Test</button><button class="btn btn--sm btn--ghost" data-act="announcer-off" aria-label="Turn announcer off">Off</button>`
        : `<button class="btn btn--sm" data-act="announcer-on">Turn on</button>`}</div>
  </div>`;

  if (!app.settings || app.settings.error) {
    const body = !app.settings ? `<div class="card card-pad">${skel("line", 6, 'style="margin:12px 0"')}</div>`
      : `<div class="card">${empty("plays", "Settings didn't load", esc(app.settings.error), `<button class="btn btn--sm" data-act="settings-reload">${icon("undo")}Try again</button>`, "empty--sm", "set-err")}</div>`;
    patch(root, head + tv + sec("Preferences", { key: "s-pref" }) + body + healthSec(S));
    return;
  }

  const teams = `${sec("Your teams", { key: "s-teams", aside: "Biggest boost" })}<div class="card card-pad" data-key="teams"><div class="my-team-rows">${LEAGUES.map((lg) => {
    const list = teamList(lg);
    const mine = list.length ? list.filter((t) => t.mine) : arr(cfg(`teams.${lg}`)).map((abbr) => ({ abbr, color: "" }));
    return `<div class="mtr">${leagueImg(lg)}<span class="lbl">${LEAGUE_NAME[lg]}</span><div class="chips">${mine.map((t) => `<span class="tteam">${logo(t, { size: 24 })}${esc(t.abbr)}</span>`).join("") || `<span class="dim" style="font-size:13px">None</span>`}</div>
      <button class="btn btn--sm btn--ghost" data-act="edit-teams" data-lg="${lg}" aria-label="Edit ${LEAGUE_NAME[lg]} teams">Edit</button></div>`;
  }).join("")}</div></div>`;

  const leagues = `${sec("Leagues", { key: "s-lg" })}<div class="card set" data-key="leagues">${LEAGUES.map((lg) => `<div class="set-r">${leagueImg(lg)}<div class="grow"><div class="t1">${LEAGUE_NAME[lg]}</div></div>
    <label class="switch"><input type="checkbox" data-change="league-toggle" data-lg="${lg}" ${cfg(`mode.leagues.${lg}`) === false ? "" : "checked"} aria-label="${LEAGUE_NAME[lg]}"><span></span></label></div>`).join("")}</div>`;

  const f = S.fantasy;
  const fan = `${sec("Fantasy", { key: "s-fan" })}<div class="card card-pad" data-key="fantasy" id="fantasy">
    <div class="row" style="gap:10px;margin-bottom:12px">${f && !f.error ? `<span class="hdot hdot--g"></span><span style="font-size:13.5px">Connected: <b>${esc(f.me)}</b> vs ${esc(f.opponent)}</span>`
      : f && f.error ? `<span class="hdot hdot--y"></span><span style="font-size:13.5px">${esc(f.error)}</span>` : `<span class="hdot"></span><span class="muted" style="font-size:13.5px">Not set up. Your starters get a boost when they're on the field.</span>`}</div>
    <label class="overline" for="fan-league" style="display:block;margin-bottom:6px">League ID</label>
    <input id="fan-league" class="input" inputmode="numeric" placeholder="e.g. 1234567" value="${attr(cfg("fantasy.league_id") ?? "")}" data-input="fan" autocomplete="off">
    <label class="overline" for="fan-team" style="display:block;margin:12px 0 6px">Team number <span style="text-transform:none;letter-spacing:0;font:500 12px var(--font-ui)">(0 = figure it out)</span></label>
    <input id="fan-team" class="input" inputmode="numeric" placeholder="0" value="${attr(cfg("fantasy.team_id") ?? 0)}" data-input="fan" autocomplete="off">
    <button class="btn btn--primary btn--block" style="margin-top:14px" data-act="fan-save">Save fantasy league</button></div>`;

  const sliders = `${sec("How it picks", { key: "s-pick" })}<div class="card set" data-key="pick">${SIMPLE.map(([path, label, help, lo, hi, step]) => {
    const lim = limitFor(path);
    const min = lim ? Math.max(lo, lim[0]) : lo, max = lim ? Math.min(hi, lim[1]) : hi;
    const v = num(cfg(path), min);
    const p = ((v - min) / Math.max(1, max - min)) * 100;
    return `<div class="slider"><div class="slider-top"><span class="t1">${esc(label)}</span><span class="val tnum" data-val-for="${attr(path)}">${esc(v)}</span></div>
      <div class="t2">${esc(help)}</div>
      <input class="range" type="range" min="${min}" max="${max}" step="${step}" value="${v}" style="--p:${p.toFixed(1)}" data-sync data-input="slider" data-path="${attr(path)}" aria-label="${attr(label)}"></div>`;
  }).join("")}
    <details class="adv"${ui.advOpen ? " open" : ""} data-keep-open><summary data-act="adv-toggle">Advanced — every weight ${icon("chevron-down")}</summary>${advanced()}</details></div>`;

  const n = Object.keys(ui.pending).length;
  const save = n ? `<div class="savebar" data-key="savebar"><div class="savebar-in"><span class="t">${n} unsaved change${n === 1 ? "" : "s"}</span>
    <button class="btn btn--sm btn--ghost" data-act="settings-discard">Discard</button><button class="btn btn--sm btn--primary" data-act="settings-save">Save</button></div></div>` : "";

  patch(root, head + tv + teams + leagues + fan + sliders + prioritySection()
    + `<button class="btn btn--ghost btn--block" style="margin-top:10px" data-act="settings-undo" data-key="undo">${icon("undo")}Undo last save</button>`
    + channels() + sundayTicket() + healthSec(S)
    + `<div class="foot-note" data-key="foot">RedZone Everything · home network only</div>` + save);
}

function advanced() {
  const c = (app.settings && app.settings.config) || {};
  return ["score", "decider", "replay", "delay", "break_lengths_s", "polling"].filter((g) => c[g]).map((g) => {
    const leaves = [];
    (function walk(o, p) {
      for (const [k, v] of Object.entries(o || {})) {
        const path = p ? `${p}.${k}` : k;
        if (RETIRED_LIMITS.has(path)) continue;
        if (v && typeof v === "object" && !Array.isArray(v)) walk(v, path);
        else if (typeof v === "number") leaves.push(path);
      }
    })(c[g], g);
    return `<div class="adv-g"><h5>${esc(g.replace(/_/g, " "))}</h5>${leaves.map((path) => {
      const lim = limitFor(path);
      return `<div class="adv-r"><label for="adv-${attr(path)}">${esc(path.slice(g.length + 1).replace(/_/g, " ").replace(/\./g, " › "))}</label>
        <input id="adv-${attr(path)}" class="${path in ui.pending ? "changed" : ""}" inputmode="decimal" value="${attr(cfg(path))}" data-sync data-change="adv" data-path="${attr(path)}" ${lim ? `data-min="${lim[0]}" data-max="${lim[1]}"` : ""}></div>`;
    }).join("")}</div>`;
  }).join("");
}

function channels() {
  const chs = arr(get(app.channels, "channels"));
  const set = chs.filter((c) => c.code).length;
  return `${sec("YouTube TV channels", { key: "s-ch", aside: chs.length ? `${set} of ${chs.length} linked` : "" })}<div class="card card-pad" data-key="channels">
    <div class="howto"><b>${icon("info", "ico--sm")}Automatic channel checks</b><p>Once a stick is connected, channels are checked when the TV can be left undisturbed. Unconfirmed channels stay unlinked. You can add or correct a link below.</p><details><summary>Add a link manually</summary><ol><li>On a computer, open tv.youtube.com → Live.</li><li>Right-click a channel → Copy link.</li><li>Paste it next to that channel here.</li></ol></details></div>
    ${chs.length ? `<div class="chan-list">${chs.map((c) => `<div class="chan" data-key="ch:${attr(c.key)}"><div class="chan-top"><span class="chan-ic ${c.code ? "on" : ""}">${icon("tv")}</span><b>${esc(c.label || c.key)}</b>
        <span class="chan-st ${c.code ? "chan-st--ok" : "chan-st--no"}">${c.code ? `${icon("check", "ico--xs")}Linked` : "Not set"}</span>
        <button class="btn btn--sm ${c.code ? "btn--ghost" : ""}" data-act="chan-open" data-key="${attr(c.key)}" aria-expanded="${ui.chanOpen === c.key}">${ui.chanOpen === c.key ? "Cancel" : c.code ? "Replace" : "Add link"}</button></div>
      ${ui.chanOpen === c.key ? `<div class="chan-paste"><input class="input" data-chan="${attr(c.key)}" placeholder="tv.youtube.com/watch/…" autocomplete="off" autocapitalize="off" spellcheck="false">
      <button class="btn btn--sm btn--primary" data-act="chan-save" data-key="${attr(c.key)}">Save</button></div>` : ""}</div>`).join("")}</div>` : `<p class="dim" style="font-size:13px;margin:12px 0 0">No channels listed.</p>`}</div>`;
}

function sundayTicket() {
  return `${sec("Sunday Ticket", { key: "s-st", aside: "16 game channels" })}<div class="card card-pad" data-key="st">
    <label class="overline" for="st-links" style="display:block;margin-bottom:8px">Paste all 16 Sunday Ticket channel links</label>
    <textarea id="st-links" class="input" rows="4" style="height:auto;padding:12px 14px;resize:vertical;font-size:14px" placeholder="One link per line — tv.youtube.com/watch/…" autocapitalize="off" spellcheck="false"></textarea>
    <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:12px">
      <button class="btn" data-act="st-save">${icon("link")}Save links</button>
      <button class="btn" data-act="st-scan">${icon("wand")}Scan now</button></div>
    <p class="dim" style="margin:10px 2px 0;font-size:12.5px;line-height:1.4">Scanning flips through the Sunday Ticket channels on the TV to learn which game is on which.</p></div>`;
}

const SRC_NAME = { espn_scoreboard: "ESPN scoreboard", espn_summary: "ESPN play-by-play", nhl: "NHL feed", nba: "NBA feed", espn_fastcast: "ESPN push" };
const SWITCH_NAME = { youtubetv: "YouTube TV", prime: "Prime", espn: "ESPN", flip: "input flip", seek: "rewind" };
function healthSec(S) {
  const h = S.health || {};
  const rows = [];
  for (const s of arr(h.sources)) {
    const cls = s.benched ? "r" : !s.ok ? "y" : num(s.age_s, 999) > 60 ? "y" : "g";
    const nm = SRC_NAME[s.name] || String(s.name || "").replace(/^espn[:_]/i, "ESPN ").replace(/_/g, " ");
    rows.push(`<div class="health-r"><span class="hdot hdot--${cls}"></span><div class="grow"><div class="t1">${esc(nm)}</div>${s.benched ? `<div class="t2" style="color:var(--danger-ink)">Benched after errors</div>` : ""}</div>
      <span class="meta">${s.age_s != null ? `${Math.round(s.age_s)}s ago` : "—"}${num(s.errors) ? ` · ${num(s.errors)} err` : ""}</span></div>`);
  }
  for (const st of arr(h.sticks)) {
    const cls = st.state === "connected" ? "g" : st.state === "unauthorized" ? "y" : "r";
    rows.push(`<div class="health-r"><span class="hdot hdot--${cls}"></span><div class="grow"><div class="t1">${esc(st.name || st.host)}${st.role ? ` <span class="dim" style="font-weight:500">· ${esc(st.role)}</span>` : ""}</div><div class="t2">${esc(st.os, "")}${st.host ? ` · ${esc(st.host)}` : ""}</div></div>
      <span class="meta">${esc(st.state === "unauthorized" ? "Approve on TV" : st.state)}</span></div>`);
  }
  if (h.tv) {
    const ok = ["on", "connected", "ok"].includes(String(h.tv.state || "").toLowerCase());
    rows.push(`<div class="health-r"><span class="hdot ${ok ? "hdot--g" : h.tv.state && h.tv.state !== "unknown" ? "hdot--y" : ""}"></span><div class="grow"><div class="t1">TV</div></div><span class="meta">${esc(h.tv.state)}</span></div>`);
  }
  if (h.adb !== undefined) rows.push(`<div class="health-r"><span class="hdot ${h.adb ? "hdot--g" : "hdot--r"}"></span><div class="grow"><div class="t1">ADB (stick control)</div></div><span class="meta">${h.adb ? "ready" : "missing"}</span></div>`);
  const race = Object.entries(h.race || {});
  if (race.length) {
    rows.push(`<div class="health-r"><span class="hdot"></span><div class="grow"><div class="t1">Fastest feed</div><div class="t2">${race.sort((a, b) => num(get(b[1], "first")) - num(get(a[1], "first")))
      .map(([k, v]) => `${esc(SRC_NAME[k] || k)}: first ${num(get(v, "first"))}× · +${num(get(v, "avg_lag_s")).toFixed(1)}s`).join("<br>")}</div></div></div>`);
  }
  // Measured switch times (median of the last 15 real ones): what the decider plans with.
  const sw = h.switch_times || {};
  const meas = Object.entries(sw.measured || {}).filter(([, v]) => num(get(v, "n")) > 0);
  if (meas.length) {
    rows.push(`<div class="health-r"><span class="hdot hdot--g"></span><div class="grow"><div class="t1">Switch speed</div><div class="t2">${meas
      .map(([k, v]) => `${esc(SWITCH_NAME[k] || k)} ${num(get(v, "median_s")).toFixed(1)}s`).join(" · ")}</div></div>
      <span class="meta">${sw.path === "flip" ? "two sticks" : "one stick"}</span></div>`);
  }
  const discovery = h.discovery || {};
  for (const step of arr(discovery.setup)) {
    rows.push(`<div class="health-r"><span class="hdot hdot--y"></span><div class="grow"><div class="t1">Device setup</div><div class="t2">${esc(step)}</div></div></div>`);
  }
  if (discovery.jobs) {
    const jobs = arr(discovery.jobs);
    rows.push(`<div class="health-r"><span class="hdot ${discovery.dry_run ? "" : "hdot--g"}"></span><div class="grow"><div class="t1">Automatic maintenance</div><div class="t2">${discovery.dry_run ? "Device checks wait while dry run is on." : "Channel verification runs only when it can leave the TV undisturbed."}</div><details style="margin-top:8px"><summary>View checks and streaming apps</summary><div class="t2" style="margin-top:8px">${jobs.map(j => `<div>${esc(j.label)}: ${esc(j.running ? "running" : j.message)}</div>`).join("")}${arr(discovery.providers).map(p => `<div>${esc(SWITCH_NAME[p.key] || p.key)}: ${esc(p.why || (p.installed == null ? "not checked on a stick yet" : "installed; access verified when tuned"))}</div>`).join("")}</div></details></div></div>`);
  }
  return `${sec("Health", { key: "s-health" })}<div class="card set" data-key="health">${rows.join("") || `<div class="health-r"><span class="hdot"></span><span class="muted" style="font-size:13px">Waiting for the first health report…</span></div>`}</div>`;
}

/* ---------- pending changes ---------- */
function setPending(path, value) {
  const orig = get(app.settings && app.settings.config, path);
  if (JSON.stringify(orig) === JSON.stringify(value)) delete ui.pending[path];
  else ui.pending[path] = value;
}
function nested() {
  const out = {};
  for (const [path, v] of Object.entries(ui.pending)) {
    const parts = path.split(".");
    let node = out;
    parts.slice(0, -1).forEach((p) => { node = node[p] = node[p] || {}; });
    node[parts[parts.length - 1]] = v;
  }
  return out;
}
const rerender = () => hooks.renderScreen("settings");

/* ---------- handlers ---------- */
CHANGES["tv-control"] = async (el) => {
  const wantTv = el.checked;
  if (wantTv) {
    const ok = await confirmSheet({ title: "Let it control your TV?", body: "It will change channels on your Fire TV by itself — never mid-play. You can switch back to dry run any time.", ok: "Control my TV", ic: "power" });
    if (!ok) { el.checked = false; return; }
  }
  const r = await act("dry_run", { value: !wantTv });
  if (!r || r.ok === false) el.checked = !wantTv;
};
CHANGES["mic"] = async (el) => { const r = await act("mic", { value: el.checked }); if (!r || r.ok === false) el.checked = !el.checked; };
CHANGES["league-toggle"] = (el) => { setPending(`mode.leagues.${el.dataset.lg}`, el.checked); rerender(); };
CHANGES["adv"] = (el) => {
  const v = parseFloat(el.value);
  const path = el.dataset.path;
  if (!Number.isFinite(v)) { toast("That needs to be a number", "warn"); el.value = cfg(path); return; }
  const lo = el.dataset.min !== undefined ? +el.dataset.min : -Infinity, hi = el.dataset.max !== undefined ? +el.dataset.max : Infinity;
  if (v < lo || v > hi) { toast(`Keep it between ${lo} and ${hi}`, "warn"); el.value = cfg(path); return; }
  setPending(path, v); rerender();
};
INPUTS["slider"] = (el) => {
  const v = Number(el.value);
  const lbl = document.querySelector(`[data-val-for="${CSS.escape(el.dataset.path)}"]`);
  if (lbl) lbl.textContent = v;
  el.style.setProperty("--p", (((v - +el.min) / Math.max(1, +el.max - +el.min)) * 100).toFixed(1));
  setPending(el.dataset.path, v);
  clearTimeout(INPUTS.slider.t);
  INPUTS.slider.t = setTimeout(rerender, 250);
};
INPUTS["fan"] = () => { /* read on save */ };
ACTIONS["adv-toggle"] = (el) => { setTimeout(() => { ui.advOpen = !!el.closest("details").open; }, 0); };
ACTIONS["settings-save"] = async (el) => {
  if (!Object.keys(ui.pending).length) return;
  el.setAttribute("aria-busy", "true");
  try {
    const r = await api("/api/settings", { body: { changes: nested() } });
    if (r && r.ok === false) { toast(r.message || "Not saved", "warn"); return; }
    toast((r && r.message) || "Saved", "ok"); vibrate(15);
    app.settings = null; ui.pending = {};
    await loadSettings(true);
  } catch (e) { toast(e.message, "error"); }
  finally { el.removeAttribute("aria-busy"); }
};
ACTIONS["settings-discard"] = () => { ui.pending = {}; rerender(); toast("Changes discarded"); };
ACTIONS["settings-undo"] = async () => {
  const ok = await confirmSheet({ title: "Undo your last save?", body: "Settings go back to how they were before it.", ok: "Undo", ic: "undo" });
  if (!ok) return;
  try { const r = await api("/api/settings/rollback", { body: {} }); toast((r && r.message) || "Undone", r && r.ok === false ? "warn" : "ok"); app.settings = null; loadSettings(true); }
  catch (e) { toast(e.message, "error"); }
};
ACTIONS["settings-reload"] = () => { app.settings = null; loadSettings(true); };
ACTIONS["edit-teams"] = (el) => { showTeams(el.dataset.lg); hooks.go("games"); };
ACTIONS["go-fantasy"] = () => { hooks.go("settings"); setTimeout(() => { const f = document.getElementById("fantasy"); if (f) f.scrollIntoView({ behavior: "smooth", block: "center" }); }, 350); };
ACTIONS["go-priorities"] = () => { hooks.go("settings"); setTimeout(() => { const f = document.querySelector('[data-key="pick"]'); if (f) f.scrollIntoView({ behavior: "smooth", block: "start" }); }, 350); };
ACTIONS["go-tv"] = () => { hooks.go("settings"); setTimeout(() => { const f = document.getElementById("tv-control"); if (f) f.scrollIntoView({ behavior: "smooth", block: "start" }); }, 350); };
ACTIONS["fan-save"] = async (el) => {
  const league = (document.getElementById("fan-league") || {}).value || "";
  const team = parseInt((document.getElementById("fan-team") || {}).value || "0", 10);
  el.setAttribute("aria-busy", "true");
  try {
    const r = await api("/api/settings", { body: { changes: { fantasy: { league_id: league.trim(), team_id: Number.isFinite(team) ? team : 0 } } } });
    toast((r && r.message) || "Saved", r && r.ok === false ? "warn" : "ok");
    if (!r || r.ok !== false) { app.settings = null; loadSettings(true); }
  } catch (e) { toast(e.message, "error"); }
  finally { el.removeAttribute("aria-busy"); }
};
ACTIONS["chan-save"] = async (el) => {
  const key = el.dataset.key;
  const inp = document.querySelector(`[data-chan="${CSS.escape(key)}"]`);
  const link = inp ? inp.value.trim() : "";
  if (!link) { toast("Paste the channel link first", "warn"); return; }
  el.setAttribute("aria-busy", "true");
  try {
    const r = await api("/api/channels", { body: { channel: key, link } });
    toast((r && r.message) || "Saved", r && r.ok === false ? "warn" : "ok");
    if (!r || r.ok !== false) { if (inp) inp.value = ""; ui.chanOpen = null; app.channels = await api("/api/channels").catch(() => app.channels); rerender(); }
  } catch (e) { toast(e.message, "error"); }
  finally { el.removeAttribute("aria-busy"); }
};
ACTIONS["st-save"] = async (el) => {
  const ta = document.getElementById("st-links");
  const text = ta ? ta.value.trim() : "";
  if (!text) { toast("Paste the Sunday Ticket links first", "warn"); return; }
  el.setAttribute("aria-busy", "true");
  try {
    const r = await api("/api/channels/sunday-ticket", { body: { links: text } });
    toast((r && r.message) || "Saved", r && r.ok === false ? "warn" : "ok");
    if (r && r.ok !== false && ta) ta.value = "";
  } catch (e) { toast(e.message, "error"); }
  finally { el.removeAttribute("aria-busy"); }
};
ACTIONS["st-scan"] = (el) => act("scan_st", {}, el);
ACTIONS["chan-open"] = (el) => {
  ui.chanOpen = ui.chanOpen === el.dataset.key ? null : el.dataset.key;
  rerender();
  if (ui.chanOpen) setTimeout(() => { const i = document.querySelector(`[data-chan="${CSS.escape(ui.chanOpen)}"]`); if (i) i.focus(); }, 30);
};

/* ---------- announcer: short ORIGINAL callouts in a generic system voice ---------- */
const SPEAK = [
  [/\s*·\s*/g, ", "], [/&/g, " and "], [/\bQ([1-4])\b/g, (m, q) => ["", "the first", "the second", "the third", "the fourth"][q] + " quarter"],
  [/\bOT\b/g, "overtime"], [/\b(\d+):(\d\d)\b/g, (m, a, b) => (a === "0" ? `${Number(b)} seconds` : `${a} ${b}`)],
  [/\bopp\b/gi, "the opponent's"], [/\bML\b/g, "moneyline"], [/\bvs\b/g, "versus"], [/—/g, ". "], [/\s@\s/g, " at "],
];
export const speakable = (t) => SPEAK.reduce((s, [re, rep]) => s.replace(re, rep), String(t || ""));
export function say(text) {
  if (!("speechSynthesis" in window)) return;
  try {
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.04; u.pitch = 1; u.volume = 1; u.lang = "en-US";
    speechSynthesis.cancel();
    speechSynthesis.speak(u);
  } catch { /* no voice */ }
}
export function announce(n) {
  if (!ui.announcer || !n) return;
  const g = gameById(n.game_id) || n;
  const lead = n.kind === "replay" ? "Catch-up replay." : `Now on: ${matchupWords(g) || n.matchup || "a new game"}.`;
  say(`${lead} ${speakable(String(n.reason || "").replace(/^garbage time\s*[—–-]\s*/i, ""))}`);
}
ACTIONS["announcer-on"] = () => { ui.announcer = true; store.set("announcer", true); say("Announcer on. I'll call out every switch."); rerender(); };
ACTIONS["announcer-off"] = () => { ui.announcer = false; store.set("announcer", false); try { speechSynthesis.cancel(); } catch { /* none */ } rerender(); };
ACTIONS["announcer-test"] = () => { const n = get(app.S, "now"); if (n) announce(n); else say("Announcer is on. Nothing is live yet."); };
