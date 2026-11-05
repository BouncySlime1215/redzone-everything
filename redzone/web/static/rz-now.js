/* RedZone Everything — Now screen: mode row, hero scoreboard, controls, on-deck, next up,
   fantasy, catch-up queue, recent switches. Plus the Focus and More sheets. */

import {
  app, ACTIONS, CHANGES, attr, esc, num, get, arr, games, gameById, status, pairInks, fmtAgo, fmtTime, fmtStart, nowS,
  reduceMotion, SPRING, EASE_OUT, ink, teamBy, whereLabel,
} from "./rz-core.js?v=41";
import { patch } from "./rz-dom.js?v=40";
import { icon, logo, avatar, empty, skel, sec, seg, ART } from "./rz-ui.js?v=40";
import { heroCard, fantasyCard } from "./rz-cards.js?v=40";
import { openSheet, closeSheet, sheetById, confirmSheet } from "./rz-sheet.js?v=40";
import { act } from "./rz-api.js?v=43";
import { screenEvidence } from "./rz-evidence.js?v=40";
import { recordedPanel } from "./rz-recorded.js?v=40";
import { tonightCard } from "./rz-plan.js?v=40";
import { switchExtras, missedBar } from "./rz-v2.js?v=20";

const STATE_LABEL = { idle: "Idle", watching: "Watching", locked: "Locked in", break: "On a break", paused: "Paused",
  frozen: "Holding still", safe: "Safe mode", replay: "Replay" };
const KIND = { better: ["trend", "Better game"], break_visit: ["megaphone", "Break visit"], home: ["back", "Back home"],
  return: ["back", "Moment over"], chase: ["zap", "Catch it live"], must_see: ["flame", "Must-see"], ended: ["check", "Game ended"],
  start: ["play", "Start"], user: ["target", "You picked"], replay: ["replay", "Replay"], live: ["play", "Live"] };

/* ---------- render ---------- */
export function renderNow(root) {
  const S = app.S;
  if (!S) {
    patch(root, `<div class="mode-row">${skel("pill")}</div>${skel("hero")}<div class="dock">${skel("row", 4, 'style="height:66px;border-radius:18px"')}</div>`);
    return;
  }
  const n = S.now;
  if (S.recorded_playback?.active) {
    patch(root, recordedPanel(S) + screenEvidence(S));
    return;
  }
  const g = n ? gameById(n.game_id) : null;
  const before = root.querySelector(".hero");
  const ghost = before && n && before.dataset.gid !== n.game_id ? before.cloneNode(true) : null;
  const html = [
    modeRow(S),
    n ? `<div class="hero-wrap" data-key="hero-wrap">${heroCard(n, g, S)}</div>` : idle(S),
    screenEvidence(S),
    calibrate(S),
    dock(S),
    plan(S),
    tonightCard(S),
    controlPanel(S),
    S.fantasy ? `<div data-key="fantasy">${sec("Fantasy", { key: "sec-fantasy" })}${fantasyCard(S.fantasy)}</div>` : "",
    queue(S),
    timeline(S),
  ].join("");
  patch(root, html);
  if (ghost) heroSwitch(root, ghost);
}

function modeRow(S) {
  const m = S.mode || {};
  const fg = m.focus_game ? gameById(m.focus_game) : null;
  const focused = m.focus_mode && m.focus_mode !== "autopilot" && m.focus_game;
  const label = focused ? (fg ? fg.matchup : "One game") : "Autopilot";
  const sub = focused ? (m.focus_mode === "focus_must_see" ? "Focus + must-see" : "Focus") : "Mode";
  const st = S.now && S.now.kind === "replay" ? "replay" : m.state || "idle";
  const liveN = games().filter((x) => x.phase === "live").length;
  return `<div class="mode-row" data-key="mode">
    <button class="focus-btn" data-act="open-focus" aria-label="Change mode: ${attr(label)}">
      ${focused && fg ? `<span class="logo-pair">${logo(fg.away, { size: 20 })}${logo(fg.home, { size: 20 })}</span>` : icon(focused ? "target" : "autopilot")}
      <span class="lbl"><small>${esc(sub)}</small><span class="ell" style="display:block;max-width:150px">${esc(label)}</span></span>${icon("chevron-down", "chev")}
    </button>
    <span class="state-txt" data-s="${attr(st)}">${m.mic ? `${icon("mic", "ico--xs")}` : ""}<i></i>${esc(STATE_LABEL[st] || "Idle")}${liveN ? ` · ${liveN} live` : ""}</span>
  </div>`;
}

function dock(S) {
  const m = S.mode || {};
  const paused = !!m.paused;
  const has = !!S.now;
  return `<div class="dock" data-key="dock">
    <button class="dock-btn" data-act="now-act" data-a="back">${icon("back")}Go back</button>
    <button class="dock-btn" data-act="now-act" data-a="skip" ${has ? "" : "disabled"}>${icon("skip", "ico--fill")}Skip</button>
    <button class="dock-btn ${paused ? "dock-btn--on" : ""}" data-act="now-act" data-a="${paused ? "resume" : "pause"}">${icon(paused ? "play" : "pause", "ico--fill")}${paused ? "Resume" : "Pause"}</button>
    <button class="dock-btn" data-act="open-more">${icon("more", "ico--fill")}More</button>
  </div>`;
}

/* "I just saw it" is how it learns your stream delay: big and obvious right after a game
   comes on (or while this channel's delay is still a guess), tucked into More otherwise, and
   never on Now once the app's delay is settled (3+ agreeing taps). */
function calibrate(S) {
  const n = S.now;
  if (!n || n.kind === "replay" || n.game_id === "redzone") return "";
  const d = arr(S.delays).find((x) => x.label && x.label === n.where);
  const fresh = num(n.since) && nowS() - num(n.since) < 600;
  if (d && (d.settled || (d.calibrated && !fresh))) return "";   // settled: learned, stop asking (still in More)
  const dry = !!get(S, "mode.dry_run");
  return `<div class="actions-grid calib" data-key="calib">
    <button class="act-tile act-tile--wide act-tile--accent" data-act="now-act" data-a="saw_it" ${dry ? "disabled" : ""}>${icon("eye", "ico--lg")}<span>I just saw it<small>${dry
      ? "Works once TV control is on: tap the instant a play ends on your TV."
      : d && d.calibrated ? `Delay ${num(d.mu).toFixed(0)}s on ${esc(n.where)}. Tap as a play ends to keep it sharp.`
      : `Tap the instant a play ends on your TV. It learns ${esc(n.where || "this channel")}'s delay so it never cuts mid-play.`}</small></span></button>
  </div>`;
}

/* What it's doing: on now (why), what's waiting on the hidden stick, the next planned switch. */
function plan(S) {
  const m = S.mode || {};
  const sb = standby(S), nx = nextUp(S);
  const head = sec("What it's doing", { key: "sec-plan", aside: m.dry_run ? "Dry run" : "" });
  const state = m.hold ? `Holding on ${m.hold.matchup} until you tap Resume auto${m.hold.auto_resume ? " (or it's over)" : ""}`
    : m.paused ? "Paused: staying put until you resume"
    : S.now ? `Staying on ${S.now.matchup || "this game"}: ${S.now.reason || "best thing on"}`
    : "Waiting for the first game";
  const stay = sb || nx ? "" : `<div class="next" data-key="plan-stay"><span class="pick-ic" style="width:40px;height:40px">${icon(m.hold ? "lock" : m.paused ? "pause" : "autopilot")}</span>
    <div class="grow"><div class="lbl">No switch planned</div><div class="what">${esc(state)}</div></div></div>`;
  return `<div data-key="plan">${head}${sb}${nx}${stay}</div>`;
}

/* One place to run everything from the phone. Every control is a real /api/action. */
function controlPanel(S) {
  const m = S.mode || {};
  const n = S.now;
  const g = n ? gameById(n.game_id) : null;
  const auto = !m.paused && !m.hold;
  const row = (ic, cls, t1, t2, ctl) => `<div class="set-r"><span class="ic ${cls}">${icon(ic)}</span><div class="grow"><div class="t1">${t1}</div><div class="t2">${t2}</div></div>${ctl}</div>`;
  const sw = (change, on, label, danger = false) => `<label class="switch ${danger ? "switch--danger" : ""}"><input type="checkbox" data-change="${change}" ${on ? "checked" : ""} aria-label="${attr(label)}"><span></span></label>`;
  const quick = (a, ic, label, pressed, dis) => `<button class="mini-act" data-act="now-act" data-a="${a}" aria-pressed="${!!pressed}" ${dis ? "disabled" : ""}>${icon(ic)}${esc(label)}</button>`;
  return `${sec("Control", { key: "sec-control" })}<div class="card set" data-key="control">
    ${row("autopilot", "ic--red", "Autopilot", m.hold ? `Holding ${esc(m.hold.matchup)}: turning this on resumes auto` : auto ? "Picks the best game and switches by itself" : "Paused: it stays on this channel", sw("autopilot", auto, "Autopilot"))}
    ${row("power", "", "Control the TV", m.dry_run ? "Dry run: shows what it would do, TV untouched" : "Live: changes channels on your Fire TV", sw("tv-control", !m.dry_run, "Control the TV", true))}
    ${row("swap", "", "Two sticks", m.two_stick ? "Hidden stick pre-tunes the next game for instant flips" : m.two_stick_enabled === false ? "Off: one stick does everything" : "On, but only one stick is ready", sw("two-stick", m.two_stick_enabled !== false, "Two sticks"))}
    ${m.redzone_on ? row("football", "ic--red", "NFL RedZone", "Every score, no commercials", `<button class="btn btn--sm" data-act="go-redzone">${icon("play", "ico--fill")}Watch</button>`) : ""}
    ${row("sliders", "", "Priorities", "Your teams, bets, closeness, big moments", `<button class="btn btn--sm" data-act="go-priorities">Tune${icon("chevron-right")}</button>`)}
    <div class="ctl-quick">
      ${quick(g && g.boosted ? "unboost" : "boost", "flame", g && g.boosted ? "Boosted" : "Boost", g && g.boosted, !g)}
      ${quick("mute", "mute", "Mute", false, !g)}
      ${quick("skip", "skip", "Leave now", false, !n)}
      ${quick("back", "back", "Go back", false, false)}
    </div>
  </div>`;
}

function standby(S) {
  const sb = S.standby;
  if (!sb || !sb.game_id) return "";
  const g = gameById(sb.game_id);
  const [ai, hi] = g ? pairInks(g.away, g.home) : ["", ""];
  return `<div class="next next--deck" data-key="standby" style="--a-ink:${ai};--h-ink:${hi}">
    ${g ? `<div class="logo-pair">${logo(g.away, { size: 30 })}${logo(g.home, { size: 30 })}</div>` : `<span class="pick-ic" style="width:40px;height:40px">${icon("tv")}</span>`}
    <div class="grow"><div class="lbl" style="color:var(--success-ink)">On deck · hidden stick</div>
      <div class="what ell">${esc(sb.matchup || (g && g.matchup))}</div><div class="why ell">${esc(sb.where, "Tuning the second stick")}${sb.why ? ` · ${esc(sb.why)}` : ""}</div></div>
    ${sb.ready ? `<span class="ready">${icon("check")}Ready</span>` : `<span class="wait-ring" aria-label="Getting ready"></span>`}
  </div>`;
}

function nextFor(S) {
  const nx = S.next;
  if (!nx) return null;
  let g = nx.game_id ? gameById(nx.game_id) : null;
  if (!g && nx.reason) {
    const r = String(nx.reason);
    g = games().find((x) => x.phase === "live" && x.reason && (r.includes(x.reason) || x.reason.includes(r) || (x.matchup && r.includes(x.matchup)))) || null;
  }
  return { nx, g };
}
function nextUp(S) {
  const f = nextFor(S);
  if (!f) return "";
  const { nx, g } = f;
  const [ai, hi] = g ? pairInks(g.away, g.home) : ["var(--info)", "var(--info)"];
  return `<div class="next" data-key="next" style="--a-ink:${ai};--h-ink:${hi}" ${g ? `data-act="open-game" data-gid="${attr(g.id)}" role="button"` : ""}>
    ${g ? `<div class="logo-pair">${logo(g.away, { size: 30 })}${logo(g.home, { size: 30 })}</div>` : `<span class="pick-ic" style="width:40px;height:40px;background:color-mix(in srgb,var(--info) 14%,transparent);color:var(--info-ink)">${icon("arrow-right")}</span>`}
    <div class="grow"><div class="lbl">Next up${g ? ` · ${esc(g.matchup)}` : nx.matchup ? ` · ${esc(nx.matchup)}` : ""}</div>
      <div class="what">${esc(stripMatchup(nx.reason, g ? g.matchup : nx.matchup))}</div>
      ${nx.why_waiting ? `<div class="why">Waiting: ${esc(nx.why_waiting)}</div>` : ""}</div>
    <span class="wait-ring" aria-hidden="true"></span>
  </div>`;
}

const stripMatchup = (r, m) => { const s = String(r || ""); return m && s.startsWith(m) ? s.slice(m.length).replace(/^\s*[·:—-]\s*/, "") : s; };

/* nothing on the TV yet: illustrated card + countdown to the first game + tonight's slate */
function idle(S) {
  const all = games();
  const up = all.filter((g) => g.phase === "pre" && g.start_time).sort((a, b) => a.start_time - b.start_time);
  const first = up.find((g) => g.mine) && up[0] && up.find((g) => g.mine).start_time - up[0].start_time < 1800 ? up.find((g) => g.mine) : up[0];
  const liveN = all.filter((g) => g.phase === "live").length;
  const title = liveN ? "Picking a game" : up.length ? "Nothing live yet" : "Quiet night";
  const body = S.mode?.dry_run ? "Dry run previews the choices without changing your TV." : liveN ? "Autopilot is lining up the best game on." : up.length ? "Autopilot takes over when a game starts and TV control is ready." : "No games on the schedule right now.";
  const nextCard = first ? `<div class="idle-next" data-act="open-game" data-gid="${attr(first.id)}" role="button">
      <div>${logo(first.away, { size: 52, dark: true })}<div class="ab">${esc(get(first, "away.abbr"))}</div></div>
      <div><div class="cd" data-clockdown="${num(first.start_time)}" data-own>${esc(countdown(first.start_time))}</div><div class="when">${esc(fmtStart(first.start_time))}${first.league ? ` · ${first.league.toUpperCase()}` : ""}</div></div>
      <div>${logo(first.home, { size: 52, dark: true })}<div class="ab">${esc(get(first, "home.abbr"))}</div></div>
    </div>` : "";
  const mineFirst = [...up].sort((a, b) => (b.mine - a.mine) || a.start_time - b.start_time).slice(0, 8);
  const tonight = mineFirst.length && !(S.tonight && arr(S.tonight.games).length) ? `${sec("Tonight", { n: up.length, key: "sec-tonight", aside: up.length > 8 ? "Your teams first" : "" })}
    <div class="card rows" data-key="tonight" data-flip>${mineFirst.map((g) => `<div class="r r--tap" data-key="t:${attr(g.id)}" data-act="open-game" data-gid="${attr(g.id)}" role="button">
      <span class="logo-pair">${logo(g.away, { size: 30 })}${logo(g.home, { size: 30 })}</span>
      <div class="grow"><div class="t1">${esc(g.matchup)}${g.mine ? ` ${icon("star", "ico--fill ico--xs")}` : ""}</div><div class="t2 ell">${esc(whereLabel(g) || "Channel not set")}</div></div>
      <span class="side">${esc(fmtTime(g.start_time))}</span></div>`).join("")}</div>` : "";
  return `<div class="idle on-dark" data-key="idle">${ART.tv("idle")}<h2>${esc(title)}</h2><p>${esc(body)}</p>${nextCard}</div>${tonight}`;
}
function countdown(ts) {
  const s = Math.max(0, num(ts) - nowS());
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = Math.floor(s % 60);
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`;
}
export { countdown };

const KIND_TAG = { td: "TD", goal: "Goal", hr: "HR", turnover: "Turnover", fg: "FG", safety: "Safety", three: "3PT" };
function queue(S) {
  const q = arr(S.queue);
  const head = sec("Catch-up", { n: q.length || "", key: "sec-queue", aside: q.length ? "Plays during the next break" : "" });
  if (!q.length) {
    return `${head}<div class="card" data-key="queue-empty">${empty("replay", "All caught up", "Big plays you miss land here and replay during the next commercial.", "", "empty--sm", "q")}</div>`;
  }
  return `${head}<div class="card rows" data-key="queue" data-flip>${q.map((it) => {
    const g = gameById(it.game_id);
    const sp = g && g.spotlight && g.spotlight.play_id && g.spotlight.play_id === it.play_id ? g.spotlight : null;
    const t = sp && g ? teamBy(g, sp.team) : null;
    const media = sp
      ? `${avatar(sp, { size: 52, team: t })}${t ? `<span class="logo-badge">${logo(t, { size: 18 })}</span>` : ""}`
      : g ? `<span class="logo-pair" style="flex-direction:column;align-items:flex-start">${logo(g.away, { size: 30 })}<span style="margin:-12px 0 0 18px">${logo(g.home, { size: 30 })}</span></span>` : `<span class="avatar" style="--s:52px"><b class="on">${icon("replay")}</b></span>`;
    const tag = KIND_TAG[it.kind] || "Big play";
    return `<div class="r" data-key="q:${attr(it.play_id || it.label)}">
      <div class="q-media">${media}</div>
      <div class="grow"><div class="row" style="gap:6px"><span class="q-kind q-kind--${attr(it.kind)}">${esc(tag)}</span><span class="t2 ell" style="margin:0">${esc(it.matchup || (g && g.matchup))} · ${esc(fmtAgo(it.at))}</span></div>
        <div class="t1 clamp2" style="margin-top:4px">${esc(it.label)}</div></div>
      <button class="play-btn" data-act="replay" data-gid="${attr(it.game_id)}" data-play="${attr(it.play_id)}" aria-label="Play replay: ${attr(it.label)}">${icon("play")}</button>
    </div>`;
  }).join("")}</div>`;
}

function timeline(S) {
  const log = arr(S.log).slice(0, 12);
  const allDry = log.length && log.every((l) => l.dry_run);
  const head = sec("Recent switches", { key: "sec-log", aside: allDry ? "Dry run" : "" });
  const miss = missedBar();
  if (!log.length) return `${head}${miss}<div class="card" data-key="log-empty">${empty("timeline", "No switches yet", "Every channel change shows up here with the reason it made it.", "", "empty--sm", "tl")}</div>`;
  return `${head}${miss}<div class="card tl" data-key="log" data-flip>${log.map((l) => {
    const g = gameById(l.to);
    const [ic, kname] = KIND[l.kind] || ["tv", l.kind || "Switch"];
    return `<div class="tl-i" data-key="l:${attr(l.ts)}:${attr(l.to)}" ${g ? `data-act="open-game" data-gid="${attr(g.id)}" role="button"` : ""}>
      <span class="tl-node">${icon(ic)}</span>
      <div style="min-width:0">
        <div class="tl-top">${g ? `<span class="logo-pair">${logo(g.away, { size: 20 })}${logo(g.home, { size: 20 })}</span>` : ""}<span class="m ell">${esc(l.matchup || (g && g.matchup))}</span><span class="t">${esc(fmtTime(l.ts))}</span></div>
        <div class="tl-why">${esc(l.reason)}</div>
        <div class="tl-tags"><span class="tag tag--kind">${esc(kname)}</span>${l.dry_run && !allDry ? `<span class="tag tag--would">Would switch</span>` : ""}${l.ok === false && !l.dry_run ? `<span class="tag tag--fail">Failed</span>` : ""}${l.took ? `<span class="tag">${num(l.took).toFixed(1)}s</span>` : ""}</div>
        ${switchExtras(l)}
      </div></div>`;
  }).join("")}</div>`;
}

/* ---------- hero switch: old hero fades out while a team-color sweep brings the new one in ---------- */
function heroSwitch(root, ghost) {
  const wrap = root.querySelector(".hero-wrap");
  const hero = wrap && wrap.querySelector(".hero");
  if (!wrap || !hero || reduceMotion() || document.hidden) return;
  ghost.setAttribute("data-ghost", "");
  ghost.removeAttribute("data-key");
  ghost.classList.add("hero-ghost");
  ghost.style.cssText = "position:absolute;inset:0;z-index:3;pointer-events:none;margin:0";
  wrap.appendChild(ghost);
  ghost.animate([{ opacity: 1, transform: "none", filter: "blur(0)" }, { opacity: 0, transform: "translateX(-28px) scale(.97)", filter: "blur(4px)" }],
    { duration: 420, easing: "cubic-bezier(.55,0,1,.45)", fill: "forwards" }).onfinish = () => ghost.remove();
  setTimeout(() => ghost.remove(), 900);
  hero.animate([{ opacity: 0, transform: "translateX(26px) scale(.985)" }, { opacity: 1, transform: "none" }], { duration: 620, easing: EASE_OUT, delay: 120, fill: "backwards" });
  const sweep = hero.querySelector(".hero-sweep");
  if (sweep) sweep.animate([{ opacity: 0.95, transform: "translateX(-110%)" }, { opacity: 0.95, transform: "translateX(110%)", offset: 0.85 }, { opacity: 0, transform: "translateX(120%)" }],
    { duration: 900, easing: "cubic-bezier(.65,0,.35,1)", delay: 80 });
  hero.querySelectorAll(".board-team .logo").forEach((el, i) => el.animate([{ transform: "scale(.4) rotate(-8deg)", opacity: 0 }, { transform: "none", opacity: 1 }],
    { duration: 720, easing: SPRING, delay: 260 + i * 90, fill: "backwards" }));
  hero.querySelectorAll(".board-team .pts, .hero-reason").forEach((el, i) => el.animate([{ transform: "translateY(10px)", opacity: 0 }, { transform: "none", opacity: 1 }],
    { duration: 520, easing: EASE_OUT, delay: 380 + i * 60, fill: "backwards" }));
}

/* ---------- actions ---------- */
ACTIONS["now-act"] = async (el) => {
  const a = el.dataset.a;
  const gid = get(app.S, "now.game_id");
  if (a === "mute") {
    const ok = await confirmSheet({ title: "Never show this game?", body: "Autopilot leaves it and won't switch back. Unmute it from its game card any time.", ok: "Mute it", danger: true, ic: "mute" });
    if (!ok) return;
  }
  await act(a, gid && a !== "back" && a !== "saw_it" ? { game_id: gid } : {}, el);
};
ACTIONS["hold"] = (el, e) => {
  e && e.stopPropagation();
  act("hold", { game_id: el.dataset.gid, ...(el.dataset.force ? { value: "force" } : {}) }, el);
};
ACTIONS["go-redzone"] = (el) => act("redzone", {}, el);
CHANGES["autopilot"] = async (el) => {
  const m = get(app.S, "mode") || {};
  const r = await act(el.checked ? (m.hold ? "release" : "resume") : "pause", {}, el);
  if (!r || r.ok === false) el.checked = !el.checked;
};
CHANGES["two-stick"] = async (el) => { const r = await act("two_stick", { value: el.checked }, el); if (!r || r.ok === false) el.checked = !el.checked; };
ACTIONS["replay"] = (el, e) => { e && e.stopPropagation(); act("replay", { game_id: el.dataset.gid, value: el.dataset.play }, el); };

/* ---------- Focus sheet ---------- */
function focusSheet() {
  const S = app.S || {};
  const m = S.mode || {};
  const focused = m.focus_mode && m.focus_mode !== "autopilot" && m.focus_game;
  const mode = m.focus_mode === "focus_must_see" ? "focus_must_see" : "focus";
  const live = games().filter((g) => g.phase === "live").sort((a, b) => (b.mine - a.mine) || num(b.score) - num(a.score));
  const pre = games().filter((g) => g.phase === "pre").sort((a, b) => num(a.start_time) - num(b.start_time)).slice(0, 10);
  const pick = (g) => `<button class="pick" data-act="focus-pick" data-gid="${attr(g.id)}" aria-pressed="${!!focused && m.focus_game === g.id}">
    <span class="logo-pair">${logo(g.away, { size: 30 })}${logo(g.home, { size: 30 })}</span>
    <span class="grow"><span class="t1" style="display:block">${esc(g.matchup)}${g.mine ? ` ${icon("star", "ico--fill ico--xs")}` : ""}</span><span class="t2 ell" style="display:block">${esc(g.phase === "live" ? status(g).text + (g.reason ? " · " + g.reason : "") : fmtStart(g.start_time))}</span></span>
    <span class="tick">${icon("check")}</span></button>`;
  return `<h3>What's on the TV?</h3>
    <p class="lead">Autopilot chases the best moment anywhere. Focus keeps one game on and only leaves during its breaks.</p>
    <div class="pick-list" style="margin-top:16px">
      <button class="pick" data-act="focus-pick" data-gid="" aria-pressed="${!focused}">
        <span class="pick-ic">${icon("autopilot")}</span><span class="grow"><span class="t1" style="display:block">Autopilot</span><span class="t2" style="display:block">Best game anywhere · your teams first</span></span><span class="tick">${icon("check")}</span></button>
    </div>
    <div class="sheet-sec"><h4>When focused</h4></div>
    ${seg([{ v: "focus", label: "Only its breaks" }, { v: "focus_must_see", label: "+ Must-see moments" }], mode, { act: "focus-mode", label: "Focus style" })}
    ${live.length ? `<div class="sheet-sec"><h4>Live</h4><small>${live.length} games</small></div><div class="pick-list">${live.map(pick).join("")}</div>` : ""}
    ${pre.length ? `<div class="sheet-sec"><h4>Starting soon</h4></div><div class="pick-list">${pre.map(pick).join("")}</div>` : ""}`;
}
ACTIONS["open-focus"] = () => openSheet({ id: "focus", live: true, label: "Focus", render: focusSheet });
ACTIONS["focus-pick"] = async (el) => {
  const gid = el.dataset.gid;
  const mode = get(app.S, "mode.focus_mode") === "focus_must_see" ? "focus_must_see" : "focus";
  const r = gid ? await act("focus", { game_id: gid, value: mode }, el) : await act("autopilot", {}, el);
  if (r && r.ok !== false) closeSheet(sheetById("focus"));
};
ACTIONS["focus-mode"] = (el) => {
  const gid = get(app.S, "mode.focus_game");
  app.ui.focusMode = el.dataset.v;
  if (gid) act("focus", { game_id: gid, value: el.dataset.v }, el);
  else { const s = sheetById("focus"); el.closest(".seg").style.setProperty("--i", el.dataset.v === "focus" ? 0 : 1); if (s) s.update(); }
};

/* ---------- More sheet ---------- */
function moreSheet() {
  const S = app.S || {};
  const n = S.now;
  const g = n ? gameById(n.game_id) : null;
  const gid = n ? n.game_id : "";
  const locked = !!(n && get(S, "mode.locked_game") === gid);
  const dis = n ? "" : "disabled";
  const tile = (a, ic, t, sub, cls = "", extra = "") => `<button class="act-tile ${cls}" data-act="more-act" data-a="${a}" data-gid="${attr(gid)}" ${extra}>${icon(ic)}<span>${esc(t)}<small>${esc(sub)}</small></span></button>`;
  return `<h3>${n ? esc(n.matchup || (g && g.matchup)) : "Controls"}</h3>
    <p class="lead">${n ? esc(n.reason) : "Nothing is on yet — these work once a game is on."}</p>
    <div class="actions-grid">
      <button class="act-tile act-tile--wide act-tile--accent" data-act="more-act" data-a="saw_it">${icon("eye", "ico--lg")}<span>I just saw it<small>Tap the instant a big play shows on your TV — it learns your stream delay.</small></span></button>
      ${tile(locked ? "unlock" : "lock", locked ? "unlock" : "lock", locked ? "Unlock" : "Lock here", locked ? "Let autopilot move again" : "Stay on this game", "", dis)}
      ${tile(g && g.boosted ? "unboost" : "boost", "flame", g && g.boosted ? "Remove boost" : "Boost", "Rank this game higher", "", dis)}
      ${tile("mute", "mute", "Mute game", "Never switch to it", "", dis)}
      ${tile("set-channel", "tv", "Set channel", "Fix where it's on", "", dis)}
      ${tile("vote-up", "thumb-up", "Good switch", "Teach it what you like", "act-tile--good", dis)}
      ${tile("vote-down", "thumb-down", "Bad switch", "Teach it what to avoid", "act-tile--bad", dis)}
    </div>`;
}
ACTIONS["open-more"] = () => openSheet({ id: "more", live: true, label: "More controls", render: moreSheet });
ACTIONS["more-act"] = async (el) => {
  const a = el.dataset.a, gid = el.dataset.gid;
  if (a === "set-channel") { ACTIONS["set-channel"](el); return; }
  if (a === "mute") {
    const ok = await confirmSheet({ title: "Never show this game?", body: "Autopilot won't switch to it again. Unmute it from its game card any time.", ok: "Mute it", danger: true, ic: "mute" });
    if (!ok) return;
  }
  const map = { "vote-up": ["vote", 1], "vote-down": ["vote", -1] };
  const [action, value] = map[a] || [a, undefined];
  const extra = {};
  if (gid && action !== "saw_it") extra.game_id = gid;
  if (value !== undefined) extra.value = value;
  const r = await act(action, extra, el);
  if (r && r.ok !== false && action !== "saw_it") closeSheet(sheetById("more"));
};

