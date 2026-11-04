/* RedZone Everything — ?mock=1 design preview. Loads mock_*.json (built from real ESPN data),
   shifts timestamps to "now", and gently simulates a live night (clocks run, scores tick,
   heat moves, the TV switches games) so every animation can be seen. ?mock=1&still=1 freezes it. */

import { Q, num, nowS } from "./rz-core.js?v=41";

const J = (p) => fetch(`/static/${p}?v=40`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
let STATE = null, GAMES = null, TEAMS = null, ROSTERS = null, H = null;
let lastSwitch = 0;

const CONFIG = {
  mode: { dry_run: true, leagues: { nfl: true, nba: true, nhl: true, mlb: true } },
  sunday_ticket: true, market: "raleigh-durham",
  teams: { nfl: ["WSH"], nba: ["WSH"], nhl: ["WSH"], mlb: ["WSH"] },
  polling: { live_s: 10, idle_s: 300 },
  score: { neutral_cap: 0.9, personal_cap: 30, smoothing_half_life_s: 30,
    personal: { my_team: 25, bet_max: 20, fantasy_mine: 12, fantasy_opponent: 8, manual_boost: 15 },
    nfl: { two_minute_drill: 15, uncertainty: 15, player_going_off: 8 }, nhl: { power_play: 15, empty_net: 30 } },
  decider: { min_dwell_s: 45, switch_margin: 10, max_switches_per_5min: 4, min_gap_s: 20, return_tolerance: 5,
    break_destination_min: 25, chase_min_dwell_s: 20, must_see_score: 92, must_see_lead: 20 },
  replay: { enabled: true, max_age_s: 1800, clip_s: 15 },
  delay: { default: { youtubetv: 45, prime: 40 }, sigma_default: 15 },
};
const LIMITS = { "polling.live_s": [5, 60], "polling.idle_s": [30, 3600], "score.personal_cap": [0, 60],
  "decider.min_dwell_s": [10, 600], "decider.min_gap_s": [5, 300], "decider.max_switches_per_5min": [1, 20],
  score: [-100, 100], decider: [0, 1000], delay: [0, 180], replay: [0, 7200] };
const CHANNELS = [["espn", "ESPN", "D1vdtklRyHg"], ["espn2", "ESPN2", null], ["abc", "ABC", null], ["cbs", "CBS", "aB3dEfGhIjK"],
  ["fox", "FOX", "Zx9Yw8Vu7Ts"], ["nbc", "NBC", null], ["nflnetwork", "NFL Network", null], ["redzone", "NFL RedZone", "Rz0ne000001"],
  ["tnt", "TNT", null], ["nhlnetwork", "NHL Network", null], ["mlbnetwork", "MLB Network", null]].map(([key, label, code]) => ({ key, label, code }));

function shiftTimes(data) {
  const d = nowS() - num(data.ts, nowS());
  (function walk(o) {
    if (!o || typeof o !== "object") return;
    for (const k of Object.keys(o)) {
      if (["ts", "since", "at", "start_time", "frozen_until", "server_time"].includes(k) && typeof o[k] === "number") o[k] += d;
      else walk(o[k]);
    }
  })(data);
}
const copy = (x) => JSON.parse(JSON.stringify(x));
const emit = () => H && H.state(copy(STATE));
const byId = (id) => STATE.games.find((g) => g.id === id);

export async function startMock(handlers) {
  H = handlers;
  H.conn("mock");
  const [s, g, t, r] = await Promise.all([J("mock_state.json"), J("mock_game.json"), J("mock_teams.json"), J("mock_roster.json")]);
  if (!s) { H.conn("wait"); return; }
  shiftTimes(s);
  STATE = s; GAMES = g || { games: {} }; TEAMS = t || {}; ROSTERS = r || {};
  if (!STATE.tonight) STATE.tonight = mockTonight(STATE.games || []);
  lastSwitch = nowS();
  emit();
  if (!Q.has("still")) setInterval(simulate, 9000);
}

/* "Tonight's plan" (redzone/plan.py Tonight.view) for the mock slate: a mix of every row state. */
function mockTonight(games) {
  const fmt = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const states = [["ready", "verified", "played in 6s"], ["ready", "pending", ""], ["needs setup", "n/a", ""],
    ["ready", "failed", "video never started"], ["ready", "pending", ""], ["blackout", "n/a", ""]];
  const rows = games.filter((g) => g.start_time).sort((a, b) => a.start_time - b.start_time).map((g, i) => {
    const [status, check, detail] = states[i % states.length];
    return { game_id: g.id, league: g.league, matchup: g.matchup, start: g.start_time, start_et: fmt(g.start_time),
      mine: !!g.mine, status, why: status === "blackout" ? "Prime Video: blacked out here" : status === "needs setup" ? "TNT · YouTube TV" : "",
      source: status === "blackout" ? "" : (g.where && g.where.label) || "ESPN · YouTube TV", fallbacks: ["Prime Video (2 feeds)"],
      check, check_detail: detail, gaps: status === "needs setup" ? ["No YouTube TV code for TNT: paste its link in Settings"] : [] };
  });
  const next = rows.find((r) => r.start > nowS());
  return { day: "", games: rows, gaps: ["No YouTube TV code for TNT: paste its link in Settings", "Peacock is signed in on one stick only (4K 2023)"],
    note: "", checking: null, checking_games: rows.filter((r) => r.check === "pending").slice(0, 1).map((r) => r.game_id),
    counts: { games: rows.length, ready: rows.filter((r) => r.status === "ready").length,
      verified: rows.filter((r) => r.check === "verified").length, failed: rows.filter((r) => r.check === "failed").length },
    next_window: next ? { start: next.start, start_et: next.start_et, games: [next.game_id] } : null };
}

/* ---------- live-looking simulation ---------- */
function clockDown(g, secs) {
  const m = String(g.clock || "").match(/^(\d+):(\d\d)$/);
  if (!m) return;
  let left = Math.max(0, +m[1] * 60 + +m[2] - secs);
  g.clock = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
}
function simulate() {
  const live = STATE.games.filter((g) => g.phase === "live");
  for (const g of live) {
    if (g.league !== "mlb") clockDown(g, 12 + Math.floor(Math.random() * 25));
    g.score = Math.max(3, Math.min(99, Math.round(g.score + (Math.random() * 10 - 5))));
    g.moment = g.score;
    if (g.wp != null) g.wp = Math.max(0.02, Math.min(0.98, +(g.wp + (Math.random() * 0.08 - 0.04)).toFixed(3)));
    if (g.league === "nba" && Math.random() < 0.55) {
      const side = Math.random() < 0.5 ? "home" : "away";
      g[side].score += Math.random() < 0.35 ? 3 : 2;
    } else if (g.league === "nfl" && Math.random() < 0.12) {
      const s = g.situation || {};
      const side = g.home.abbr === s.possession ? "home" : "away";
      g[side].score += Math.random() < 0.7 ? 7 : 3;
    }
  }
  STATE.games.sort((a, b) => ({ live: 0, pre: 1, post: 2 }[a.phase] - { live: 0, pre: 1, post: 2 }[b.phase]) || b.score - a.score);
  // every ~45s the "TV" follows the hottest game (shows the hero transition)
  if (nowS() - lastSwitch > 40 && !STATE.mode.paused && !STATE.mode.locked_game) {
    const best = live.filter((g) => !g.muted && g.id !== STATE.now.game_id).sort((a, b) => b.score - a.score)[0];
    if (best && Math.random() < 0.5) switchTo(best.id, "better", `${best.reason || "best game on"}`);
  }
  syncNow();
  emit();
}
function syncNow() {
  if (!STATE.now) return;
  const g = byId(STATE.now.game_id);
  if (!g) return;
  const keep = { game_id: g.id, where: (g.where && g.where.label) || STATE.now.where, reason: STATE.now.reason,
    since: STATE.now.since, kind: STATE.now.kind, replay: STATE.now.replay };
  STATE.now = { ...copy(g), ...keep };
  for (const x of STATE.games) x.on_now = x.id === g.id;
}
function switchTo(id, kind, reason) {
  const g = byId(id);
  if (!g) return;
  lastSwitch = nowS();
  const from = STATE.now && STATE.now.game_id;
  STATE.log.unshift({ ts: nowS(), kind, to: id, matchup: g.matchup, reason, dry_run: STATE.mode.dry_run, ok: true,
    took: +(1.8 + Math.random() * 1.5).toFixed(1), logo: g.home.logo });
  STATE.log = STATE.log.slice(0, 12);
  STATE.now = { ...copy(g), game_id: id, where: (g.where && g.where.label) || "", reason, since: nowS(), kind: "live", replay: null };
  if (from) {
    const prev = byId(from);
    STATE.next = prev ? { game_id: prev.id, matchup: prev.matchup, reason: prev.reason, why_waiting: "watching for a break before going back" } : null;
  }
  syncNow();
}

/* ---------- mock endpoints ---------- */
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const mineFor = (lg) => new Set(CONFIG.teams[lg] || []);
function teamsPayload() {
  const out = {};
  for (const [lg, rows] of Object.entries(TEAMS)) out[lg] = rows.map((t) => ({ ...t, mine: mineFor(lg).has(t.abbr) }));
  return out;
}
function gameForTeam(lg, id) {
  return copy(STATE.games.find((g) => g.league === lg && (g.home.id === id || g.away.id === id)) || null);
}

const TAPS = {};
export async function mockApi(path, { method, body } = {}) {
  await sleep(140 + Math.random() * 160);
  const m = (method || (body !== undefined ? "POST" : "GET")).toUpperCase();
  const b = body || {};
  let x;
  if (path === "/api/state") return copy(STATE);
  if ((x = path.match(/^\/api\/game\/([^/?]+)/))) {
    const g = byId(x[1]);
    const d = (GAMES.games && GAMES.games[x[1]]) || GAMES.default || { plays: [], wp_series: [], linescore: { home: [], away: [], labels: [] }, leaders: { home: [], away: [] }, venue: {} };
    if (!g) throw new Error("game not found");
    return { ...copy(d), game: copy(g), venue: d.venue && Object.keys(d.venue).length ? d.venue : g.venue };
  }
  if (path.startsWith("/api/v2/")) return mockV2(path, b);
  if (path === "/api/teams") return teamsPayload();
  if ((x = path.match(/^\/api\/teams\/(\w+)\/(\d+)/))) {
    const [, lg, id] = x;
    const r = ROSTERS[`${lg}:${id}`];
    const base = (TEAMS[lg] || []).find((t) => t.id === id) || {};
    const team = { ...base, ...(r ? r.team : {}), mine: mineFor(lg).has(base.abbr) };
    if (!team.record) team.record = "";
    return { team, roster: r ? copy(r.roster) : [], game: gameForTeam(lg, id) };
  }
  if (path === "/api/settings" && m === "GET") return { config: copy(CONFIG), limits: copy(LIMITS) };
  if (path === "/api/settings") { deepMerge(CONFIG, b.changes || {}); applyConfig(); emit(); return { ok: true, message: "Saved" }; }
  if (path === "/api/settings/rollback") return { ok: true, message: "Undid the last change" };
  if (path === "/api/channels" && m === "GET") return { channels: copy(CHANNELS), overrides: {} };
  if (path === "/api/channels") {
    const c = CHANNELS.find((c) => c.key === b.channel);
    const code = String(b.link || "").match(/watch\/([\w-]{6,})/);
    if (!code) return { ok: false, message: "That doesn't look like a tv.youtube.com/watch/… link" };
    if (c) c.code = code[1];
    return { ok: true, message: `Saved (${code[1]})` };
  }
  if (path === "/api/override") {
    const g = byId(b.game_id);
    if (g) g.where = b.target ? { label: String(b.target).replace(/^youtubetv:/, "").toUpperCase() + " · YouTube TV", confidence: "sure", tunable: true, target: b.target } : g.where;
    emit();
    return { ok: true, message: "Saved — I'll remember this" };
  }
  if (path === "/api/bets" && m === "GET") return copy(STATE.bets);
  if (path === "/api/bets") {
    const g = byId(b.game_id);
    const bet = { id: Date.now() % 100000, game_id: b.game_id, matchup: g ? g.matchup : "", kind: b.kind, team: b.team || "", line: b.line,
      side: b.side || "", note: b.note || "", sweat: g && g.phase === "live" ? 0.45 : 0 };
    bet.label = "your bet: " + betText(bet);
    STATE.bets.unshift(bet);
    emit();
    return { ok: true, message: "Bet added — I'll prioritize the sweat" };
  }
  if ((x = path.match(/^\/api\/bets\/(\d+)/))) {
    STATE.bets = STATE.bets.filter((t) => String(t.id) !== x[1]);
    emit();
    return { ok: true, message: "Removed" };
  }
  if (path === "/api/lab") return labPayload();
  if (path === "/api/action") {
    const r = action(b);
    if (!Q.has("v2") || !b.id || !r || r.ok === false) return r;
    // phone v2 tap states: a game tap waits ~3 s for a "safe moment", then the TV shows it
    const game = ["hold", "watch", "redzone", "focus"].includes(b.action) && b.game_id;
    if (game) TAPS[b.id] = { at: Date.now(), gid: b.game_id };
    return { ...r, id: b.id, state: game ? "waiting" : "done", on_tv: game ? false : undefined };
  }
  if (path.startsWith("/api/action/")) {
    const t = TAPS[decodeURIComponent(path.slice(12))];
    if (!t) throw new Error("unknown or expired tap id");
    const on = Date.now() - t.at > 3000;
    return { ok: true, id: path.slice(12), state: on ? "done" : "waiting", on_tv: on, game_id: t.gid };
  }
  return { ok: true, message: "(preview) saved" };
}

function betText(b) {
  if (b.kind === "spread") return `${b.team} ${num(b.line) > 0 ? "+" : ""}${b.line}`;
  if (b.kind === "moneyline") return `${b.team} ML`;
  if (b.kind === "total") return `${b.side === "under" ? "Under" : "Over"} ${b.line}`;
  return b.note || "Prop";
}
function deepMerge(t, s) {
  for (const [k, v] of Object.entries(s)) {
    if (v && typeof v === "object" && !Array.isArray(v)) { t[k] = t[k] || {}; deepMerge(t[k], v); } else t[k] = v;
  }
}
function applyConfig() {
  STATE.mode.dry_run = !!CONFIG.mode.dry_run;
  for (const g of STATE.games) {
    const mine = mineFor(g.league);
    g.mine = mine.has(g.home.abbr) || mine.has(g.away.abbr);
  }
}

function action(b) {
  const a = b.action, M = STATE.mode;
  const gid = b.game_id || (STATE.now && STATE.now.game_id);
  const g = byId(gid);
  let msg = "Done";
  switch (a) {
    case "pause": M.paused = true; M.state = "paused"; msg = "Auto-switching paused"; break;
    case "resume": M.paused = false; M.safe = false; M.frozen_until = null; M.state = "watching"; msg = "Back on autopilot"; break;
    case "dry_run": M.dry_run = !!b.value; CONFIG.mode.dry_run = M.dry_run; msg = b.value ? "Dry run on: the TV won't be touched" : "TV control ON"; break;
    case "focus": M.focus_game = gid; M.focus_mode = b.value || "focus"; msg = `Focused on ${g ? g.matchup : "that game"}`; if (g && STATE.now.game_id !== gid) switchTo(gid, "user", "you picked this game"); break;
    case "autopilot": M.focus_game = null; M.focus_mode = "autopilot"; msg = "Autopilot on"; break;
    case "boost": case "unboost": if (g) g.boosted = a === "boost"; msg = `${a === "boost" ? "Boosted" : "Unboosted"} ${g ? g.matchup : ""}`; break;
    case "mute": case "unmute": if (g) g.muted = a === "mute"; msg = `${a === "mute" ? "Muted" : "Unmuted"} ${g ? g.matchup : ""}`; break;
    case "lock": M.locked_game = gid; M.state = "locked"; msg = `Staying on ${g ? g.matchup : "this game"}`; break;
    case "unlock": M.locked_game = null; M.state = "watching"; msg = "Unlocked"; break;
    case "watch": case "hold": {
      if (!g || g.phase !== "live") return { ok: false, message: "That game isn't live" };
      M.paused = false; M.locked_game = gid; M.state = "locked";
      M.hold = { game_id: gid, matchup: g.matchup, since: nowS(), auto_resume: M.hold ? M.hold.auto_resume : true, resume_at: null, why: "", force: false, on: true, waiting: false };
      if (STATE.now.game_id !== gid) switchTo(gid, "user", "you selected this game");
      msg = `On ${g.matchup} — holding here`; break;
    }
    case "release": M.hold = null; M.locked_game = null; M.paused = false; M.focus_game = null; M.focus_mode = "autopilot"; M.state = "watching"; msg = "Back on autopilot"; break;
    case "hold_auto": if (!M.hold) return { ok: false, message: "Nothing is held" }; M.hold.auto_resume = !!b.value; M.hold.resume_at = null; msg = b.value ? "I'll hand back to autopilot when it's over or a blowout" : "Staying until you tap Resume auto"; break;
    case "two_stick": M.two_stick_enabled = !!b.value; M.two_stick = !!b.value; msg = b.value ? "Two-stick mode on" : "Two-stick mode off: one stick does everything"; break;
    case "redzone": return { ok: false, message: "NFL RedZone isn't on the air (or its channel isn't set)" };
    case "skip": {
      const cur = STATE.now && byId(STATE.now.game_id);
      if (cur) cur.muted = true;
      const best = STATE.games.filter((x) => x.phase === "live" && !x.muted).sort((p, q) => q.score - p.score)[0];
      if (best) switchTo(best.id, "better", best.reason || "best game on");
      msg = `Skipping ${cur ? cur.matchup : "this game"} for 5 minutes`; break;
    }
    case "back": {
      const prev = STATE.log.find((l) => l.to !== (STATE.now && STATE.now.game_id));
      if (!prev) return { ok: false, message: "Nowhere to go back to" };
      switchTo(prev.to, "user", "back to where you were");
      msg = `Back to ${prev.matchup}`; break;
    }
    case "replay": {
      const q = STATE.queue.find((x) => x.play_id === String(b.value));
      if (!q) return { ok: false, message: "That replay isn't available anymore" };
      switchTo(q.game_id, "replay", `catch-up: ${q.label}`);
      STATE.now.kind = "replay";
      STATE.now.replay = { label: q.label, game_id: q.game_id, play_id: q.play_id };
      STATE.queue = STATE.queue.filter((x) => x !== q);
      msg = `Replaying: ${q.label.slice(0, 60)}`; break;
    }
    case "vote": STATE.feedback = STATE.feedback || {}; STATE.feedback[b.value > 0 ? "up" : "down"] = num(STATE.feedback[b.value > 0 ? "up" : "down"]) + 1; msg = "Thanks — logged for training"; break;
    case "saw_it": msg = "Delay 44s (±3) for FOX · YouTube TV"; break;
    case "label": msg = `Labeled: ${b.value}`; break;
    default: return { ok: false, message: `Unknown action ${a}` };
  }
  for (const x of STATE.games) x.focus = x.id === M.focus_game;
  syncNow();
  setTimeout(emit, 60);
  return { ok: true, message: msg };
}

function labPayload() {
  return {
    backtests: [
      { title: "Sunday 1pm slate · Dec 14 (8 games)", summary: { mid_play_cuts: 0, big_plays_live: "27/82 (33%)", your_team_big_plays_live: "8/11", dead_air_pct: 20.2, switches_per_hour: 15.1, switches: 49 } },
      { title: "Sunday 4pm slate · Nov 30 (3 games)", summary: { mid_play_cuts: 0, big_plays_live: "19/29 (66%)", your_team_big_plays_live: "0/0", dead_air_pct: 35.7, switches_per_hour: 6.5, switches: 21 } },
    ],
    optimize: {
      slates: 14,
      current: { score: -1907.19, big_seen_pct: 92.4, big_live_pct: 39.0, dead_air_pct: 16.1, switches_per_hour: 26.8, mid_play_cuts: 2,
        params: { "decider.switch_margin": 10, "decider.min_dwell_s": 45, "decider.chase_min_dwell_s": 20, "replay.clip_s": 15 } },
      best: { score: -1903.05, big_seen_pct: 94.4, big_live_pct: 37.9, dead_air_pct: 12.7, switches_per_hour: 27.3, mid_play_cuts: 2,
        params: { "decider.switch_margin": 10, "decider.min_dwell_s": 60, "decider.chase_min_dwell_s": 15, "replay.clip_s": 12 } },
    },
    feedback: STATE.feedback || {},
  };
}

/* ---------- phone v2 (?mock&v2 turns the flag on) ---------- */
const V2 = { votes: {}, prefs: { bets_weight: 1, rivalry_points: 6, nhl_pp_ot_boost: 1, switch_frequency: 1, reels: true } };
const V2_RULES = [["yours", "Your team", /your team|UNC|WSH|Commanders|Capitals|Wizards/i], ["crunch", "Crunch time", /crunch|close|final|overtime/i],
  ["redzone", "Red zone", /red ?zone|goal[- ]to[- ]go/i], ["score", "Score", /touchdown|goal|scor/i], ["break", "Break elsewhere", /break|timeout|commercial/i],
  ["replay", "Catch-up", /replay|catch[- ]up|missed/i], ["manual", "You picked", /you (picked|asked)|manual/i]];
function mockV2(path, b) {
  if (path === "/api/v2/status") return { enabled: Q.has("v2") };
  if (path === "/api/v2/switches") return { enabled: true, switches: (STATE.log || []).map((l) => {
    const key = `${l.ts}|${l.to}`;
    const chips = V2_RULES.filter(([, , rx]) => rx.test(`${l.reason || ""} ${l.kind || ""}`)).slice(0, 4).map(([key, label]) => ({ key, label }));
    return { ...l, key, chips: chips.length ? chips : [{ key: "best", label: "Best on" }], vote: V2.votes[key] || 0 };
  }) };
  if (path === "/api/v2/vote") { V2.votes[b.key] = b.vote; return { ok: true, vote: b.vote, message: b.vote ? (b.vote > 0 ? "Good switch — noted" : "Bad switch — noted") : "Rating cleared" }; }
  if (path === "/api/v2/missed") {
    const live = (STATE.games || []).filter((g) => g.phase === "live").slice(0, 3);
    const c = live.map((g, i) => ({ game_id: g.id, matchup: g.matchup, text: "Touchdown pass", kind: "td", ago_s: 90 + i * 140 }));
    const guess = b.game_id ? c.find((x) => x.game_id === b.game_id) : c[0];
    return { ok: true, guess, candidates: c, message: guess ? `Logged: ${guess.matchup} (${guess.text}, ${Math.floor(guess.ago_s / 60)} min ago)` : "Logged" };
  }
  if (path === "/api/v2/slate") {
    const now = Date.now() / 1000;
    const L = { nfl: [190, 45], nba: [135, 25], nhl: [150, 35], mlb: [165, 30], cbb: [120, 20], cfb: [210, 50], wcbb: [120, 20] };
    const gs = (STATE.games || []).filter((g) => g.phase === "live" || g.phase === "pre").slice(0, 9).map((g, i) => {
      const [d, c] = L[g.league] || [150, 30];
      const start = g.phase === "live" ? now - 3600 + i * 600 : now + 1800 + i * 1500;
      const end = start + d * 60;
      return { game_id: g.id, league: g.league, matchup: g.matchup, live: g.phase === "live", start, end, crunch: [end - c * 60, end], yours: !!g.mine };
    });
    const cl = [];
    for (let i = 0; i < gs.length; i++) for (let j = i + 1; j < gs.length; j++) {
      const t0 = Math.max(gs[i].crunch[0], gs[j].crunch[0]), t1 = Math.min(gs[i].crunch[1], gs[j].crunch[1]);
      if (t1 - t0 > 120) { const [w, l] = gs[j].yours && !gs[i].yours ? [gs[j], gs[i]] : [gs[i], gs[j]]; cl.push({ t0, t1, games: [w.game_id, l.game_id], on_tv: w.matchup, second: [l.matchup], why: w.yours && !l.yours ? "your team" : "league order" }); }
    }
    return { enabled: true, now, games: gs, clashes: cl.sort((x, y) => x.t0 - y.t0).slice(0, 6), yours: gs.filter((g) => g.yours).length };
  }
  if (path.startsWith("/api/v2/night")) {
    const now = Date.now() / 1000, t0 = now - 4 * 3600;
    const live = (STATE.games || []).slice(0, 5);
    const segs = [], events = [];
    let t = t0;
    const reasons = ["Crunch time: close late playbook=CRUNCH-02", "FOX in commercial — break visit", "Your team in the red zone", "catch it live — big play", "Moment over, back home"];
    for (let i = 0; t < now - 300; i++) {
      const g = live[i % live.length] || { matchup: "Game " + i, id: "g" + i };
      const len = 300 + ((i * 937) % 1500);
      segs.push({ t0: t, t1: Math.min(now, t + len), game: g.id, matchup: g.matchup });
      const reason = reasons[i % reasons.length];
      events.push({ ts: t, type: "switch", kind: "better", game: g.id, matchup: g.matchup, reason, ok: i % 9 !== 4, dry_run: false,
        chips: V2_RULES.filter(([, , rx]) => rx.test(reason)).slice(0, 3).map(([key, label]) => ({ key, label })), playbooks: (reason.match(/playbook=([A-Z]+-\d+)/) || []).slice(1) });
      if (i % 4 === 1) events.push({ ts: t + 60, type: "decision", kind: "hold", game: g.id, matchup: g.matchup, reason: "wait for the whistle playbook=SAFE-07", chips: [], playbooks: ["SAFE-07"] });
      if (i % 5 === 2) events.push({ ts: t + 120, type: "tap", kind: i % 2 ? "up" : "down", game: g.id, matchup: g.matchup, reason });
      t += len;
    }
    const air = {};
    segs.forEach((x) => { air[x.matchup] = (air[x.matchup] || 0) + x.t1 - x.t0; });
    const pb = {};
    events.forEach((e) => (e.playbooks || []).forEach((k) => { pb[k] = (pb[k] || 0) + 1; }));
    const day = new Date().toISOString().slice(0, 10);
    const oseg = segs.map((x, i) => ({ ...x, ...(i % 3 === 1 ? live[(i + 1) % live.length] && { game: live[(i + 1) % live.length].id, matchup: live[(i + 1) % live.length].matchup } : {}) }));
    const markers = segs.flatMap((x, i) => [{ ts: x.t0 + 200, game: x.game, matchup: x.matchup, kind: "td", label: "", seen: true, on: x.matchup },
      ...(i % 3 === 1 ? [{ ts: x.t0 + 260, game: oseg[i].game, matchup: oseg[i].matchup, kind: "goal", label: "", seen: false, on: x.matchup }] : [])]).filter((m) => m.ts < now);
    return { enabled: true, day, nights: [day, "2026-11-03", "2026-11-02"], segments: segs, events,
      oracle: { objective: "combined", segments: oseg, agree_pct: 71.4 }, markers,
      big: { plays: markers.length, seen: markers.filter((m) => m.seen).length, missed: markers.filter((m) => !m.seen).length },
      summary: { switches: segs.length, failed: 1, games: Object.keys(air).length, taps: { up: 2, down: 1, missed: 1 },
        on_air: Object.entries(air).map(([matchup, s]) => ({ matchup, s: Math.round(s) })).sort((a, b) => b.s - a.s), playbooks: pb } };
  }
  if (path === "/api/v2/priority" && !Object.keys(b).length) {
    return { enabled: true, prefs: { ...V2.prefs }, ranges: { bets_weight: [0, 2], rivalry_points: [0, 15], nhl_pp_ot_boost: [0, 2], switch_frequency: [0.5, 2] },
      votes: Object.keys(V2.votes).length, suggestions: [] };
  }
  if (path === "/api/v2/priority") { Object.assign(V2.prefs, b); return { ok: true, message: "Saved", prefs: b }; }
  throw new Error("not in mock");
}
