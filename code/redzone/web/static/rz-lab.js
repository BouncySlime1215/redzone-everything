/* RedZone Everything — Lab: Screen Lab labeling, stream delay + "I just saw it", auto-tuner, backtests, votes. */

import { app, ACTIONS, hooks, attr, esc, num, get, arr, clamp, reduceMotion, SPRING, vibrate } from "./rz-core.js?v=41";
import { patch } from "./rz-dom.js?v=40";
import { icon, empty, sec, screenHead, skel, odo } from "./rz-ui.js?v=40";
import { toast } from "./rz-sheet.js?v=40";
import { api, act } from "./rz-api.js?v=43";
import { screenEvidence } from "./rz-evidence.js?v=40";
import { recordedPanel } from "./rz-recorded.js?v=40";

let loading = null;
export async function loadLab(force = false) {
  if (loading) return loading;
  if (!force && app.lab && !app.lab.error && Date.now() - (app.labAt || 0) < 60000) return app.lab;
  loading = api("/api/lab").then((d) => { app.lab = d || {}; app.labAt = Date.now(); })
    .catch((e) => { app.lab = app.lab && !app.lab.error ? app.lab : { error: e.message }; })
    .finally(() => { loading = null; hooks.renderScreen("lab"); });
  return loading;
}

const LABELS = [
  { v: "game", t: "Game", sub: "Live action", ic: "play", cls: "lb-game" },
  { v: "commercial", t: "Commercial", sub: "Ads or a break", ic: "megaphone", cls: "lb-commercial" },
  { v: "loading", t: "Loading", sub: "Spinner or black", ic: "hourglass", cls: "lb-loading" },
  { v: "error", t: "Error", sub: "Prompt or error", ic: "octagon", cls: "lb-error" },
  { v: "replay", t: "Replay", sub: "A replay is on screen", ic: "replay", cls: "lb-replay labbtn--wide" },
];

export function renderLab(root) {
  if (!app.lab) loadLab();
  const S = app.S || {};
  const pad = `<div class="card card-pad" data-key="pad"><p class="muted" style="margin:0 0 12px;font-size:13px;line-height:1.45">Tap what your TV is showing right now. These observations are saved alongside the app's prediction for accuracy checks. Use “I just saw it” below to measure stream delay.</p>
    <div class="labpad">${LABELS.map((l) => `<button class="labbtn ${l.cls}" data-act="lab-label" data-v="${l.v}">
      <span class="ic">${icon(l.ic, l.v === "game" ? "ico--fill" : "")}</span><span><b>${esc(l.t)}</b><small>${esc(l.sub)}</small></span><span class="done">${icon("check")}</span></button>`).join("")}</div></div>`;
  const delays = arr(S.delays);
  const delay = `<div class="card card-pad" data-key="delay">${delays.length ? `<div data-flip>${delays.map(delayRow).join("")}</div>`
      : `<p class="muted" style="margin:0;font-size:13px">No channels measured yet. Tap the button as a play finishes on the live broadcast to estimate your stream delay.</p>`}
    <button class="saw-btn" data-act="saw-it" ${S.recorded_playback?.active ? "disabled" : ""}>${icon("zap")}I just saw it</button>
    <p class="dim" style="margin:10px 2px 0;font-size:12.5px;line-height:1.4">Tap as the play finishes: the tackle is complete or the basket or goal goes in. Use the live broadcast, not a replay. Take several measurements; buffering can change the delay.</p></div>`;
  const f = S.feedback || get(app.lab, "feedback", {}) || {};
  const up = num(f.up), down = num(f.down);
  const votes = `<div class="card card-pad" data-key="votes"><div class="votes">
    <span class="v up">${icon("thumb-up")}${odo(up)}</span><div class="bar ${up + down ? "" : "bar--empty"}" style="--p:${up + down ? Math.round((up / (up + down)) * 100) : 50}"><i></i></div><span class="v down">${odo(down)}${icon("thumb-down")}</span></div>
    <p class="dim" style="margin:10px 0 0;font-size:12.5px">Your 👍 / 👎 on switches train the auto-tuner.</p></div>`;
  patch(root, `${screenHead("Lab", "Teach it · measure it · tune it")}
    ${recordedPanel(S, true)}
    ${sec("Screen estimate", { key: "s-evidence" })}${screenEvidence(S, "lab-evidence")}
    ${sec("Screen Lab", { key: "s-pad", aside: "What's on your TV?" })}${pad}
    ${sec("Stream delay", { key: "s-delay", aside: "How far behind live you are" })}${delay}
    ${sec("Your votes", { key: "s-votes" })}${votes}
    ${tuner()}${backtests()}`);
}

function delayRow(d) {
  const mu = num(d.mu, NaN), sg = num(d.sigma, 0);
  const pct = (x) => clamp((x / 90) * 100, 0, 100);
  const c = d.calibrated ? "var(--success)" : "var(--break)";
  return `<div class="delay-r" data-key="d:${attr(d.target)}">
    <div class="nm"><span class="hdot ${d.calibrated ? "hdot--g" : "hdot--y"}"></span><span class="ell">${esc(d.label || d.target)}</span></div>
    <div class="v">${Number.isFinite(mu) ? odo(Math.round(mu)) : "—"}<small>s ±${Math.round(sg)}</small></div>
    <div class="ruler" style="--c:${c};--lo:${pct(mu - sg)};--hi:${pct(mu + sg)};--mu:${pct(mu)}"><span class="rng"></span><span class="mk"></span></div>
    <div class="dim" style="grid-column:1/-1;font-size:11.5px;margin-top:-2px">${delayBasis(d)}${d.settled ? " · learned, no more taps needed" : ""}</div>
  </div>`;
}

/* Where a channel's delay comes from: a guess, your taps, or show changes timed automatically. */
function delayBasis(d) {
  const n = num(d.boundary_samples, 0);
  const auto = `measured automatically from show changes (${n} sample${n === 1 ? "" : "s"})`;
  if (d.source === "boundary") return `M${auto.slice(1)}${d.live ? " · timed for this tune" : ""}`;
  if (!d.calibrated) return n ? `Guess — ${auto}; needs 2 that agree` : "Guess — not measured yet";
  return d.source === "app" ? "From your taps on other channels of this app" : "Measured";
}

function tuner() {
  const lab = app.lab;
  if (!lab) return `${sec("Auto-tuner", { key: "s-tune" })}<div class="card card-pad">${skel("line", 4, 'style="margin:10px 0"')}</div>`;
  if (lab.error) return `${sec("Auto-tuner", { key: "s-tune" })}<div class="card">${empty("plays", "Lab didn't load", esc(lab.error), `<button class="btn btn--sm" data-act="lab-reload">${icon("undo")}Try again</button>`, "empty--sm", "lab-err")}</div>`;
  const opt = lab.optimize;
  if (!opt || (!opt.current && !opt.best)) return "";
  const cur = opt.current || {}, best = opt.best || {};
  const rows = [["Score", "score", 1], ["Big plays seen", "big_seen_pct", 1, "%"], ["Big plays live", "big_live_pct", 1, "%"],
    ["Modeled dead time", "dead_air_pct", -1, "%"], ["Switches / hour", "switches_per_hour", 0], ["Mid-play cuts", "mid_play_cuts", -1]];
  const body = rows.filter(([, k]) => cur[k] !== undefined || best[k] !== undefined).map(([label, k, dir, unit = ""]) => {
    const a = num(cur[k], NaN), b = num(best[k], NaN);
    const d = Number.isFinite(a) && Number.isFinite(b) ? b - a : NaN;
    const good = dir === 0 || !Number.isFinite(d) || Math.abs(d) < 1e-9 ? "" : d * dir > 0 ? "up" : "down";
    const fmt = (v) => (Number.isFinite(v) ? (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1).replace(/\.0$/, "")) + unit : "—");
    return `<div class="cmp-r"><span class="k">${esc(label)}</span><span class="a">${fmt(a)}</span><span class="b ${good}">${fmt(b)}${Number.isFinite(d) && Math.abs(d) > 1e-9 ? `<span class="d">${d > 0 ? "▲" : "▼"} ${fmt(Math.abs(d))}</span>` : ""}</span></div>`;
  }).join("");
  const pc = cur.params || {}, pb = best.params || {};
  const retired = new Set(["decider.min_dwell_s", "decider.chase_min_dwell_s", "decider.max_switches_per_5min", "decider.min_gap_s", "decider.ping_pong_freeze_s"]);
  const changed = Object.keys(pb).filter((k) => !retired.has(k) && JSON.stringify(pc[k]) !== JSON.stringify(pb[k]));
  return `${sec("Saved optimizer report", { key: "s-tune", aside: opt.slates ? `${num(opt.slates)} slates replayed` : "" })}
    <div class="card card-pad" data-key="tune"><p class="dim" style="font-size:12.5px;margin:0 0 12px">This saved comparison may predate the current engine. Results model recorded feeds; actual TV playback has not been measured.</p><div class="cmp-h"><span>Metric</span><span>Baseline</span><span>Best found</span></div><div class="cmp">${body}</div>
    ${changed.length ? `<div class="overline" style="margin-top:14px">What it would change</div><div class="param-list">${changed.map((k) =>
      `<span class="chip">${icon("wand")}<span>${paramChip(k, pc[k], pb[k])}</span></span>`).join("")}</div>` : ""}</div>`;
}

function paramChip(k, a, b) {
  const leaf = k.split(".").pop();
  const secs = /_s$/.test(leaf);
  const name = leaf.replace(/_s$/, "").replace(/_/g, " ");
  const v = (x) => (x === undefined || x === null ? "—" : `${x}${secs ? "s" : ""}`);
  return `${esc(name)} ${esc(v(a))} → <b style="color:var(--text-1)">${esc(v(b))}</b>`;
}
function backtests() {
  const bts = arr(get(app.lab, "backtests"));
  if (!app.lab || app.lab.error) return "";
  const head = sec("Backtests", { n: bts.length || "", key: "s-bt" });
  if (!bts.length) return `${head}<div class="card">${empty("timeline", "No backtests yet", "Run scripts/backtest.py on the Mac to replay a past slate.", "", "empty--sm", "bt")}</div>`;
  const nice = { mid_play_cuts: "Modeled mid-play cuts", big_plays_live: "Big plays live", your_team_big_plays_live: "Your team, live", dead_air_pct: "Modeled dead time %", switches_per_hour: "Switches / hr", switches: "Switches" };
  return head + bts.map((b, i) => `<div class="card card-pad" data-key="bt:${i}"><div style="font:800 18px/1.1 var(--font-display);letter-spacing:.04em">${esc(b.title)}</div>
    <div class="bt-tiles">${Object.entries(b.summary || {}).map(([k, v]) => {
      const cls = k === "mid_play_cuts" ? (num(v) === 0 ? "good" : "bad") : "";
      const big = String(v).match(/^(\d+\/\d+)\s*\((\d+%)\)$/);
      return `<div class="bt-tile ${cls}"><b>${esc(big ? big[2] : v)}</b><span>${esc(nice[k] || k.replace(/_/g, " "))}${big ? ` · ${esc(big[1])}` : ""}</span></div>`;
    }).join("")}</div></div>`).join("");
}

/* ---------- actions ---------- */
ACTIONS["lab-label"] = async (el, e) => {
  vibrate(22);
  if (!reduceMotion()) {
    const r = el.getBoundingClientRect();
    const rip = document.createElement("span");
    rip.className = "ripple";
    rip.style.left = `${(e && e.clientX ? e.clientX - r.left : r.width / 2)}px`;
    rip.style.top = `${(e && e.clientY ? e.clientY - r.top : r.height / 2)}px`;
    el.appendChild(rip);
    rip.animate([{ transform: "scale(1)", opacity: 0.55 }, { transform: `scale(${Math.max(r.width, r.height) / 7})`, opacity: 0 }], { duration: 650, easing: "cubic-bezier(.22,1,.36,1)" }).onfinish = () => rip.remove();
  }
  const r = await api("/api/action", { body: { action: "label", value: el.dataset.v } }).catch((err) => ({ ok: false, message: err.message }));
  if (r && r.ok === false) { toast(r.message || "Not logged", "error"); return; }
  const done = el.querySelector(".done");
  if (done && !reduceMotion()) done.animate([{ opacity: 0, transform: "scale(.3)" }, { opacity: 1, transform: "scale(1.15)", offset: 0.5 }, { opacity: 1, transform: "scale(1)", offset: 0.8 }, { opacity: 0, transform: "scale(1)" }], { duration: 1300, easing: SPRING });
  toast(`Logged: ${el.dataset.v}`, "ok");
};
ACTIONS["saw-it"] = async (el) => {
  vibrate(30);
  if (!reduceMotion()) el.animate([{ transform: "scale(1)" }, { transform: "scale(.94)", offset: 0.3 }, { transform: "scale(1)" }], { duration: 420, easing: SPRING });
  await act("saw_it", {}, el);
};
ACTIONS["lab-reload"] = () => { app.lab = null; loadLab(true); };
