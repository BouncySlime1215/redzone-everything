/* RedZone Everything — shared game components: hero scoreboard, game card, chips, leaders. */

import {
  app, attr, esc, num, get, arr, status, phase, teamVars, pairInks, ink, isDark, whereLabel, whereUnknown,
  leaderLabel, winner, teamBy, fmtDur, nowS, fmtCountdown, imgSrc, games, hex,
} from "./rz-core.js?v=20";
import { icon, logo, leagueImg, avatar, odo, meter, heat, stateBadge } from "./rz-ui.js?v=20";
import { situation, wpBar } from "./rz-viz.js?v=20";

/* ---------- chips ---------- */
export function whereChip(g, { onDark = false } = {}) {
  const gid = attr(g && (g.id || g.game_id));
  if (whereUnknown(g)) {
    return `<button class="chip chip--warn chip--btn where" data-act="set-channel" data-gid="${gid}">${icon("tv")}<span>Set channel</span></button>`;
  }
  const label = whereLabel(g).replace(/\s*\((event link|channel)[^)]*\)\s*/gi, " ").trim();
  const needs = g && g.where && typeof g.where === "object" && g.where.tunable === false;
  return `<button class="chip chip--where chip--btn where" data-act="set-channel" data-gid="${gid}" aria-label="Playing on ${attr(label)}. Change channel">${icon("tv")}<span>${esc(label)}</span>${needs ? `<i class="needs" title="Link not set"></i>` : ""}</button>`;
}
export const isGarbage = (g) => arr(g && g.tags).some((t) => /garbage/i.test(String(t))) || /^garbage time/i.test(String((g && g.reason) || ""));
export const isComeback = (g) => /comeback/i.test(String((g && g.reason) || ""));
export const cleanReason = (r) => String(r || "").replace(/^garbage time\s*[—–-]\s*/i, "");
export function tagChips(g, { max = 4 } = {}) {
  const abbrs = [get(g, "home.abbr"), get(g, "away.abbr")];
  return arr(g && g.tags).filter((t) => !/garbage/i.test(String(t))).slice(0, max).map((t) => {
    const s = String(t);
    if (/^your bet/i.test(s)) return `<span class="chip chip--bet">${icon("ticket")}<span>${esc(s.replace(/^your bet:\s*/i, ""))}</span></span>`;
    if (abbrs.includes(s)) return `<span class="chip chip--mine">${icon("star", "ico--fill")}<span>${esc(s)}</span></span>`;
    if (/boost/i.test(s)) return `<span class="chip chip--hot">${icon("flame")}<span>${esc(s)}</span></span>`;
    return `<span class="chip">${icon("zap")}<span>${esc(s)}</span></span>`;
  }).join("");
}
export function stateChips(g) {
  const out = [];
  if (g.on_now) out.push(`<span class="chip chip--live">${icon("tv")}<span>On now</span></span>`);
  if (g.is_break && g.phase === "live") out.push(`<span class="chip chip--break">${icon("megaphone")}<span>Break</span></span>`);
  if (g.lock && g.phase === "live") out.push(`<span class="chip chip--locked">${icon("lock")}<span>${esc(g.lock)}</span></span>`);
  if (g.pre_moment && g.phase === "live") out.push(`<span class="chip chip--live">${icon("zap")}<span>${esc(g.pre_moment)}</span></span>`);
  if (g.boosted) out.push(`<span class="chip chip--hot">${icon("flame")}<span>Boosted</span></span>`);
  if (g.muted) out.push(`<span class="chip">${icon("mute")}<span>Muted</span></span>`);
  if (g.focus) out.push(`<span class="chip chip--locked">${icon("target")}<span>Focus</span></span>`);
  if (g.stale) out.push(`<span class="chip chip--break">${icon("clock")}<span>Data delayed</span></span>`);
  if (g.phase === "live" && isComeback(g)) out.push(`<span class="chip chip--hot">${icon("trend")}<span>Comeback</span></span>`);
  if (g.phase === "live" && isGarbage(g)) out.push(`<span class="chip chip--muted">${icon("moon")}<span>Garbage time</span></span>`);
  return out.join("");
}

/* ---------- leaders ---------- */
function pickLeaders(g, n = 2) {
  const ls = arr(g && g.leaders);
  const a = get(g, "away.abbr"), h = get(g, "home.abbr");
  const out = [];
  const fa = ls.find((l) => l.team === a), fh = ls.find((l) => l.team === h);
  if (fa) out.push(fa);
  if (fh) out.push(fh);
  for (const l of ls) if (out.length < n && !out.includes(l)) out.push(l);
  return out.slice(0, n);
}
export function leaderMini(l, g, { size = 34, dark = isDark() } = {}) {
  const t = teamBy(g, l.team);
  return `<div class="ld">${avatar(l, { size, team: t, dark })}<div style="min-width:0"><div class="nm">${esc(l.short || l.name)}</div><div class="st"><em>${esc(leaderLabel(l))}</em>${esc(l.stat)}</div></div></div>`;
}

/* ---------- game card ---------- */
function possessionFor(g, t) {
  const s = (g && g.situation) || {};
  if (g.phase !== "live" || !t) return "";
  if ((g.league === "nfl" || g.league === "nba") && s.possession === t.abbr) return icon("football", "pss");
  if (g.league === "nhl" && s.power_play === t.abbr) return `<span class="pp-tag">PP</span>`;
  if (g.league === "nhl" && s.empty_net === t.abbr) return `<span class="pp-tag">EN</span>`;
  if (g.league === "mlb" && s.half) {
    const bats = /^top/i.test(s.half) ? g.away : g.home;
    if (bats === t) return icon("activity", "pss");
  }
  return "";
}
function teamRow(g, side, inkc) {
  const t = (side === "a" ? g.away : g.home) || {};
  const ph = phase(g);
  const w = winner(g);
  const trail = (ph === "live" || ph === "post") && w && w !== side;
  const showScore = ph === "live" || ph === "post";
  return `<div class="gc-t ${trail ? "trail" : ""}" style="--tc-ink:${inkc}">
    ${logo(t, { size: 34 })}
    <div class="nm"><span class="ab">${esc(t.abbr)}</span><span class="full">${esc(t.name, "")}</span><span class="rc">${esc(t.record, "")}</span></div>
    <div class="pts">${possessionFor(g, t)}${showScore ? odo(t.score ?? "—") : ""}</div>
  </div>`;
}
export function gameCard(g, { key = true } = {}) {
  const ph = phase(g);
  const st = status(g);
  const [ai, hi] = pairInks(g.away, g.home);
  const garbage = ph === "live" && isGarbage(g);
  const cls = ["gc", `gc--${ph}`, g.mine ? "gc--mine" : "", g.on_now ? "gc--now" : "", g.muted ? "gc--muted" : "", garbage ? "gc--garbage" : ""].join(" ");
  const statusHtml = ph === "live"
    ? `<span class="gc-status gc-status--${st.kind === "break" ? "break" : "live"}">${esc(st.text)}</span>`
    : ph === "pre" ? "" : `<span class="gc-status">${esc(st.text)}</span>`;
  const right = ph === "live" ? heat(g.score)
    : ph === "pre" ? `<div class="start"><b>${esc(st.clk || "TBD")}</b>${g.start_time && g.start_time - nowS() < 6 * 3600 && g.start_time > nowS() ? `<small>in <span data-countdown="${num(g.start_time)}" data-own>${esc(fmtCountdown(g.start_time))}</span></small>` : ""}</div>`
    : "";
  const chips = stateChips(g) + tagChips(g);
  const leaders = pickLeaders(g);
  const reason = ph === "live" ? cleanReason(g.reason) : ph === "pre" ? g.odds : "";
  return `<article class="${cls}" ${key ? `data-key="g:${attr(g.id)}"` : ""} data-act="open-game" data-gid="${attr(g.id)}" role="button" tabindex="0" aria-label="${attr(`${g.matchup || ""}, ${st.text}`)}" style="${teamVars(g.away, g.home)}">
    <i class="gc-fx"></i>
    <header class="gc-head">${leagueImg(g.league)}${statusHtml}${g.mine ? `<span class="chip chip--mine" style="height:22px;padding:0 7px">${icon("star", "ico--fill")}</span>` : ""}${right}</header>
    <div class="gc-teams">${teamRow(g, "a", ai)}${teamRow(g, "h", hi)}</div>
    ${ph === "live" ? `<div class="gc-meter">${meter(g.score)}</div>` : ""}
    ${reason ? `<p class="gc-reason ${ph === "live" && isComeback(g) ? "gc-reason--hot" : ""}">${esc(reason)}</p>` : ""}
    <footer class="gc-foot"><div class="chips">${chips}</div>${ph !== "post" ? whereChip(g) : ""}</footer>
    ${leaders.length ? `<div class="gc-lead">${leaders.map((l) => leaderMini(l, g)).join("")}</div>` : ""}
  </article>`;
}

/* ---------- hero scoreboard (Now) ---------- */
function replayInfo(n, reason) {
  const m = reason.match(/^(catch-up|instant replay)\s*:\s*(.+)$/i);
  if (n.kind !== "replay" && !m) return null;
  const label = (n.replay && n.replay.label) || (m && m[2]) || "";
  if (!label && n.kind !== "replay") return null;
  return { label: label || "Replaying a big play", prefix: m ? m[1].toLowerCase() : "catch-up", fromReason: !!m };
}
export function heroKind(n, g, mode) {
  if (n.kind === "replay" || /^(catch-up|instant replay)\s*:/i.test(String(n.reason || ""))) return "replay";
  if (mode.paused) return "paused";
  if (mode.state === "break" || (g && g.is_break)) return "break";
  if (mode.locked_game && mode.locked_game === n.game_id) return "locked";
  if (mode.state === "locked") return "locked";
  return "live";
}
function boardTeam(g, side) {
  const t = (side === "a" ? g.away : g.home) || {};
  const w = winner(g);
  const s = g.situation || {};
  let poss = "";
  if (g.phase === "live") {
    if ((g.league === "nfl" || g.league === "nba") && s.possession && s.possession === t.abbr) poss = `${icon("football")}Ball`;
    else if (g.league === "nhl" && s.power_play === t.abbr) poss = `${icon("zap")}Power play`;
    else if (g.league === "nhl" && s.empty_net === t.abbr) poss = `${icon("alert")}Empty net`;
    else if (g.league === "mlb" && s.half && ((/^top/i.test(s.half) && side === "a") || (/^bot/i.test(s.half) && side === "h"))) poss = `${icon("activity")}At bat`;
  }
  return `<div class="board-team ${side === "h" ? "board-team--h" : ""}">
    ${logo(t, { size: 64, dark: true, eager: true, cls: "logo--shadow" })}
    <div class="tm"><span class="ab">${esc(t.abbr)}</span><span class="rc">${esc(t.record, "")}</span></div>
    <div class="pts ${w && w !== side ? "trail" : ""}">${odo(t.score ?? "—")}</div>
    <div class="poss">${poss}</div>
  </div>`;
}
export function board(g) {
  const st = status(g);
  const s = g.situation || {};
  let mid;
  if (g.league === "mlb" && g.phase === "live") {
    mid = `<span class="per">${esc(st.per || st.text)}</span>${s.outs !== null && s.outs !== undefined ? `<span class="clk sm">${num(s.outs)} out</span>` : ""}`;
  } else if (st.per) {
    mid = `<span class="per">${esc(st.per)}</span><span class="clk">${esc(st.clk, "")}</span>`;
  } else {
    mid = `<span class="clk sm">${esc(st.clk || st.text)}</span>`;
  }
  return `<div class="board">${boardTeam(g, "a")}<div class="board-mid">${mid}</div>${boardTeam(g, "h")}</div>`;
}
export function heroCard(n, g, S) {
  const game = g ? { ...n, ...g } : n;
  const mode = (S && S.mode) || {};
  const kind = heroKind(n, game, mode);
  const sp = game.spotlight;
  const spTeam = sp ? teamBy(game, sp.team) : null;
  const venue = get(game, "venue.image");
  const rawReason = String(n.reason || game.reason || "");
  const rp = replayInfo(n, rawReason);
  const reason = rp && rp.fromReason ? (rp.prefix === "instant replay" ? "Instant replay while the game is on a break." : "Catch-up replay of a play you missed — back to live right after.") : cleanReason(rawReason);
  const hold = rawReason.match(/letting the last play finish[^()]*\((\d+)\s*s\)/i);
  const tags = arr(game.tags);
  const chips = [
    `<button class="chip chip--btn where" data-act="set-channel" data-gid="${attr(n.game_id)}">${icon("tv")}<span>${esc(typeof n.where === "string" && n.where ? n.where : whereLabel(game) || "Set channel")}</span></button>`,
    `<span class="chip chip--hot">${icon("flame")}<span>Heat ${Math.round(num(n.score ?? game.score))}</span></span>`,
    hold ? `<span class="chip chip--break">${icon("hourglass")}<span>Finishing play · ${esc(hold[1])}s</span></span>` : "",
    isComeback(game) ? `<span class="chip chip--hot">${icon("trend")}<span>Comeback</span></span>` : "",
    game.lock ? `<span class="chip chip--locked">${icon("lock")}<span>${esc(game.lock)}</span></span>` : "",
    game.pre_moment ? `<span class="chip">${icon("zap")}<span>${esc(game.pre_moment)}</span></span>` : "",
    tags.length ? tagChips(game, { max: 3 }) : "",
  ].join("");
  const since = num(n.since, 0);
  return `<article class="hero on-dark ${kind === "replay" ? "is-replay" : ""}" data-key="hero:${attr(n.game_id)}" data-gid="${attr(n.game_id)}" data-act="open-game" style="${teamVars(game.away, game.home, true)}">
    <div class="hero-bg">${venue ? `<img src="${attr(imgUrl(venue))}" data-src="${attr(imgUrl(venue))}" data-orig="${attr(venue)}" alt="" decoding="async" fetchpriority="high">` : ""}</div>
    <i class="hero-fx"></i><i class="hero-sweep"></i>
    <div class="hero-in">
      <div class="hero-top"><div class="left">${stateBadge(kind)}${leagueImg(game.league, { dark: true })}${game.mine ? `<span class="chip chip--mine" style="height:24px">${icon("star", "ico--fill")}<span>Your team</span></span>` : ""}</div>
        <span class="on-for">${icon("clock")}on for <b data-since="${since}" data-own>${since ? fmtDur(nowS() - since) : "—"}</b></span></div>
      ${board(game)}
      ${wpBar(game)}
      ${situation(game)}
      ${kind === "replay" && rp ? `<div class="hero-replay">${icon("replay")}<span class="grow"><b>${esc(rp.prefix === "instant replay" ? "Instant replay" : "Catch-up replay")}</b>${esc(rp.label)}</span></div>` : ""}
      <p class="hero-reason">${esc(reason, "Watching the best game on.")}</p>
      <div class="hero-chips">${chips}</div>
      ${sp ? `<div class="spot">${avatar(sp, { size: 48, team: spTeam, dark: true })}<div class="grow"><div class="who">${esc(sp.short || sp.name)}<small>${esc(sp.team, "")} · Spotlight</small></div><div class="what clamp2">${esc(sp.text)}</div></div></div>` : `<div class="hero-pad"></div>`}
    </div>
  </article>`;
}
const imgUrl = (u) => imgSrc(u).src;

/* ---------- fantasy matchup ---------- */
function teamColorByAbbr(abbr) {
  for (const g of games()) {
    if (g.league !== "nfl") continue;
    if (get(g, "home.abbr") === abbr) return ink(g.home);
    if (get(g, "away.abbr") === abbr) return ink(g.away);
  }
  const t = app.teams && Array.isArray(app.teams.nfl) ? app.teams.nfl.find((x) => x.abbr === abbr) : null;
  return t ? ink(t) : "#4a5163";
}
export function fantasyCard(f) {
  if (!f) return "";
  if (f.error) {
    return `<div class="card fan fan--err" data-key="fan"><div class="row" style="gap:12px;align-items:flex-start">
      <span class="pick-ic" style="background:color-mix(in srgb,var(--break) 16%,transparent);color:var(--break-ink)">${icon("alert")}</span>
      <div class="grow"><div class="t1" style="font:650 15px/1.25 var(--font-ui)">Fantasy isn't connected</div><div class="t2" style="font-size:13px;color:var(--text-2);margin-top:3px">${esc(f.error)}</div></div></div>
      <button class="btn btn--sm btn--block" style="margin-top:12px" data-act="go-fantasy">${icon("sliders")}Set up fantasy</button></div>`;
  }
  const my = num(f.my_points, NaN), op = num(f.opp_points, NaN);
  const both = Number.isFinite(my) && Number.isFinite(op);
  const share = both && my + op > 0 ? (my / (my + op)) * 100 : 50;
  const lead = both ? my - op : 0;
  const starters = arr(f.starters);
  const pts = (v) => (Number.isFinite(v) ? v.toFixed(1) : "—");
  return `<div class="card fan" data-key="fan">
    <div class="fan-top">
      <div class="fan-side"><span class="fan-who">You</span><span class="fan-nm ell">${esc(f.me, "My team")}</span><b class="fan-pts ${both && lead >= 0 ? "lead" : ""}">${pts(my)}</b></div>
      <span class="fan-vs">${both ? (lead >= 0 ? `+${lead.toFixed(1)}` : lead.toFixed(1)) : "vs"}</span>
      <div class="fan-side fan-side--opp"><span class="fan-who">Opponent</span><span class="fan-nm ell">${esc(f.opponent, "Opponent")}</span><b class="fan-pts ${both && lead < 0 ? "lead" : ""}">${pts(op)}</b></div>
    </div>
    <div class="fan-bar" style="--p:${share.toFixed(1)}"><i></i></div>
    ${starters.length ? `<div class="fan-st">${starters.slice(0, 8).map((p) => `<span class="fan-p ${p.mine ? "mine" : ""}">${avatarInit(p, teamColorByAbbr(p.team))}<span class="ell"><b>${esc(p.name)}</b><small>${esc(p.pos, "")}${p.team ? ` · ${esc(p.team)}` : ""}</small></span></span>`).join("")}</div>` : ""}
  </div>`;
}
function avatarInit(p, ring) {
  return `<span class="avatar" style="--s:28px;--ring:${ring}"><b class="on" style="font-size:11px">${esc((String(p.name || "").split(/\s+/).map((w) => w[0]).join("").slice(0, 2) || "?").toUpperCase())}</b></span>`;
}

export { pickLeaders, ink };
