/* Recorded-device sessions are separate from live switching and delay calibration. */
import { app, ACTIONS, INPUTS, CHANGES, hooks, attr, esc, arr } from "./rz-core.js?v=41";
import { api, loadOnce } from "./rz-api.js?v=44";
import { toast } from "./rz-sheet.js?v=40";
import { icon, sec } from "./rz-ui.js?v=40";

const draft = app.ui.recordedDraft || (app.ui.recordedDraft = { host: "", query: "", home: "", away: "", date: "" });
const STAGES = {
  none: "Preparing session", preparing_local_ocr: "Preparing local screen reading", local_ocr_ready: "Screen reader ready",
  empty_search_verified: "Search opened", sports_results_observed: "Results found",
  matching_replay_card_selected: "Matching recording opened", replay_detail_verified: "Date and access checked",
  watch_replay_selected: "Playback requested", player_title_observed: "Matching player title observed",
};
let busy = false;

export function recordedPanel(S, showForm = false) {
  const r = S.recorded_playback || {};
  if (r.active) {
    const q = r.request || {};
    return `<section class="card card-pad" data-key="recorded-active" aria-label="Recorded playback session">
      <div class="overline">${icon("replay")} Recorded playback · automatic live switching paused</div>
      <h2 style="margin:10px 0;font-size:22px">${esc(q.away)} vs. ${esc(q.home)}</h2>
      <p class="muted">${esc(q.date)} · Prime Video</p>
      <p><b>${esc(STAGES[r.stage] || "Recorded session")}</b>${r.elapsed_s ? ` · ${Math.round(r.elapsed_s)}s` : ""}</p>
      <p class="dim">${esc(r.reason || "Checking the selected recording on your stick.")}</p>
      <p class="dim" style="font-size:12.5px">${r.event_date_verified ? "Recording date checked." : "Recording date not yet checked."} ${r.identity === "player_title_observed" ? "Matching player title observed." : "Player identity not yet confirmed."} Video frames and live delay are not verified.</p>
      <button class="btn" data-act="recorded-stop" ${busy ? "disabled" : ""}>Exit recorded mode</button>
      <p class="dim" style="font-size:12px;margin-bottom:0">Exiting stops navigation and returns the app to paused dry run. The recording may continue on the stick.</p>
    </section>`;
  }
  if (!showForm) return "";
  const blocked = r.device_control_allowed === false;
  const sticks = arr(S.health?.sticks).filter((s) => !String(s.os || "").includes("Vega"));
  const field = (key, label, placeholder, type = "text") => `<label style="display:block;margin:12px 0 5px">${label}
    <input class="input" type="${type}" data-input="recorded-draft" data-field="${key}" value="${attr(draft[key])}" placeholder="${attr(placeholder)}" autocomplete="off" ${type === "date" ? "" : "maxlength=90"} required></label>`;
  return `${sec("Recorded playback", { key: "s-recorded" })}<section class="card card-pad" data-key="recorded-form">
    <p class="muted" style="margin-top:0">Open an exact Prime recording without navigating its menus yourself. This mode pauses automatic live switching and checks the date, access, focused button, and player title.</p>
    <label style="display:block">Selected stick<select class="input" data-change="recorded-draft" data-field="host" required>
      <option value="">Choose a saved stick</option>${sticks.map((s) => `<option value="${attr(s.host)}" ${draft.host === s.host ? "selected" : ""}>${esc(s.name)}${s.hdmi ? ` · ${esc(s.hdmi)}` : ""}</option>`).join("")}</select></label>
    ${field("away", "Away team as displayed", "Raptors")}${field("home", "Home team as displayed", "Lakers")}
    ${field("query", "Search words", "Raptors")}${field("date", "Exact recording date", "", "date")}
    <p class="dim" style="font-size:12.5px">Starting checks the selected stick's connection and enables device control for this recorded session. Automatic live switching stays paused. Live input control, exact live-game identity, and display delay still require validation for this setup.</p>
    ${blocked ? '<p class="dim">This login service currently enforces dry run. Recorded device control is unavailable until its operating policy is changed.</p>' : ""}
    <button class="btn btn--primary" data-act="recorded-start" ${busy || !sticks.length || blocked ? "disabled" : ""}>${icon("play")}Start recorded playback</button>
    ${!sticks.length ? '<p class="dim">A saved Fire OS stick is required.</p>' : ""}
  </section>`;
}

const save = (el) => { draft[el.dataset.field] = el.value; };
INPUTS["recorded-draft"] = save;
CHANGES["recorded-draft"] = save;
ACTIONS["recorded-start"] = async (el) => {
  for (const input of el.closest("section").querySelectorAll("input,select")) {
    save(input);
    if (!input.reportValidity()) return;
  }
  await command("/api/recorded/start", { ...draft, provider: "prime", execute: true, enable_device_control: true });
};
ACTIONS["recorded-stop"] = async () => command("/api/recorded/stop", {});

async function command(path, body) {
  if (busy) return;
  busy = true;
  hooks.renderScreen("lab"); hooks.renderScreen("now");
  try {
    const result = await api(path, { body });
    toast(result.message || "Recorded session updated", result.ok ? "ok" : "warn");
    await loadOnce();
  } catch (error) { toast(error.message || "Recorded session failed", "error"); }
  finally { busy = false; hooks.renderScreen("lab"); hooks.renderScreen("now"); }
}
