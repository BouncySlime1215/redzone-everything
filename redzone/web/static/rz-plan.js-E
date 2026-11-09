/* RedZone Everything — "Tonight's plan" card (Now screen): every game today, where it'll be
   watched, and whether that source was played on the hidden stick before it started.
   Data: S.tonight from the engine (redzone/plan.py, Tonight.view). */

import { attr, esc, num, arr, gameById, nowS } from "./rz-core.js?v=40";
import { icon, logo, sec } from "./rz-ui.js?v=40";

const MAX_ROWS = 8;
const CHECK = {
  verified: ["chip--bet", "check", "Checked"],
  failed: ["chip--live", "alert", "Failed"],
  pending: ["chip--locked", "clock", "To check"],
  "not checked": ["", "minus", "Not checked"],
};
const STATUS = {
  "needs setup": ["chip--break", "link", "Needs setup"],
  blackout: ["chip--live", "octagon", "Blacked out"],
  "at risk": ["chip--break", "alert", "At risk"],
  skip: ["", "minus", "Skipped"],
};

function chip(kind, ic, text) {
  return `<span class="chip ${kind}">${icon(ic)}<span>${esc(text)}</span></span>`;
}

function badge(r, checking) {
  if (checking) return chip("chip--locked", "hourglass", "Checking");
  if (r.status !== "ready") return chip(...(STATUS[r.status] || ["", "info", r.status]));
  return chip(...(CHECK[r.check] || ["", "info", r.check]));
}

function row(r, T) {
  const g = gameById(r.game_id);
  const checking = arr(T.checking_games).includes(r.game_id);
  const detail = r.status !== "ready" ? (r.gaps[0] || r.why || "")
    : r.check === "failed" || r.check === "not checked" ? r.check_detail
    : r.gaps[0] || (r.fallbacks.length ? `Fallback: ${r.fallbacks[0]}` : "");
  return `<div class="r ${g ? "r--tap" : ""}" data-key="tp:${attr(r.game_id)}" ${g ? `data-act="open-game" data-gid="${attr(r.game_id)}" role="button"` : ""}>
    ${g ? `<span class="logo-pair">${logo(g.away, { size: 30 })}${logo(g.home, { size: 30 })}</span>` : `<span class="pick-ic" style="width:40px;height:40px">${icon("calendar")}</span>`}
    <div class="grow" style="min-width:0">
      <div class="t1 ell">${esc(r.matchup)}${r.mine ? ` ${icon("star", "ico--fill ico--xs")}` : ""}</div>
      <div class="t2 ell">${esc(r.start_et)} · ${esc(r.source || "No source yet")}</div>
      ${detail ? `<div class="t2 ell">${esc(detail)}</div>` : ""}
    </div>
    <span class="side">${badge(r, checking)}</span>
  </div>`;
}

export function tonightCard(S) {
  const T = S && S.tonight;
  if (!T || !arr(T.games).length) return "";
  const c = T.counts || {};
  const now = nowS();
  // What's still ahead (and anything live), your teams first within the same start.
  const ahead = arr(T.games).filter((r) => !r.start || num(r.start) > now - 4 * 3600);
  const rows = ahead.slice(0, MAX_ROWS);
  const nw = T.next_window;
  const aside = c.verified || c.failed ? `${num(c.verified)} checked${c.failed ? ` · ${num(c.failed)} failed` : ""}` : `${num(c.ready)} of ${num(c.games)} ready`;
  const note = T.note ? `<div class="r"><span class="pick-ic" style="width:40px;height:40px">${icon("info")}</span><div class="grow"><div class="t2">${esc(
    nw ? `Checks for ${nw.start_et} wait: ${T.note}` : T.note)}</div></div></div>` : "";
  const gaps = arr(T.gaps).length ? `<div class="r"><span class="pick-ic" style="width:40px;height:40px">${icon("alert")}</span><div class="grow">${arr(T.gaps).slice(0, 3)
    .map((x) => `<div class="t2">${esc(x)}</div>`).join("")}</div></div>` : "";
  const more = ahead.length > rows.length ? `<div class="r"><div class="grow"><div class="t2">+${ahead.length - rows.length} more later tonight</div></div></div>` : "";
  return `<div data-key="tonight-plan">${sec("Tonight's plan", { n: c.games, key: "sec-tonight-plan", aside: esc(aside) })}
    <div class="card rows" data-key="tonight-plan-rows">${note}${rows.map((r) => row(r, T)).join("")}${more}${gaps}</div></div>`;
}
