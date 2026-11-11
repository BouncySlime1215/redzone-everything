/* RedZone Everything — Show view (server flag show.ui, default off; ?mock=1&show previews it).
   Reads GET /api/show and /api/show/events (redzone/web/show.py): what the new show has on the TV, its next
   move, pace, sound and Rule #1 counters. Read-only: nothing here sends a command.
   Loaded only when the flag is on (the server adds the script tag), so with the flag off the page is unchanged.
   Calm by design: nothing blinks, every number has a dash when unknown, a stale file says "Not updating". */

import { app, ACTIONS, hooks, attr, esc, num, get, arr, clamp, fmtTime } from "./rz-core.js?v=41";
import { patch } from "./rz-dom.js?v=40";
import { icon, sec, screenHead, empty, logo, odo, skel } from "./rz-ui.js?v=40";
import { api } from "./rz-api.js?v=44";

const STALE_AFTER = 5;          // seconds without a new state.json before it reads "Not updating"
const GONE_AFTER = 600;
// The runner reads mute and volume back from the TV every few minutes (its guard runs every 5): a read this
// recent is "checked"; older is "getting old"; older still is an alarm.
const SOUND_OK_WITHIN = 330, SOUND_WARN_WITHIN = 600;         // a file this old is a show that stopped: sound is no longer an alarm
const SPAN = 1800;              // timeline: the last 30 minutes
const WINDOWS = ["1", "5", "10", "20", "30", "45", "60"];
// Game colors for the timeline and cards: distinct from each other and from the app's red, readable in both themes.
const PALETTE = ["#5aa9ff", "#ffb020", "#a78bfa", "#34d399", "#ff7eb6", "#22d3ee", "#fb923c", "#b4d455"];

// College teams have no entry in the app's team list: their public colors and names, so the scoreboard and the
// jump list read "Michigan State at Duke" in the right colors. Unknown teams fall back to the palette.
const COLLEGE = {
  DUKE: ["Duke", "003087"], UNC: ["North Carolina", "7BAFD4"], MSU: ["Michigan State", "18453B"], UK: ["Kentucky", "0033A0"],
  KU: ["Kansas", "0051BA"], GONZ: ["Gonzaga", "041E42"], UCLA: ["UCLA", "2D68C4"], ARIZ: ["Arizona", "CC0033"],
  MICH: ["Michigan", "00274C"], PUR: ["Purdue", "CEB888"], BAY: ["Baylor", "154734"], HOU: ["Houston", "C8102E"],
  CONN: ["UConn", "000E2F"], UCONN: ["UConn", "000E2F"], TENN: ["Tennessee", "FF8200"], AUB: ["Auburn", "0C2340"],
  ALA: ["Alabama", "9E1B32"], IU: ["Indiana", "990000"], VILL: ["Villanova", "00205B"], MARQ: ["Marquette", "003366"],
  ILL: ["Illinois", "E84A27"], WIS: ["Wisconsin", "C5050C"], TEX: ["Texas", "BF5700"], UVA: ["Virginia", "232D4B"],
  LOU: ["Louisville", "AD0000"], SYR: ["Syracuse", "F76900"], NCST: ["NC State", "CC0000"], WAKE: ["Wake Forest", "9E7E38"],
  OSU: ["Ohio State", "BB0000"], FLA: ["Florida", "0021A5"], ARK: ["Arkansas", "9D2235"], CREI: ["Creighton", "005CA9"],
};

const V = { S: null, err: "", skew: 0, events: [], keys: new Set(), lastT: null, sel: null, logAll: false,
  ring: { key: "", total: 1 }, alarm: "", timer: null, active: false };

const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const isStr = (v) => typeof v === "string" && v.trim() !== "";
const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const cap = (s) => (isStr(s) ? s.charAt(0).toUpperCase() + s.slice(1) : "—");
const now = () => Date.now() / 1000 + V.skew;
const hasShow = () => !!(V.S && isNum(V.S.updated));
const age = () => (hasShow() ? Math.max(0, now() - V.S.updated) : null);
const fresh = () => hasShow() && age() <= STALE_AFTER;
/** live | replay | stale (stopped writing a moment ago) | idle (the show ended) | none */
function phase() {
  if (!hasShow()) return "none";
  if (V.S.mode === "stopped" || age() > GONE_AFTER) return "idle";
  if (!fresh()) return "stale";
  return V.S.mode === "replay" ? "replay" : "live";
}

function secs(s) {
  if (!isNum(s)) return "—";
  s = Math.max(0, s);
  if (s < 60) return `${Math.round(s)} s`;
  const m = Math.floor(s / 60), x = Math.round(s % 60);
  if (m < 60) return x ? `${m} min ${x} s` : `${m} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}
function clock(s) {
  if (!isNum(s)) return "—";
  s = Math.max(0, Math.floor(s));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
const ago = (s) => (!isNum(s) ? "never" : s < 3 ? "just now" : `${secs(s)} ago`);

/* ---------- colors and teams ---------- */
const colorMap = new Map();
function colorOf(game) {
  if (!isStr(game)) return "var(--text-3)";
  if (!colorMap.has(game)) colorMap.set(game, PALETTE[colorMap.size % PALETTE.length]);
  return colorMap.get(game);
}
function assignColors() {
  // Stable: sorted names claim colors in order, so a game keeps its color across polls.
  const names = new Set(arr(get(V.S, "games")).map((g) => g && g.game).filter(isStr));
  for (const e of V.events) for (const k of ["from", "to", "game"]) if (isStr(e[k])) names.add(e[k]);
  for (const n of [...names].sort()) colorOf(n);
}
function splitGame(name) {
  const m = isStr(name) && name.match(/^\s*(.+?)\s+(?:@|at|vs\.?)\s+(.+?)\s*$/i);
  return m ? [m[1], m[2]] : null;
}
function splitScore(score) {
  const m = isStr(score) && score.match(/^\s*(\d+)\s*[-–]\s*(\d+)\s*$/);
  return m ? [Number(m[1]), Number(m[2])] : null;
}
function team(abbr, lg) {
  const up = String(abbr).toUpperCase();
  const t = arr(get(app.teams, lg)).find((x) => String(x.abbr || "").toUpperCase() === up);
  if (t) return { ...t };
  const c = COLLEGE[up];
  return c ? { abbr, short: c[0], color: c[1] } : { abbr, color: colorOf(abbr).replace("#", "") };
}
function leagueOf(game) {
  for (const g of [get(V.S, "on_screen"), get(V.S, "parked"), ...arr(get(V.S, "games"))]) {
    if (isObj(g) && g.game === game && isStr(g.league)) return g.league.toLowerCase();
  }
  return "";
}
/** "BOS @ PHI" -> "Celtics at 76ers" when the teams are known, else "BOS at PHI". */
function niceGame(game) {
  const t = splitGame(game);
  if (!t) return isStr(game) ? game : "—";
  const lg = leagueOf(game);
  return t.map((a) => team(a, lg).short || a).join(" at ");
}

/* ---------- sound: the one thing that must never be missed ---------- */
function sound() {
  const s = get(V.S, "sound");
  if (!hasShow()) return { level: "off", title: "Sound not checked", detail: "No show running." };
  if (!isObj(s)) return phase() === "idle" ? { level: "off", title: "Sound not checked", detail: "The show has ended." }
    : { level: "bad", title: "Sound not reported", detail: "The show has not said whether the TV is muted." };
  const muted = s.mute === "muted", vol = s.volume;
  const rb = isNum(s.read_back_at) ? Math.max(0, now() - s.read_back_at) : null;
  const what = `${muted ? "Muted" : isStr(s.mute) ? cap(s.mute) : "Mute unknown"}, ${isNum(vol) ? `volume ${vol}` : "volume unknown"}`;
  const checked = isNum(rb) ? `Checked on the TV ${ago(rb)}` : "Never read back from the TV";
  const quiet = muted && vol === 0 && s.ok === true;
  if (quiet && phase() === "idle") return { level: "off", title: `Last read: ${what}`, detail: isNum(s.read_back_at) ? `Checked on the TV at ${fmtTime(s.read_back_at)}, before the show ended` : "The show has ended." };
  if (quiet && isNum(rb) && rb <= SOUND_OK_WITHIN) return { level: "ok", title: what, detail: checked };
  if (quiet && isNum(rb) && rb <= SOUND_WARN_WITHIN) return { level: "warn", title: what, detail: `${checked}: the check is getting old` };
  const title = quiet ? "Mute not confirmed" : !muted || (isNum(vol) && vol > 0) ? "TV sound is on" : "Mute not confirmed";
  return { level: "bad", title, detail: `${what}. ${s.ok === false ? "The show says sound is not OK. " : ""}${checked}.` };
}

/* Every screen: a red banner while the TV may be making sound (tap it to open Show). */
hooks.banners = () => {
  try {
    const v = sound();
    if (v.level !== "bad" || app.tab === "show") return "";
    return `<div class="banner banner--safe sh-alarm" data-key="b:show-sound" data-go="show" role="alert">${icon("sound")}<div class="grow"><b>${esc(v.title)}</b>Check the TV now. Tap for the Show view.</div></div>`;
  } catch { return ""; }
};

/* ---------- data ---------- */
async function poll() {
  clearTimeout(V.timer);
  try {
    const s = await api("/api/show");
    V.S = isObj(s) ? s : {};
    V.err = "";
    if (isNum(V.S.updated) && isNum(V.S.stale_s)) V.skew = V.S.updated + V.S.stale_s - Date.now() / 1000;
  } catch (e) { V.err = (e && e.message) || "no answer"; }
  try {
    const q = V.lastT === null ? "" : `?since=${encodeURIComponent(V.lastT)}`;
    const r = await api(`/api/show/events${q}`);
    addEvents(arr(r && r.events));
  } catch { /* the state call already says what is wrong */ }
  draw();
  const onTab = app.tab === "show" && !document.hidden;
  V.active = onTab;
  V.timer = setTimeout(poll, onTab ? 1000 : document.hidden ? 15000 : 5000);
}
function addEvents(list) {
  for (const e of list) {
    if (!isObj(e) || !isNum(e.t)) continue;
    const k = `${e.t}|${e.kind}|${e.to || e.game || e.text || e.reason || ""}`;
    if (V.keys.has(k)) continue;
    V.keys.add(k);
    V.events.push(e);
    if (V.lastT === null || e.t > V.lastT) V.lastT = e.t;
  }
  V.events.sort((a, b) => a.t - b.t);
  if (V.events.length > 600) {
    V.events = V.events.slice(-400);
    V.keys = new Set(V.events.map((e) => `${e.t}|${e.kind}|${e.to || e.game || e.text || e.reason || ""}`));
  }
}
/* The banner shows on every tab but Show; returns true when it redrew the page for it. */
function syncAlarm() {
  const v = sound();
  const bad = v.level === "bad";
  const dot = document.querySelector('#tabbar .tab[data-go="show"] .tab-alarm');
  if (dot) dot.hidden = !bad;
  const key = bad && app.tab !== "show" ? v.title : "";
  if (key === V.alarm) return false;
  V.alarm = key;
  hooks.render();
  return true;
}
function draw() {
  if (!syncAlarm()) hooks.renderScreen("show");
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });

/* ---------- the screen ---------- */
function render(root) {
  if (!V.active) { V.active = true; setTimeout(poll, 0); }   // just opened: fresh numbers now, then every second
  if (V.alarm) setTimeout(syncAlarm, 0);                      // opened Show: its own red card replaces the banner
  const S = V.S;
  if (!S && !V.err) { patch(root, `${screenHead("Show", "Connecting")}${skel("card")}${skel("hero", 1, 'style="height:300px;margin-top:12px"')}`); return; }
  assignColors();
  const has = hasShow();
  const html = [
    head(),
    soundCard(),
    status(),
    has ? onTv() : "",
    has ? nextCard() : "",
    jumpsCard(),
    has ? paceCard() : "",
    has || V.events.length ? timeline() : "",
    has ? rule1() : "",
    has ? gamesCard() : "",
    has ? stayed() : "",
    has || get(S, "eyes") ? health() : "",
    V.events.length ? log() : "",
  ].join("");
  patch(root, `<div class="sh" data-key="sh">${html}</div>`);
}

function head() {
  const S = V.S || {};
  const ph = V.err ? "none" : phase();
  const mins = get(S, "session.minutes"), plan = get(S, "session.planned_minutes");
  const len = isNum(mins) ? `${Math.round(mins)} min${isNum(plan) ? ` of ${Math.round(plan)}` : ""}` : "";
  const sub = ph === "idle" ? `Ended ${fmtTime(S.updated)}${len ? ` · ran ${len}` : ""}`
    : ph === "none" ? "No show running" : [{ live: "On air", replay: "Replaying a recorded night", stale: "On air" }[ph], len].filter(Boolean).join(" · ");
  const word = V.err ? "Offline" : { live: "Live", replay: "Replay", stale: `Not updating · ${secs(age())}`, idle: "Idle", none: "Waiting" }[ph];
  const pill = `<span class="pill sh-fresh" data-s="${ph}"><i></i><span>${esc(word)}</span></span>`;
  const bar = isNum(mins) && isNum(plan) && plan > 0
    ? `<div class="sh-prog" role="meter" aria-label="Session" aria-valuemin="0" aria-valuemax="${attr(plan)}" aria-valuenow="${attr(mins)}"><i style="--p:${clamp((mins / plan) * 100, 0, 100)}"></i></div>` : "";
  return `<div data-key="sh-head">${screenHead("Show", sub, pill)}${bar}</div>`;
}

function status() {
  if (V.err) {
    return `<div class="banner banner--stale sh-note" data-key="sh-err">${icon("wifi")}<div class="grow"><b>Can't reach the app</b>${esc(V.err)}. Trying again by itself.</div></div>`;
  }
  if (!hasShow()) {
    return `<div class="card" data-key="sh-empty">${empty("tv", "No show running", "This fills in by itself once the show starts writing its state.", "", "empty--sm", "sh")}</div>`;
  }
  const ph = phase();
  if (ph === "idle") {
    return `<div class="banner banner--stale sh-note" data-key="sh-stale">${icon("moon")}<div class="grow"><b>Idle</b>The show ended at ${esc(fmtTime(V.S.updated))}. Below is how it stood then. This fills in by itself when the next show starts.</div></div>`;
  }
  if (ph === "stale") {
    return `<div class="banner banner--stale sh-note" data-key="sh-stale">${icon("clock")}<div class="grow"><b>Not updating</b>The show last wrote ${esc(ago(age()))} (${esc(fmtTime(V.S.updated))}). Everything below is as of then.</div></div>`;
  }
  return "";
}

function soundCard() {
  const v = sound();
  const ic = v.level === "bad" ? "sound" : "mute";
  if (v.level === "bad") {
    return `<div class="sh-sound sh-sound--bad" data-key="sh-sound" role="alert">${icon(ic, "ico--lg")}<div class="grow"><b>${esc(v.title)}</b><span>${esc(v.detail)}</span><span>Check the TV now.</span></div></div>`;
  }
  return `<div class="sh-sound sh-sound--${v.level}" data-key="sh-sound">${icon(ic)}<div class="grow"><b>${esc(v.title)}</b><span>${esc(v.detail)}</span></div><span class="hdot ${v.level === "ok" ? "hdot--g" : v.level === "warn" ? "hdot--y" : ""}"></span></div>`;
}

const STATE = { "live play": ["live", "Live play"], "dead ball": ["break", "Dead ball"], break: ["break", "Break"] };
function stateBadgeOf(s) {
  let [kind, word] = STATE[s] || ["pre", isStr(s) && s !== "unknown" ? cap(s) : "Unknown"];
  if (!fresh()) kind = "pre";   // a frozen file: the last state it saw, in grey, not a live red
  return `<span class="badge badge--${kind}">${esc(word)}</span>`;
}

function onTv() {
  const on = get(V.S, "on_screen");
  if (!isObj(on) || !isStr(on.game)) {
    return `${sec("On the TV", { key: "s-ontv" })}<div class="card" data-key="sh-ontv-none">${empty("tv", "Nothing on the TV", "The show has not put a game on screen yet.", "", "empty--sm", "sh2")}</div>`;
  }
  const lg = isStr(on.league) ? on.league.toLowerCase() : "";
  const teams = splitGame(on.game), sc = splitScore(on.score);
  const extra = fresh() ? age() : 0;
  const look = isNum(on.look_s) ? on.look_s + extra : isNum(on.since) && fresh() ? now() - on.since : null;
  let boardHtml, vars = "";
  if (teams) {
    const [A, H] = [team(teams[0], lg), team(teams[1], lg)];
    const side = (t, k, cls) => `<div class="board-team ${cls}">${logo(t, { size: 64, dark: true, eager: true, cls: "logo--shadow" })}
      <div class="tm"><span class="ab">${esc(teams[k])}</span></div>
      <div class="pts ${sc && sc[k] < sc[1 - k] ? "trail" : ""}">${sc ? odo(sc[k]) : "—"}</div></div>`;
    boardHtml = `<div class="board">${side(A, 0, "")}<div class="board-mid"><span class="per">${esc(on.period)}</span><span class="clk">${esc(on.clock, "")}</span></div>${side(H, 1, "board-team--h")}</div>`;
    vars = `--a:#${String(A.color || "3a4152").replace("#", "")};--h:#${String(H.color || "3a4152").replace("#", "")}`;
  } else {
    boardHtml = `<div class="sh-solo"><b>${esc(on.game)}</b><span>${esc(on.score, "")}</span><span class="dim">${esc([on.period, on.clock].filter(isStr).join(" "), "")}</span></div>`;
  }
  const chips = [
    isStr(on.channel) ? `<span class="chip">${icon("tv")}<span>${esc(on.channel.toUpperCase())}</span></span>` : "",
    isNum(on.behind_live_s) ? `<span class="chip ${on.behind_live_s >= 90 ? "chip--break" : ""}">${icon("clock")}<span>${esc(secs(on.behind_live_s))} behind live</span></span>` : "",
    isStr(on.stick) ? `<span class="chip">${icon("signal")}<span>Stick ${esc(on.stick)}${isStr(on.input) ? ` · ${esc(on.input)}` : ""}</span></span>` : "",
  ].join("");
  return `${sec("On the TV", { key: "s-ontv", aside: esc(lg.toUpperCase(), "") })}
    <article class="hero on-dark sh-hero" data-key="sh-hero" style="${vars}">
      <div class="hero-bg"></div>
      <div class="hero-in">
        <div class="hero-top"><div class="left">${stateBadgeOf(on.state)}</div>
          <span class="on-for">${icon("clock")}on for <b>${esc(clock(look))}</b></span></div>
        ${boardHtml}
        <div class="hero-chips">${chips}</div>
        <div class="hero-pad"></div>
      </div>
    </article>`;
}

function nextSentence(nx, on) {
  const to = isStr(nx.to_game) ? nx.to_game : null;
  const here = isStr(get(on, "game")) ? on.game : "this game";
  switch (nx.action) {
    case "jump": return to ? ["Jumping to ", to] : ["Jumping next", null];
    case "park": return to ? ["Parking ", to, " on the other stick"] : ["Parking a game on the other stick", null];
    case "stay": return ["Staying on ", here];
    case "wait": return to ? ["Waiting for a dead ball to jump to ", to] : ["Waiting for a dead ball", null];
    default: return [isStr(nx.action) ? `${cap(nx.action)} ` : "Next move: ", to];
  }
}
function nextCard() {
  const nx = get(V.S, "next"), on = get(V.S, "on_screen");
  const head = sec(fresh() ? "Next" : "Last plan", { key: "s-next" });
  if (!isObj(nx)) return `${head}<div class="card card-pad sh-next" data-key="sh-next"><p class="sh-say">No plan reported.</p><p class="sh-why">The show has not said what it will do next.</p></div>${parked()}`;
  const [lead, game, tail] = nextSentence(nx, on);
  const left = isNum(nx.in_s) && fresh() ? nx.in_s - age() : null;
  const key = `${nx.action}|${nx.to_game}|${Math.round(num(V.S.updated) + num(nx.in_s))}`;
  if (key !== V.ring.key) V.ring = { key, total: Math.max(1, num(nx.in_s, 1)) };
  const when = isNum(left) ? (left <= 0.5 ? " now" : nx.action === "stay" ? `. Checks again in ${secs(left)}` : ` in ${secs(left)}`) : "";
  const say = `${esc(lead)}${game ? `<b style="--g:${colorOf(game)}">${esc(game)}</b>` : ""}${esc(tail, "")}${esc(when, "")}.`;
  const count = isNum(left)
    ? `<div class="sh-count" aria-hidden="true"><b>${Math.max(0, Math.ceil(left))}</b><small>sec</small></div>` : "";
  const bar = isNum(left) && nx.action !== "stay"
    ? `<div class="sh-drain"><i style="--p:${clamp((Math.max(0, left) / V.ring.total) * 100, 0, 100)}"></i></div>` : "";
  return `${head}<div class="card card-pad sh-next" data-key="sh-next"><div class="sh-next-row"><div class="grow"><p class="sh-say">${say}</p>
    ${isStr(nx.reason) ? `<p class="sh-why">${esc(cap(nx.reason))}</p>` : ""}</div>${count}</div>${bar}</div>${parked()}`;
}
function parked() {
  const p = get(V.S, "parked");
  if (!isObj(p) || !isStr(p.game)) return `<div class="sh-park" data-key="sh-park">${icon("pin")}<span class="grow">Other stick: nothing parked</span></div>`;
  const bits = [isStr(p.channel) ? p.channel.toUpperCase() : "", isNum(p.parked_for_s) ? `for ${secs(p.parked_for_s)}` : "",
    isNum(p.behind_live_s) ? `${secs(p.behind_live_s)} behind live` : ""].filter(Boolean).join(" · ");
  const tag = p.ready === true ? `<span class="ready">${icon("check")}Ready</span>` : p.ready === false ? `<span class="tag">Tuning</span>` : "";
  return `<div class="sh-park" data-key="sh-park"><i class="sh-sw" style="--g:${colorOf(p.game)}"></i><span class="grow"><b>${esc(p.game)}</b> parked on the other stick${bits ? `<small>${esc(bits)}</small>` : ""}</span>${tag}</div>`;
}

/* The last five jumps, newest first: "9:06 PM  Celtics at 76ers -> Michigan State at Duke, timeout, 2.7 s". */
function jumpsCard() {
  const js = V.events.filter((e) => e.kind === "jump" && isNum(e.t)).slice(-5).reverse();
  if (!js.length) return "";
  const rows = js.map((j) => `<div class="sh-j" data-key="j:${attr(j.t)}">
    <span class="sh-j-t tnum">${esc(fmtTime(j.t))}</span>
    <div class="grow"><div class="sh-j-g"><span>${esc(niceGame(j.from))}</span><span class="sh-j-to">${icon("arrow-right", "ico--xs")}<b style="--g:${colorOf(j.to)}">${esc(niceGame(j.to))}</b></span></div>
      <div class="sh-j-w">${esc([isStr(j.reason) ? cap(j.reason) : "", isNum(j.jump_s) ? `${j.jump_s.toFixed(1)} s to switch` : "", isNum(j.look_s) ? `after a ${secs(j.look_s)} look` : ""].filter(Boolean).join(" · "), "")}</div></div></div>`).join("");
  return `${sec("Last jumps", { key: "s-jumps", aside: js.length < 5 ? "" : "newest first" })}<div class="card sh-js" data-key="sh-js">${rows}</div>`;
}

function paceCard() {
  const p = get(V.S, "pace") || {};
  const wins = get(p, "windows") || {};
  const rates = WINDOWS.map((w) => (isNum(wins[w]) ? wins[w] / Number(w) : null));
  const top = Math.max(0.5, ...rates.filter(isNum));
  const cols = WINDOWS.map((w, i) => {
    const v = wins[w], r = rates[i];
    return `<div class="sh-win ${isNum(v) ? "" : "na"}"><b class="tnum">${isNum(v) ? v : "—"}</b>
      <span class="sh-win-bar"><i style="--p:${isNum(r) ? clamp((r / top) * 100, 4, 100) : 0}"></i></span>
      <span class="sh-win-k">${w} min</span><small>${isNum(r) ? `${r.toFixed(1)}/min` : "not yet"}</small></div>`;
  }).join("");
  const pl = get(V.S, "planned");
  const plan = isObj(pl) && isNum(pl.delivered) && isNum(pl.scheduled) && pl.scheduled > 0
    ? `<div class="sh-plan"><span>${pl.delivered} of ${pl.scheduled} planned jumps</span><b>${Math.round((100 * pl.delivered) / pl.scheduled)}%</b></div><div class="sh-prog"><i style="--p:${clamp((100 * pl.delivered) / pl.scheduled, 0, 100)}"></i></div>` : "";
  return `${sec("Pace", { key: "s-pace", aside: isNum(p.jumps) ? `${p.jumps} jumps so far` : "" })}
    <div class="card card-pad" data-key="sh-pace">
      <div class="sh-big"><b class="tnum">${isNum(p.per_min_now) ? p.per_min_now.toFixed(1) : "—"}</b><span>jumps a minute now<small>${isNum(p.per_min_session) ? `${p.per_min_session.toFixed(2)} a minute over the whole session` : "Session average —"}</small></span></div>
      <div class="sh-wins" role="list" aria-label="Jumps in the last 1 to 60 minutes">${cols}</div>
      ${plan}
    </div>`;
}

/* The last 30 minutes, one color per game. Built from jump events; big plays sit above (filled = seen). */
function looks(t1) {
  const t0 = t1 - SPAN;
  const jumps = V.events.filter((e) => e.kind === "jump" && isNum(e.t));
  const out = [];
  if (jumps.length) {
    const f = jumps[0];
    if (isStr(f.from)) out.push({ game: f.from, start: isNum(f.look_s) ? f.t - f.look_s : t0, end: f.t, left: f.reason });
    jumps.forEach((j, i) => {
      const n = jumps[i + 1];
      out.push({ game: j.to, start: j.t, end: n ? n.t : t1, left: n ? n.reason : null, open: !n });
    });
  } else {
    const on = get(V.S, "on_screen");
    if (isObj(on) && isStr(on.game)) out.push({ game: on.game, start: isNum(on.since) ? on.since : t0, end: t1, open: true });
  }
  const merged = [];
  for (const l of out.filter((x) => isStr(x.game) && x.end > t0)) {
    const prev = merged[merged.length - 1];
    if (prev && prev.game === l.game && l.start - prev.end < 5) { prev.end = l.end; prev.open = l.open; prev.left = l.left; prev.n = (prev.n || 1) + 1; }
    else merged.push({ ...l });
  }
  return merged;
}
function timeline() {
  const t1 = hasShow() && !fresh() ? V.S.updated : now();
  const t0 = t1 - SPAN;
  const x = (t) => clamp(((t - t0) / SPAN) * 100, 0, 100);
  const ls = looks(t1);
  const big = V.events.filter((e) => e.kind === "big_play" && isNum(e.t) && e.t >= t0 && e.t <= t1);
  const items = [];
  const blocks = ls.map((l) => {
    const k = `l:${Math.round(l.start)}:${l.game}`;
    items.push({ k, l });
    const a = x(l.start), b = x(l.end);
    return b - a < 0.05 ? "" : `<button class="sh-blk ${V.sel === k ? "on" : ""}" style="left:${a}%;width:${b - a}%;--g:${colorOf(l.game)}" data-act="show-tl" data-k="${attr(k)}" aria-label="${attr(lookText(l))}"></button>`;
  }).join("");
  const marks = big.map((e) => {
    const k = `b:${e.t}:${e.game}`;
    items.push({ k, e });
    return `<button class="sh-mark ${e.seen === true ? "seen" : ""} ${V.sel === k ? "on" : ""}" style="left:${x(e.t)}%;--g:${colorOf(e.game)}" data-act="show-tl" data-k="${attr(k)}" aria-label="${attr(bigText(e))}"></button>`;
  }).join("");
  const sel = items.find((i) => i.k === V.sel);
  const cap_ = sel ? (sel.l ? lookText(sel.l) : bigText(sel.e)) : "Tap a block or a dot to see what it was.";
  const per = new Map();
  for (const l of ls) per.set(l.game, (per.get(l.game) || 0) + Math.max(0, Math.min(l.end, t1) - Math.max(l.start, t0)));
  const legend = [...per.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6).map(([g, s]) =>
    `<span class="sh-leg"><i class="sh-sw" style="--g:${colorOf(g)}"></i>${esc(g)}<small>${esc(secs(s))}</small></span>`).join("");
  const seen = big.filter((e) => e.seen === true).length;
  return `${sec("Last 30 minutes", { key: "s-tl", aside: big.length ? `${seen} of ${big.length} big plays seen` : "" })}
    <div class="card card-pad" data-key="sh-tl">
      <div class="sh-tl"><div class="sh-marks">${marks}</div><div class="sh-track">${blocks}</div>
        <div class="sh-axis"><span>30 min ago</span><span>20</span><span>10</span><span>${fresh() || !hasShow() ? "now" : "last update"}</span></div></div>
      <p class="sh-cap">${esc(cap_)}</p>
      ${legend ? `<div class="sh-legend">${legend}${big.length ? `<span class="sh-leg"><i class="sh-dot seen"></i>big play seen<i class="sh-dot"></i>missed</span>` : ""}</div>` : ""}
    </div>`;
}
function lookText(l) {
  return `${l.game}: ${secs(l.end - l.start)}${l.open ? " so far" : ""}, from ${fmtTime(l.start)}${l.open ? "" : ` to ${fmtTime(l.end)}`}${isStr(l.left) ? ` · left for: ${l.left}` : ""}`;
}
function bigText(e) {
  return `Big play in ${isStr(e.game) ? e.game : "a game"}: ${isStr(e.what) ? e.what : "—"}, ${e.seen === true ? "seen on the TV" : "missed"} at ${fmtTime(e.t)}`;
}

function rule1() {
  const r = get(V.S, "rule1") || {};
  const tile = (label, v, badCls) => `<div class="sh-r1 ${isNum(v) ? (v === 0 ? "good" : badCls) : ""}"><b class="tnum">${isNum(v) ? v : "—"}</b><span>${label}</span></div>`;
  const j = r.judged;
  const judged = j === "replay" ? "Judged on replay" : j === "unknown live" ? "Live: judged later on replay" : isStr(j) ? cap(j) : "Not judged yet";
  const pad = get(V.S, "timing.padding_s");
  return `${sec("Rule #1", { key: "s-r1", aside: esc(judged) })}
    <div class="card card-pad" data-key="sh-r1"><div class="sh-r1s">
      ${tile("Joined mid-play", r.mid_play_joins, "warn")}${tile("Left mid-play", r.mid_play_leaves, "warn")}${tile("Left a close, late moment", r.high_leverage_leaves, "bad")}
    </div><p class="sh-foot">Joins only at a dead ball. Safety padding ${isNum(pad) ? `${pad.toFixed(1)} s` : "—"}.</p></div>`;
}

function gamesCard() {
  const games = arr(get(V.S, "games")).filter(isObj);
  const head = sec("Games", { key: "s-games", n: games.length || "" });
  if (!games.length) return `${head}<div class="card" data-key="sh-games-none">${empty("tv", "No games yet", "The slate shows up here once the show has it.", "", "empty--sm", "sh3")}</div>`;
  const onName = get(V.S, "on_screen.game"), p = get(V.S, "parked");
  const rank = (g) => (g.on_screen === true || g.game === onName ? 0 : g.parked === true ? 1 : 2);
  const rows = games.slice().sort((a, b) => rank(a) - rank(b) || num(b.value, -1) - num(a.value, -1)).map((g) => {
    const on = rank(g) === 0;
    const tag = on ? `<span class="tag sh-tag-on">On TV</span>` : g.parked === true
      ? `<span class="tag">${isObj(p) && p.game === g.game && p.ready === true ? "Parked, ready" : "Parked"}</span>` : "";
    const meta = [isStr(g.score) ? g.score : "", [g.period, g.clock].filter(isStr).join(" "), STATE[g.state] ? STATE[g.state][1] : isStr(g.state) ? cap(g.state) : ""].filter(Boolean).join(" · ");
    const v = isNum(g.value) ? clamp(g.value, 0, 1) : null;
    return `<div class="sh-g ${on ? "on" : ""}" data-key="g:${attr(g.game)}" style="--g:${colorOf(g.game)}">
      <div class="grow"><div class="sh-g-top"><b class="ell">${esc(g.game)}</b>${tag}</div>
        <div class="sh-g-meta">${esc([isStr(g.league) ? g.league.toUpperCase() : "", isStr(g.channel) ? g.channel.toUpperCase() : ""].filter(Boolean).join(" · "), "")}${meta ? ` · ${esc(meta)}` : ""}</div></div>
      <div class="sh-worth" title="How much it is worth watching now"><span class="sh-worth-bar"><i style="--p:${v === null ? 0 : v * 100}"></i></span><small class="tnum">${v === null ? "—" : g.value.toFixed(2)}</small></div></div>`;
  }).join("");
  return `${head}<div class="card sh-games" data-key="sh-games" data-flip>${rows}</div>`;
}

function stayed() {
  const sr = get(V.S, "stay_reasons");
  const items = isObj(sr) ? Object.entries(sr).filter(([, v]) => isNum(v) && v > 0).sort((a, b) => b[1] - a[1]).slice(0, 5) : [];
  const total = items.reduce((s, [, v]) => s + v, 0);
  const head = sec("Why it stayed", { key: "s-why", aside: total ? `${secs(total)} in all` : "" });
  if (!items.length) return `${head}<div class="card card-pad sh-foot" data-key="sh-why">No stays yet: it has jumped whenever it wanted to.</div>`;
  const max = items[0][1];
  return `${head}<div class="card card-pad" data-key="sh-why">${items.map(([k, v]) => `<div class="sh-bar"><div class="sh-bar-top"><span>${esc(cap(k))}</span><b class="tnum">${esc(secs(v))}</b></div><span class="sh-bar-tr"><i style="--p:${Math.max(2, (v / max) * 100)}"></i></span></div>`).join("")}</div>`;
}

function lvl(word) {
  if (!isStr(word)) return "";
  const w = word.toLowerCase();
  if (["ok", "good", "ready"].includes(w)) return "g";
  if (w === "off") return "";
  return /err|down|fail|lost|signed out|dead|crash|no signal/.test(w) ? "r" : "y";
}
function hrow(name, word, sub = "", force) {
  const l = force !== undefined ? force : lvl(word);
  return `<div class="health-r"><span class="hdot ${l ? `hdot--${l}` : ""}"></span><div class="grow"><div class="t1">${esc(name)}</div>${sub ? `<div class="t2">${esc(sub)}</div>` : ""}</div><span class="meta">${esc(isStr(word) ? cap(word) : "—")}</span></div>`;
}
function health() {
  const h = get(V.S, "health") || {}, eyes = get(V.S, "eyes");
  const rows = [hrow("TV", h.tv), hrow("Game feed", h.feed)];
  const sticks = isObj(h.sticks) ? Object.keys(h.sticks).slice(0, 4) : [];
  if (sticks.length) for (const k of sticks) rows.push(hrow(`Stick ${k}`, h.sticks[k]));
  else rows.push(hrow("Sticks", null));
  const raw = isObj(eyes) && isStr(eyes.status) ? eyes.status : isStr(h.eyes) ? h.eyes : null;
  const EW = { ok: "Watching", "no clock": "No clock", "signed out": "Signed out", off: "Off" };
  let ew = isStr(raw) ? EW[raw.toLowerCase()] || cap(raw) : null;
  let sub = "", stale = false;
  if (isObj(eyes)) {
    const ea = isNum(eyes.updated) ? Math.max(0, now() - eyes.updated) : null;
    stale = isNum(ea) && ea > 10 && raw !== "off";
    if (raw === "off" && isStr(eyes.why)) ew = `Off (${eyes.why})`;
    const stops = `last clock stop ${isNum(eyes.last_stop_y) ? fmtTime(eyes.last_stop_y) : "—"}, restart ${isNum(eyes.last_restart_y) ? fmtTime(eyes.last_restart_y) : "—"}`;
    sub = [[eyes.channel, eyes.league].filter(isStr).map((x) => x.toUpperCase()).join(" "), stops,
      isNum(eyes.reads_per_min) ? `${eyes.reads_per_min} reads a minute${isNum(eyes.failed_reads_per_min) ? `, ${eyes.failed_reads_per_min} failed` : ""}` : "",
      isNum(ea) ? `updated ${ago(ea)}` : ""].filter(Boolean).join(" · ");
  }
  const el = raw === "ok" ? (stale ? "y" : "g") : raw === "no clock" ? "y" : raw === "signed out" ? "r" : "";
  rows.push(hrow("Eyes (reads the game clock)", stale && ew ? `${ew}, not updating` : ew, sub, el));
  const chans = get(V.S, "timing.channels"), ek = isObj(eyes) ? eyes.k : null;
  const signed = (v) => (isNum(v) ? `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(2)} s` : "—");
  let trows = [];
  if (isObj(chans)) trows = Object.entries(chans).map(([k, c]) => [k.toUpperCase(), signed(get(c, "k_s")), isNum(get(c, "k_spread_s")) ? `±${c.k_spread_s.toFixed(1)} s` : "—", c && c.tune_offset_known === true ? "Known" : c && c.tune_offset_known === false ? "Learning" : "—"]);
  if (!trows.length && isObj(ek)) trows = Object.entries(ek).map(([k, c]) => [k.split("|").slice(0, 2).map((s) => s.toUpperCase()).join(" · "), signed(get(c, "k_s")), isNum(get(c, "spread_s")) ? `±${c.spread_s.toFixed(1)} s` : "—", isNum(get(c, "n")) ? `${c.n} reads` : "—"]);
  const table = trows.length ? `<div class="sh-tbl" role="table" aria-label="Timing offset per channel"><div class="sh-tr sh-th" role="row"><span>Channel</span><span>Offset</span><span>Spread</span><span>Tune</span></div>${trows.slice(0, 8).map((r) => `<div class="sh-tr" role="row">${r.map((c) => `<span class="tnum">${esc(c)}</span>`).join("")}</div>`).join("")}</div>`
    : `<p class="sh-foot">No channel timing yet.</p>`;
  const le = h.last_error;
  return `${sec("Health", { key: "s-health" })}<div class="card set" data-key="sh-health">${rows.join("")}</div>
    ${sec("Timing per channel", { key: "s-timing" })}<div class="card card-pad" data-key="sh-timing">${table}
      <p class="sh-foot ${isStr(le) ? "sh-err" : ""}">${isStr(le) ? `Last error: ${esc(le)}` : "No errors."}</p></div>`;
}

const LOG_IC = { jump: "swap", park: "pin", stay: "hourglass", big_play: "star", alert: "alert" };
function logRow(e) {
  let title = "", why = "";
  switch (e.kind) {
    case "jump": title = `Jumped to ${e.to || "—"}`; why = [isStr(e.from) ? `from ${e.from}` : "", isStr(e.reason) ? e.reason : "", isNum(e.look_s) ? `${secs(e.look_s)} look` : "", isNum(e.jump_s) ? `switch took ${e.jump_s.toFixed(1)} s` : ""].filter(Boolean).join(" · "); break;
    case "park": title = `Parked ${e.game || "—"}${e.n > 1 ? ` (${e.n} times)` : ""}`; why = [isStr(e.channel) ? e.channel.toUpperCase() : "", isNum(e.took_s) ? `ready in ${e.took_s.toFixed(1)} s` : ""].filter(Boolean).join(" · "); break;
    case "stay": title = `Stayed${isNum(e.for_s) ? ` ${secs(e.for_s)}` : ""}`; why = isStr(e.reason) ? cap(e.reason) : ""; break;
    case "big_play": title = `Big play: ${e.what || "—"}`; why = `${e.game || "—"} · ${e.seen === true ? "seen on the TV" : "missed"}`; break;
    case "alert": title = cap(e.text); why = e.level === "error" ? "Error" : "Warning"; break;
    default: title = cap(String(e.kind).replace(/_/g, " ")); why = isStr(e.text) ? e.text : isStr(e.reason) ? e.reason : "";
  }
  const tone = e.kind === "alert" ? (e.level === "error" ? "bad" : "warn") : e.kind === "big_play" && e.seen !== true ? "warn" : "";
  const g = e.to || e.game;
  return `<div class="tl-i sh-log-i ${tone}" data-key="e:${attr(e.t)}:${attr(e.kind)}:${attr(g || "")}"><span class="tl-node" style="${isStr(g) ? `--g:${colorOf(g)}` : ""}">${icon(LOG_IC[e.kind] || "info")}</span>
    <div style="min-width:0"><div class="tl-top"><span class="m ell">${esc(title)}</span><span class="t">${esc(fmtTime(e.t))}</span></div>${why ? `<div class="tl-why">${esc(why)}</div>` : ""}</div></div>`;
}
/* One row per run: back-to-back stays for the same reason become one "Stayed 3 min" row, repeated parks of the
   same game one "Parked x 10" row. */
function folded() {
  const out = [];
  for (const e of V.events) {
    const prev = out[out.length - 1];
    if (prev && e.kind === "stay" && prev.kind === "stay" && prev.reason === e.reason) {
      out[out.length - 1] = { ...prev, t: e.t, for_s: Math.max(num(prev.for_s), num(e.for_s)), n: (prev.n || 1) + 1 };
    } else if (prev && e.kind === "park" && prev.kind === "park" && prev.game === e.game) {
      out[out.length - 1] = { ...e, n: (prev.n || 1) + 1 };
    } else out.push(e);
  }
  return out;
}
function log() {
  const all = folded().reverse();
  const shown = V.logAll ? all.slice(0, 120) : all.slice(0, 12);
  const more = all.length > 12 ? `<button class="btn btn--ghost btn--sm btn--block" data-act="show-log">${V.logAll ? "Show fewer" : `Show ${Math.min(120, all.length) - 12} more`}</button>` : "";
  return `${sec("What happened", { key: "s-log", n: all.length })}<div class="card tl sh-log" data-key="sh-log">${shown.map(logRow).join("")}</div>${more ? `<div data-key="sh-log-more" style="margin-top:6px">${more}</div>` : ""}`;
}

/* ---------- actions ---------- */
ACTIONS["show-tl"] = (el) => { V.sel = V.sel === el.dataset.k ? null : el.dataset.k; hooks.renderScreen("show"); };
ACTIONS["show-log"] = () => { V.logAll = !V.logAll; hooks.renderScreen("show"); };

/* ---------- boot ---------- */
if (hooks.addTab) hooks.addTab({ id: "show", label: "Show", ic: "activity", render });
poll();
