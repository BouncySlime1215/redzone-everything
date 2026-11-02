/* RedZone Everything — keyed DOM reconciliation.
   Screens render HTML strings; patch() morphs the live DOM toward them:
   - children with data-key are matched by key (moved, never recreated), the rest by position + tag
   - only changed text/attributes are written, so CSS transitions (meters, thumbs) animate naturally
   - data-own: JS owns the children (odometers, timers) · data-keep: never touched after creation
   - data-rt-*: runtime attributes (image load state) that survive patches
   - [data-flip] containers glide keyed children to their new positions (FLIP) and fade exits
   - focused inputs are never overwritten; scroll position is untouched */

import { reduceMotion, settleImages, SPRING, EASE_OUT } from "./rz-core.js?v=24";

const tpl = document.createElement("template");
const keyOf = (n) => (n.nodeType === 1 ? n.getAttribute("data-key") : null);
const isGhost = (n) => n.nodeType === 1 && n.hasAttribute("data-ghost");

export function patch(root, html) {
  if (!root) return;
  tpl.innerHTML = html;
  const flips = measureFlips(root);
  morphChildren(root, tpl.content);
  tpl.innerHTML = "";
  if (flips) playFlips(flips);
  hydrate(root);
}

function sameType(a, b) {
  if (a.nodeType !== b.nodeType || a.nodeName !== b.nodeName) return false;
  if (a.nodeType === 1 && a.nodeName === "IMG") return a.getAttribute("data-src") === b.getAttribute("data-src");
  return true;
}

function morphChildren(from, to) {
  const old = [];
  for (let n = from.firstChild; n; n = n.nextSibling) if (!isGhost(n)) old.push(n);
  const keyed = new Map(), loose = [];
  for (const n of old) { const k = keyOf(n); if (k != null) keyed.set(k, n); else loose.push(n); }
  let u = 0;
  const out = [];
  for (let nn = to.firstChild; nn; nn = nn.nextSibling) {
    const k = keyOf(nn);
    let m = null;
    if (k != null) {
      const c = keyed.get(k);
      if (c && sameType(c, nn)) { m = c; keyed.delete(k); }
    } else {
      for (let j = u; j < loose.length; j++) {
        if (sameType(loose[j], nn)) { m = loose[j]; u = j + 1; break; }
      }
    }
    if (m) morphNode(m, nn);
    else m = document.importNode(nn, true);
    out.push(m);
  }
  const keep = new Set(out);
  for (const n of old) if (!keep.has(n)) n.remove();
  let ref = from.firstChild;
  for (const n of out) {
    while (ref && isGhost(ref)) ref = ref.nextSibling;
    if (n === ref) { ref = ref.nextSibling; continue; }
    from.insertBefore(n, ref);
  }
}

function morphNode(a, b) {
  if (a.nodeType !== 1) {
    if (a.nodeValue !== b.nodeValue) a.nodeValue = b.nodeValue;
    return;
  }
  if (a.hasAttribute("data-keep")) return;
  syncAttrs(a, b);
  if (a.nodeName === "INPUT") { syncInput(a, b); return; }
  if (a.nodeName === "TEXTAREA" || a.hasAttribute("data-own")) return;
  if (a.nodeName === "SELECT") { morphChildren(a, b); syncSelect(a, b); return; }
  morphChildren(a, b);
}

function syncAttrs(a, b) {
  const img = a.nodeName === "IMG";
  for (const { name, value } of Array.from(b.attributes)) {
    if (img && name === "src") continue; // may hold the fallback URL; data-src decides identity
    if (a.getAttribute(name) !== value) a.setAttribute(name, value);
  }
  for (const { name } of Array.from(a.attributes)) {
    if (b.hasAttribute(name) || name.startsWith("data-rt-") || (img && name === "src")) continue;
    a.removeAttribute(name);
  }
}

function syncInput(a, b) {
  if (document.activeElement === a) return;
  if (a.type === "checkbox" || a.type === "radio") {
    const want = b.hasAttribute("checked");
    if (a.checked !== want) a.checked = want;
  } else if (b.hasAttribute("data-sync")) {
    const v = b.getAttribute("value") ?? "";
    if (a.value !== v) a.value = v;
  }
}
function syncSelect(a, b) {
  if (document.activeElement === a) return;
  const sel = b.querySelector("option[selected]");
  const v = sel ? sel.getAttribute("value") ?? sel.textContent : a.options[0] && a.options[0].value;
  if (v != null && a.value !== v) a.value = v;
}

/* ---------- FLIP ---------- */
function measureFlips(root) {
  if (reduceMotion()) return null;
  const conts = root.matches && root.matches("[data-flip]") ? [root] : [];
  conts.push(...root.querySelectorAll("[data-flip]"));
  const out = [];
  for (const c of conts) {
    const box = c.getBoundingClientRect();
    if (!box.width || !box.height) continue;
    const items = new Map();
    for (const ch of c.children) if (keyOf(ch) && !isGhost(ch)) items.set(ch, ch.getBoundingClientRect());
    out.push({ c, items });
  }
  return out.length ? out : null;
}
function playFlips(list) {
  for (const { c, items } of list) {
    if (!c.isConnected) continue;
    const box = c.getBoundingClientRect();
    for (const ch of c.children) {
      if (!keyOf(ch) || isGhost(ch)) continue;
      const was = items.get(ch);
      if (was) {
        const now = ch.getBoundingClientRect();
        const dx = was.left - now.left, dy = was.top - now.top;
        if (Math.abs(dx) > 1 || Math.abs(dy) > 1) {
          ch.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }], { duration: 620, easing: SPRING });
        }
      } else if (items.size) {
        ch.animate([{ opacity: 0, transform: "translateY(10px) scale(.98)" }, { opacity: 1, transform: "none" }],
          { duration: 420, easing: EASE_OUT, delay: 80, fill: "backwards" });
      }
    }
    for (const [el, r] of items) {
      if (el.parentNode === c) continue;
      ghostOut(el, r, box, c);
    }
  }
}
function ghostOut(el, r, box, c) {
  el.setAttribute("data-ghost", "");
  el.removeAttribute("data-key");
  Object.assign(el.style, { position: "absolute", left: `${r.left - box.left}px`, top: `${r.top - box.top}px`,
    width: `${r.width}px`, height: `${r.height}px`, margin: "0", pointerEvents: "none", zIndex: "0" });
  c.appendChild(el);
  const a = el.animate([{ opacity: 1, transform: "none" }, { opacity: 0, transform: "scale(.96)" }],
    { duration: 260, easing: "ease-in", fill: "forwards" });
  a.onfinish = () => el.remove();
}

/* ---------- hydrate: odometers + image settle ---------- */
export function hydrate(root) {
  for (const el of root.querySelectorAll("[data-odo]")) odoSet(el, el.getAttribute("data-odo"));
  settleImages(root);
}

const DIGITS = Array.from({ length: 20 }, (_, i) => `<span>${i % 10}</span>`).join("");
function odoSet(el, v) {
  const s = String(v ?? "");
  if (el._odo === s) return;
  const prev = el._odo;
  el._odo = s;
  if (!/^\d+$/.test(s)) { el.textContent = s || "—"; el._cols = null; return; }
  const digits = s.split("").map(Number);
  const animate = prev !== undefined && /^\d+$/.test(prev) && !reduceMotion() && el.isConnected;
  if (!el._cols || el._cols.length !== digits.length) {
    el.textContent = "";
    el._cols = digits.map(() => {
      const d = document.createElement("span");
      d.className = "odo-d";
      const col = document.createElement("span");
      col.className = "odo-col";
      col.innerHTML = DIGITS;
      d.appendChild(col);
      el.appendChild(d);
      return { col, pos: 0 };
    });
    el._cols.forEach((c, i) => setPos(c, digits[i], false));
    if (animate) el.animate([{ transform: "translateY(.3em)", opacity: 0.2 }, { transform: "none", opacity: 1 }], { duration: 480, easing: SPRING });
    return;
  }
  const up = Number(s) > Number(prev);
  el._cols.forEach((c, i) => {
    const d = digits[i];
    if (c.pos % 10 === d && c.pos < 10) return;
    if (!animate) { setPos(c, d, false); return; }
    const target = up && d < c.pos % 10 ? d + 10 : d;
    setPos(c, target, true);
    if (target >= 10) {
      const done = () => setPos(c, d, false);
      c.col.addEventListener("transitionend", done, { once: true });
      setTimeout(done, 900);
    }
  });
}
function setPos(c, p, anim) {
  const col = c.col;
  if (anim) { col.classList.add("roll"); }
  else { col.classList.remove("roll"); col.style.transition = "none"; }
  col.style.transform = `translateY(${-p}em)`;
  if (!anim) { void col.offsetHeight; col.style.transition = ""; }
  c.pos = p;
}

/* ---------- one-shot effects (WAAPI only, so patches never cancel them) ---------- */
export function flash(el, color) {
  if (!el || reduceMotion()) return;
  el.animate([
    { opacity: 0, backgroundImage: `radial-gradient(120% 120% at 50% 50%, ${color}, transparent 70%)` },
    { opacity: 0.55, offset: 0.12, backgroundImage: `radial-gradient(120% 120% at 50% 50%, ${color}, transparent 70%)` },
    { opacity: 0, backgroundImage: `radial-gradient(120% 120% at 50% 50%, ${color}, transparent 70%)` },
  ], { duration: 1600, easing: "ease-out" });
}
export function pop(el, scale = 1.25) {
  if (!el || reduceMotion()) return;
  el.animate([{ transform: "scale(1)" }, { transform: `scale(${scale})`, offset: 0.35 }, { transform: "scale(1)" }],
    { duration: 520, easing: SPRING });
}
