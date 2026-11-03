/* Screen timing is an estimate. Transport playback never proves live action on the TV. */
import { ACTIONS, attr, esc } from "./rz-core.js?v=40";
import { icon } from "./rz-ui.js?v=40";

const STATUS = {
  unknown: "Screen timing unknown",
  play_possible: "A play may be underway",
  break_estimated: "Break estimated on screen",
  gap_estimated: "Gap estimated between plays",
};
const STAGE = {
  idle: "No switch in progress", tuning: "Opening the next stream",
  waiting_safe_exit: "Waiting for an exit window", rewinding: "Preparing a replay",
  verifying_input: "Checking the TV input", confirmed: "Switch completed", failed: "Switch did not complete",
};
const seconds = (value) => typeof value === "number" && Number.isFinite(value) ? `${Math.round(Math.max(0, value))}s` : "Unknown";
const expanded = new Set();
ACTIONS["evidence-toggle"] = (el) => {
  const key = el.dataset.evidence;
  if (el.closest("details").open) expanded.delete(key); else expanded.add(key);
};

export function screenEvidence(S, key = "screen-evidence") {
  const e = S.screen_evidence || {};
  const recorded = e.mode === "recorded";
  const simulation = e.mode === "simulation" || S.mode?.dry_run !== false;
  const stage = e.switch || {};
  const title = recorded ? "Recorded playback · live timing unavailable" : STATUS[e.status] || STATUS.unknown;
  const delay = seconds(e.delay_s);
  const margin = seconds(e.delay_margin_s);
  const rows = [
    ["Sports data age", seconds(e.feed_age_s)],
    ["Estimated screen delay", delay === "Unknown" ? delay : `${delay} ± ${margin}`],
    ["Delay basis", e.calibrated ? "Measured samples" : "Not measured on this TV"],
    ["Media playback", e.media_state === "playing" ? "Video reported playing" : e.media_state === "buffering" ? "Buffering" : "Not confirmed"],
    ["Game identity", e.identity === "stream_continuity" ? "Stream continuity only" : "Not independently verified"],
  ];
  return `<section class="card evidence" data-key="${attr(key)}" aria-label="Screen timing evidence">
    <div class="evidence-head">${icon(e.safe_to_leave ? "clock" : "lock")}<div class="grow"><b>${esc(title)}</b>
      <small>${recorded ? "Automatic live switching is paused" : simulation ? "Simulation · TV control is off" : e.safe_to_leave ? "An exit window is estimated" : "Holding until timing is clearer"}</small></div></div>
    <p>${esc(e.reason || "Waiting for a game and stream timing evidence.")}</p>
    <div class="evidence-stage">${icon("arrow-right", "ico--xs")}<span>${esc(STAGE[stage.stage] || STAGE.idle)}${stage.reason ? ` · ${esc(stage.reason)}` : ""}</span></div>
    <details class="adv"${expanded.has(key) ? " open" : ""}><summary data-act="evidence-toggle" data-evidence="${attr(key)}">What this is based on${icon("chevron-down")}</summary>
      <dl>${rows.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl>
      <p class="evidence-note">Estimated from sports feeds and stream delay. A provider reporting video playback cannot tell us whether a live play, replay, or ad is on your TV.</p>
    </details>
  </section>`;
}
