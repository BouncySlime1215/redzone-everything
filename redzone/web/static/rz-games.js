/* RedZone Everything — Games screen: Scores (live / up next / final) and Teams (every team, star yours). */

import { app, ACTIONS, store, attr, esc, num, games, LEAGUES, LEAGUE_NAME, raw, lum, onColor, todayLabel, fmtTime } from "./rz-core.js?v=41";
import { patch } from "./rz-dom.js?v=40";
import { icon, logo, leagueImg, empty, skel, seg, screenHead } from "./rz-ui.js?v=40";
import { gameCard } from "./rz-cards.js?v=40";
import { loadTeams, teamList } from "./rz-detail.js?v=43";

const ui = app.ui;
ui.gview = store.get("gview", "scores");
ui.league = store.get("league", "all");
ui.tleague = store.get("tleague", "nfl");

export function renderGames(root) {
  const all = games();
  const liveN = all.filter((g) => g.phase === "live").length;
  const sub = `${esc(todayLabel())}${liveN ? ` · <span style="color:var(--live-ink)">${liveN} live</span>` : ""}`;
  const head = screenHead("Games", sub);
  const tabs = seg([{ v: "scores", html: `${icon("scores", "ico--sm")}Scores` }, { v: "teams", html: `${icon("grid", "ico--sm")}Teams` }], ui.gview, { act: "gview", label: "Games view" });
  const filters = ui.gview === "scores" ? scoreFilters(all) : teamFilters();
  const body = ui.gview === "scores" ? scores(all) : teams();
  patch(root, `${head}<div class="subbar" data-key="subbar">${tabs}${filters}</div>${body}`);
  if (ui.gview === "teams" && !app.teams) loadTeams();
}

/* ---------- scores ---------- */
function scoreFilters(all) {
  const present = LEAGUES.filter((lg) => all.some((g) => g.league === lg));
  const n = (f) => all.filter(f).length;
  const chips = [
    { k: "all", label: "All", count: all.length },
    { k: "mine", label: "Mine", count: n((g) => g.mine), star: true },
    ...present.map((lg) => ({ k: lg, label: LEAGUE_NAME[lg], count: n((g) => g.league === lg), lg })),
  ];
  return `<div class="filters" role="toolbar" aria-label="Filter games">${chips.map((c) => `<button class="fchip ${c.star ? "fchip--star" : ""}" data-act="league" data-v="${c.k}" aria-pressed="${ui.league === c.k}">
    ${c.lg ? leagueImg(c.lg, { cls: "" }) : c.star ? icon("star") : ""}${esc(c.label)}<span class="n">${c.count}</span></button>`).join("")}</div>`;
}
function scores(all) {
  const f = ui.league;
  const shown = all.filter((g) => f === "all" || (f === "mine" ? g.mine : g.league === f));
  const live = shown.filter((g) => g.phase === "live").sort((a, b) => (b.on_now - a.on_now) || num(b.score) - num(a.score));
  const pre = shown.filter((g) => g.phase === "pre" || g.phase === "unknown").sort((a, b) => num(a.start_time, 9e12) - num(b.start_time, 9e12));
  const post = shown.filter((g) => g.phase === "post").sort((a, b) => (b.mine - a.mine) || num(b.start_time) - num(a.start_time));
  if (!app.S) return `<div class="list">${skel("card", 3)}</div>`;
  if (!shown.length) {
    const msg = f === "mine" ? "None of your teams play today. Star more teams in the Teams tab." : "Nothing on the schedule for this filter.";
    return `<div class="card" style="margin-top:12px">${empty("scoreboard", "No games here", msg, f === "mine" ? `<button class="btn btn--sm" data-act="gview" data-v="teams">${icon("star")}Pick your teams</button>` : "", "", "g-none")}</div>`;
  }
  const out = [];
  const head = (k, title, n, extra = "") => out.push(`<div class="sec" data-key="h:${k}"><h2>${k === "live" ? '<i class="live-dot"></i>' : ""}${esc(title)}<span class="n">${n}</span></h2>${extra}</div>`);
  if (live.length) { head("live", "Live", live.length, `<span class="aside">${screenNote(live) || "Hottest first"}</span>`); live.forEach((g) => out.push(gameCard(g))); }
  else if (pre.length) out.push(`<div class="card idle-lite" data-key="nolive"><div class="row" style="gap:12px">${icon("clock", "ico--lg")}<div><b>Nothing live yet</b><div class="t2">Next up: ${esc(pre[0].matchup)} · ${esc(pre[0].start_time ? fmtTime(pre[0].start_time) : "soon")}</div></div></div></div>`);
  if (pre.length) { head("pre", "Up next", pre.length); pre.forEach((g) => out.push(gameCard(g))); }
  if (post.length) { head("post", "Final", post.length); post.forEach((g) => out.push(gameCard(g))); }
  return `<div class="list" data-key="glist" data-flip>${out.join("")}</div>`;
}

/* Screen-time clock (server flag views.screen_clock): a card's clock is what the TV shows, or blank when the
   delay is unknown. Absent field = flag off = nothing said. */
function screenNote(live) {
  if (!live.some((g) => g.screen_clock)) return "";
  return live.some((g) => g.screen_clock === "unknown") ? "Clocks match your screen · blank = not synced" : "Clocks match your screen";
}

/* ---------- teams ---------- */
function teamFilters() {
  return `<div class="filters" role="toolbar" aria-label="League">${LEAGUES.map((lg) => {
    const n = teamList(lg).filter((t) => t.mine).length;
    return `<button class="fchip" data-act="tleague" data-v="${lg}" aria-pressed="${ui.tleague === lg}">${leagueImg(lg, { cls: "" })}${LEAGUE_NAME[lg]}${n ? `<span class="n">★${n}</span>` : ""}</button>`;
  }).join("")}</div>`;
}
function teams() {
  if (!app.teams) return `<div class="tgrid" style="margin-top:14px">${skel("tile", 12, "")}</div>`;
  if (app.teams.error && !teamList("nfl").length) {
    return `<div class="card" style="margin-top:12px">${empty("roster", "Teams didn't load", esc(app.teams.error), `<button class="btn btn--sm" data-act="reload-teams">${icon("undo")}Try again</button>`, "", "t-err")}</div>`;
  }
  const mine = LEAGUES.flatMap((lg) => teamList(lg).filter((t) => t.mine).map((t) => ({ ...t, lg })));
  const lg = ui.tleague;
  const list = teamList(lg);
  const myRow = `<div class="sec" data-key="h:mine" style="margin-top:14px"><h2>${icon("star", "ico--fill ico--sm")}Your teams<span class="n">${mine.length}</span></h2><span class="aside">Tap ★ to add or remove</span></div>
    <div class="my-teams" data-key="myteams">${mine.length ? mine.map((t) => `<button class="tchip" data-act="open-team" data-lg="${t.lg}" data-id="${attr(t.id)}">${logo(t, { size: 28 })}<span>${esc(t.abbr)}<br><small>${LEAGUE_NAME[t.lg]}</small></span></button>`).join("")
      : `<span class="dim" style="font-size:13px;padding:10px 0">No teams yet — star a few below.</span>`}</div>`;
  const grid = `<div class="sec" data-key="h:all"><h2>${esc(LEAGUE_NAME[lg])}<span class="n">${list.length}</span></h2><span class="aside">A–Z</span></div>
    <div class="tgrid" data-key="tgrid-${lg}" data-flip>${list.map((t) => tile(t, lg)).join("")}</div>`;
  return myRow + grid;
}
function tile(t, lg) {
  const tc = raw(t);
  const dark = lum(tc) < 0.5;
  return `<div class="tile ${t.mine ? "tile--mine" : ""}" data-key="t:${lg}:${attr(t.id)}" data-act="open-team" data-lg="${lg}" data-id="${attr(t.id)}" role="button" tabindex="0" aria-label="${attr(t.name)}" style="--tc:${tc};--on:${onColor(tc)}">
    ${logo(t, { size: 54, dark })}
    <span class="ab">${esc(t.abbr)}</span><span class="nm">${esc(t.short || t.name)}</span>
    <button class="star-btn" data-act="star" data-lg="${lg}" data-abbr="${attr(t.abbr)}" aria-pressed="${!!t.mine}" aria-label="${t.mine ? "Remove" : "Add"} ${attr(t.name)} ${t.mine ? "from" : "to"} your teams">${icon("star")}<i class="burst"></i></button>
  </div>`;
}

/* ---------- actions ---------- */
ACTIONS["gview"] = (el) => { ui.gview = el.dataset.v; store.set("gview", ui.gview); rerender(); window.scrollTo({ top: 0 }); };
ACTIONS["league"] = (el) => { ui.league = el.dataset.v; store.set("league", ui.league); rerender(); };
ACTIONS["tleague"] = (el) => { ui.tleague = el.dataset.v; store.set("tleague", ui.tleague); rerender(); };
ACTIONS["reload-teams"] = () => loadTeams(true);
function rerender() { const root = document.getElementById("scr-games"); if (root) renderGames(root); }

export function showTeams(lg) {
  ui.gview = "teams"; store.set("gview", "teams");
  if (lg) { ui.tleague = lg; store.set("tleague", lg); }
}
