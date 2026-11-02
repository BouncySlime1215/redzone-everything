"""The producer loop: poll every feed -> merge -> score -> decide -> act -> tell the phone.

Runs forever. Every stage is wrapped so one failure (a feed down, a bad game, a stick
offline) degrades that piece and keeps everything else running.
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from redzone import bets as betlib
from redzone.channels import SETUP_CHANNELS, ChannelMap, describe
from redzone.config import Config
from redzone.control import Controller
from redzone.decider.decider import Candidate, Decider, FocusMode, Missed
from redzone.decider.screen import PLAY_LEN_S, DelayBook
from redzone.models import GameState, League, Phase
from redzone.scoring.engine import Personal, Scored, Scorer
from redzone.sources import espn, leagues
from redzone.sources.http import Fetcher
from redzone.sources.merge import Merger
from redzone.store import Store

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


class Engine:
    def __init__(self, cfg: Config | None = None, store: Store | None = None):
        self.cfg = cfg or Config()
        self.store = store or Store()
        self.channels = ChannelMap()
        self.fetcher = Fetcher()
        self.merger = Merger()
        self.scorer = Scorer(self.cfg)
        self.delays = DelayBook(self.cfg.get("delay.default"), self.cfg.get("delay.sigma_default"))
        self.decider = Decider(self.cfg, self.delays)
        self.control = Controller(self.cfg, self.channels)
        self.boosted: set[str] = set()
        self.skipped: dict[str, float] = {}   # game -> skip expires at
        self.scored: dict[str, Scored] = {}
        self.where: dict[str, object] = {}
        self.last_decision = None
        self.subscribers: set[asyncio.Queue] = set()
        self.source_status: dict[str, dict] = {}
        self._caffeinate: subprocess.Popen | None = None
        self._last_logged_reason = ""
        self._tick = 0
        self.started = time.time()

    # ------------------------------------------------------------------ loop

    async def run(self) -> None:
        await self.control.start()
        while True:
            t0 = time.time()
            try:
                await self.tick()
            except Exception:
                log.exception("tick failed")
                self.decider.note_error()
            live = any(g.phase == Phase.LIVE for g in self.merger.games.values())
            soon = any(g.phase == Phase.PRE and g.start_time and 0 < g.start_time - time.time() < 900
                       for g in self.merger.games.values())
            self._keep_awake(live)
            interval = self.cfg.get("polling.live_s") if (live or soon) else self.cfg.get("polling.idle_s")
            await asyncio.sleep(max(1.0, interval - (time.time() - t0)))

    async def tick(self) -> None:
        self._tick += 1
        await self.poll()
        await self.control.keepalive()
        self.decide()
        self.broadcast()
        if self._tick % 360 == 0:
            self.store.prune()

    # ------------------------------------------------------------------ data

    def _leagues(self) -> list[League]:
        on = self.cfg.get("mode.leagues", {})
        return [lg for lg in League if on.get(lg.value, True)]

    async def poll(self) -> None:
        lgs = self._leagues()
        boards = await asyncio.gather(*(self._scoreboard(lg) for lg in lgs), return_exceptions=True)
        for lg, games in zip(lgs, boards):
            if isinstance(games, Exception) or games is None:
                self.merger.record_error(f"espn:{lg.value}")
                continue
            for g in games:
                self.merger.update("espn_scoreboard", g)
        live = [g for g in self.merger.games.values() if g.phase == Phase.LIVE and g.league in lgs]
        sem = asyncio.Semaphore(6)

        async def one(g: GameState):
            async with sem:
                data = await self.fetcher.get_json(espn.summary_url(g.league, g.game_id))
            if not data:
                self.merger.record_error("espn_summary")
                return
            try:
                s = espn.parse_summary(g.league, data)
            except Exception:
                log.exception("summary parse failed %s", g.game_id)
                s = None
            if s and s.game_id:
                self.merger.update("espn_summary", s)
        await asyncio.gather(*(one(g) for g in live))
        await self._league_feeds(live)
        self._source_health()
        for g in self.merger.games.values():
            if g.phase == Phase.LIVE:
                self.decider.timeline(g.game_id, g.league).observe(g)

    async def _scoreboard(self, lg: League) -> list[GameState] | None:
        data = await self.fetcher.get_json(espn.scoreboard_url(lg), min_interval=8)
        if not data:
            return None
        return espn.parse_scoreboard(lg, data)

    async def _league_feeds(self, live: list[GameState]) -> None:
        today = datetime.now(ET).strftime("%Y-%m-%d")
        nhl = [g for g in live if g.league == League.NHL]
        if nhl:
            sc = await self.fetcher.get_json(leagues.NHL_SCORE.format(date=today), min_interval=8)
            if sc:
                idx = leagues.nhl_index(sc)
                for g in nhl:
                    gm = leagues.nhl_match(g, idx)
                    if not gm:
                        continue
                    pbp = await self.fetcher.get_json(leagues.NHL_PBP.format(id=gm.get("id")), min_interval=8)
                    try:
                        self.merger.update("nhl", leagues.enrich_nhl(self.merger.games[g.game_id], gm, pbp))
                    except Exception:
                        log.exception("nhl enrich")
        nba = [g for g in live if g.league == League.NBA]
        if nba:
            sb = await self.fetcher.get_json(leagues.NBA_SCOREBOARD, min_interval=5)
            if sb:
                idx = leagues.nba_index(sb)
                for g in nba:
                    gm = leagues.nba_match(g, idx)
                    if gm:
                        try:
                            self.merger.update("nba", leagues.enrich_nba(self.merger.games[g.game_id], gm))
                        except Exception:
                            log.exception("nba enrich")

    def _source_health(self) -> None:
        now = time.time()
        for name, h in self.merger.health.items():
            self.source_status[name] = {"name": name, "ok": (now - h.last_ok) < 120 and not h.benched,
                                        "age_s": round(now - h.last_ok) if h.last_ok else None,
                                        "errors": h.errors, "benched": h.benched}

    # ------------------------------------------------------------------ decide

    def personal(self) -> Personal:
        teams = {League(k): set(v) for k, v in (self.cfg.get("teams") or {}).items() if k in League._value2member_map_}
        sweat, labels = {}, {}
        for b in self.store.bets():
            g = self.merger.games.get(b["game"])
            if not g:
                continue
            s = betlib.sweat(g, b)
            if s >= sweat.get(g.game_id, 0):
                sweat[g.game_id] = s
                labels[g.game_id] = betlib.label(g, b)
        return Personal(my_teams=teams, bet_sweat=sweat, bet_labels=labels, boosted=set(self.boosted))

    def decide(self) -> None:
        now = time.time()
        for gid, until in list(self.skipped.items()):
            if now >= until:
                self.skipped.pop(gid)
                self.decider.user.muted.discard(gid)
        me = self.personal()
        cands: dict[str, Candidate] = {}
        dry = self.cfg.get("mode.dry_run", True)
        st = self.cfg.get("sunday_ticket", True)
        for gid, g in self.merger.games.items():
            if g.league not in self._leagues():
                continue
            try:
                sc = self.scorer.score(g, me)
            except Exception:
                log.exception("score failed %s", gid)
                continue
            self.scored[gid] = sc
            w = self.channels.where(g, sunday_ticket=st)
            self.where[gid] = w
            if g.phase != Phase.LIVE:
                continue
            # In dry run, "likely" targets count so you can see what it would do.
            target = w.target if (w.tunable or dry) else None
            cands[gid] = Candidate(sc, target, stale=self.merger.is_stale(gid))
        before = self.decider.current
        d = self.decider.decide(cands)
        self.last_decision = d
        if d.reason != self._last_logged_reason or d.action != "stay":
            self.store.decision(d)
            self._last_logged_reason = d.reason
        if d.action in ("switch", "replay") and d.target:
            self._act(d, before, cands)
        if self._tick % 6 == 0:
            self.store.snapshot([(time.time(), gid, s.league.value, s.score, s.moment, s.reason,
                                  "now" if gid == self.decider.current else "")
                                 for gid, s in self.scored.items() if s.raw > 0])

    def _act(self, d, before, cands) -> None:
        # A catch-up replay can be of a game that just ended (no longer a live candidate).
        target = cands[d.target].target if d.target in cands else self.decider.target_of(d.target)
        back = None
        if d.action == "replay" and d.replay:
            delay = self.delays.get(target or "unknown")
            back = d.replay.back_s(time.time(), delay.mu, PLAY_LEN_S.get(self.merger.games[d.target].league, 8))
        cmd_ts = time.time()
        event_ts = d.replay.t if d.replay else None

        def done(out):
            self.store.switch(ts=cmd_ts, frm=before, to_game=d.target, target=target, kind=d.kind, reason=d.reason,
                              event_ts=event_ts, cmd_ts=cmd_ts, confirmed_ts=out.confirmed_at, ok=out.ok,
                              dry_run=out.dry_run, detail={"took": out.took, "detail": out.detail})
            if not out.ok and not out.dry_run:
                self.decider.note_error()
                self.toast(f"Couldn't switch: {out.detail[:80]}", "error")
            self.broadcast()
        self.control.submit(target, back, done)

    # ------------------------------------------------------------------ user actions

    def action(self, action: str, game_id: str | None = None, value=None) -> tuple[bool, str]:
        dec, u = self.decider, self.decider.user
        gid = game_id or dec.current
        if action == "focus":
            if not gid:
                return False, "Pick a game to focus on"
            u.focus_game = gid
            u.focus_mode = FocusMode(value or "focus")
            if dec.current != gid:
                dec.manual_switch(gid)
                self._act_manual(gid)
            return True, f"Focused on {self._name(gid)}"
        if action == "autopilot":
            u.focus_game, u.focus_mode = None, FocusMode.AUTOPILOT
            return True, "Autopilot on"
        if action in ("boost", "unboost"):
            (self.boosted.add if action == "boost" else self.boosted.discard)(gid)
            return True, f"{'Boosted' if action == 'boost' else 'Unboosted'} {self._name(gid)}"
        if action in ("mute", "unmute"):
            (u.muted.add if action == "mute" else u.muted.discard)(gid)
            return True, f"{'Muted' if action == 'mute' else 'Unmuted'} {self._name(gid)}"
        if action == "lock":
            u.locked_game = gid
            return True, f"Staying on {self._name(gid)}"
        if action == "unlock":
            u.locked_game = None
            return True, "Unlocked"
        if action == "pause":
            u.paused = True
            return True, "Auto-switching paused"
        if action == "resume":
            dec.resume()
            return True, "Back on autopilot"
        if action == "skip":
            if not dec.current:
                return False, "Nothing to skip"
            self.skipped[dec.current] = time.time() + 300
            u.muted.add(dec.current)
            dec.user.manual_until = 0
            return True, f"Skipping {self._name(dec.current)} for 5 minutes"
        if action == "back":
            if not dec.stack:
                return False, "Nowhere to go back to"
            prev = dec.stack.pop()
            dec.manual_switch(prev)
            self._act_manual(prev)
            return True, f"Back to {self._name(prev)}"
        if action == "watch":
            if not gid:
                return False, "Pick a game"
            dec.manual_switch(gid)
            self._act_manual(gid)
            return True, f"Switching to {self._name(gid)}"
        if action == "saw_it":
            return self._calibrate()
        if action == "vote":
            self.store.feedback(dec.current, int(value or 0), context={"reason": getattr(self.last_decision, "reason", "")})
            return True, "Thanks — logged for training"
        if action == "dry_run":
            self.cfg.update({"mode": {"dry_run": bool(value)}})
            return True, "Dry run on: the TV won't be touched" if value else "TV control ON"
        if action == "replay":
            m = next((m for m in dec.queue if m.pid == str(value)), None)
            if not m:
                return False, "That replay isn't available anymore"
            x = self.where.get(m.game_id)
            dec._start_replay(m, {m.game_id: Candidate(self.scored[m.game_id], getattr(x, "target", None))},
                              time.time())
            self._act_manual(m.game_id, replay=m)
            return True, f"Replaying: {m.label[:60]}"
        if action == "label":
            self.store.label(str(value), dec.current, {"decision": getattr(self.last_decision, "reason", "")})
            return True, f"Labeled: {value}"
        return False, f"Unknown action {action}"

    def _act_manual(self, gid: str, replay: Missed | None = None) -> None:
        w = self.where.get(gid)
        target = getattr(w, "target", None)
        back = None
        if replay:
            d = self.delays.get(target or "unknown")
            back = replay.back_s(time.time(), d.mu, PLAY_LEN_S.get(self.merger.games[gid].league, 8))
        self.control.submit(target, back, lambda out: self.broadcast())

    def _calibrate(self) -> tuple[bool, str]:
        """You tapped "I just saw it": match the tap to the play that just aired on your screen."""
        dec = self.decider
        gid = dec.current
        tl = dec.timelines.get(gid or "")
        w = self.where.get(gid or "")
        target = getattr(w, "target", None)
        if not tl or not target:
            return False, "Nothing on to calibrate against"
        tap = time.time() - 0.7  # typical reaction time
        d = self.delays.get(target)
        plays = [e for e in tl.events if e.kind == "play" and tap - 150 < e.seen < tap - 3]
        if not plays:
            return False, "No recent play to match — tap right as a play ends"
        e = min(plays, key=lambda e: abs(e.seen + d.mu - tap))
        sample = tap - e.seen
        d = self.delays.calibrate(target, sample)
        self.store.calibration(target, sample, d.mu, d.sigma)
        return True, f"Delay {d.mu:.0f}s (±{d.sigma:.0f}) for {describe(target)}"

    def _name(self, gid: str | None) -> str:
        g = self.merger.games.get(gid or "")
        return g.matchup if g else "that game"

    # ------------------------------------------------------------------ phone

    def snapshot(self) -> dict:
        dec = self.decider
        d = self.last_decision
        games = []
        for gid, g in self.merger.games.items():
            if g.league not in self._leagues():
                continue
            s = self.scored.get(gid)
            w = self.where.get(gid)
            games.append({
                "id": gid, "league": g.league.value, "matchup": g.matchup, "phase": g.phase.value,
                "status_text": g.status_text or _status(g), "home": _team(g.home), "away": _team(g.away),
                "score": s.display if s else 0, "moment": round(s.moment) if s else 0,
                "stakes": round(s.stakes, 2) if s else 1.0, "personal": round(s.personal) if s else 0,
                "reason": s.reason if s else "", "tags": s.tags if s else [], "is_break": bool(s and s.is_break),
                "lock": s.lock if s else None, "pre_moment": s.pre_moment if s else None,
                "where": {"label": getattr(w, "label", "unknown"), "confidence": getattr(w, "confidence", "unknown"),
                          "tunable": getattr(w, "tunable", False), "target": getattr(w, "target", None)},
                "odds": g.odds_text, "mine": bool(s and any(t in (s.tags or []) for t in (g.home.abbr, g.away.abbr))),
                "boosted": gid in self.boosted, "muted": gid in dec.user.muted, "focus": gid == dec.user.focus_game,
                "stale": self.merger.is_stale(gid), "start_time": g.start_time, "on_now": gid == dec.current,
            })
        order = {"live": 0, "pre": 1, "unknown": 2, "post": 3}
        games.sort(key=lambda x: (order.get(x["phase"], 2), -x["score"], x["start_time"] or 0))
        cur = self.merger.games.get(dec.current or "")
        now = None
        if cur:
            s = self.scored.get(cur.game_id)
            now = {"game_id": cur.game_id, "matchup": cur.matchup, "league": cur.league.value,
                   "where": getattr(self.where.get(cur.game_id), "label", ""),
                   "reason": (d.reason if d and d.action != "stay" else (s.reason if s else "")) or "",
                   "since": dec.since, "score": s.display if s else 0,
                   "kind": "replay" if dec.replaying else "live",
                   "replay": {"label": dec.replaying.label, "game_id": dec.replaying.game_id,
                              "play_id": dec.replaying.pid} if dec.replaying else None,
                   "home": _team(cur.home), "away": _team(cur.away), "status_text": cur.status_text}
        nxt = None
        if d and d.action == "stay" and d.reason.startswith("want "):
            nxt = {"game_id": None, "matchup": "", "reason": d.reason[5:].split(" — waiting")[0],
                   "why_waiting": d.gate}
        return {
            "ts": time.time(),
            "mode": {"dry_run": self.cfg.get("mode.dry_run", True), "paused": dec.user.paused,
                     "safe": dec.safe_mode, "frozen_until": dec.frozen_until or None,
                     "focus_game": dec.user.focus_game, "focus_mode": dec.user.focus_mode.value,
                     "locked_game": dec.user.locked_game, "state": d.state.value if d else "idle",
                     "tv_control": self.control.live_control, "announcer_hint": ""},
            "now": now, "next": nxt, "standby": None, "games": games,
            "queue": [{"game_id": m.game_id, "matchup": self._name(m.game_id), "play_id": m.pid,
                       "label": m.label, "at": m.t, "kind": m.kind}
                      for m in sorted(dec.queue, key=lambda m: -m.value)[:10]],
            "log": self._log(),
            "delays": [{"target": t, "label": describe(t), "mu": round(x.mu, 1), "sigma": round(x.sigma, 1),
                        "calibrated": x.calibrated_at is not None} for t, x in self.delays.book.items()],
            "health": {"sources": list(self.source_status.values()), "sticks": self.control.status(),
                       "tv": {"state": "unknown"}, "race": self.merger.race_summary(),
                       "adb": self.control.adb_ok},
            "bets": self.bets_view(), "feedback": self.store.feedback_counts(),
        }

    def _log(self) -> list[dict]:
        out = []
        for r in self.store.recent_switches(25):
            took = json.loads(r.get("detail") or "{}").get("took") if isinstance(r.get("detail"), str) else None
            out.append({"ts": r["ts"], "kind": r["kind"], "to": r["to_game"], "matchup": self._name(r["to_game"]),
                        "reason": r["reason"], "dry_run": bool(r["dry_run"]), "ok": bool(r["ok"]),
                        "took": took, "logo": _logo_of(self.merger.games.get(r["to_game"] or ""))})
        return out

    def bets_view(self) -> list[dict]:
        out = []
        for b in self.store.bets():
            g = self.merger.games.get(b["game"])
            out.append({"id": b["id"], "game_id": b["game"], "matchup": g.matchup if g else "game not found",
                        "kind": b["kind"], "team": b["team"], "line": b["line"], "side": b["side"],
                        "note": b["note"], "label": betlib.label(g, b) if g else "",
                        "sweat": round(betlib.sweat(g, b), 2) if g else 0.0})
        return out

    def channels_view(self) -> dict:
        from redzone.channels import PRETTY
        return {"channels": [{"key": k, "label": PRETTY.get(k, k), "code": self.channels.codes.get(k)}
                             for k in SETUP_CHANNELS],
                "overrides": self.channels.overrides}

    # ------------------------------------------------------------------ push

    def broadcast(self) -> None:
        if not self.subscribers:
            return
        payload = json.dumps(self.snapshot(), default=str)
        for q in list(self.subscribers):
            if q.qsize() > 5:
                continue  # slow phone: skip, it'll get the next one
            q.put_nowait(("state", payload))

    def toast(self, text: str, level: str = "info") -> None:
        for q in list(self.subscribers):
            q.put_nowait(("toast", json.dumps({"text": text, "level": level})))

    # ------------------------------------------------------------------ mac

    def _keep_awake(self, live: bool) -> None:
        """caffeinate only while games are live, so the Mac sleeps normally otherwise."""
        running = self._caffeinate is not None and self._caffeinate.poll() is None
        if live and not running:
            try:
                self._caffeinate = subprocess.Popen(["caffeinate", "-i", "-s"])
            except OSError:
                self._caffeinate = None
        elif not live and running:
            self._caffeinate.terminate()
            self._caffeinate = None

    async def close(self) -> None:
        self._keep_awake(False)
        await self.fetcher.close()


def _team(t) -> dict:
    rec = f"{t.wins}-{t.losses}" + (f"-{t.ties}" if t.ties else "") if t.wins is not None and t.losses is not None else ""
    return {"abbr": t.abbr, "name": t.name, "score": t.score, "logo": t.logo, "color": t.color, "record": rec}


def _logo_of(g: GameState | None) -> str:
    return g.home.logo if g else ""


def _status(g: GameState) -> str:
    if g.phase == Phase.PRE and g.start_time:
        return datetime.fromtimestamp(g.start_time, ET).strftime("%-I:%M %p")
    return g.phase.value
