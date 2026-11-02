/* RedZone Everything — ?mock=1 design preview. Loads mock_*.json (built from real ESPN data),
   shifts timestamps to "now", and gently simulates a live night (clocks run, scores tick,
   heat moves, the TV switches games) so every animation can be seen. ?mock=1&still=1 freezes it. */

import { Q, num, nowS } from "./rz-core.js?v=31";

const J = (p) => fetch(`/static/${p}?v=31`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
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
  lastSwitch = nowS();
  emit();
  if (!Q.has("still")) setInterval(simulate, 9000);
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
  if (path === "/api/action") return action(b);
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
    case "watch": if (g) switchTo(gid, "user", "you picked this game"); msg = `Switching to ${g ? g.matchup : "that game"}`; break;
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
