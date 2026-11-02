/* RedZone Everything — component library (HTML builders). Pure functions: data in, markup out. */

import {
  attr, esc, num, clamp, hex, initials, imgTag, isDark, teamLogoUrl, leagueLogoUrl, LEAGUE_NAME, heatColor, ink,
} from "./rz-core.js?v=32";

export const icon = (name, cls = "") => `<svg class="ico ${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;

/** Team logo in a fixed box; abbr bubble in team color if the image is missing or fails. */
export function logo(team, { size = 32, dark = isDark(), cls = "", lazy = true, eager = false } = {}) {
  const t = team || {};
  const url = teamLogoUrl(t, dark);
  const tc = hex(t.color) || "#4a5163";
  const im = url ? imgTag(url, { alt: "", lazy, eager }) : "";
  return `<span class="logo ${cls}" style="--s:${size}px;--tc:${tc}">${im}<b${im ? "" : ' class="on"'}>${esc(t.abbr, "?")}</b></span>`;
}
export function leagueImg(lg, { dark = isDark(), cls = "lg-logo" } = {}) {
  const url = leagueLogoUrl(lg, dark);
  return url ? imgTag(url, { cls, alt: LEAGUE_NAME[lg] || "", lazy: false }) : "";
}
/** Player headshot: circle crop, team-color ring, initials fallback. */
export function avatar(p, { size = 44, ring, team, dark = isDark(), cls = "" } = {}) {
  const r = ring || (team ? ink(team, dark) : "#4a5163");
  const url = p && p.headshot;
  const im = url ? imgTag(url, { alt: "" }) : "";
  return `<span class="avatar ${cls}" style="--s:${size}px;--ring:${r}">${im}<b${im ? "" : ' class="on"'}>${esc(initials(p && (p.name || p.short)), "")}</b></span>`;
}
export const odo = (v, cls = "") => `<b class="odo ${cls}" data-own data-odo="${attr(v)}" aria-label="${attr(v)}"></b>`;

export function chip(text, { kind = "", ic = "", attrs = "", html = "" } = {}) {
  return `<span class="chip ${kind ? "chip--" + kind : ""}" ${attrs}>${ic ? icon(ic) : ""}${html}<span>${esc(text, "")}</span></span>`;
}
export function chipBtn(text, { kind = "", ic = "", act = "", data = "", label = "" } = {}) {
  return `<button class="chip chip--btn ${kind ? "chip--" + kind : ""}" data-act="${attr(act)}" ${data} ${label ? `aria-label="${attr(label)}"` : ""}>${ic ? icon(ic) : ""}<span>${esc(text, "")}</span></button>`;
}
export function meter(p, { cls = "", label = "Excitement" } = {}) {
  const v = Math.round(clamp(num(p), 0, 100));
  return `<div class="meter ${v >= 80 ? "meter--hot" : ""} ${cls}" style="--p:${v}" role="meter" aria-label="${attr(label)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${v}"><i></i></div>`;
}
export function heat(p) {
  const v = Math.round(clamp(num(p), 0, 100));
  return `<div class="heat" style="--hc:${heatColor(v)}">${odo(v)}<small>Heat</small></div>`;
}
export function seg(items, active, { act, cls = "", label = "" } = {}) {
  const i = Math.max(0, items.findIndex((x) => x.v === active));
  return `<div class="seg ${cls}" role="tablist" aria-label="${attr(label)}" style="--n:${items.length};--i:${i}"><span class="seg-thumb"></span>${items
    .map((x) => `<button class="seg-btn" role="tab" aria-selected="${x.v === active}" data-act="${attr(act)}" data-v="${attr(x.v)}">${x.html || esc(x.label)}</button>`)
    .join("")}</div>`;
}
export function sec(title, { n = "", aside = "", live = false, key = "" } = {}) {
  return `<div class="sec"${key ? ` data-key="${attr(key)}"` : ""}><h2>${live ? '<i class="live-dot"></i>' : ""}${esc(title)}${n !== "" ? `<span class="n">${esc(n)}</span>` : ""}</h2>${aside ? `<span class="aside">${aside}</span>` : ""}</div>`;
}
export function screenHead(title, sub = "", right = "") {
  return `<div class="screen-head"><div><h1>${esc(title)}</h1>${sub ? `<div class="sub">${sub}</div>` : ""}</div>${right}</div>`;
}
export function empty(art, title, body = "", action = "", cls = "", scope = art) {
  return `<div class="empty ${cls}">${ART[art] ? ART[art](scope) : ""}<h3>${esc(title)}</h3>${body ? `<p>${body}</p>` : ""}${action}</div>`;
}
export function skel(kind, n = 1, extra = "") {
  return Array.from({ length: n }, () => `<div class="skel skel--${kind}" ${extra}></div>`).join("");
}
export function stateBadge(kind, text) {
  const label = text || { live: "Live", break: "Break", replay: "Replay", locked: "Locked", paused: "Paused", final: "Final", pre: "Upcoming" }[kind] || kind;
  return `<span class="badge badge--${kind}">${esc(label)}</span>`;
}

/* ---------- empty-state illustrations (inline SVG; theme-aware via currentColor + tokens) ---------- */
const g = (id, a, b) => `<linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${a}"/><stop offset="1" stop-color="${b}"/></linearGradient>`;
export const ART = {
  tv: (s) => `<svg class="art" viewBox="0 0 180 120" aria-hidden="true"><defs>${g(`tvS${s}`, "#232838", "#11141b")}${g(`tvF${s}`, "#14432a", "#0b2a1a")}
    <radialGradient id="tvGlow${s}" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#ff2d3d" stop-opacity=".35"/><stop offset="1" stop-color="#ff2d3d" stop-opacity="0"/></radialGradient></defs>
    <ellipse cx="90" cy="64" rx="86" ry="52" fill="url(#tvGlow${s})"/>
    <rect x="24" y="14" width="132" height="84" rx="12" fill="url(#tvS${s})" stroke="rgba(255,255,255,.14)"/>
    <rect x="32" y="22" width="116" height="68" rx="6" fill="url(#tvF${s})"/>
    <g stroke="rgba(255,255,255,.22)" stroke-width="1">${[0, 1, 2, 3, 4, 5, 6, 7, 8].map((i) => `<line x1="${44 + i * 11.5}" y1="22" x2="${44 + i * 11.5}" y2="90"/>`).join("")}</g>
    <rect x="32" y="22" width="12" height="68" fill="#5a1414" opacity=".9"/><rect x="136" y="22" width="12" height="68" fill="#0b1c3a" opacity=".9"/>
    <circle cx="90" cy="56" r="13" fill="none" stroke="rgba(255,255,255,.3)"/>
    <path d="M84 49v14l12-7z" fill="#fff" opacity=".9"/>
    <rect x="70" y="100" width="40" height="5" rx="2.5" fill="#232838"/><rect x="82" y="98" width="16" height="4" fill="#1a1e27"/>
    <circle cx="146" cy="14" r="9" fill="#ffc83d"/><circle cx="150" cy="11" r="8" fill="#0d0f14"/>
    <text x="152" y="34" fill="rgba(255,255,255,.5)" font-family="Barlow Condensed,sans-serif" font-weight="800" font-size="11">Z</text>
    <text x="160" y="26" fill="rgba(255,255,255,.35)" font-family="Barlow Condensed,sans-serif" font-weight="800" font-size="8">z</text></svg>`,
  scoreboard: (s) => `<svg class="art" viewBox="0 0 160 110" aria-hidden="true"><defs>${g(`sbS${s}`, "var(--surface-3)", "var(--surface-2)")}</defs>
    <rect x="16" y="18" width="128" height="70" rx="12" fill="url(#sbS${s})" stroke="var(--line-2)"/>
    <rect x="26" y="30" width="46" height="46" rx="8" fill="var(--bg-0)" stroke="var(--line-1)"/><rect x="88" y="30" width="46" height="46" rx="8" fill="var(--bg-0)" stroke="var(--line-1)"/>
    <rect x="38" y="51" width="22" height="5" rx="2.5" fill="var(--text-3)"/><rect x="100" y="51" width="22" height="5" rx="2.5" fill="var(--text-3)"/>
    <circle cx="80" cy="46" r="2.5" fill="var(--text-3)"/><circle cx="80" cy="60" r="2.5" fill="var(--text-3)"/>
    <rect x="58" y="88" width="8" height="14" fill="var(--surface-3)"/><rect x="94" y="88" width="8" height="14" fill="var(--surface-3)"/>
    <rect x="62" y="8" width="36" height="12" rx="6" fill="#ff2d3d"/><text x="80" y="17" text-anchor="middle" fill="#fff" font-family="Barlow Condensed,sans-serif" font-weight="800" font-size="9" letter-spacing="1.5">FINAL?</text></svg>`,
  ticket: (s) => `<svg class="art" viewBox="0 0 160 110" aria-hidden="true"><defs>${g(`tkA${s}`, "#34d399", "#059669")}</defs>
    <g transform="rotate(-8 80 55)"><path d="M22 30a8 8 0 0 1 8-8h100a8 8 0 0 1 8 8v12a10 10 0 0 0 0 20v12a8 8 0 0 1-8 8H30a8 8 0 0 1-8-8V62a10 10 0 0 0 0-20z" fill="var(--surface-2)" stroke="var(--line-2)"/>
    <line x1="104" y1="26" x2="104" y2="82" stroke="var(--line-3)" stroke-dasharray="4 4"/>
    <rect x="34" y="36" width="54" height="7" rx="3.5" fill="var(--text-3)" opacity=".7"/><rect x="34" y="50" width="38" height="6" rx="3" fill="var(--text-3)" opacity=".45"/>
    <rect x="34" y="64" width="46" height="6" rx="3" fill="var(--text-3)" opacity=".3"/>
    <circle cx="121" cy="54" r="12" fill="url(#tkA${s})"/><path d="M121 46v16M125 49.5c-.8-1-2.2-1.5-4-1.5-2.3 0-3.8 1.1-3.8 2.7 0 3.8 8 2 8 5.8 0 1.6-1.6 2.8-4.2 2.8-1.9 0-3.4-.6-4.3-1.7" stroke="#fff" stroke-width="1.8" fill="none" stroke-linecap="round"/></g></svg>`,
  replay: (s) => `<svg class="art" viewBox="0 0 160 110" aria-hidden="true"><defs>${g(`rpA${s}`, "#a78bfa", "#7c3aed")}</defs>
    <rect x="20" y="26" width="120" height="58" rx="10" fill="var(--surface-2)" stroke="var(--line-2)"/>
    ${[0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((i) => `<rect x="${26 + i * 11.4}" y="30" width="6" height="5" rx="1.5" fill="var(--line-3)"/><rect x="${26 + i * 11.4}" y="75" width="6" height="5" rx="1.5" fill="var(--line-3)"/>`).join("")}
    <rect x="30" y="40" width="30" height="30" rx="4" fill="var(--surface-3)"/><rect x="65" y="40" width="30" height="30" rx="4" fill="var(--surface-3)"/><rect x="100" y="40" width="30" height="30" rx="4" fill="var(--surface-3)"/>
    <circle cx="80" cy="55" r="20" fill="url(#rpA${s})"/><path d="M72 49v-5h5M72.6 49a9 9 0 1 1-.8 8" stroke="#fff" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M78 51.5v7l5.5-3.5z" fill="#fff"/></svg>`,
  timeline: (s) => `<svg class="art" viewBox="0 0 160 110" aria-hidden="true">
    <line x1="44" y1="16" x2="44" y2="96" stroke="var(--line-3)" stroke-width="3" stroke-linecap="round" stroke-dasharray="1 8"/>
    ${[[24, "#ff3b47", 72], [54, "var(--surface-3)", 54], [84, "var(--surface-3)", 64]].map(([y, c, w]) => `<circle cx="44" cy="${y}" r="10" fill="${c}" stroke="var(--bg-0)" stroke-width="3"/><rect x="62" y="${y - 7}" width="${w}" height="6" rx="3" fill="var(--text-3)" opacity=".7"/><rect x="62" y="${y + 3}" width="${w - 22}" height="5" rx="2.5" fill="var(--text-3)" opacity=".35"/>`).join("")}</svg>`,
  roster: (s) => `<svg class="art" viewBox="0 0 160 110" aria-hidden="true">
    ${[[40, .45], [80, .8], [120, .45]].map(([x, o]) => `<g opacity="${o}"><circle cx="${x}" cy="44" r="16" fill="var(--surface-3)"/><circle cx="${x}" cy="40" r="7" fill="var(--text-3)"/><path d="M${x - 11} 56a11 11 0 0 1 22 0" fill="var(--text-3)"/><rect x="${x - 14}" y="68" width="28" height="6" rx="3" fill="var(--text-3)" opacity=".6"/><rect x="${x - 8}" y="78" width="16" height="5" rx="2.5" fill="var(--text-3)" opacity=".35"/></g>`).join("")}</svg>`,
  plays: (s) => `<svg class="art" viewBox="0 0 160 110" aria-hidden="true">
    ${[24, 52, 80].map((y, i) => `<g opacity="${1 - i * 0.25}"><circle cx="34" cy="${y}" r="11" fill="var(--surface-3)"/><rect x="52" y="${y - 7}" width="${84 - i * 10}" height="6" rx="3" fill="var(--text-3)" opacity=".6"/><rect x="52" y="${y + 3}" width="${60 - i * 8}" height="5" rx="2.5" fill="var(--text-3)" opacity=".3"/></g>`).join("")}</svg>`,
};
