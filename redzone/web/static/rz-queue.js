/* RedZone Everything — "Up next on the other stick" card (Now screen): the top 5 queued games with how
   ready each is and why, Pin and Move buttons, and the games that can't be reached tonight.
   Data: S.game_queue_card from the server (redzone/web/server.py queue_card). Absent when queue.enabled is off,
   so the card is simply not drawn.
   Why: show what the hidden stick is lining up in plain words, and never what happens next (no scores to come,
   no join times, no outcomes: the server only sends level, reason and app). */

import { ACTIONS, attr, esc, arr, gameById } from "./rz-core.js?v=41";
import { icon, sec } from "./rz-ui.js?v=40";
import { api } from "./rz-api.js?v=44";
import { toast } from "./rz-sheet.js?v=40";

const name = (gid) => { const g = gameById(gid); return g ? g.matchup : "A game"; };

function row(r, i) {
  const gid = attr(r.game);
  return `<div class="r" data-key="q:${gid}">
    <span class="pick-ic" style="width:40px;height:40px">${icon(r.pinned ? "target" : "clock")}</span>
    <div class="grow" style="min-width:0">
      <div class="t1 ell">${esc(name(r.game))}</div>
      <div class="t2 ell">${esc(r.level_word || "")}${r.reason ? ` · ${esc(r.reason)}` : ""}</div>
    </div>
    <span class="side">
      <button class="mini-act" data-act="queue-pin" data-gid="${gid}" data-on="${r.pinned ? "" : "1"}" aria-pressed="${!!r.pinned}">${r.pinned ? "Unpin" : "Pin"}</button>
      <button class="mini-act" data-act="queue-move" data-gid="${gid}" ${i === 0 ? "disabled" : ""} aria-label="Move up">Move up</button>
    </span>
  </div>`;
}

export function queueCard(S) {
  const Q = S && S.game_queue_card;
  if (!Q) return "";
  const rows = arr(Q.rows).slice(0, 5), cant = arr(Q.cant_reach);
  if (!rows.length && !cant.length) return "";
  const cr = cant.length ? `<div class="r"><span class="pick-ic" style="width:40px;height:40px">${icon("alert")}</span><div class="grow">
    <div class="t1">Can't reach tonight</div>${cant.slice(0, 5).map((g) => `<div class="t2 ell">${esc(name(g))}</div>`).join("")}${cant.length > 5 ? `<div class="t2">+${cant.length - 5} more</div>` : ""}</div></div>` : "";
  return `<div data-key="queue-card">${sec("Up next", { n: rows.length || "", key: "sec-queue-card", aside: "The other stick" })}
    <div class="card rows" data-key="queue-card-rows">${rows.map(row).join("")}${cr}</div></div>`;
}

const tapId = () => { try { if (crypto.randomUUID) return crypto.randomUUID(); } catch { /* older Safari */ } return `q${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`; };
const inFlight = new Set();   // one request per button at a time: a second tap while one is out is the same tap

async function tap(path, body, el) {
  const k = `${path}|${body.game_id}`;
  if (inFlight.has(k)) return;
  inFlight.add(k);
  if (el) { el.disabled = true; el.setAttribute("aria-busy", "true"); }
  try {
    // the id lets the server answer a retry from its log instead of moving the game twice
    const r = await api(path, { body: { ...body, id: tapId() } });
    if (!r || !r.ok) toast((r && r.message) || "That didn't work", "error");
  } catch (e) {
    toast(e.message || "That didn't work", "error");
  } finally {
    inFlight.delete(k);
    if (el) { el.disabled = false; el.removeAttribute("aria-busy"); }
  }
}
ACTIONS["queue-pin"] = (el, e) => { e && e.stopPropagation(); return tap("/api/queue/pin", { game_id: el.dataset.gid, value: !!el.dataset.on }, el); };
ACTIONS["queue-move"] = (el, e) => { e && e.stopPropagation(); return tap("/api/queue/move", { game_id: el.dataset.gid, value: "up" }, el); };
