/* RedZone Everything — phone control page.
   Vanilla JS, no build step. Live updates over Server-Sent Events.
   Every field from the server is optional: missing data renders as "—", never throws. */
(() => {
  "use strict";

  const Q = new URLSearchParams(location.search);
  const MOCK = Q.has("mock");
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const LEAGUES = ["nfl", "nba", "nhl", "mlb"];
  const LEAGUE_LOGO = (lg) => `https://a.espncdn.com/i/teamlogos/leagues/500/${lg}.png`;

  // localStorage that never throws (private mode, blocked storage)
  const store = {
    get(k) { try { return localStorage.getItem("rz:" + k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem("rz:" + k, v); } catch {} },
  };

  let S = null;               // latest snapshot
  let MOCK_STATE = null;
  const ui = {
    tab: store.get("tab") || "now",
    league: store.get("league") || "all",
    expanded: null,
    settings: null,           // {config, limits}
    pending: {},              // dotted path -> value
    channels: null,
    lab: null,
    labAt: 0,
    announcer: store.get("announcer") === "1",
    announcerReady: false,
    lastNowId: undefined,
    bet: { game: null, kind: "spread", team: null, line: "", side: "over", note: "" },
  };

  // ---------- tiny utils ----------
  function esc(v) {
    if (v === null || v === undefined || v === "") return "—";
    return String(v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function escRaw(v) { return v === null || v === undefined ? "" : esc(v); }
  function num(v, d = 0) { const n = Number(v); return Number.isFinite(n) ? n : d; }
  function get(o, path, d) {
    try { const v = path.split(".").reduce((a, k) => (a == null ? undefined : a[k]), o); return v === undefined ? d : v; }
    catch { return d; }
  }
  function nowS() { return Date.now() / 1000; }
  function fmtDur(s) {
    s = Math.max(0, Math.floor(s));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
  }
  function fmtAgo(ts) {
    if (!ts) return "—";
    const d = nowS() - ts;
    if (d < 10) return "just now";
    if (d < 60) return `${Math.floor(d)}s ago`;
    if (d < 3600) return `${Math.floor(d / 60)}m ago`;
    if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
    return `${Math.floor(d / 86400)}d ago`;
  }
  function fmtClock(ts) {
    if (!ts) return "—";
    try { return new Date(ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); } catch { return "—"; }
  }
  function fmtStart(ts) {
    if (!ts) return "—";
    const d = new Date(ts * 1000), t = new Date();
    const same = d.toDateString() === t.toDateString();
    const tom = new Date(t.getTime() + 86400000).toDateString() === d.toDateString();
    const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    if (same) return time;
    if (tom) return `Tomorrow ${time}`;
    return `${d.toLocaleDateString([], { weekday: "short" })} ${time}`;
  }
  function heatColor(p) {
    p = Math.max(0, Math.min(100, num(p)));
    const stops = ["--heat-0", "--heat-1", "--heat-2", "--heat-3", "--heat-4", "--heat-5"];
    return `var(${stops[Math.min(5, Math.floor(p / 17))]})`;
  }
  function vibrate(ms = 12) { try { navigator.vibrate && navigator.vibrate(ms); } catch {} }


  // ---------- logos ----------
  function logo(team, size, league) {
    const t = team || {};
    const abbr = escRaw(t.abbr || "?");
    const color = /^#?[0-9a-f]{6}$/i.test(t.color || "") ? (t.color.startsWith("#") ? t.color : "#" + t.color) : "#5a6275";
    const src = t.logo || (league && t.abbr ? `https://a.espncdn.com/i/teamlogos/${league}/500/${String(t.abbr).toLowerCase()}.png` : "");
    const sz = size ? `--sz:${size}px;` : "";
    if (!src) return `<span class="logo fb" style="${sz}--tc:${color}"><b>${abbr}</b></span>`;
    return `<span class="logo" style="${sz}--tc:${color}"><img src="${escRaw(src)}" alt="" loading="lazy" decoding="async" onerror="this.parentNode.classList.add('fb');this.remove()"><b>${abbr}</b></span>`;
  }
  function leagueBadge(lg) {
    if (!LEAGUES.includes(lg)) return `<span class="lg-text">${esc(lg)}</span>`;
    return `<img class="lg-badge" src="${LEAGUE_LOGO(lg)}" alt="${lg.toUpperCase()}" loading="lazy" onerror="this.outerHTML='<span class=&quot;lg-text&quot;>${lg}</span>'">`;
  }
  function teamColor(t) {
    const c = (t && t.color) || "";
    return /^#?[0-9a-f]{6}$/i.test(c) ? (c.startsWith("#") ? c : "#" + c) : "#3a4256";
  }

  // ---------- API ----------
  async function api(path, opts = {}) {
    if (MOCK) return mockApi(path, opts);
    const init = { method: opts.method || (opts.body ? "POST" : "GET"), headers: {} };
    if (opts.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
    const r = await fetch(path, init);
    let data = null;
    try { data = await r.json(); } catch { data = null; }
    if (!r.ok) {
      const msg = (data && (data.message || data.detail)) || `Server said ${r.status}`;
      throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    }
    return data;
  }

  async function act(action, extra = {}, btn) {
    if (btn) btn.classList.add("busy");
    try {
      const res = await api("/api/action", { body: { action, ...extra } });
      vibrate();
      if (res && res.message) toast(res.message, res.ok === false ? "warn" : "ok");
      return res;
    } catch (e) {
      toast(e.message || "That didn't work", "error");
      return null;
    } finally {
      if (btn) btn.classList.remove("busy");
    }
  }

  // ---------- live stream ----------
  let es = null, backoff = 1000, pollTimer = null;
  function setConn(kind, text) {
    const el = $("#conn");
    el.className = "conn conn-" + kind;
    $("span", el).textContent = text;
  }
  async function loadOnce() {
    try { const s = await api("/api/state"); if (s) applyState(s); } catch {}
  }
  function connect() {
    if (MOCK) { setConn("mock", "Preview"); return; }
    try { es && es.close(); } catch {}
    es = new EventSource("/api/stream");
    es.addEventListener("open", () => { backoff = 1000; setConn("live", "Live"); });
    es.addEventListener("state", (ev) => {
      try { applyState(JSON.parse(ev.data)); setConn("live", "Live"); } catch (e) { console.warn("bad state", e); }
    });
    es.addEventListener("toast", (ev) => {
      try { const t = JSON.parse(ev.data); toast(t.text, t.level); } catch {}
    });
    es.onerror = () => {
      setConn("wait", "Reconnecting");
      try { es.close(); } catch {}
      clearTimeout(pollTimer);
      pollTimer = setTimeout(() => { loadOnce(); connect(); }, backoff);
      backoff = Math.min(15000, backoff * 2);
    };
  }

  function applyState(s) {
    if (!s || typeof s !== "object") return;
    const prevNow = S && S.now ? S.now.game_id + "|" + (S.now.kind || "") : null;
    S = s;
    const curNow = S.now ? S.now.game_id + "|" + (S.now.kind || "") : null;
    if (ui.lastNowId !== undefined && curNow && curNow !== prevNow) announce(S.now);
    ui.lastNowId = curNow;
    render();
  }

  // ---------- rendering ----------
  function render() {
    renderHeader();
    renderBanners();
    const t = ui.tab;
    if (t === "now") renderNow();
    else if (t === "games") renderGames();
    else if (t === "bets") renderBets(false);
    else if (t === "lab") renderLabLive();
    else if (t === "settings") renderSettingsLive();
    renderTabPips();
  }

  function stateLabel(st) {
    return ({ idle: "Idle", watching: "Watching", locked: "Locked in", break: "On a break", paused: "Paused",
      frozen: "Holding", safe: "Safe mode", replay: "Replay" })[st] || null;
  }
  function renderHeader() {
    const chip = $("#state-chip");
    const st = get(S, "mode.state");
    const lbl = stateLabel(st);
    if (!lbl) { chip.hidden = true; return; }
    chip.hidden = false;
    chip.textContent = lbl;
    chip.className = "chip chip-state " + ({ locked: "chip-lock", break: "chip-break", safe: "chip-lock",
      frozen: "chip-break", replay: "chip-info", paused: "" }[st] || "");
  }

  function renderBanners() {
    const m = (S && S.mode) || {};
    const out = [];
    if (m.safe) {
      out.push(`<div class="banner banner-safe"><span class="ico">⛑️</span><div class="grow"><b>Safe mode</b>Too many errors in a row, so it stopped switching. Check Health in Settings.</div><button class="btn btn-sm btn-soft" data-act="resume">Resume</button></div>`);
    } else if (m.frozen_until && m.frozen_until > nowS()) {
      out.push(`<div class="banner banner-frozen"><span class="ico">🧊</span><div class="grow"><b>Holding still</b>It caught itself flip-flopping. Back to normal in <span data-countdown="${m.frozen_until}">${fmtDur(m.frozen_until - nowS())}</span>.</div><button class="btn btn-sm btn-soft" data-act="resume">Resume</button></div>`);
    } else if (m.paused) {
      out.push(`<div class="banner banner-paused"><span class="ico">⏸</span><div class="grow"><b>Auto-switching paused</b>It'll stay right here until you resume.</div><button class="btn btn-sm btn-soft" data-act="resume">Resume</button></div>`);
    }
    if (m.dry_run) {
      out.push(`<div class="banner banner-dry"><span class="ico">🧪</span><div class="grow"><b>Dry run</b>Showing what it WOULD do. TV is not touched.</div><label class="switch" title="TV control"><input type="checkbox" data-dry-toggle aria-label="Turn on TV control"><span></span></label></div>`);
    }
    const el = $("#banners");
    const html = out.join("");
    if (el.dataset.html !== html) { el.innerHTML = html; el.dataset.html = html; }
  }

  function gameById(id) { return ((S && S.games) || []).find((g) => g && g.id === id) || null; }

  // ----- Now -----
  function renderNow() {
    const root = $("#tab-now");
    if (!S) { root.innerHTML = `<div class="sk sk-bar"></div><div class="sk sk-hero"></div><div class="sk sk-row"></div>`; return; }
    const m = S.mode || {};
    const n = S.now;
    const g = n ? gameById(n.game_id) : null;
    const games = (S.games || []).filter(Boolean);
    const focusable = games.filter((x) => x.phase === "live" || x.phase === "pre");
    const focusGame = m.focus_game || "";
    const focusMode = m.focus_mode || "autopilot";

    let html = `<div class="card focus-card">
      <label for="focus-sel">Focus</label>
      <div class="select-wrap"><select id="focus-sel" aria-label="Focus">
        <option value="">🎯 Autopilot — best game anywhere</option>
        ${focusable.map((x) => `<option value="${escRaw(x.id)}" ${x.id === focusGame ? "selected" : ""}>${x.mine ? "★ " : ""}${escRaw(x.matchup)} · ${escRaw(x.status_text)}</option>`).join("")}
      </select></div>
      ${focusGame ? `<div class="seg" role="group" aria-label="Focus style">
        <button data-focus-mode="focus" class="${focusMode === "focus" ? "on" : ""}">Only during its breaks</button>
        <button data-focus-mode="focus_must_see" class="${focusMode === "focus_must_see" ? "on" : ""}">+ Must-see moments</button>
      </div>` : ""}
    </div>`;

    if (n) {
      html += heroCard(n, g, m);
    } else {
      const upcoming = games.filter((x) => x.phase === "pre" && x.start_time).sort((a, b) => a.start_time - b.start_time)[0];
      html += `<div class="card empty" style="margin-top:12px"><div class="big">📺</div><h3>Nothing on yet</h3>
        <div>${upcoming ? `Next up: <b>${esc(upcoming.matchup)}</b> at ${esc(fmtStart(upcoming.start_time))}` : "No games scheduled right now."}</div></div>`;
    }

    if (S.next && S.next.game_id) {
      const ng = gameById(S.next.game_id);
      html += `<div class="next">
        <div class="logos">${ng ? logo(ng.away, 26, ng.league) + logo(ng.home, 26, ng.league) : ""}</div>
        <div class="grow"><div class="lbl">Next up · ${esc(S.next.matchup)}</div>
          <div class="ellipsis" style="font-weight:650;font-size:13.5px">${esc(S.next.reason)}</div>
          <div class="why">Waiting: ${esc(S.next.why_waiting)}</div></div>
        <span class="spin" aria-hidden="true"></span></div>`;
    }

    const paused = !!m.paused;
    html += `<div class="actions">
      <button class="btn" data-act="back"><svg viewBox="0 0 24 24"><path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/></svg>Go back</button>
      <button class="btn" data-act="skip"><svg viewBox="0 0 24 24"><path d="m5 4 10 8-10 8z"/><path d="M19 5v14"/></svg>Skip</button>
      <button class="btn ${paused ? "btn-primary" : ""}" data-act="${paused ? "resume" : "pause"}">${paused
        ? `<svg viewBox="0 0 24 24"><path d="m6 4 14 8-14 8z"/></svg>Resume`
        : `<svg viewBox="0 0 24 24"><path d="M8 5v14M16 5v14"/></svg>Pause`}</button>
      <button class="btn btn-more btn-soft" data-open="more">More · Lock · Mute · Boost · 👍 👎 · I just saw it</button>
    </div>`;

    if (S.standby && S.standby.game_id) {
      const sg = gameById(S.standby.game_id);
      html += `<div class="section-title">On standby <span class="count">second stick</span></div>
        <div class="card row">${sg ? logo(sg.away, 28, sg.league) + logo(sg.home, 28, sg.league) : ""}
          <div class="grow"><b>${esc(S.standby.matchup)}</b><div class="small muted">${esc(S.standby.where)} · ready to flip in ~2s</div></div></div>`;
    }

    const queue = (S.queue || []).filter(Boolean);
    html += `<div class="section-title">Catch-up queue <span class="count">${queue.length ? queue.length + " waiting for a break" : ""}</span></div>`;
    if (queue.length) {
      html += `<div class="card">${queue.map((q) => {
        const qg = gameById(q.game_id);
        const icon = ({ td: "🏈", turnover: "🔄", goal: "🚨", hr: "💣", three: "🎯", fg: "🥅" })[q.kind] || "⭐";
        return `<div class="queue-item"><span class="kind-icon">${icon}</span>
          <div class="grow"><div class="ellipsis" style="font-weight:700">${esc(q.label)}</div>
          <div class="small muted">${esc(q.matchup)} · ${esc(fmtAgo(q.at))}</div></div>
          <button class="btn btn-sm btn-soft" data-replay="${escRaw(q.game_id)}" data-play="${escRaw(q.play_id)}">Play</button></div>`;
      }).join("")}</div>`;
    } else {
      html += `<div class="card small muted">Big plays you miss show up here and play during the next break.</div>`;
    }

    const log = (S.log || []).filter(Boolean).slice(0, 12);
    html += `<div class="section-title">Recent switches <span class="count">${m.dry_run ? "dry run" : ""}</span></div>`;
    if (log.length) {
      html += `<div class="card feed">${log.map((l) => {
        const lg = gameById(l.to);
        const status = l.ok === false ? `<span class="chip chip-lock">failed</span>` : l.dry_run ? `<span class="chip chip-info">would</span>` : "";
        return `<div class="feed-item">${lg ? logo(lg.home, 28, lg.league) : `<span class="logo fb" style="--sz:28px"><b>•</b></span>`}
          <div class="grow"><div class="row between"><span class="what ellipsis">${esc(l.matchup)}</span><span class="t">${esc(fmtClock(l.ts))}</span></div>
          <div class="why">${esc(l.reason)}</div>
          <div class="row" style="gap:6px;margin-top:3px"><span class="kind-tag">${esc(kindName(l.kind))}</span>${status}${l.took ? `<span class="small dim">${num(l.took).toFixed(1)}s</span>` : ""}</div></div></div>`;
      }).join("")}</div>`;
    } else {
      html += `<div class="card small muted">No switches yet.</div>`;
    }
    morph(root, html);
    tick();
  }

  function kindName(k) {
    return ({ better: "Better game", break_visit: "Break visit", home: "Back home", return: "Moment over",
      chase: "Catch it live", must_see: "Must-see", ended: "Game ended", start: "Start", user: "You picked",
      replay: "Replay" })[k] || (k || "switch");
  }

  function heroCard(n, g, m) {
    const isReplay = n.kind === "replay";
    const st = m.state;
    const badge = isReplay ? `<span class="badge badge-replay">Replay</span>`
      : st === "break" ? `<span class="badge badge-break">Break</span>`
      : st === "locked" ? `<span class="badge badge-locked">Locked</span>`
      : `<span class="badge badge-live">Live</span>`;
    const away = (g && g.away) || {}, home = (g && g.home) || {};
    const league = (g && g.league) || n.league;
    const hs = away.score, as = home.score;
    const lead = (a, b) => (num(a, -1) > num(b, -1) ? "" : "opacity:.72");
    return `<div class="hero ${isReplay ? "is-replay" : ""}" style="--away:${teamColor(away)};--home:${teamColor(home)}">
      <div class="hero-top">
        <div class="left">${badge}${leagueBadge(league)}${g && g.mine ? `<span class="chip chip-mine">★ Your team</span>` : ""}</div>
        <span class="hero-timer">on for <span class="tnum" data-since="${num(n.since)}">${n.since ? fmtDur(nowS() - n.since) : "—"}</span></span>
      </div>
      <div class="score-line">
        <div class="side">${logo(away, 64, league)}<span class="abbr">${esc(away.abbr)}</span><span class="rec">${escRaw(away.record)}</span></div>
        <div class="mid"><div class="pts tnum"><b style="${lead(hs, as)}">${away.score ?? "—"}</b><span>–</span><b style="${lead(as, hs)}">${home.score ?? "—"}</b></div>
          <div class="status">${esc(g ? g.status_text : "")}</div></div>
        <div class="side">${logo(home, 64, league)}<span class="abbr">${esc(home.abbr)}</span><span class="rec">${escRaw(home.record)}</span></div>
      </div>
      ${isReplay && n.replay ? `<div class="chip chip-info" style="margin-top:4px">🔁 ${esc(n.replay.label)}</div>` : ""}
      <div class="hero-reason">${esc(n.reason)}</div>
      <div class="hero-meta">
        <span class="chip chip-where">📺 ${esc(n.where)}</span>
        <span class="score-pill">Heat <span class="num tnum">${n.score ?? "—"}</span></span>
      </div>
      ${g && (g.tags || []).length ? `<div class="chips" style="margin-top:8px">${tagChips(g)}</div>` : ""}
    </div>`;
  }

  function tagChips(g) {
    return (g.tags || []).map((t) => {
      const s = String(t);
      const cls = /bet/i.test(s) ? "chip-bet" : g.mine && s.length <= 4 ? "chip-mine" : /boost/i.test(s) ? "chip-pre" : "chip-info";
      return `<span class="chip ${cls}">${esc(s)}</span>`;
    }).join("");
  }

  // ----- Games -----
  function renderGames() {
    const root = $("#tab-games");
    if (!S) { root.innerHTML = `<div class="sk sk-bar"></div><div class="sk sk-row"></div><div class="sk sk-row"></div><div class="sk sk-row"></div>`; return; }
    const all = (S.games || []).filter(Boolean);
    const present = LEAGUES.filter((lg) => all.some((g) => g.league === lg));
    const chips = [["all", "All"], ["mine", "★ Mine"], ...present.map((lg) => [lg, lg.toUpperCase()])];
    let html = `<div class="filters">${chips.map(([k, l]) => `<button class="filter ${ui.league === k ? "on" : ""}" data-league="${k}">${LEAGUES.includes(k) ? `<img src="${LEAGUE_LOGO(k)}" alt="">` : ""}${l}</button>`).join("")}</div>`;
    const shown = all.filter((g) => ui.league === "all" || (ui.league === "mine" ? g.mine : g.league === ui.league));
    const live = shown.filter((g) => g.phase === "live").sort((a, b) => num(b.score) - num(a.score));
    const pre = shown.filter((g) => g.phase === "pre" || g.phase === "unknown").sort((a, b) => num(a.start_time, 9e12) - num(b.start_time, 9e12));
    const post = shown.filter((g) => g.phase === "post");
    if (!shown.length) {
      html += `<div class="card empty" style="margin-top:12px"><div class="big">🗓️</div><h3>No games here</h3><div>Try another filter.</div></div>`;
    }
    if (live.length) html += `<div class="section-title">Live <span class="count">${live.length}</span></div>` + live.map(gameRow).join("");
    if (pre.length) html += `<div class="section-title">Up next <span class="count">${pre.length}</span></div>` + pre.map(gameRow).join("");
    if (post.length) html += `<div class="section-title">Final <span class="count">${post.length}</span></div>` + post.map(gameRow).join("");
    if (!live.length && shown.length) {
      const nx = pre[0];
      html = html.replace(`<div class="section-title">Up next`, `<div class="card empty" style="margin-top:12px"><div class="big">⏳</div><h3>No games live</h3><div>${nx ? `Next up: <b>${esc(nx.matchup)}</b> at ${esc(fmtStart(nx.start_time))}` : ""}</div></div><div class="section-title">Up next`);
    }
    morph(root, html);
  }

  function gameRow(g) {
    const live = g.phase === "live";
    const away = g.away || {}, home = g.home || {};
    const aS = away.score, hS = home.score;
    const lineCls = (a, b) => (live || g.phase === "post") && a != null && b != null ? (num(a) >= num(b) ? "lead" : "trail") : "";
    const where = g.where || {};
    const unknown = !where.target || where.confidence === "unknown";
    const p = Math.max(0, Math.min(100, num(g.score)));
    const badges = [
      g.on_now ? `<span class="chip chip-lock">● On now</span>` : "",
      g.is_break ? `<span class="chip chip-break">Break</span>` : "",
      g.lock ? `<span class="chip chip-lock">🔒 ${esc(g.lock)}</span>` : "",
      g.pre_moment ? `<span class="chip chip-pre">⚡ ${esc(g.pre_moment)}</span>` : "",
      g.stale ? `<span class="chip chip-break">data delayed</span>` : "",
      g.boosted ? `<span class="chip chip-pre">Boosted</span>` : "",
      g.muted ? `<span class="chip">Muted</span>` : "",
      g.focus ? `<span class="chip chip-info">Focus</span>` : "",
    ].join("");
    const exp = ui.expanded === g.id;
    const statusRight = g.phase === "pre" ? fmtStart(g.start_time) : g.status_text;
    return `<article class="game ${g.mine ? "mine" : ""} ${g.on_now ? "on-now" : ""} ${g.muted ? "muted-game" : ""}" data-game="${escRaw(g.id)}">
      <div class="game-head"><div class="left">${leagueBadge(g.league)}${g.mine ? `<span class="chip chip-mine">★</span>` : ""}${badges ? `<span class="chips" style="flex-wrap:nowrap;overflow:hidden">${badges}</span>` : ""}</div>
        <span class="status">${esc(statusRight)}</span></div>
      <div class="game-body">
        <div class="teams">
          <div class="team-line ${lineCls(aS, hS)}">${logo(away, 26, g.league)}<span class="nm">${esc(away.abbr)}<small>${escRaw(away.record)}</small></span><span class="pts">${g.phase === "pre" ? "" : aS ?? "—"}</span></div>
          <div class="team-line ${lineCls(hS, aS)}">${logo(home, 26, g.league)}<span class="nm">${esc(home.abbr)}<small>${escRaw(home.record)}</small></span><span class="pts">${g.phase === "pre" ? "" : hS ?? "—"}</span></div>
        </div>
        ${live ? `<div class="heat"><div class="ring" style="--p:${p};--c:${heatColor(p)}"><b>${Math.round(p)}</b></div><span class="lbl">Heat</span></div>`
          : `<div class="heat"><span class="lbl" style="text-align:center">${g.phase === "pre" ? (g.odds ? esc(g.odds) : "Pregame") : "Final"}</span></div>`}
      </div>
      ${g.reason && live ? `<div class="reason">${esc(g.reason)}</div>` : g.phase === "pre" && g.odds ? "" : ""}
      <div class="game-foot">
        <div class="chips">${tagChips(g)}</div>
        ${unknown
          ? `<button class="btn btn-sm set-channel" data-setch="${escRaw(g.id)}">Set channel</button>`
          : `<span class="chip chip-where ${where.tunable === false ? "unknown" : ""}" title="${escRaw(where.confidence)}">📺 ${esc(where.label)}${where.confidence && where.confidence !== "sure" ? ` · ${esc(where.confidence)}` : ""}</span>`}
      </div>
      ${exp ? `<div class="game-actions">
          <button class="btn btn-primary" data-act="watch" data-gid="${escRaw(g.id)}" ${live ? "" : "disabled"}>▶ Watch now</button>
          <button class="btn btn-soft" data-act="focus" data-gid="${escRaw(g.id)}">🎯 Focus</button>
          <button class="btn btn-soft" data-act="${g.boosted ? "unboost" : "boost"}" data-gid="${escRaw(g.id)}">${g.boosted ? "Unboost" : "🔥 Boost"}</button>
          <button class="btn btn-soft" data-act="${g.muted ? "unmute" : "mute"}" data-gid="${escRaw(g.id)}">${g.muted ? "Unmute" : "🔇 Mute"}</button>
          <button class="btn btn-soft" data-act="${get(S, "mode.locked_game") === g.id ? "unlock" : "lock"}" data-gid="${escRaw(g.id)}">${get(S, "mode.locked_game") === g.id ? "Unlock" : "🔒 Lock"}</button>
          <button class="btn btn-soft" data-setch="${escRaw(g.id)}">📺 Channel</button>
          <button class="btn btn-soft" data-addbet="${escRaw(g.id)}">💵 Bet</button>
        </div>` : ""}
    </article>`;
  }

  // ----- Bets -----
  let betsFormBuilt = false;
  function renderBets(force) {
    const root = $("#tab-bets");
    if (!root.dataset.built || force) {
      root.innerHTML = `<div class="section-title">Your bets <span class="count" id="bet-count"></span></div>
        <div id="bets-list"></div>
        <div class="section-title">Add a bet <span class="count">takes ~20 seconds</span></div>
        <div class="card" id="bet-form"></div>`;
      root.dataset.built = "1";
      betsFormBuilt = false;
    }
    renderBetList();
    if (!betsFormBuilt || force) { renderBetForm(); betsFormBuilt = true; }
    else refreshBetGames();
  }

  function renderBetList() {
    const el = $("#bets-list");
    if (!el) return;
    const bets = ((S && S.bets) || []).filter(Boolean);
    $("#bet-count").textContent = bets.length ? `${bets.length} active` : "";
    if (!S) { el.innerHTML = `<div class="sk sk-row"></div>`; return; }
    if (!bets.length) {
      morph(el, `<div class="card empty"><div class="big">💵</div><h3>No bets yet</h3><div>Add one below. Games you have money on get a big priority boost while they're close.</div></div>`);
      return;
    }
    morph(el, bets.map((b) => {
      const g = gameById(b.game_id);
      const sw = Math.max(0, Math.min(1, num(b.sweat)));
      return `<div class="card"><div class="bet">
        <div class="logos">${g ? logo(g.away, 30, g.league) + logo(g.home, 30, g.league) : ""}</div>
        <div class="grow"><div class="lbl ellipsis">${esc(b.label || describeBet(b))}</div>
          <div class="small muted ellipsis">${esc(b.matchup || (g && g.matchup))}${g ? " · " + esc(g.status_text) : ""}</div></div>
        <button class="btn btn-sm btn-ghost btn-danger" data-delbet="${escRaw(b.id)}" aria-label="Delete bet">✕</button></div>
        <div class="sweat-wrap"><div class="row between"><span>Sweat</span><span class="tnum">${sw >= 0.66 ? "🔥 on the edge" : sw >= 0.33 ? "getting tense" : sw > 0 ? "calm" : "decided / not live"}</span></div>
        <div class="meter"><i style="width:${Math.round(sw * 100)}%;--full:${Math.max(1, 100 / Math.max(sw, 0.01))}%"></i></div></div></div>`;
    }).join(""));
  }
  function describeBet(b) {
    if (b.kind === "spread") return `${b.team} ${num(b.line) > 0 ? "+" : ""}${b.line}`;
    if (b.kind === "moneyline") return `${b.team} ML`;
    if (b.kind === "total") return `${b.side === "under" ? "Under" : "Over"} ${b.line}`;
    return b.note || "Prop";
  }

  function betGames() {
    return ((S && S.games) || []).filter((g) => g && (g.phase === "live" || g.phase === "pre"))
      .sort((a, b) => (a.phase === b.phase ? num(a.start_time) - num(b.start_time) : a.phase === "live" ? -1 : 1));
  }
  function refreshBetGames() {
    const list = $("#bet-picks");
    if (!list || document.activeElement && list.contains(document.activeElement)) return;
    const html = betPicksHtml();
    if (list.dataset.html !== html) { list.innerHTML = html; list.dataset.html = html; }
  }
  function betPicksHtml() {
    const gs = betGames();
    if (!gs.length) return `<div class="small muted">No live or upcoming games to bet on right now.</div>`;
    return gs.map((g) => `<button type="button" class="pick ${ui.bet.game === g.id ? "on" : ""}" data-pick="${escRaw(g.id)}">
      ${logo(g.away, 24, g.league)}${logo(g.home, 24, g.league)}<b>${esc(g.matchup)}</b>
      <span class="when">${g.phase === "live" ? esc(g.status_text) : esc(fmtStart(g.start_time))}</span></button>`).join("");
  }
  function renderBetForm() {
    const el = $("#bet-form");
    if (!el) return;
    const b = ui.bet;
    const g = gameById(b.game);
    const needsTeam = b.kind === "spread" || b.kind === "moneyline";
    const needsLine = b.kind === "spread" || b.kind === "total";
    el.innerHTML = `
      <div class="field" style="margin-top:0"><span class="flabel">1 · Game</span><div class="pick-list" id="bet-picks">${betPicksHtml()}</div></div>
      <div class="field"><span class="flabel">2 · Bet type</span>
        <div class="seg" style="margin-top:0">${[["spread", "Spread"], ["moneyline", "Moneyline"], ["total", "Over/Under"], ["prop", "Prop"]]
          .map(([k, l]) => `<button type="button" data-kind="${k}" class="${b.kind === k ? "on" : ""}">${l}</button>`).join("")}</div></div>
      ${needsTeam ? `<div class="field"><span class="flabel">3 · Team</span><div class="team-toggle">
        ${g ? [g.away, g.home].map((t) => `<button type="button" data-team="${escRaw(t && t.abbr)}" class="${b.team === (t && t.abbr) ? "on" : ""}">${logo(t, 26, g.league)}${esc(t && t.abbr)}</button>`).join("")
          : `<div class="small muted" style="grid-column:1/-1">Pick a game first.</div>`}
      </div></div>` : ""}
      ${b.kind === "total" ? `<div class="field"><span class="flabel">3 · Side</span><div class="seg" style="margin-top:0">
        <button type="button" data-side="over" class="${b.side === "over" ? "on" : ""}">Over</button>
        <button type="button" data-side="under" class="${b.side === "under" ? "on" : ""}">Under</button></div></div>` : ""}
      ${needsLine ? `<div class="field"><label for="bet-line">${b.kind === "total" ? "4 · Total" : "4 · Line"}</label>
        <div class="line-input">${b.kind === "spread" ? `<button type="button" class="btn btn-soft" data-flip aria-label="Flip sign">±</button>` : `<span></span>`}
        <input id="bet-line" class="input tnum" inputmode="decimal" placeholder="${b.kind === "total" ? "46.5" : "+3.5"}" value="${escRaw(b.line)}" autocomplete="off"></div></div>` : ""}
      ${b.kind === "prop" ? `<div class="field"><label for="bet-note">3 · What's the prop?</label>
        <input id="bet-note" class="input" placeholder="e.g. McLaurin 70+ rec yds" value="${escRaw(b.note)}" autocomplete="off"></div>` : ""}
      <button class="btn btn-primary btn-block" style="margin-top:16px" id="bet-save">Add bet</button>`;
    $("#bet-picks").dataset.html = $("#bet-picks").innerHTML;
  }

  async function saveBet(btn) {
    const b = ui.bet;
    const lineEl = $("#bet-line"), noteEl = $("#bet-note");
    if (lineEl) b.line = lineEl.value.trim();
    if (noteEl) b.note = noteEl.value.trim();
    if (!b.game) return toast("Pick a game first", "warn");
    if ((b.kind === "spread" || b.kind === "moneyline") && !b.team) return toast("Pick a team", "warn");
    if ((b.kind === "spread" || b.kind === "total") && !Number.isFinite(parseFloat(b.line))) return toast("Enter the line, like +3.5", "warn");
    if (b.kind === "prop" && !b.note) return toast("Describe the prop", "warn");
    const body = { game_id: b.game, kind: b.kind, team: b.team || null, line: b.line === "" ? null : parseFloat(b.line), side: b.side, note: b.note || "" };
    btn.classList.add("busy");
    try {
      const r = await api("/api/bets", { body });
      toast((r && r.message) || "Bet added", "ok");
      vibrate(20);
      if (MOCK && r && r.bet) { S.bets = [r.bet, ...(S.bets || [])]; }
      ui.bet = { game: null, kind: b.kind, team: null, line: "", side: "over", note: "" };
      if (!MOCK) { try { const list = await api("/api/bets"); if (Array.isArray(list) && S) S.bets = list; } catch {} }
      renderBets(true);
    } catch (e) { toast(e.message, "error"); }
    finally { btn.classList.remove("busy"); }
  }

  // ----- Lab -----
  async function loadLab(force) {
    if (!force && ui.lab && nowS() - ui.labAt < 60) return;
    try { ui.lab = await api("/api/lab"); ui.labAt = nowS(); } catch (e) { ui.lab = ui.lab || { error: e.message }; }
    if (ui.tab === "lab") renderLab();
  }
  function renderLabLive() {
    // State updates only touch the delay table; the rest is static between refreshes.
    if (!$("#tab-lab").dataset.built) { renderLab(); return; }
    const d = $("#delay-table");
    if (d) morph(d, delayRows());
    const fb = $("#fb-counts");
    if (fb) fb.textContent = feedbackText();
  }
  function feedbackText() {
    const f = (S && S.feedback) || (ui.lab && ui.lab.feedback) || {};
    return `👍 ${num(f.up)} · 👎 ${num(f.down)}`;
  }
  function delayRows() {
    const ds = ((S && S.delays) || []).filter(Boolean);
    if (!ds.length) return `<div class="small muted">No channels measured yet. Tap "I just saw it" the moment a big play shows on your TV.</div>`;
    return ds.map((d) => `<div class="delay-row"><span class="hdot ${d.calibrated ? "g" : "y"}"></span>
      <div class="grow"><b>${esc(d.label || d.target)}</b><div class="small muted">${d.calibrated ? "measured" : "guess — not measured yet"}</div></div>
      <span class="v">${Number.isFinite(Number(d.mu)) ? Math.round(d.mu) + "s" : "—"}</span><span class="small dim">±${Number.isFinite(Number(d.sigma)) ? Math.round(d.sigma) : "—"}s</span></div>`).join("");
  }
  function renderLab() {
    const root = $("#tab-lab");
    root.dataset.built = "1";
    const lab = ui.lab;
    let html = `<div class="section-title">Screen Lab <span class="count">tap what your TV shows</span></div>
      <div class="card"><div class="small muted" style="margin-bottom:10px">Every tap is matched against what the app thinks is on screen, so it learns your real delay and how to spot commercials.</div>
      <div class="labpad">
        <button class="labbtn lb-game" data-label="game">🏈 Game<small>live action</small></button>
        <button class="labbtn lb-commercial" data-label="commercial">📺 Commercial<small>ads / break</small></button>
        <button class="labbtn lb-loading" data-label="loading">⏳ Loading<small>spinner / black</small></button>
        <button class="labbtn lb-error" data-label="error">⚠️ Error<small>prompt / error</small></button>
        <button class="labbtn lb-replay" data-label="replay">🔁 Replay on screen</button>
      </div></div>
      <div class="section-title">Stream delay <span class="count">how far behind live you are</span></div>
      <div class="card"><div id="delay-table">${delayRows()}</div>
        <button class="btn btn-primary btn-block" style="margin-top:12px" data-act="saw_it">⚡ I just saw it</button>
        <div class="small muted" style="margin-top:8px">Tap the instant a score or big play shows on your TV. Three taps per channel is plenty.</div></div>`;

    html += `<div class="section-title">Your votes</div><div class="card row between"><span>Switch feedback</span><b id="fb-counts">${feedbackText()}</b></div>`;

    if (!lab) {
      html += `<div class="section-title">Backtests</div><div class="sk sk-row"></div>`;
    } else if (lab.error) {
      html += `<div class="section-title">Backtests</div><div class="card small muted">Couldn't load lab results: ${esc(lab.error)}</div>`;
    } else {
      const opt = lab.optimize;
      if (opt && (opt.best || opt.current)) {
        const rows = [["Score", "score", true], ["Big plays live %", "big_live_pct", true], ["Commercials %", "dead_air_pct", false],
          ["Switches / hour", "switches_per_hour", null], ["Mid-play cuts", "mid_play_cuts", false]];
        const cur = opt.current || {}, best = opt.best || {};
        html += `<div class="section-title">Auto-tuner <span class="count">${opt.slates ? opt.slates + " slates" : ""}</span></div>
          <div class="card"><table class="compare"><thead><tr><th></th><th>Now</th><th>Best found</th></tr></thead><tbody>
          ${rows.map(([l, k, hi]) => {
            const a = cur[k], b = best[k];
            const better = hi === null || a == null || b == null ? false : hi ? num(b) > num(a) : num(b) < num(a);
            return `<tr><td>${l}</td><td class="tnum">${esc(a)}</td><td class="tnum ${better ? "better" : ""}">${esc(b)}</td></tr>`;
          }).join("")}</tbody></table>
          ${best.params ? `<details class="acc" style="margin-top:10px"><summary class="small">Settings it picked</summary><div class="tiles">${Object.entries(best.params).map(([k, v]) =>
            `<div class="tile"><div class="k">${esc(k.split(".").pop().replace(/_/g, " "))}</div><div class="v">${esc(v)}</div></div>`).join("")}</div></details>` : ""}
          </div>`;
      }
      const bts = (lab.backtests || []).filter(Boolean);
      html += `<div class="section-title">Backtests <span class="count">${bts.length || ""}</span></div>`;
      if (!bts.length) html += `<div class="card small muted">No backtests yet. Run <code>scripts/backtest.py</code> on the Mac.</div>`;
      html += bts.map((b) => `<div class="card"><b>${esc(b.title)}</b><div class="tiles">${Object.entries(b.summary || {}).map(([k, v]) => {
        const cls = k === "mid_play_cuts" ? (num(v) === 0 ? "good" : "bad") : "";
        return `<div class="tile"><div class="k">${esc(k.replace(/_/g, " "))}</div><div class="v ${cls}">${esc(v)}</div></div>`;
      }).join("")}</div></div>`).join("");
    }
    root.innerHTML = html;
  }

  // ----- Settings -----
  async function loadSettings(force) {
    if (ui.settings && !force) return;
    try {
      const [s, ch] = await Promise.all([api("/api/settings"), api("/api/channels")]);
      ui.settings = s; ui.channels = ch; ui.pending = {};
    } catch (e) { ui.settings = { error: e.message }; }
    if (ui.tab === "settings") renderSettings();
  }
  function cfgVal(path) {
    if (path in ui.pending) return ui.pending[path];
    return get(ui.settings && ui.settings.config, path);
  }
  function limitFor(path) {
    const L = (ui.settings && ui.settings.limits) || {};
    let best = null;
    for (const k of Object.keys(L)) if ((path === k || path.startsWith(k + ".")) && (!best || k.length > best.length)) best = k;
    return best ? L[best] : null;
  }
  const SIMPLE = [
    ["score.personal.my_team", "Your-team boost", "How much your teams jump the line", 0, 60, 1],
    ["score.personal.bet_max", "Bet boost", "Extra priority for a sweating bet", 0, 60, 1],
    ["decider.switch_margin", "Switch margin", "How much better a game must be to switch", 0, 30, 1],
    ["decider.min_dwell_s", "Minimum stay", "Seconds before it's allowed to leave a game", 10, 180, 5],
    ["decider.max_switches_per_5min", "Max switches / 5 min", "Hard cap on channel flipping", 1, 12, 1],
  ];
  function renderSettingsLive() {
    if (!$("#tab-settings").dataset.built) { renderSettings(); return; }
    const h = $("#health");
    if (h) morph(h, healthHtml());
    const dry = $("#dry-switch");
    if (dry && document.activeElement !== dry) dry.checked = !get(S, "mode.dry_run", true);
  }
  function renderSettings() {
    const root = $("#tab-settings");
    if (!ui.settings) { root.innerHTML = `<div class="sk sk-row"></div><div class="sk sk-row"></div>`; loadSettings(); return; }
    root.dataset.built = "1";
    if (ui.settings.error) {
      root.innerHTML = `<div class="card empty"><div class="big">⚙️</div><h3>Settings didn't load</h3><div>${esc(ui.settings.error)}</div>
        <button class="btn btn-soft" style="margin-top:12px" data-reload-settings>Try again</button></div><div class="section-title">Health</div><div class="card" id="health">${healthHtml()}</div>`;
      return;
    }
    const cfg = ui.settings.config || {};
    let html = `<div class="section-title">TV control</div>
      <div class="card"><div class="set-row"><div class="grow"><b>Control the TV</b><span>Off = dry run: it only shows what it would do.</span></div>
        <label class="switch"><input type="checkbox" id="dry-switch" ${get(S, "mode.dry_run", true) ? "" : "checked"} aria-label="Control the TV"><span></span></label></div>
        <div class="set-row"><div class="grow"><b>Announcer</b><span>Short spoken callouts on this phone when it switches.</span></div>
        <button class="btn btn-sm ${ui.announcer ? "btn-good" : "btn-soft"}" id="announcer-btn">${ui.announcer ? "On · test" : "Enable"}</button></div></div>`;

    html += `<div class="section-title">Your teams</div><div class="card">${LEAGUES.map((lg) => {
      const teams = cfgVal(`teams.${lg}`) || [];
      return `<div class="league-head">${leagueBadge(lg)} ${lg.toUpperCase()}</div><div class="team-chips">
        ${teams.map((t) => `<span class="team-chip">${logo({ abbr: t }, 22, lg)}${esc(t)}<button data-rmteam="${lg}:${escRaw(t)}" aria-label="Remove ${escRaw(t)}">×</button></span>`).join("")}
        <span class="team-add"><input data-addteam="${lg}" maxlength="4" placeholder="+ ABBR" aria-label="Add ${lg} team" autocapitalize="characters"></span></div>`;
    }).join("")}</div>`;

    html += `<div class="section-title">Leagues</div><div class="card">${LEAGUES.map((lg) => `<div class="set-row">${leagueBadge(lg)}<div class="grow"><b>${lg.toUpperCase()}</b></div>
      <label class="switch"><input type="checkbox" data-league-toggle="${lg}" ${cfgVal(`mode.leagues.${lg}`) === false ? "" : "checked"}><span></span></label></div>`).join("")}</div>`;

    html += `<div class="section-title">How it picks</div><div class="card">${SIMPLE.map(([path, label, help, lo, hi, step]) => {
      const lim = limitFor(path);
      const min = lim ? Math.max(lo, lim[0]) : lo, max = lim ? Math.min(hi, lim[1]) : hi;
      const v = cfgVal(path);
      return `<div class="slider"><div class="row"><b>${label}</b><span class="val" data-val-for="${path}">${esc(v)}</span></div><small>${help}</small>
        <input type="range" min="${min}" max="${max}" step="${step}" value="${num(v, min)}" data-path="${path}" aria-label="${label}"></div>`;
    }).join("")}
      <details class="acc" style="margin-top:12px"><summary>Advanced — every weight</summary>${advancedHtml(cfg)}</details></div>`;

    html += `<div class="savebar"><button class="btn btn-soft" id="undo-btn">↶ Undo last save</button><button class="btn btn-primary" id="save-btn" ${Object.keys(ui.pending).length ? "" : "disabled"}>Save${Object.keys(ui.pending).length ? ` (${Object.keys(ui.pending).length})` : ""}</button></div>`;

    html += `<div class="section-title">YouTube TV channels</div><div class="card">
      <div class="howto"><b>How to get a channel link</b><ol><li>Open tv.youtube.com on your computer and go to Live.</li><li>Hover a channel, right-click it → Copy link.</li><li>Paste it next to that channel here.</li></ol></div>
      ${channelRows()}</div>`;

    html += `<div class="section-title">Health</div><div class="card" id="health">${healthHtml()}</div>
      <div class="small dim" style="text-align:center;margin:18px 0 6px">RedZone Everything · LAN only</div>`;
    root.innerHTML = html;
  }
  function advancedHtml(cfg) {
    const groups = ["score", "decider", "delay", "break_lengths_s", "polling"];
    return groups.filter((g) => cfg[g]).map((g) => {
      const leaves = [];
      (function walk(o, p) {
        for (const [k, v] of Object.entries(o || {})) {
          const path = p ? `${p}.${k}` : k;
          if (v && typeof v === "object" && !Array.isArray(v)) walk(v, path);
          else if (typeof v === "number") leaves.push(path);
        }
      })(cfg[g], g);
      return `<div class="adv-group"><h4>${esc(g.replace(/_/g, " "))}</h4>${leaves.map((path) => {
        const lim = limitFor(path);
        const v = cfgVal(path);
        return `<div class="adv-row"><label for="adv-${path}">${esc(path.slice(g.length + 1).replace(/_/g, " ").replace(/\./g, " › "))}</label>
          <input id="adv-${path}" class="tnum ${path in ui.pending ? "changed" : ""}" inputmode="decimal" data-adv="${path}" value="${escRaw(v)}" ${lim ? `data-min="${lim[0]}" data-max="${lim[1]}"` : ""}></div>`;
      }).join("")}</div>`;
    }).join("");
  }
  function channelRows() {
    const chs = ((ui.channels && ui.channels.channels) || []).filter(Boolean);
    if (!chs.length) return `<div class="small muted" style="margin-top:10px">No channels listed.</div>`;
    return chs.map((c) => `<div class="chan-row"><div class="row"><b>${esc(c.label || c.key)}</b>
      <span class="status ${c.code ? "ok" : "no"}">${c.code ? "✓ set" : "not set"}</span></div>
      <div class="paste"><input class="input" data-chan-input="${escRaw(c.key)}" placeholder="${c.code ? "Paste a new link to replace" : "Paste tv.youtube.com/watch/… link"}" autocomplete="off" autocapitalize="off" spellcheck="false">
      <button class="btn btn-sm btn-soft" data-chan-save="${escRaw(c.key)}">Save</button></div></div>`).join("");
  }
  function healthHtml() {
    const h = (S && S.health) || {};
    const rows = [];
    for (const s of h.sources || []) {
      if (!s) continue;
      const cls = s.benched ? "r" : !s.ok ? "y" : num(s.age_s, 999) > 60 ? "y" : "g";
      rows.push(`<div class="health-row"><span class="hdot ${cls}"></span><div class="grow"><b>${esc(s.name)}</b>${s.benched ? ` <span class="chip chip-lock">benched</span>` : ""}</div>
        <span class="meta">${s.age_s != null ? `${Math.round(s.age_s)}s ago` : "—"}${num(s.errors) ? ` · ${num(s.errors)} err` : ""}</span></div>`);
    }
    for (const st of h.sticks || []) {
      if (!st) continue;
      const cls = st.state === "connected" ? "g" : st.state === "unauthorized" ? "y" : "r";
      rows.push(`<div class="health-row"><span class="hdot ${cls}"></span><div class="grow"><b>${esc(st.name || st.host)}</b> <span class="small muted">${esc(st.role)}</span>
        <div class="small muted">${esc(st.os)} · ${esc(st.host)}</div></div><span class="meta">${esc(st.state === "unauthorized" ? "approve on TV" : st.state)}</span></div>`);
    }
    if (h.tv) {
      const ok = /on|connected|ok/i.test(h.tv.state || "");
      rows.push(`<div class="health-row"><span class="hdot ${ok ? "g" : h.tv.state ? "y" : "n"}"></span><div class="grow"><b>Samsung TV</b></div><span class="meta">${esc(h.tv.state)}</span></div>`);
    }
    const race = h.race || {};
    const re = Object.entries(race);
    if (re.length) {
      rows.push(`<div class="health-row"><span class="hdot n"></span><div class="grow"><b>Fastest feed</b><div class="small muted">${re
        .sort((a, b) => num(b[1] && b[1].first) - num(a[1] && a[1].first))
        .map(([k, v]) => `${esc(k)}: first ${num(v && v.first)}× · +${num(v && v.avg_lag_s).toFixed(1)}s`).join("<br>")}</div></div></div>`);
    }
    return rows.join("") || `<div class="small muted">Waiting for the first health report…</div>`;
  }

  function setPending(path, value) {
    const orig = get(ui.settings && ui.settings.config, path);
    if (JSON.stringify(orig) === JSON.stringify(value)) delete ui.pending[path];
    else ui.pending[path] = value;
    const btn = $("#save-btn");
    if (btn) {
      const n = Object.keys(ui.pending).length;
      btn.disabled = !n;
      btn.textContent = n ? `Save (${n})` : "Save";
    }
  }
  function nestedChanges() {
    const out = {};
    for (const [path, v] of Object.entries(ui.pending)) {
      const parts = path.split(".");
      let node = out;
      parts.slice(0, -1).forEach((p) => { node = node[p] = node[p] || {}; });
      node[parts[parts.length - 1]] = v;
    }
    return out;
  }

  // ----- sheets -----
  function openSheet(html) {
    const sh = $("#sheet"), scrim = $("#sheet-scrim");
    sh.innerHTML = `<div class="grab"></div>${html}`;
    sh.hidden = false; scrim.hidden = false;
    document.body.style.overflow = "hidden";
  }
  function closeSheet() {
    $("#sheet").hidden = true; $("#sheet-scrim").hidden = true;
    document.body.style.overflow = "";
  }
  function confirmSheet(title, body, okLabel, danger) {
    return new Promise((resolve) => {
      openSheet(`<h3>${esc(title)}</h3><p class="sub">${body}</p><div class="sheet-grid">
        <button class="btn btn-soft" data-confirm="0">Cancel</button>
        <button class="btn ${danger ? "btn-danger btn-soft" : "btn-primary"}" data-confirm="1">${esc(okLabel)}</button></div>`);
      const handler = (e) => {
        const b = e.target.closest("[data-confirm]");
        if (!b) return;
        $("#sheet").removeEventListener("click", handler);
        closeSheet();
        resolve(b.dataset.confirm === "1");
      };
      $("#sheet").addEventListener("click", handler);
      $("#sheet-scrim").onclick = () => { $("#sheet").removeEventListener("click", handler); closeSheet(); resolve(false); };
    });
  }
  function moreSheet() {
    const n = S && S.now;
    const g = n ? gameById(n.game_id) : null;
    const locked = get(S, "mode.locked_game") && n && get(S, "mode.locked_game") === n.game_id;
    openSheet(`<h3>${n ? esc(n.matchup) : "Nothing on"}</h3><p class="sub">${n ? esc(n.reason) : "Controls for the current game."}</p>
      <div class="sheet-grid">
        <button class="btn btn-soft" data-act="${locked ? "unlock" : "lock"}" data-gid="${escRaw(n && n.game_id)}" ${n ? "" : "disabled"}><span class="e">🔒</span>${locked ? "Unlock" : "Lock here"}</button>
        <button class="btn btn-soft" data-act="mute" data-gid="${escRaw(n && n.game_id)}" ${n ? "" : "disabled"}><span class="e">🔇</span>Never show this game</button>
        <button class="btn btn-soft" data-act="${g && g.boosted ? "unboost" : "boost"}" data-gid="${escRaw(n && n.game_id)}" ${n ? "" : "disabled"}><span class="e">🔥</span>${g && g.boosted ? "Remove boost" : "Boost this game"}</button>
        <button class="btn btn-soft" data-act="saw_it"><span class="e">⚡</span>I just saw it</button>
        <button class="btn btn-soft btn-good" data-act="vote" data-val="1" data-gid="${escRaw(n && n.game_id)}" ${n ? "" : "disabled"}><span class="e">👍</span>Good switch</button>
        <button class="btn btn-soft btn-danger" data-act="vote" data-val="-1" data-gid="${escRaw(n && n.game_id)}" ${n ? "" : "disabled"}><span class="e">👎</span>Bad switch</button>
      </div>
      <p class="hint"><b>I just saw it:</b> tap the instant a big play shows on your TV. It measures how far behind live your stream is, so it never cuts away mid-play.</p>`);
  }
  async function channelSheet(gid) {
    const g = gameById(gid);
    if (!ui.channels) { try { ui.channels = await api("/api/channels"); } catch {} }
    const chs = ((ui.channels && ui.channels.channels) || []).filter(Boolean);
    const cur = get(g, "where.target");
    openSheet(`<h3>Where is ${esc(g ? g.matchup : "this game")} on?</h3><p class="sub">It remembers your choice.</p>
      <div class="sheet-list">
        ${chs.map((c) => `<button class="btn btn-soft" data-settarget="youtubetv:${escRaw(c.key)}" data-gid="${escRaw(gid)}">📺 ${esc(c.label || c.key)}${c.code ? "" : ` <span class="small dim">(link not set)</span>`}${cur === "youtubetv:" + c.key ? `<span class="tick">✓</span>` : ""}</button>`).join("")}
        <button class="btn btn-soft" data-settarget="youtubetv:st" data-gid="${escRaw(gid)}">🏈 Sunday Ticket${cur && cur.startsWith("youtubetv:st") ? `<span class="tick">✓</span>` : ""}</button>
        <button class="btn btn-soft" data-settarget="prime:leaguepass" data-gid="${escRaw(gid)}">🟦 Prime Video${cur && cur.startsWith("prime") ? `<span class="tick">✓</span>` : ""}</button>
      </div>
      <div class="field"><label for="custom-target">Or paste a YouTube TV / Prime Video link</label>
        <div class="paste" style="display:grid;grid-template-columns:1fr auto;gap:8px"><input id="custom-target" class="input" placeholder="https://tv.youtube.com/watch/…" autocapitalize="off" spellcheck="false">
        <button class="btn btn-sm btn-primary" data-settarget-custom="${escRaw(gid)}">Use</button></div></div>
      <button class="btn btn-ghost btn-block" style="margin-top:10px" data-settarget="" data-gid="${escRaw(gid)}">Clear my choice (auto-detect)</button>`);
  }

  // ----- announcer (original, generic voice) -----
  const SPEAK_FIX = [
    [/\s*·\s*/g, ", "], [/&/g, " and "], [/\bQ([1-4])\b/g, (m, q) => ["", "the first quarter", "the second quarter", "the third quarter", "the fourth quarter"][q]],
    [/\bP([1-3])\b/g, (m, p) => ["", "the first period", "the second period", "the third period"][p]],
    [/\bOT\b/g, "overtime"], [/\b(\d+):(\d\d)\b/g, (m, a, b) => (a === "0" ? `${Number(b)} seconds` : `${a} ${b}`)],
    [/\bopp\b/g, "the opponent's"], [/\bML\b/g, "moneyline"], [/\bvs\b/g, "versus"], [/—/g, ". "],
  ];
  function speakable(text) { let t = String(text || ""); for (const [re, rep] of SPEAK_FIX) t = t.replace(re, rep); return t; }
  function teamWords(g) {
    if (!g) return "";
    const a = (g.away && (g.away.name || g.away.abbr)) || "", h = (g.home && (g.home.name || g.home.abbr)) || "";
    return a && h ? `${a} at ${h}` : g.matchup || "";
  }
  function announce(n) {
    if (!ui.announcer || !n || !("speechSynthesis" in window)) return;
    const g = gameById(n.game_id);
    const lead = n.kind === "replay" ? "Catch-up replay." : `Switching to ${teamWords(g) || n.matchup}.`;
    say(`${lead} ${speakable(n.reason)}`);
  }
  function say(text) {
    try {
      const u = new SpeechSynthesisUtterance(text);
      u.rate = 1.05; u.pitch = 1.0; u.volume = 1.0; u.lang = "en-US";
      speechSynthesis.cancel();
      speechSynthesis.speak(u);
    } catch {}
  }

  // ---------- toasts ----------
  function toast(text, level = "info") {
    if (!text) return;
    const el = document.createElement("div");
    el.className = `toast ${level === "error" ? "error" : level === "warn" ? "warn" : level === "ok" ? "ok" : ""}`;
    el.innerHTML = `<i></i><span>${esc(text)}</span>`;
    const host = $("#toasts");
    host.appendChild(el);
    while (host.children.length > 3) host.firstChild.remove();
    setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 260); }, level === "error" ? 4200 : 2600);
  }

  // ---------- morph: replace only when changed (keeps scroll + avoids flicker) ----------
  function morph(el, html) {
    if (el.dataset.html === html) return;
    el.innerHTML = html;
    el.dataset.html = html;
  }

  // ---------- timers ----------
  function tick() {
    for (const el of $$("[data-since]")) {
      const s = num(el.dataset.since);
      if (s) el.textContent = fmtDur(nowS() - s);
    }
    for (const el of $$("[data-countdown]")) {
      el.textContent = fmtDur(num(el.dataset.countdown) - nowS());
    }
  }
  setInterval(tick, 1000);

  function renderTabPips() {
    const bets = ((S && S.bets) || []).filter((b) => b && num(b.sweat) >= 0.5).length;
    const btn = $('.tabbar [data-go="bets"]');
    let pip = $(".pip", btn);
    if (bets) { if (!pip) { pip = document.createElement("span"); pip.className = "pip"; btn.appendChild(pip); } pip.textContent = bets; }
    else if (pip) pip.remove();
  }

  // ---------- tabs ----------
  function go(tab) {
    if (!["now", "games", "bets", "lab", "settings"].includes(tab)) tab = "now";
    ui.tab = tab;
    store.set("tab", tab);
    for (const s of $$(".tab")) s.hidden = s.dataset.tab !== tab;
    for (const b of $$(".tabbar button")) b.classList.toggle("on", b.dataset.go === tab);
    if (tab === "lab") { renderLab(); loadLab(); }
    else if (tab === "settings") { if (!ui.settings) loadSettings(); else renderSettings(); }
    else if (tab === "bets") renderBets(false);
    render();
    window.scrollTo({ top: 0 });
  }

  // ---------- events (one delegated listener) ----------
  document.addEventListener("click", async (e) => {
    const t = e.target;
    const goBtn = t.closest("[data-go]");
    if (goBtn) { go(goBtn.dataset.go); return; }

    const actBtn = t.closest("[data-act]");
    if (actBtn && !actBtn.disabled) {
      const action = actBtn.dataset.act;
      const extra = {};
      const gid = actBtn.dataset.gid || (S && S.now && S.now.game_id) || undefined;
      if (["lock", "unlock", "mute", "unmute", "boost", "unboost", "watch", "vote", "focus"].includes(action) && gid) extra.game_id = gid;
      if (action === "vote") extra.value = Number(actBtn.dataset.val);
      if (action === "focus") extra.value = "focus";
      if (action === "mute") {
        const ok = await confirmSheet("Never show this game?", "It won't switch to it again. You can unmute it from the Games tab.", "Mute it", true);
        if (!ok) return;
      }
      const inSheet = actBtn.closest("#sheet");
      await act(action, extra, actBtn);
      if (inSheet) closeSheet();
      if (action === "saw_it") { vibrate(30); }
      return;
    }

    if (t.closest('[data-open="more"]')) { moreSheet(); return; }
    if (t.closest("#sheet-scrim")) { closeSheet(); return; }

    const fm = t.closest("[data-focus-mode]");
    if (fm) { act("focus", { game_id: get(S, "mode.focus_game"), value: fm.dataset.focusMode }, fm); return; }

    const lg = t.closest("[data-league]");
    if (lg) { ui.league = lg.dataset.league; store.set("league", ui.league); renderGames(); return; }

    const setch = t.closest("[data-setch]");
    if (setch) { e.stopPropagation(); channelSheet(setch.dataset.setch); return; }

    const st = t.closest("[data-settarget]");
    if (st) {
      try {
        const r = await api("/api/override", { body: { game_id: st.dataset.gid, target: st.dataset.settarget || null } });
        toast((r && r.message) || "Saved", "ok");
      } catch (err) { toast(err.message, "error"); }
      closeSheet();
      return;
    }
    const stc = t.closest("[data-settarget-custom]");
    if (stc) {
      const v = ($("#custom-target") || {}).value || "";
      if (!v.trim()) return toast("Paste a link first", "warn");
      try {
        const r = await api("/api/override", { body: { game_id: stc.dataset.settargetCustom, target: v.trim() } });
        toast((r && r.message) || "Saved", "ok");
        closeSheet();
      } catch (err) { toast(err.message, "error"); }
      return;
    }

    const addbet = t.closest("[data-addbet]");
    if (addbet) { ui.bet.game = addbet.dataset.addbet; ui.bet.team = null; go("bets"); renderBets(true); return; }

    const rp = t.closest("[data-replay]");
    if (rp) { act("replay", { game_id: rp.dataset.replay, value: rp.dataset.play }, rp); return; }

    const row = t.closest(".game");
    if (row && !t.closest("button")) {
      ui.expanded = ui.expanded === row.dataset.game ? null : row.dataset.game;
      renderGames();
      return;
    }

    // bets form
    const pick = t.closest("[data-pick]");
    if (pick) { ui.bet.game = pick.dataset.pick; ui.bet.team = null; keepBetInputs(); renderBetForm(); return; }
    const kind = t.closest("[data-kind]");
    if (kind) { ui.bet.kind = kind.dataset.kind; keepBetInputs(); renderBetForm(); return; }
    const team = t.closest("[data-team]");
    if (team) { ui.bet.team = team.dataset.team; keepBetInputs(); renderBetForm(); return; }
    const side = t.closest("[data-side]");
    if (side) { ui.bet.side = side.dataset.side; keepBetInputs(); renderBetForm(); return; }
    if (t.closest("[data-flip]")) {
      const inp = $("#bet-line");
      if (inp) { const v = inp.value.trim(); inp.value = v.startsWith("-") ? v.slice(1) : v.startsWith("+") ? "-" + v.slice(1) : v ? "-" + v : "-"; ui.bet.line = inp.value; inp.focus(); }
      return;
    }
    if (t.closest("#bet-save")) { saveBet(t.closest("#bet-save")); return; }
    const del = t.closest("[data-delbet]");
    if (del) {
      const ok = await confirmSheet("Delete this bet?", "It stops boosting that game.", "Delete", true);
      if (!ok) return;
      try {
        await api(`/api/bets/${encodeURIComponent(del.dataset.delbet)}`, { method: "DELETE" });
        if (S) S.bets = (S.bets || []).filter((b) => String(b.id) !== del.dataset.delbet);
        toast("Bet deleted", "ok");
        renderBets(false);
      } catch (err) { toast(err.message, "error"); }
      return;
    }

    // lab
    const lb = t.closest("[data-label]");
    if (lb) {
      lb.classList.remove("flash"); void lb.offsetWidth; lb.classList.add("flash");
      vibrate(25);
      const r = await api("/api/action", { body: { action: "label", value: lb.dataset.label } }).catch((err) => ({ ok: false, message: err.message }));
      toast(r && r.ok === false ? r.message : `Logged: ${lb.dataset.label}`, r && r.ok === false ? "error" : "ok");
      return;
    }

    // settings
    const rm = t.closest("[data-rmteam]");
    if (rm) {
      const [lgk, ab] = rm.dataset.rmteam.split(":");
      const list = (cfgVal(`teams.${lgk}`) || []).filter((x) => x !== ab);
      setPending(`teams.${lgk}`, list); renderSettings(); return;
    }
    if (t.closest("#save-btn")) { saveSettings(t.closest("#save-btn")); return; }
    if (t.closest("#undo-btn")) {
      const ok = await confirmSheet("Undo your last save?", "Settings go back to how they were before it.", "Undo");
      if (!ok) return;
      try { const r = await api("/api/settings/rollback", { body: {} }); toast((r && r.message) || "Undone", "ok"); ui.settings = null; loadSettings(true); }
      catch (err) { toast(err.message, "error"); }
      return;
    }
    const cs = t.closest("[data-chan-save]");
    if (cs) {
      const key = cs.dataset.chanSave;
      const inp = $(`[data-chan-input="${CSS.escape(key)}"]`);
      const link = inp ? inp.value.trim() : "";
      if (!link) return toast("Paste the channel link first", "warn");
      cs.classList.add("busy");
      try {
        const r = await api("/api/channels", { body: { channel: key, link } });
        toast((r && r.message) || "Saved", r && r.ok === false ? "warn" : "ok");
        if (!r || r.ok !== false) { ui.channels = await api("/api/channels").catch(() => ui.channels); renderSettings(); }
      } catch (err) { toast(err.message, "error"); }
      finally { cs.classList.remove("busy"); }
      return;
    }
    if (t.closest("#announcer-btn")) {
      ui.announcer = true; store.set("announcer", "1");
      say("Announcer on. I'll call out every switch.");
      renderSettings();
      return;
    }
    if (t.closest("[data-reload-settings]")) { ui.settings = null; loadSettings(true); return; }
  });

  function keepBetInputs() {
    const l = $("#bet-line"), n = $("#bet-note");
    if (l) ui.bet.line = l.value;
    if (n) ui.bet.note = n.value;
  }

  document.addEventListener("change", async (e) => {
    const t = e.target;
    if (t.id === "focus-sel") {
      if (!t.value) act("autopilot", {}, null);
      else act("focus", { game_id: t.value, value: get(S, "mode.focus_mode") === "focus_must_see" ? "focus_must_see" : "focus" }, null);
      return;
    }
    if (t.matches("[data-dry-toggle]") || t.id === "dry-switch") {
      const wantTv = t.checked;
      if (wantTv) {
        const ok = await confirmSheet("Turn on TV control?", "The app will start changing channels on your Fire TV by itself. You can turn it off any time.", "Control my TV");
        if (!ok) { t.checked = false; return; }
      }
      const r = await act("dry_run", { value: !wantTv }, null);
      if (!r) t.checked = !wantTv;
      return;
    }
    if (t.matches("[data-league-toggle]")) { setPending(`mode.leagues.${t.dataset.leagueToggle}`, t.checked); return; }
    if (t.matches("[data-adv]")) {
      const v = parseFloat(t.value);
      if (!Number.isFinite(v)) { toast("That needs to be a number", "warn"); t.value = cfgVal(t.dataset.adv); return; }
      const lo = t.dataset.min !== undefined ? Number(t.dataset.min) : -Infinity, hi = t.dataset.max !== undefined ? Number(t.dataset.max) : Infinity;
      if (v < lo || v > hi) { toast(`Keep it between ${lo} and ${hi}`, "warn"); t.value = cfgVal(t.dataset.adv); return; }
      setPending(t.dataset.adv, v); t.classList.toggle("changed", t.dataset.adv in ui.pending);
      return;
    }
    if (t.matches("[data-addteam]")) {
      const lgk = t.dataset.addteam, ab = t.value.trim().toUpperCase();
      if (!/^[A-Z]{2,4}$/.test(ab)) { if (ab) toast("Use the 2–4 letter abbreviation, like WSH", "warn"); return; }
      const list = cfgVal(`teams.${lgk}`) || [];
      if (!list.includes(ab)) setPending(`teams.${lgk}`, [...list, ab]);
      renderSettings();
    }
  });
  document.addEventListener("input", (e) => {
    const t = e.target;
    if (t.matches('input[type=range][data-path]')) {
      const v = Number(t.value);
      const lbl = $(`[data-val-for="${CSS.escape(t.dataset.path)}"]`);
      if (lbl) lbl.textContent = v;
      setPending(t.dataset.path, v);
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("#sheet").hidden) closeSheet();
    if (e.key === "Enter" && e.target.matches("[data-addteam]")) e.target.blur();
  });

  async function saveSettings(btn) {
    if (!Object.keys(ui.pending).length) return;
    btn.classList.add("busy");
    try {
      const r = await api("/api/settings", { body: { changes: nestedChanges() } });
      if (r && r.ok === false) { toast(r.message || "Not saved", "warn"); return; }
      toast((r && r.message) || "Saved", "ok");
      ui.settings = null; ui.pending = {};
      await loadSettings(true);
    } catch (err) { toast(err.message, "error"); }
    finally { btn.classList.remove("busy"); }
  }

  // ---------- mock mode (design preview: ?mock=1) ----------
  async function loadMock() {
    let data = null;
    for (const p of ["/static/mock_state.json", "mock_state.json"]) {
      try { const r = await fetch(p); if (r.ok) { data = await r.json(); break; } } catch {}
    }
    if (!data) { toast("Couldn't load mock data", "error"); return; }
    const shift = nowS() - num(data.ts, nowS());
    (function walk(o) {
      if (!o || typeof o !== "object") return;
      for (const k of Object.keys(o)) {
        if (["ts", "since", "at", "start_time", "frozen_until"].includes(k) && typeof o[k] === "number") o[k] += shift;
        else walk(o[k]);
      }
    })(data);
    MOCK_STATE = data;
    applyState(JSON.parse(JSON.stringify(data)));
  }
  const MOCK_CONFIG = {
    mode: { dry_run: true, leagues: { nfl: true, nba: true, nhl: true, mlb: true } },
    teams: { nfl: ["WSH"], nba: ["WSH"], nhl: ["WSH"], mlb: ["WSH"] },
    polling: { live_s: 10, idle_s: 300 },
    score: { neutral_cap: 0.9, personal_cap: 30, smoothing_half_life_s: 30,
      personal: { my_team: 25, bet_max: 20, fantasy_mine: 12, fantasy_opponent: 8, manual_boost: 15 },
      nfl: { two_minute_drill: 15, uncertainty: 15, player_going_off: 8 } },
    decider: { min_dwell_s: 45, switch_margin: 10, max_switches_per_5min: 4, min_gap_s: 20, return_tolerance: 5,
      break_destination_min: 25, must_see_score: 92, must_see_lead: 20 },
    delay: { default: { youtubetv: 45, prime: 40 }, sigma_default: 15 },
  };
  async function mockApi(path, opts) {
    await new Promise((r) => setTimeout(r, 180));
    const body = opts.body || {};
    if (path === "/api/state") return MOCK_STATE;
    if (path === "/api/settings") return { config: MOCK_CONFIG, limits: { "polling.live_s": [5, 60], "decider.min_dwell_s": [10, 600], "decider.max_switches_per_5min": [1, 20], score: [-100, 100], decider: [0, 1000], delay: [0, 180] } };
    if (path === "/api/channels" && !opts.body) return { channels: [
      { key: "espn", label: "ESPN", code: "D1vdtklRyHg" }, { key: "espn2", label: "ESPN2", code: null }, { key: "abc", label: "ABC", code: null },
      { key: "cbs", label: "CBS", code: "aB3dEfGhIjK" }, { key: "fox", label: "FOX", code: "Zx9Yw8Vu7Ts" }, { key: "nbc", label: "NBC", code: null },
      { key: "nflnetwork", label: "NFL Network", code: null }, { key: "redzone", label: "NFL RedZone", code: null }, { key: "tnt", label: "TNT", code: null } ], overrides: {} };
    if (path === "/api/bets" && !opts.body) return (S && S.bets) || [];
    if (path === "/api/lab") return {
      backtests: [
        { title: "Backtest 20251214 1pm slate (8 games)", summary: { mid_play_cuts: 0, big_plays_live: "27/82 (33%)", your_team_big_plays_live: "8/11", dead_air_pct: 20.2, switches_per_hour: 15.1, switches: 49 } },
        { title: "Backtest 20251130 4pm slate (3 games)", summary: { mid_play_cuts: 0, big_plays_live: "19/29 (66%)", your_team_big_plays_live: "0/0", dead_air_pct: 35.7, switches_per_hour: 6.5, switches: 21 } } ],
      optimize: { slates: 4, current: { score: 28.4, big_live_pct: 41.2, dead_air_pct: 25.6, switches_per_hour: 13.4, mid_play_cuts: 0 },
        best: { score: 36.9, big_live_pct: 47.8, dead_air_pct: 21.8, switches_per_hour: 16.2, mid_play_cuts: 0, params: { "decider.switch_margin": 8, "decider.min_dwell_s": 35, "decider.chase_min_dwell_s": 15 } } },
      feedback: { up: 12, down: 3 } };
    if (path === "/api/bets" && opts.body) {
      const g = gameById(body.game_id);
      const bet = { id: Date.now(), game_id: body.game_id, matchup: g && g.matchup, kind: body.kind, team: body.team, line: body.line, side: body.side, note: body.note, sweat: 0.4 };
      bet.label = "your bet: " + describeBet(bet);
      return { ok: true, message: "Bet added", bet };
    }
    if (path.startsWith("/api/bets/")) return { ok: true };
    if (path === "/api/action") {
      const m = MOCK_STATE.mode;
      const a = body.action;
      if (a === "pause") m.paused = true, m.state = "paused";
      if (a === "resume") m.paused = false, m.safe = false, m.frozen_until = null, m.state = "watching";
      if (a === "dry_run") m.dry_run = !!body.value;
      if (a === "focus") { m.focus_game = body.game_id; m.focus_mode = body.value || "focus"; }
      if (a === "autopilot") { m.focus_game = null; m.focus_mode = "autopilot"; }
      for (const g of MOCK_STATE.games) {
        if (g.id !== body.game_id) continue;
        if (a === "boost") g.boosted = true; if (a === "unboost") g.boosted = false;
        if (a === "mute") g.muted = true; if (a === "unmute") g.muted = false;
        if (a === "lock") m.locked_game = g.id; if (a === "unlock") m.locked_game = null;
      }
      for (const g of MOCK_STATE.games) g.focus = g.id === m.focus_game;
      setTimeout(() => applyState(JSON.parse(JSON.stringify(MOCK_STATE))), 50);
      const msgs = { pause: "Paused — staying put", resume: "Back on autopilot", skip: "Skipping this game", back: "Going back",
        saw_it: "Got it — delay measured", vote: body.value > 0 ? "Thanks — noted 👍" : "Noted — it'll learn from that",
        focus: "Focus set", autopilot: "Autopilot on", boost: "Boosted", mute: "Muted", lock: "Locked here", watch: "Switching now",
        replay: "Playing replay", dry_run: body.value ? "Dry run on" : "TV control on" };
      return { ok: true, message: "(preview) " + (msgs[a] || a) };
    }
    if (path === "/api/settings/rollback") return { ok: true, message: "(preview) Undone" };
    return { ok: true, message: "(preview) saved" };
  }

  // ---------- boot ----------
  go(ui.tab);
  if (MOCK) { setConn("mock", "Preview"); loadMock(); } else { loadOnce(); connect(); }
  document.addEventListener("visibilitychange", () => { if (!document.hidden && !MOCK) loadOnce(); });
})();
