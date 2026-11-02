/* RedZone Everything — detail sheets: game detail, team + roster, set-channel; teams directory data. */

import {
  app, ACTIONS, hooks, store, attr, esc, num, get, arr, status, phase, teamVars, pairInks, ink, raw, onColor, lum,
  gameById, games, fmtStart, whereLabel, teamBy, imgSrc, vibrate,
} from "./rz-core.js?v=32";
import { icon, logo, leagueImg, avatar, empty, skel, stateBadge, chip } from "./rz-ui.js?v=32";
import { situation, wpBar, sparkline, linescore } from "./rz-viz.js?v=32";
import { board, gameCard } from "./rz-cards.js?v=32";
import { openSheet, sheetById, closeSheet, confirmSheet, toast, updateSheets } from "./rz-sheet.js?v=32";
import { api, act } from "./rz-api.js?v=32";
import { pop } from "./rz-dom.js?v=32";

/* ====================================================================== game detail */
async function loadDetail(gid, force = false) {
  const c = app.details.get(gid);
  if (c && !force && Date.now() - c.at < 15000) return c.data;
  try {
    const d = await api(`/api/game/${encodeURIComponent(gid)}`);
    app.details.set(gid, { data: d || {}, at: Date.now() });
  } catch (e) {
    app.details.set(gid, { data: { error: e.message }, at: Date.now() });
  }
  return app.details.get(gid).data;
}

export function openGame(gid) {
  if (!gid) return;
  const id = "game:" + gid;
  const s = openSheet({ id, live: true, full: true, flush: true, onImg: true, label: "Game details", render: () => gameSheet(gid) });
  loadDetail(gid).then(() => s.update());
  const iv = setInterval(() => {
    if (!sheetById(id)) { clearInterval(iv); return; }
    const g = gameById(gid);
    if (g && g.phase === "live") loadDetail(gid, true).then(() => s.update());
  }, 20000);
}

function gameSheet(gid) {
  const det = (app.details.get(gid) || {}).data || null;
  const g = gameById(gid) || (det && det.game);
  if (!g) {
    return det && det.error
      ? empty("scoreboard", "Game not found", esc(det.error), "", "", "gs-err")
      : `<div style="padding-top:40px">${skel("hero")}</div>`;
  }
  const mode = get(app.S, "mode", {});
  const ph = phase(g);
  const st = status(g);
  const venue = (det && det.venue && det.venue.image) || get(g, "venue.image");
  const badge = ph === "live" ? stateBadge(g.is_break ? "break" : "live") : ph === "post" ? stateBadge("final", st.text) : stateBadge("pre", st.text || "Upcoming");
  const meta = [
    whereLabel(g) ? chip(whereLabel(g).replace(/\s*\((event link|channel)[^)]*\)\s*/gi, " ").trim(), { ic: "tv" }) : "",
    ph === "pre" && g.start_time ? chip(fmtStart(g.start_time), { ic: "calendar" }) : "",
    g.odds ? chip(g.odds, { ic: "trend" }) : "",
  ].join("");
  const venueUrl = venue ? imgSrc(venue) : null;
  const hero = `<div class="gs-hero on-dark" style="${teamVars(g.away, g.home, true)}">
    <div class="hero-bg">${venueUrl ? `<img src="${attr(venueUrl.src)}" data-src="${attr(venueUrl.src)}" data-orig="${attr(venueUrl.orig)}" alt="" decoding="async">` : ""}</div>
    <div class="row" style="justify-content:center;gap:8px">${badge}${leagueImg(g.league, { dark: true })}</div>
    ${board(g)}
    ${meta ? `<div class="meta">${meta}</div>` : ""}
  </div>`;

  const locked = mode.locked_game === gid, focused = mode.focus_game === gid;
  const mini = (a, ic, label, pressed = false) => `<button class="mini-act" data-act="game-act" data-a="${a}" data-gid="${attr(gid)}" aria-pressed="${pressed}">${icon(ic)}${esc(label)}</button>`;
  const actions = `<div class="gs-actions">
    ${g.on_now ? `<button class="btn btn--good btn--lg" style="grid-column:1/-1" data-act="open-more">${icon("check")}On your TV now</button>`
      : `<button class="btn btn--primary btn--lg" data-act="game-act" data-a="watch" data-gid="${attr(gid)}" ${ph === "live" ? "" : "disabled"}>${icon("play", "ico--fill")}${ph === "live" ? "Watch now" : ph === "pre" ? `Starts ${esc(st.clk || "later")}` : "Game over"}</button>`}
    ${mini(focused ? "autopilot" : "focus", "target", focused ? "Focused" : "Focus", focused)}
    ${mini(g.boosted ? "unboost" : "boost", "flame", g.boosted ? "Boosted" : "Boost", !!g.boosted)}
    ${mini(g.muted ? "unmute" : "mute", g.muted ? "sound" : "mute", g.muted ? "Muted" : "Mute", !!g.muted)}
    ${mini(locked ? "unlock" : "lock", locked ? "lock" : "unlock", locked ? "Locked" : "Lock", locked)}
    <button class="mini-act" data-act="set-channel" data-gid="${attr(gid)}">${icon("tv")}Channel</button>
  </div>`;

  const live = ph === "live" ? `<div class="sheet-sec"><h4>Right now</h4><small>${esc(st.text)}</small></div>
    <div class="panel on-dark" style="${teamVars(g.away, g.home, true)}">
      ${g.reason ? `<p style="margin:0;font:650 16px/1.35 var(--font-ui)">${esc(g.reason)}</p>` : ""}
      ${wpBar(g)}${situation(g)}
      ${g.spotlight ? `<div class="spot" style="margin:14px -14px -14px;border-radius:0 0 20px 20px">${avatar(g.spotlight, { size: 44, team: teamBy(g, g.spotlight.team), dark: true })}<div class="grow"><div class="who">${esc(g.spotlight.short || g.spotlight.name)}<small>Spotlight</small></div><div class="what clamp2">${esc(g.spotlight.text)}</div></div></div>` : ""}
    </div>` : "";

  const series = arr(det && det.wp_series);
  const [aInk, hInk] = pairInks(g.away, g.home, true);
  const lastWp = series.length ? series[series.length - 1] : num(g.wp, NaN);
  const wpSec = series.length > 1 ? `<div class="sheet-sec"><h4>Win probability</h4><small>${ph === "post" ? "How it played out" : `Since ${({ nfl: "kickoff", nba: "tip-off", nhl: "puck drop", mlb: "first pitch" })[g.league] || "the start"}`}</small></div>
    <div class="panel on-dark">
      <div class="spark-head"><span class="spark-team" style="color:${hInk}">${logo(g.home, { size: 22, dark: true })}${esc(get(g, "home.abbr"))} <b>${Number.isFinite(lastWp) ? Math.round(lastWp * 100) + "%" : ""}</b></span>
      <span class="spark-team" style="color:${aInk}"><b>${Number.isFinite(lastWp) ? Math.round((1 - lastWp) * 100) + "%" : ""}</b> ${esc(get(g, "away.abbr"))}${logo(g.away, { size: 22, dark: true })}</span></div>
      ${sparkline(series, g, { id: "wp-" + gid })}
      <div class="sit-row" style="margin-top:10px"><span>Start</span><span>${esc(get(g, "home.abbr"))} above · ${esc(get(g, "away.abbr"))} below</span><span>${ph === "post" ? "Final" : "Now"}</span></div>
    </div>` : "";

  const ls = det && det.linescore ? linescore(det.linescore, g) : "";
  const lsSec = ls ? `<div class="sheet-sec"><h4>Scoring</h4></div><div class="panel">${ls}</div>` : "";

  const leadersSec = leadersBlock(g, det);
  const playsSec = playsBlock(g, det);
  const v = (det && det.venue) || g.venue || {};
  const info = [
    v.name ? infoRow("pin", "Venue", `${esc(v.name)}${v.city ? ` · ${esc(v.city)}` : ""}${v.indoor === true ? " · Indoor" : v.indoor === false ? " · Outdoor" : ""}`) : "",
    infoRow("tv", "Where it's on", whereLabel(g) ? esc(whereLabel(g)) : `Not set — <button class="chip chip--warn chip--btn" data-act="set-channel" data-gid="${attr(gid)}" style="height:24px">${icon("tv")}<span>Set channel</span></button>`),
    g.odds ? infoRow("trend", "Odds", esc(g.odds)) : "",
    g.start_time ? infoRow("calendar", ph === "pre" ? "Starts" : "Started", esc(`${fmtStart(g.start_time)}`)) : "",
  ].join("");

  return `${hero}${actions}${live}${wpSec}${lsSec}${leadersSec}${playsSec}
    <div class="sheet-sec"><h4>Game info</h4></div><div class="panel info-rows">${info}</div>`;
}

function infoRow(ic, k, v) { return `<div class="info-r">${icon(ic)}<div class="grow"><div class="k">${esc(k)}</div><div class="v">${v}</div></div></div>`; }

function leadersBlock(g, det) {
  let la = arr(get(det, "leaders.away")), lh = arr(get(det, "leaders.home"));
  if (!la.length && !lh.length) {
    la = arr(g.leaders).filter((l) => l.team === get(g, "away.abbr"));
    lh = arr(g.leaders).filter((l) => l.team === get(g, "home.abbr"));
  }
  if (!la.length && !lh.length) return "";
  const col = (t, ls) => `<div class="lead-col"><div class="hd">${logo(t, { size: 20 })}${esc(t && t.abbr)}</div>${ls.slice(0, 4).map((l) =>
    `<div class="lc">${avatar(l, { size: 42, team: t })}<div style="min-width:0"><div class="lb">${esc(l.label)}</div><div class="nm">${esc(l.short || l.name)}</div><div class="st">${esc(l.stat)}</div></div></div>`).join("") || `<div class="dim" style="font-size:13px">—</div>`}</div>`;
  return `<div class="sheet-sec"><h4>Leaders</h4><small>${phase(g) === "pre" ? "Season" : "This game"}</small></div>
    <div class="panel lead-cols">${col(g.away, la)}${col(g.home, lh)}</div>`;
}

function playsBlock(g, det) {
  if (!det) return `<div class="sheet-sec"><h4>Plays</h4></div><div class="panel">${skel("row", 3, 'style="margin-bottom:8px"')}</div>`;
  if (det.error) return `<div class="sheet-sec"><h4>Plays</h4></div><div class="panel">${empty("plays", "Plays unavailable", esc(det.error), "", "empty--sm", "pl-err")}</div>`;
  const plays = arr(det.plays).slice(0, 20);
  if (!plays.length) {
    return `<div class="sheet-sec"><h4>Plays</h4></div><div class="panel">${empty("plays", phase(g) === "pre" ? "No plays yet" : "No plays to show", phase(g) === "pre" ? "Play-by-play shows up here once it starts." : "", "", "empty--sm", "pl-none")}</div>`;
  }
  const [aInk, hInk] = pairInks(g.away, g.home);
  return `<div class="sheet-sec"><h4>Plays</h4><small>Newest first</small></div><div class="panel plays" data-flip>${plays.map((p) => {
    const t = teamBy(g, p.team);
    const tink = t === g.home ? hInk : aInk;
    const media = p.headshot
      ? `${avatar({ headshot: p.headshot, name: p.player }, { size: 40, team: t })}${t ? `<span class="mini">${logo(t, { size: 16 })}</span>` : ""}`
      : t ? logo(t, { size: 34 }) : `<span class="logo" style="--s:34px"><b class="on">•</b></span>`;
    const when = [p.period ? periodShort(g, p.period) : "", p.clock].filter(Boolean).join(" · ");
    return `<div class="pl ${p.scoring ? "pl--score" : ""}" data-key="p:${attr(p.id)}" style="--tc-ink:${tink}">
      <div class="pl-media">${media}</div>
      <div style="min-width:0"><div class="pl-meta">${p.scoring ? `<span class="score-tag">Score</span>` : ""}<span>${esc(when, "")}</span>${p.type && !p.scoring ? `<span>· ${esc(p.type)}</span>` : ""}<span class="sc">${esc(p.score, "")}</span></div>
      <div class="pl-text">${esc(p.text)}</div></div></div>`;
  }).join("")}</div>`;
}
function periodShort(g, p) {
  p = num(p);
  if (g.league === "mlb") return `Inn ${p}`;
  if (g.league === "nhl") return p <= 3 ? `P${p}` : p === 4 ? "OT" : "SO";
  return p <= 4 ? `Q${p}` : "OT";
}

ACTIONS["open-game"] = (el) => openGame(el.dataset.gid);
ACTIONS["game-act"] = async (el) => {
  const a = el.dataset.a, gid = el.dataset.gid;
  if (a === "mute") {
    const ok = await confirmSheet({ title: "Never show this game?", body: "Autopilot won't switch to it again. You can unmute it from its game card.", ok: "Mute it", danger: true, ic: "mute" });
    if (!ok) return;
  }
  const extra = a === "autopilot" ? {} : { game_id: gid };
  if (a === "focus") extra.value = get(app.S, "mode.focus_mode") === "focus_must_see" ? "focus_must_see" : "focus";
  await act(a, extra, el);
};

/* ====================================================================== set channel */
export async function openChannel(gid) {
  const g = gameById(gid);
  const s = openSheet({ id: "channel", label: "Set channel", render: () => channelSheet(gid) });
  if (!app.channels) {
    try { app.channels = await api("/api/channels"); } catch (e) { app.channels = { channels: [], error: e.message }; }
    s.update();
  }
  return g;
}
function channelSheet(gid) {
  const g = gameById(gid);
  const cur = get(g, "where.target", "");
  const chs = arr(get(app.channels, "channels"));
  const row = (target, ic, t1, t2) => `<button class="pick pick--compact" data-act="set-target" data-target="${attr(target)}" data-gid="${attr(gid)}" aria-pressed="${!!target && cur === target}">
    <span class="pick-ic">${icon(ic)}</span><span class="grow"><span class="t1" style="display:block">${esc(t1)}</span>${t2 ? `<span class="t2" style="display:block">${t2}</span>` : ""}</span><span class="tick">${icon("check")}</span></button>`;
  return `<h3>Where's it on?</h3><p class="lead lead--game">${g ? `<span class="logo-pair">${logo(g.away, { size: 24 })}${logo(g.home, { size: 24 })}</span>` : ""}<span>${esc(g ? g.matchup : "This game")} — pick the channel and it'll remember.</span></p>
    <div class="pick-list" style="margin-top:16px">
      ${!app.channels ? skel("row", 3) : chs.map((c) => row(`youtubetv:${c.key}`, "tv", c.label || c.key, c.code ? `<span style="color:var(--success-ink)">Linked</span>` : "No link yet")).join("")}
      ${row("youtubetv:st", "football", "Sunday Ticket", "NFL out-of-market games")}
      ${row("prime:leaguepass", "play", "Prime Video", "NBA League Pass, Monumental, TNF")}
    </div>
    <div class="sheet-sec"><h4>Or paste a link</h4></div>
    <div class="chan-paste" style="margin-top:0"><input id="custom-target" class="input" placeholder="tv.youtube.com/watch/… or Prime link" autocapitalize="off" autocomplete="off" spellcheck="false" data-keep>
      <button class="btn btn--primary" data-act="set-target-custom" data-gid="${attr(gid)}">Use</button></div>
    <button class="btn btn--ghost btn--block" style="margin-top:14px" data-act="set-target" data-target="" data-gid="${attr(gid)}">${icon("undo")}Clear — auto-detect again</button>`;
}
ACTIONS["set-channel"] = (el, e) => { e && e.stopPropagation(); openChannel(el.dataset.gid); };
ACTIONS["set-target"] = async (el) => {
  try {
    const r = await api("/api/override", { body: { game_id: el.dataset.gid, target: el.dataset.target || null } });
    toast((r && r.message) || "Saved", r && r.ok === false ? "warn" : "ok");
    vibrate(12);
    closeSheet(sheetById("channel"));
  } catch (err) { toast(err.message, "error"); }
};
ACTIONS["set-target-custom"] = async (el) => {
  const v = (document.getElementById("custom-target") || {}).value || "";
  if (!v.trim()) { toast("Paste a link first", "warn"); return; }
  try {
    const r = await api("/api/override", { body: { game_id: el.dataset.gid, target: v.trim() } });
    toast((r && r.message) || "Saved", r && r.ok === false ? "warn" : "ok");
    if (!r || r.ok !== false) closeSheet(sheetById("channel"));
  } catch (err) { toast(err.message, "error"); }
};

/* ====================================================================== teams directory */
export async function loadTeams(force = false) {
  if (!app.teams) { const c = store.get("teams:v2"); if (c && typeof c === "object" && !c.error) app.teams = c; }
  if (app.teams && !app.teams.error && !force && app.teamsAt && Date.now() - app.teamsAt < 600000) return app.teams;
  try {
    const t = await api("/api/teams");
    if (t && typeof t === "object") { app.teams = t; app.teamsAt = Date.now(); store.set("teams:v2", t); }
  } catch (e) { if (!app.teams) app.teams = { error: e.message }; }
  hooks.renderScreen("games"); hooks.renderScreen("settings");
  updateSheets();
  return app.teams;
}
export const teamList = (lg) => (app.teams && Array.isArray(app.teams[lg]) ? app.teams[lg] : []);
export const teamEntry = (lg, id) => teamList(lg).find((t) => String(t.id) === String(id)) || null;

export async function toggleStar(lg, abbr, el) {
  const list = teamList(lg);
  const t = list.find((x) => x.abbr === abbr);
  if (!t) return;
  t.mine = !t.mine;
  vibrate(t.mine ? 18 : 8);
  if (el) { pop(el.querySelector(".ico") || el, 1.4); burst(el); }
  hooks.renderScreen("games"); hooks.renderScreen("settings"); updateSheets();
  const mine = list.filter((x) => x.mine).map((x) => x.abbr);
  try {
    const r = await api("/api/settings", { body: { changes: { teams: { [lg]: mine } } } });
    if (r && r.ok === false) throw new Error(r.message || "Not saved");
    store.set("teams:v2", app.teams);
    if (app.settings && app.settings.config) app.settings.config.teams = { ...(app.settings.config.teams || {}), [lg]: mine };
    toast(t.mine ? `${t.short || t.abbr} added to your teams` : `${t.short || t.abbr} removed from your teams`, "ok");
  } catch (e) {
    t.mine = !t.mine;
    hooks.renderScreen("games"); updateSheets();
    toast(e.message || "Couldn't save", "error");
  }
}
function burst(el) {
  const b = el.querySelector(".burst");
  if (!b || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  b.animate([{ opacity: 0.9, transform: "scale(.4)" }, { opacity: 0, transform: "scale(1.8)" }], { duration: 520, easing: "cubic-bezier(.22,1,.36,1)" });
}
ACTIONS["star"] = (el, e) => { e && e.stopPropagation(); toggleStar(el.dataset.lg, el.dataset.abbr, el); };

/* ====================================================================== team sheet */
async function loadRoster(lg, id, force = false) {
  const k = `${lg}:${id}`;
  const c = app.rosters.get(k);
  if (c && !force && Date.now() - c.at < 600000) return c.data;
  try { const d = await api(`/api/teams/${lg}/${encodeURIComponent(id)}`); app.rosters.set(k, { data: d || {}, at: Date.now() }); }
  catch (e) { app.rosters.set(k, { data: { error: e.message }, at: Date.now() }); }
  return app.rosters.get(k).data;
}
export function openTeam(lg, id) {
  if (!lg || !id) return;
  const s = openSheet({ id: `team:${lg}:${id}`, live: true, full: true, flush: true, onImg: true, label: "Team", render: () => teamSheet(lg, id) });
  loadRoster(lg, id).then(() => s.update());
  if (!app.teams) loadTeams();
}
function teamSheet(lg, id) {
  const det = (app.rosters.get(`${lg}:${id}`) || {}).data || null;
  const base = teamEntry(lg, id) || {};
  const t = { ...(det && det.team), ...base, record: get(det, "team.record", base.record || ""), standing: get(det, "team.standing", "") };
  if (!t.abbr && !det) return `<div style="padding-top:40px">${skel("hero")}</div>`;
  const tc = raw(t);
  const darkTile = lum(tc) < 0.5;
  const on = onColor(tc);
  const live = games().find((g) => g.league === lg && (String(get(g, "home.id")) === String(id) || String(get(g, "away.id")) === String(id))) || (det && det.game);
  const wm = imgSrc(darkTile ? t.logo_dark || t.logo : t.logo);
  const tink = ink(t);
  const hero = `<div class="ts-hero" style="--tc:${tc};--on:${on}">
    ${wm.src ? `<img class="wm" src="${attr(wm.src)}" data-src="${attr(wm.src)}" data-orig="${attr(wm.orig)}" alt="">` : ""}
    ${logo(t, { size: 96, dark: darkTile, eager: true })}
    <h3>${esc(t.name || t.abbr)}</h3>
    <div class="rec">${t.record ? `<b>${esc(t.record)}</b>` : ""}${esc(t.standing, "")}</div>
    <button class="star-toggle" data-act="star" data-lg="${attr(lg)}" data-abbr="${attr(t.abbr)}" aria-pressed="${!!t.mine}">${icon("star")}<span>${t.mine ? "Your team" : "Make it my team"}</span><i class="burst"></i></button>
  </div>`;
  const gp = live ? phase(live) : "";
  const gameSec = live ? `<div class="sheet-sec"><h4>${gp === "live" ? "Live now" : gp === "pre" ? "Next game" : "Last game"}</h4></div>${gameCard(live, { key: false })}` : "";
  let roster;
  if (!det) roster = `<div class="roster">${Array.from({ length: 9 }, () => `<div class="pc"><div class="skel" style="width:70px;height:70px;border-radius:50%"></div><div class="skel skel--line" style="width:70%;margin-top:10px"></div></div>`).join("")}</div>`;
  else if (det.error) roster = empty("roster", "Roster unavailable", esc(det.error), "", "empty--sm", "ro-err");
  else if (!arr(det.roster).length) roster = empty("roster", "No roster yet", "ESPN hasn't published one for this team.", "", "empty--sm", "ro-none");
  else {
    roster = `<div class="roster">${arr(det.roster).map((p) => `<div class="pc"><div class="ph">${avatar(p, { size: 70, ring: tink })}${p.jersey ? `<span class="num" style="--tc-ink:${tink};--on-ink:${onColor(tink)}">${esc(p.jersey)}</span>` : ""}</div>
      <div class="nm">${esc(p.short || p.name)}</div><div class="pos">${esc(p.position, "")}</div></div>`).join("")}</div>`;
  }
  return `${hero}${gameSec}<div class="sheet-sec"><h4>Roster</h4><small>${det && arr(det.roster).length ? `${arr(det.roster).length} players` : ""}</small></div>${roster}<div style="height:12px"></div>`;
}
ACTIONS["open-team"] = (el) => openTeam(el.dataset.lg, el.dataset.id);

