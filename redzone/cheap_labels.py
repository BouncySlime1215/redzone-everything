"""Cheap labels: record the data nobody else has, from reads the app already makes (logging only).

Three row types, all off unless `labels.enabled` is true (code default off; read as cfg.get("labels.enabled", False)):

  * picture-confirmed dead or live at a cut: `bug_state` says, from the Prime score-bug clock reads the screen
    loop already takes, whether the clock was running at a leave or an entry (supervisor_cuts.jsonl gets
    leave_/enter_bug_clock_running and leave_/enter_bug_read_age_s).
  * ad-break-on-picture: no score bug for over BREAK_MIN_S on a game the feed says is live, one row with start
    and end in labels/break_on_picture.jsonl.
  * one joined per-switch outcome row (supervisor's switch_outcomes.jsonl): switch id, command, confirmation and
    first picture times, landed_dead, landed_play_s, with the keys of switches, traces and cuts in one place.

Never changes a switch, tune, key press or timing; makes no adb or OCR call (it is handed bug reads already
made). Rows carry no addresses, stick hosts or account details: the app name and the game id, never a host.

Why: the two most valuable data types (ad breaks on a real channel, dead/live at the moment of a cut) have no
public rows, so what the system records itself matters most (docs/agents/data own-data inventory, section 6).
"""
from __future__ import annotations

import json
import logging
from collections import deque
from pathlib import Path
from typing import Any

from redzone.screen.scorebug import clock_seconds

log = logging.getLogger(__name__)

RING_LEN = 120

TARGETS_KEPT = 8


def knob(cfg: Any, name: str, default: float) -> float:
    """labels.<name> from the config, else the code default (no YAML key needed)."""
    try:
        return float(cfg.get(f"labels.{name}", default))
    except Exception:
        return default


def is_on(cfg: Any) -> bool:
    """The one flag. Missing key = off."""
    try:
        return bool(cfg.get("labels.enabled", False))
    except Exception:
        return False


def app_of(target: str | None) -> str | None:
    """The app name of a target ("prime:..." -> "prime"); never the rest (it may carry a content id)."""
    return str(target).split(":", 1)[0] if target else None


def _append(path: Path, rec: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(rec, default=str, separators=(",", ":")) + "\n")
    except OSError:
        log.exception("could not write %s", path)


class CheapLabels:
    """In-memory ring of recent bug reads per target, and the break-on-picture tracker."""

    def __init__(self, cfg: Any, root: Path | None = None):
        self.cfg, self.root = cfg, root
        self.reads: dict[str, deque] = {}            # target -> (t, clock_s | None, period)
        self._gap: dict[str, dict] = {}              # target -> the open no-bug stretch

    @property
    def enabled(self) -> bool:
        return is_on(self.cfg)

    def observe_bug(self, target: str, t: float, bug: Any, *, game: str | None, league: str | None,
                    live: bool, visible: bool) -> None:
        """One score-bug read the screen loop already made (bug None = nothing readable). No-op when off."""
        if not self.enabled:
            return
        clock = clock_seconds(getattr(bug, "clock", "") or "") if bug is not None else None
        period = (getattr(bug, "period", "") or "") if bug is not None else ""
        if target not in self.reads and len(self.reads) >= TARGETS_KEPT:
            self.reads.pop(next(iter(self.reads)))
        self.reads.setdefault(target, deque(maxlen=RING_LEN)).append((float(t), clock, period))
        gap = self._gap.get(target)
        if gap is not None and (float(t) - gap["last"] > knob(self.cfg, "max_read_gap_s", 30.0) or not live):
            self._close_gap(target, gap, gap["last"])   # the reads stopped, or the game ended: the stretch ends at the last read
            gap = None
        if bug is None:
            if live and gap is None:
                self._gap[target] = {"start": float(t), "last": float(t), "game": game, "league": league,
                                     "visible": bool(visible)}
            elif gap is not None:
                gap["last"] = float(t)
            return
        if gap is not None:
            self._close_gap(target, gap, float(t))

    def _close_gap(self, target: str, gap: dict, end: float) -> None:
        """A no-bug stretch is over at `end`: one row if it ran long enough to be a break."""
        self._gap.pop(target, None)
        if gap["last"] - gap["start"] >= knob(self.cfg, "break_min_s", 20.0) and self.root is not None:
            _append(self.root / "break_on_picture.jsonl", {
                "type": "break_on_picture", "start": round(gap["start"], 2), "end": round(end, 2),
                "dur_s": round(end - gap["start"], 1), "game": gap["game"], "league": gap["league"],
                "app": app_of(target), "visible": gap["visible"], "bug": False})

    def bug_state(self, target: str | None, at: float) -> dict:
        """What the picture said about the clock at `at`: {bug_clock_running: bool | None, bug_read_age_s}."""
        out: dict = {"bug_clock_running": None, "bug_read_age_s": None}
        rs = [r for r in self.reads.get(target or "", ()) if r[1] is not None and r[0] <= at]
        if not rs:
            return out
        t1, c1, p1 = rs[-1]
        out["bug_read_age_s"] = round(at - t1, 1)
        if at - t1 > knob(self.cfg, "stale_s", 10.0):
            return out
        lo, hi = knob(self.cfg, "pair_window_s", 20.0), knob(self.cfg, "min_gap_s", 1.5)
        prev = [r for r in rs[:-1] if t1 - lo <= r[0] <= t1 - hi and r[2] == p1]
        if prev:
            out["bug_clock_running"] = prev[-1][1] != c1
        return out


def cut_fields(labels: CheapLabels | None, t: dict) -> dict:
    """The extra fields for a supervisor_cuts row: the bug clock at the leave and at the entry."""
    if labels is None or not labels.enabled:
        return {}
    out = {}
    if t.get("cut_at") is not None and t.get("from_target"):
        s = labels.bug_state(t["from_target"], float(t["cut_at"]))
        out.update(leave_bug_clock_running=s["bug_clock_running"], leave_bug_read_age_s=s["bug_read_age_s"])
    if t.get("picture_at") is not None and t.get("target"):
        s = labels.bug_state(t["target"], float(t["picture_at"]))
        out.update(enter_bug_clock_running=s["bug_clock_running"], enter_bug_read_age_s=s["bug_read_age_s"])
    return out


def outcome_row(t: dict, row: dict, grade: dict | None) -> dict:
    """One joined row for one live switch: the switches row (cmd/confirmed), its trace and its cut grade."""
    g = grade or {}
    enter = g.get("enter")
    return {
        "type": "switch_outcome", "switch_id": t.get("id"),
        "cmd_ts": row.get("cmd_ts"), "confirmed_ts": row.get("confirmed_ts"),
        "first_picture_ts": t.get("picture_at"), "cut_at": t.get("cut_at"),
        "landed_dead": None if enter in (None, "unknown") else enter == "dead",
        "landed_play_s": g.get("enter_before_play_s"),
        "leave": g.get("leave"), "leave_why": g.get("leave_why"), "enter": enter, "enter_why": g.get("enter_why"),
        "kind": t.get("kind"), "path": t.get("path"), "action": t.get("action"), "forced": bool(t.get("forced")),
        "ok": bool(t.get("ok")), "dry_run": bool(t.get("dry_run")),
        "from": t.get("from"), "to": t.get("to"),
        "from_app": app_of(t.get("from_target")), "to_app": app_of(t.get("target")),
        "dead_air_s": (t.get("spans") or {}).get("dead_air"),
    }


def write_outcome(path: Path, t: dict, row: dict, grade: dict | None) -> None:
    _append(path, outcome_row(t, row, grade))


__all__ = ["CheapLabels", "cut_fields", "is_on", "outcome_row", "write_outcome"]
