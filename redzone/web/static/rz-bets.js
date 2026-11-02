/* RedZone Everything — Bets: sweat meters, add-bet flow (sheet), delete with confirm. */

import { app, ACTIONS, INPUTS, hooks, attr, esc, num, get, arr, games, gameById, status, pairInks, fmtStart, vibrate } from "./rz-core.js?v=36";
import { patch } from "./rz-dom.js?v=36";
import { icon, logo, empty, sec, seg, screenHead, meter, odo } from "./rz-ui.js?v=36";
import { fantasyCard } from "./rz-cards.js?v=36";
import { openSheet, closeSheet, sheetById, confirmSheet, toast } from "./rz-sheet.js?v=36";
import { api } from "./rz-api.js?v=36";

const ui = app.ui;
ui.bet = ui.bet || { game: null, kind: "spread", team: null, line: "", side: "over", note: "" };

const sweatWord = (s, live) => (!live ? "Not live" : s >= 0.75 ? "On the edge" : s >= 0.5 ? "Sweating" : s >= 0.25 ? "Getting tense" : s > 0 ? "Calm" : "Decided");
export function describeBet(b) {
  if (b.kind === "spread") return `${b.team || "—"} ${num(b.line) > 0 ? "+" : ""}${b.line ?? "—"}`;
  if (b.kind === "moneyline") return `${b.team || "—"} ML`;
  if (b.kind === "total") return `${b.side === "under" ? "Under" : "Over"} ${b.line ?? "—"}`;
  return b.note || "Prop";
}

export function renderBets(root) {
  const S = app.S;
  const bets = arr(S && S.bets);
  const sweating = bets.filter((b) => num(b.sweat) >= 0.5).length;
  const live = bets.filter((b) => get(gameById(b.game_id), "phase") === "live").length;
  const tiles = `<div class="stat-tiles" data-key="tiles">
    <div class="stile"><b>${odo(bets.length)}</b><span>Active</span></div>
    <div class="stile ${live ? "stile--good" : ""}"><b>${odo(live)}</b><span>Live now</span></div>
    <div class="stile ${sweating ? "stile--hot" : ""}"><b>${odo(sweating)}</b><span>Sweating</span></div></div>`;
  const list = !S ? `<div class="skel skel--card"></div>`
    : bets.length ? `<div class="list" data-key="bets" data-flip>${bets.map(betCard).join("")}</div>`
      : `<div class="card" data-key="bets-empty">${empty("ticket", "No bets yet", "Add one and autopilot gives that game a big boost while it's close.", "", "", "bets")}</div>`;
  const fan = S && S.fantasy ? `${sec("Fantasy", { key: "sec-fan" })}${fantasyCard(S.fantasy)}` : "";
  patch(root, `${screenHead("Bets", "Money on the line jumps the queue")}${tiles}${sec("Your bets", { n: bets.length || "", key: "sec-bets" })}${list}
    <button class="btn btn--primary btn--lg btn--block" style="margin-top:16px" data-act="add-bet" data-key="add">${icon("plus")}Add a bet</button>${fan}`);
}

function betCard(b) {
  const g = gameById(b.game_id);
  const label = String(b.label || "").replace(/^your bet:\s*/i, "");
  const [line, ...rest] = label.split(/\s+·\s+/);
  const t = g && b.team ? (get(g, "home.abbr") === b.team ? g.home : get(g, "away.abbr") === b.team ? g.away : null) : null;
  const [ai, hi] = g ? pairInks(g.away, g.home) : ["var(--bet)", "var(--bet)"];
  const tink = t ? (t === g.home ? hi : ai) : "var(--bet)";
  const sw = Math.max(0, Math.min(1, num(b.sweat)));
  const isLive = get(g, "phase") === "live";
  const word = sweatWord(sw, isLive || sw > 0);
  return `<div class="card bet" data-key="b:${attr(b.id)}" style="--tc-ink:${tink}" ${g ? `data-act="open-game" data-gid="${attr(g.id)}" role="button"` : ""}>
    <div class="bet-top">
      ${g ? `<span class="logo-pair">${logo(g.away, { size: 34 })}${logo(g.home, { size: 34 })}</span>` : `<span class="pick-ic">${icon("ticket")}</span>`}
      <div class="grow"><div class="bet-line">${esc(line || describeBet(b))}</div>
        <div class="bet-sub ell">${esc(b.matchup || (g && g.matchup))}${g ? ` · ${esc(g.phase === "pre" ? fmtStart(g.start_time) : status(g).text)}` : ""}</div></div>
      <button class="del-btn" data-act="del-bet" data-id="${attr(b.id)}" aria-label="Delete bet">${icon("trash")}</button>
    </div>
    ${rest.length ? `<div class="bet-status">${esc(rest.join(" · "))}</div>` : ""}
    <div class="sweat"><div class="sweat-head"><span>Sweat</span><b class="${sw >= 0.66 ? "hot" : ""}">${esc(word)}</b></div>${meter(sw * 100, { cls: "meter--sweat meter--lg", label: "Sweat" })}</div>
  </div>`;
}

/* ---------- add-bet flow ---------- */
function oddsFor(g) {
  const s = String((g && g.odds) || "");
  const fav = s.match(/^([A-Z]{2,4})\s+([+-]?\d+(?:\.\d+)?)/);
  const ou = s.match(/O\/U\s+(\d+(?:\.\d+)?)/i);
  const spread = fav && Math.abs(parseFloat(fav[2])) < 40 ? { team: fav[1], line: parseFloat(fav[2]) } : null;
  return { spread, total: ou ? parseFloat(ou[1]) : null };
}
function defaultLine(b) {
  const g = gameById(b.game);
  const o = oddsFor(g);
  if (b.kind === "total" && o.total) return String(o.total);
  if (b.kind === "spread" && o.spread && b.team) return String(b.team === o.spread.team ? o.spread.line : -o.spread.line).replace(/^(\d)/, "+$1");
  return b.line;
}
function betSheet() {
  const b = ui.bet;
  const gs = games().filter((g) => g.phase === "live" || g.phase === "pre")
    .sort((a, c) => (a.phase === c.phase ? num(a.start_time) - num(c.start_time) : a.phase === "live" ? -1 : 1));
  const g = gameById(b.game);
  const needsTeam = b.kind === "spread" || b.kind === "moneyline";
  const needsLine = b.kind === "spread" || b.kind === "total";
  const n = (i) => `<i>${i}</i>`;
  const picks = gs.length ? gs.slice(0, 30).map((x) => `<button class="pick" data-act="bet-game" data-gid="${attr(x.id)}" aria-pressed="${b.game === x.id}">
      <span class="logo-pair">${logo(x.away, { size: 28 })}${logo(x.home, { size: 28 })}</span>
      <span class="grow"><span class="t1" style="display:block">${esc(x.matchup)}</span><span class="t2" style="display:block">${esc(x.phase === "live" ? status(x).text : fmtStart(x.start_time))}${x.odds ? ` · ${esc(x.odds)}` : ""}</span></span>
      <span class="tick">${icon("check")}</span></button>`).join("")
    : `<div class="dim" style="font-size:13px">No live or upcoming games right now.</div>`;
  let step = 2;
  const body = `<h3>Add a bet</h3><p class="lead">Takes 20 seconds. Close games you have money on get a big boost.</p>
    <div class="step"><div class="step-lbl">${n(1)}Game</div><div class="pick-list no-drag" style="max-height:${g ? "150px" : "300px"};overflow:auto">${picks}</div></div>
    <div class="step"><div class="step-lbl">${n(step++)}Bet type</div>
      ${seg([{ v: "spread", label: "Spread" }, { v: "moneyline", label: "Moneyline" }, { v: "total", label: "Total" }, { v: "prop", label: "Prop" }], b.kind, { act: "bet-kind", label: "Bet type" })}</div>
    ${needsTeam ? `<div class="step"><div class="step-lbl">${n(step++)}Team</div><div class="team-toggle">${g ? [g.away, g.home].map((t, i) => {
      const [ai, hi] = pairInks(g.away, g.home);
      return `<button class="tt" data-act="bet-team" data-v="${attr(t && t.abbr)}" aria-pressed="${b.team === (t && t.abbr)}" style="--tc-ink:${i ? hi : ai}">${logo(t, { size: 40 })}<span><b>${esc(t && t.abbr)}</b><small>${esc(t && t.name, "")}</small></span></button>`;
    }).join("") : `<div class="dim" style="font-size:13px;grid-column:1/-1">Pick a game first.</div>`}</div></div>` : ""}
    ${b.kind === "total" ? `<div class="step"><div class="step-lbl">${n(step++)}Side</div>${seg([{ v: "over", label: "Over" }, { v: "under", label: "Under" }], b.side, { act: "bet-side", label: "Over or under" })}</div>` : ""}
    ${needsLine ? `<div class="step"><div class="step-lbl">${n(step++)}${b.kind === "total" ? "Total" : "Line"}</div>
      <div class="stepper"><button class="btn btn--icon" data-act="bet-step" data-d="-0.5" aria-label="Minus half a point">${icon("minus")}</button>
      <div class="val"><input id="bet-line" inputmode="decimal" autocomplete="off" value="${attr(b.line)}" placeholder="${b.kind === "total" ? "47.5" : "+3.5"}" data-sync data-input="bet-line" aria-label="Line"><small>${b.kind === "total" ? "points" : "points · + for underdog"}</small></div>
      <button class="btn btn--icon" data-act="bet-step" data-d="0.5" aria-label="Plus half a point">${icon("plus")}</button></div></div>` : ""}
    ${b.kind === "prop" ? `<div class="step"><div class="step-lbl">${n(step++)}What's the prop?</div><input id="bet-note" class="input" placeholder="e.g. McLaurin 70+ receiving yards" value="${attr(b.note)}" data-input="bet-note" autocomplete="off"></div>` : ""}`;
  const missing = !g ? "Pick a game" : needsTeam && !b.team ? "Pick a team" : needsLine && !String(b.line || "").trim() ? "Set the line" : b.kind === "prop" && !String(b.note || "").trim() ? "Describe the prop" : "";
  const summary = g ? `${esc(g.matchup)} · ${missing ? `<span style="color:var(--text-3)">${missing}</span>` : esc(describeBet(b))}` : "Pick a game";
  const foot = `<div class="row" style="gap:12px"><div class="grow"><div class="overline" style="margin-bottom:4px">Your bet</div><div class="ell" style="font:800 18px/1.1 var(--font-display);letter-spacing:.04em">${summary}</div></div>
    <button class="btn btn--primary btn--lg" data-act="bet-save">${icon("check")}Add bet</button></div>`;
  return { body, foot };
}
const refresh = () => { const s = sheetById("bet"); if (s) s.update(); };
ACTIONS["add-bet"] = (el) => {
  if (el && el.dataset.gid) { ui.bet.game = el.dataset.gid; ui.bet.team = null; }
  openSheet({ id: "bet", full: true, label: "Add a bet", render: betSheet });
};
ACTIONS["bet-game"] = (el) => { ui.bet.game = el.dataset.gid; ui.bet.team = null; ui.bet.line = defaultLine(ui.bet) || ""; refresh(); };
ACTIONS["bet-kind"] = (el) => { ui.bet.kind = el.dataset.v; ui.bet.line = defaultLine({ ...ui.bet, line: "" }) || ""; refresh(); };
ACTIONS["bet-team"] = (el) => { ui.bet.team = el.dataset.v; const d = defaultLine(ui.bet); if (d) ui.bet.line = d; refresh(); };
ACTIONS["bet-side"] = (el) => { ui.bet.side = el.dataset.v; refresh(); };
ACTIONS["bet-step"] = (el) => {
  const cur = parseFloat(ui.bet.line);
  const base = Number.isFinite(cur) ? cur : ui.bet.kind === "total" ? 44.5 : 0;
  const v = Math.round((base + parseFloat(el.dataset.d)) * 2) / 2;
  ui.bet.line = ui.bet.kind === "spread" && v > 0 ? `+${v}` : String(v);
  vibrate(6);
  refresh();
};
ACTIONS["bet-save"] = async (el) => {
  const b = ui.bet;
  if (!b.game) return toast("Pick a game first", "warn");
  if ((b.kind === "spread" || b.kind === "moneyline") && !b.team) return toast("Pick a team", "warn");
  const line = parseFloat(b.line);
  if ((b.kind === "spread" || b.kind === "total") && !Number.isFinite(line)) return toast("Enter the line, like +3.5", "warn");
  if (b.kind === "prop" && !String(b.note || "").trim()) return toast("Describe the prop", "warn");
  el.setAttribute("aria-busy", "true");
  try {
    const r = await api("/api/bets", { body: { game_id: b.game, kind: b.kind, team: b.team || null, line: Number.isFinite(line) ? line : null, side: b.kind === "total" ? b.side : "", note: b.note || "" } });
    if (r && r.ok === false) { toast(r.message || "Not saved", "warn"); return; }
    toast((r && r.message) || "Bet added", "ok");
    vibrate(20);
    ui.bet = { game: null, kind: b.kind, team: null, line: "", side: "over", note: "" };
    closeSheet(sheetById("bet"));
    if (app.S) { try { const list = await api("/api/bets"); if (Array.isArray(list)) app.S.bets = list; } catch { /* next snapshot */ } }
    hooks.render();
  } catch (e) { toast(e.message, "error"); }
  finally { el.removeAttribute("aria-busy"); }
};
ACTIONS["del-bet"] = async (el, e) => {
  e && e.stopPropagation();
  const id = el.dataset.id;
  const b = arr(get(app.S, "bets")).find((x) => String(x.id) === id);
  const ok = await confirmSheet({ title: "Delete this bet?", body: `${esc(b ? String(b.label || describeBet(b)).replace(/^your bet:\s*/i, "") : "This bet")} stops boosting its game.`, ok: "Delete", danger: true, ic: "trash" });
  if (!ok) return;
  try {
    await api(`/api/bets/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (app.S) app.S.bets = arr(app.S.bets).filter((x) => String(x.id) !== id);
    toast("Bet deleted", "ok");
    hooks.render();
  } catch (err) { toast(err.message, "error"); }
};
INPUTS["bet-line"] = (el) => { ui.bet.line = el.value.trim(); };
INPUTS["bet-note"] = (el) => { ui.bet.note = el.value; };
