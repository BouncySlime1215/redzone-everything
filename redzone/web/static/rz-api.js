/* RedZone Everything — server API + live stream (SSE with backoff). ?mock=1 swaps in rz-mock.js. */

import { MOCK, vibrate } from "./rz-core.js?v=41";
import { toast } from "./rz-sheet.js?v=40";

let mock = null;
async function mockApi() {
  if (!mock) mock = await import("./rz-mock.js?v=40");
  return mock;
}

export async function api(path, { method, body } = {}) {
  if (MOCK) return (await mockApi()).mockApi(path, { method, body });
  const init = { method: method || (body !== undefined ? "POST" : "GET"), headers: {} };
  if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
  const r = await fetch(path, init);
  let data = null;
  try { data = await r.json(); } catch { data = null; }
  if (!r.ok) {
    const msg = (data && (data.message || data.detail)) || `Server said ${r.status}`;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

function tapId() {
  try { if (crypto.randomUUID) return crypto.randomUUID(); } catch { /* older Safari */ }
  return `t${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}

const RETRY_MS = [400, 1200, 2500];

/** POST /api/action once per tap: the tap carries an id, and a lost answer (Wi-Fi blip, server
 *  restart) is retried with the SAME id, so the server answers instead of running it twice. */
export async function sendTap(body) {
  const id = tapId();
  for (let i = 0; ; i++) {
    try {
      return await api("/api/action", { body: { ...body, id } });
    } catch (e) {
      // fetch() rejects with a TypeError when the request never got an answer; a 5xx is a server hiccup.
      const retry = e instanceof TypeError || /Server said 5\d\d/.test(e.message || "");
      if (!retry || i >= RETRY_MS.length) throw e;
      await new Promise((r) => setTimeout(r, RETRY_MS[i]));
    }
  }
}

/** POST /api/action with a toast + haptic; marks the button busy while in flight. */
export async function act(action, extra = {}, btn = null) {
  if (btn) btn.setAttribute("aria-busy", "true");
  try {
    const res = await sendTap({ action, ...extra });
    vibrate(12);
    if (res && res.message) toast(res.message, res.ok === false ? "warn" : "ok");
    return res;
  } catch (e) {
    // playbook=BTN-03: after the retries, say plainly the TV never got it (a 4xx keeps its own message).
    const lost = e instanceof TypeError || /Server said 5\d\d/.test(e.message || "");
    toast(lost ? "Not delivered: the app didn't answer. Tap again." : (e.message || "That didn't work"), "error");
    return null;
  } finally {
    if (btn) btn.removeAttribute("aria-busy");
  }
}

/* ---------- live stream ---------- */
let es = null, backoff = 1000, timer = null, handlers = null;

export function startStream(h) {
  handlers = h;
  if (MOCK) { mockApi().then((m) => m.startMock(h)); return; }
  loadOnce();
  open();
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    loadOnce();
    if (!es || es.readyState === 2) open();
  });
  window.addEventListener("online", () => { backoff = 1000; open(); });
}

export async function loadOnce() {
  if (MOCK) return;
  try { const s = await api("/api/state"); if (s && handlers) handlers.state(s); } catch { /* stream will retry */ }
}

function open() {
  clearTimeout(timer);
  try { es && es.close(); } catch { /* closed */ }
  es = new EventSource("/api/stream");
  es.addEventListener("open", () => { backoff = 1000; handlers.conn("live"); });
  es.addEventListener("state", (ev) => {
    try { handlers.state(JSON.parse(ev.data)); handlers.conn("live"); } catch (e) { console.warn("bad state", e); }
  });
  es.addEventListener("toast", (ev) => {
    try { const t = JSON.parse(ev.data); toast(t.text, t.level === "error" ? "error" : t.level === "warn" ? "warn" : "info"); } catch { /* ignore */ }
  });
  es.onerror = () => {
    handlers.conn("wait");
    try { es.close(); } catch { /* closed */ }
    timer = setTimeout(() => { loadOnce(); open(); }, backoff);
    backoff = Math.min(20000, Math.round(backoff * 1.8));
  };
}
