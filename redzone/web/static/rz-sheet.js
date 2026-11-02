/* RedZone Everything — bottom sheets (stacked, drag-to-dismiss, spring) and toasts. */

import { esc, reduceMotion, SPRING } from "./rz-core.js?v=28";
import { patch } from "./rz-dom.js?v=28";
import { icon } from "./rz-ui.js?v=28";

const stack = [];
const host = () => document.getElementById("sheets");

/**
 * openSheet({ id, render(sheet) -> string | {body, foot}, live, full, flush, onImg, onClose })
 * `live` sheets re-render (keyed patch) on every snapshot, so scroll and focus stay put.
 */
export function openSheet(opts) {
  const existing = stack.find((s) => s.id && s.id === opts.id);
  if (existing) { Object.assign(existing, opts); existing.update(); return existing; }
  const layer = document.createElement("div");
  layer.className = "sheet-layer";
  layer.innerHTML = `<div class="scrim"></div>
    <div class="sheet ${opts.full ? "sheet--full" : ""}" role="dialog" aria-modal="true" aria-label="${esc(opts.label || "Details", "Details")}">
      <div class="sheet-grab ${opts.onImg ? "on-img" : ""}" aria-hidden="true"></div>
      <button class="sheet-close ${opts.onImg ? "on-img" : ""}" aria-label="Close">${icon("x")}</button>
      <div class="sheet-body ${opts.flush ? "flush" : ""}"></div>
      <div class="sheet-foot" hidden></div>
    </div>`;
  host().appendChild(layer);
  const s = {
    ...opts, layer,
    el: layer.querySelector(".sheet"),
    body: layer.querySelector(".sheet-body"),
    foot: layer.querySelector(".sheet-foot"),
  };
  s.update = () => render(s);
  s.close = (v) => closeSheet(s, v);
  layer.querySelector(".scrim").addEventListener("click", () => closeSheet(s));
  layer.querySelector(".sheet-close").addEventListener("click", () => closeSheet(s));
  stack.push(s);
  render(s);
  wireDrag(s);
  document.documentElement.classList.add("sheet-open");
  document.body.style.overflow = "hidden";
  requestAnimationFrame(() => requestAnimationFrame(() => {
    layer.classList.add("open");
    if (!reduceMotion()) {
      s.el.animate([{ transform: "translateY(100%)" }, { transform: "translateY(0)" }], { duration: 560, easing: SPRING });
    }
  }));
  return s;
}

function render(s) {
  let out;
  try { out = s.render(s); } catch (e) { console.error(e); out = `<p class="lead">Something went wrong drawing this.</p>`; }
  const body = typeof out === "string" ? out : out.body;
  patch(s.body, body || "");
  if (out && out.foot) { s.foot.hidden = false; patch(s.foot, out.foot); } else s.foot.hidden = true;
}

export function closeSheet(s = stack[stack.length - 1], value) {
  if (!s || s.closing) return;
  s.closing = true;
  const i = stack.indexOf(s);
  if (i >= 0) stack.splice(i, 1);
  s.layer.classList.remove("open");
  s.el.style.removeProperty("--drag");
  setTimeout(() => s.layer.remove(), reduceMotion() ? 0 : 480);
  if (!stack.length) {
    document.documentElement.classList.remove("sheet-open");
    document.body.style.overflow = "";
  }
  try { s.onClose && s.onClose(value); } catch (e) { console.error(e); }
}
export const topSheet = () => stack[stack.length - 1] || null;
export const sheetById = (id) => stack.find((s) => s.id === id) || null;
export function updateSheets() { for (const s of stack) if (s.live) render(s); }

/* Drag to dismiss: from the grab bar anywhere, or from content that is scrolled to the top. */
function wireDrag(s) {
  let y0 = null, t0 = 0, dy = 0, pulling = false;
  const scrim = s.layer.querySelector(".scrim");
  const set = (d) => {
    dy = Math.max(0, d);
    s.el.style.setProperty("--drag", `${dy}px`);
    scrim.style.opacity = String(Math.max(0.15, 1 - dy / 500));
  };
  const release = () => {
    if (y0 === null && !pulling) return;
    const v = dy / Math.max(1, performance.now() - t0);
    s.layer.classList.remove("dragging");
    scrim.style.opacity = "";
    if (dy > 110 || v > 0.55) closeSheet(s);
    else s.el.style.setProperty("--drag", "0px");
    y0 = null; pulling = false; dy = 0;
  };
  const grab = s.layer.querySelector(".sheet-grab");
  grab.addEventListener("pointerdown", (e) => {
    y0 = e.clientY; t0 = performance.now(); dy = 0;
    s.layer.classList.add("dragging");
    try { grab.setPointerCapture(e.pointerId); } catch { /* old Safari */ }
  });
  grab.addEventListener("pointermove", (e) => { if (y0 !== null) set(e.clientY - y0); });
  grab.addEventListener("pointerup", release);
  grab.addEventListener("pointercancel", release);

  let sy = null;
  s.body.addEventListener("touchstart", (e) => {
    sy = s.body.scrollTop <= 0 && e.touches.length === 1 ? e.touches[0].clientY : null;
    pulling = false; t0 = performance.now();
  }, { passive: true });
  s.body.addEventListener("touchmove", (e) => {
    if (sy === null) return;
    const d = e.touches[0].clientY - sy;
    if (!pulling && d > 8 && s.body.scrollTop <= 0 && !e.target.closest("input, textarea, .filters, .my-teams, .no-drag")) {
      pulling = true; s.layer.classList.add("dragging"); t0 = performance.now();
    }
    if (pulling) { e.preventDefault(); set(d - 8); }
  }, { passive: false });
  s.body.addEventListener("touchend", () => { if (pulling) release(); sy = null; });
  s.body.addEventListener("touchcancel", () => { if (pulling) release(); sy = null; });
}

/** Promise<boolean> confirmation sheet. */
export function confirmSheet({ title, body = "", ok = "OK", cancel = "Cancel", danger = false, ic = "" }) {
  return new Promise((resolve) => {
    let done = false;
    const s = openSheet({
      id: "confirm", label: title,
      render: () => `<div class="confirm">${ic ? `<div class="pick-ic" style="margin-bottom:14px;${danger ? "background:color-mix(in srgb,var(--danger) 14%,transparent);color:var(--danger-ink)" : ""}">${icon(ic)}</div>` : ""}
        <h3>${esc(title)}</h3>${body ? `<p class="lead">${body}</p>` : ""}
        <div class="confirm-actions"><button class="btn btn--lg" data-confirm="0">${esc(cancel)}</button>
        <button class="btn btn--lg ${danger ? "btn--danger" : "btn--primary"}" data-confirm="1">${esc(ok)}</button></div></div>`,
      onClose: (v) => { if (!done) { done = true; resolve(v === true); } },
    });
    s.body.addEventListener("click", (e) => {
      const b = e.target.closest("[data-confirm]");
      if (!b) return;
      const yes = b.dataset.confirm === "1";
      done = true; resolve(yes); closeSheet(s, yes);
    });
  });
}

/* ---------- toasts ---------- */
export function toast(text, level = "info") {
  if (!text) return;
  const hostEl = document.getElementById("toasts");
  const el = document.createElement("div");
  const lv = ["ok", "warn", "error", "info"].includes(level) ? level : "info";
  el.className = `toast toast--${lv}`;
  el.setAttribute("role", lv === "error" ? "alert" : "status");
  el.innerHTML = `<span class="ti">${icon({ ok: "check", warn: "alert", error: "x", info: "info" }[lv])}</span><span>${esc(text)}</span>`;
  hostEl.prepend(el);
  while (hostEl.children.length > 3) hostEl.lastChild.remove();
  if (!reduceMotion()) el.animate([{ opacity: 0, transform: "translateY(18px) scale(.94)" }, { opacity: 1, transform: "none" }], { duration: 520, easing: SPRING });
  const out = () => {
    if (!el.isConnected) return;
    const a = el.animate([{ opacity: 1 }, { opacity: 0, transform: "translateY(10px) scale(.97)" }], { duration: reduceMotion() ? 1 : 220, easing: "ease-in", fill: "forwards" });
    a.onfinish = () => el.remove();
  };
  setTimeout(out, lv === "error" ? 4600 : 2800);
  el.addEventListener("click", out);
}
