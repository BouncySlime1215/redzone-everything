"""SQLite log of everything the app decides and everything you tell it.

This is the training lab's raw material: every switch with three timestamps
(data event, command sent, playback confirmed), every 👍/👎, every calibration,
every screen-lab label, and the source race.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

DB = Path(__file__).resolve().parent.parent / "data" / "redzone.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (ts REAL, action TEXT, game TEXT, kind TEXT, state TEXT, gate TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS switches (ts REAL, frm TEXT, to_game TEXT, target TEXT, kind TEXT, reason TEXT,
    event_ts REAL, cmd_ts REAL, confirmed_ts REAL, ok INTEGER, dry_run INTEGER, detail TEXT);
CREATE TABLE IF NOT EXISTS feedback (ts REAL, game TEXT, vote INTEGER, note TEXT, context TEXT);
CREATE TABLE IF NOT EXISTS calibrations (ts REAL, target TEXT, sample REAL, mu REAL, sigma REAL);
CREATE TABLE IF NOT EXISTS labels (ts REAL, label TEXT, game TEXT, signals TEXT);
CREATE TABLE IF NOT EXISTS snapshots (ts REAL, game TEXT, league TEXT, score REAL, moment REAL, reason TEXT, state TEXT);
CREATE TABLE IF NOT EXISTS bets (id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL, game TEXT, kind TEXT,
    team TEXT, line REAL, side TEXT, note TEXT, active INTEGER DEFAULT 1);
CREATE INDEX IF NOT EXISTS idx_dec_ts ON decisions(ts);
CREATE INDEX IF NOT EXISTS idx_snap_ts ON snapshots(ts);
"""


class Store:
    def __init__(self, path: Path = DB):
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._open(path)
        except sqlite3.DatabaseError as e:
            # A database the app can't open (disk filled mid-write, a bad copy) must not stop the
            # service from starting: set it aside, untouched, and start a fresh one. Only for a file
            # that isn't a usable database: "locked" / "unable to open" (OperationalError) is a
            # healthy file we can't use right now, and is never moved.
            if isinstance(e, sqlite3.OperationalError):
                raise
            aside = _set_aside(path)
            log.error("store: %s can't be opened (%s); moved it to %s and started a fresh database",
                      path, e, aside)
            self._open(path)
        self._lock = threading.Lock()

    def _open(self, path: Path) -> None:
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        try:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)
        except sqlite3.DatabaseError:
            self._db.close()
            raise
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(calibrations)")}
        if "kind" not in cols:   # 'tap' ("I just saw it") | 'boundary' (a show change) | 'screen' (the score bug)
            self._db.execute("ALTER TABLE calibrations ADD COLUMN kind TEXT DEFAULT 'tap'")

    def _x(self, sql: str, args: tuple = ()):
        with self._lock:
            return self._db.execute(sql, args)

    def decision(self, d) -> None:
        self._x("INSERT INTO decisions VALUES (?,?,?,?,?,?,?)",
                (d.at, d.action, d.target, d.kind, d.state.value, d.gate, d.reason))

    def switch(self, **kw) -> int:
        cur = self._x("INSERT INTO switches VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
            kw.get("ts", time.time()), kw.get("frm"), kw.get("to_game"), kw.get("target"), kw.get("kind"),
            kw.get("reason"), kw.get("event_ts"), kw.get("cmd_ts"), kw.get("confirmed_ts"),
            int(bool(kw.get("ok"))), int(bool(kw.get("dry_run"))), json.dumps(kw.get("detail") or {})))
        return cur.lastrowid

    def feedback(self, game: str | None, vote: int, note: str = "", context: dict | None = None) -> None:
        self._x("INSERT INTO feedback VALUES (?,?,?,?,?)", (time.time(), game, vote, note, json.dumps(context or {})))

    def calibration(self, target: str, sample: float, mu: float, sigma: float, kind: str = "tap") -> None:
        self._x("INSERT INTO calibrations (ts, target, sample, mu, sigma, kind) VALUES (?,?,?,?,?,?)",
                (time.time(), target, sample, mu, sigma, kind))

    def calibrations(self, since: float = 0.0) -> list[tuple[float, str, float, str]]:
        """Every delay sample since `since`, oldest first: (ts, target, sample, kind), kind 'tap' for
        "I just saw it" and 'boundary' for a show change measured automatically."""
        return self._x("SELECT ts, target, sample, COALESCE(kind, 'tap') FROM calibrations WHERE ts >= ? "
                       "ORDER BY ts", (since,)).fetchall()

    def label(self, label: str, game: str | None, signals: dict) -> None:
        self._x("INSERT INTO labels VALUES (?,?,?,?)", (time.time(), label, game, json.dumps(signals)))

    def snapshot(self, rows: list[tuple]) -> None:
        with self._lock:
            self._db.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)", rows)

    def recent_switches(self, n: int = 50) -> list[dict]:
        cur = self._x("SELECT ts, frm, to_game, target, kind, reason, event_ts, cmd_ts, confirmed_ts, ok, dry_run, detail "
                      "FROM switches ORDER BY ts DESC LIMIT ?", (n,))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def switches_between(self, t0: float, t1: float) -> list[dict]:
        """Every switch with t0 <= ts < t1, oldest first (the supervisor's nightly report)."""
        cur = self._x("SELECT ts, frm, to_game, target, kind, reason, event_ts, cmd_ts, confirmed_ts, ok, dry_run, detail "
                      "FROM switches WHERE ts >= ? AND ts < ? ORDER BY ts", (t0, t1))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def last_snapshot_between(self, t0: float, t1: float) -> float | None:
        """When the last live-games snapshot in [t0, t1) was taken (the night report's "screen on until")."""
        row = self._x("SELECT MAX(ts) FROM snapshots WHERE ts >= ? AND ts < ?", (t0, t1)).fetchone()
        return float(row[0]) if row and row[0] is not None else None

    def checkpoint(self) -> None:
        """Fold the write-ahead log back into the database file so it can't grow without bound."""
        self._x("PRAGMA wal_checkpoint(TRUNCATE)")

    def taps(self, since: float = 0.0) -> list[dict]:
        """Phone v2 decision records, oldest first, for the phone page only (no training or grading path reads them: self-taught rule, Nov 4):
        {ts, game, tap: "up" | "down" | "clear" | "missed", reason, switch, guess}. Rows without
        note 'switch' or 'missed' (the old one-tap vote) are left out."""
        out = []
        for ts, game, vote, note, ctx in self._x(
                "SELECT ts, game, vote, note, context FROM feedback WHERE note IN ('switch', 'missed') AND ts >= ? "
                "ORDER BY ts", (since,)).fetchall():
            try:
                c = json.loads(ctx or "{}")
            except ValueError:
                c = {}
            tap = "missed" if note == "missed" else {1: "up", -1: "down"}.get(vote, "clear")
            out.append({"ts": ts, "game": game, "tap": tap, "reason": c.get("reason"),
                        "switch": c.get("switch"), "guess": c.get("guess")})
        return out

    def feedback_counts(self) -> dict:
        cur = self._x("SELECT vote, COUNT(*) FROM feedback GROUP BY vote")
        return {("up" if v > 0 else "down"): n for v, n in cur.fetchall()}

    # bets
    def add_bet(self, game: str, kind: str, team: str, line: float | None, side: str, note: str) -> int:
        cur = self._x("INSERT INTO bets (created, game, kind, team, line, side, note) VALUES (?,?,?,?,?,?,?)",
                      (time.time(), game, kind, team, line, side, note))
        return cur.lastrowid

    def bets(self, active_only: bool = True) -> list[dict]:
        cur = self._x("SELECT id, created, game, kind, team, line, side, note, active FROM bets"
                      + (" WHERE active=1" if active_only else "") + " ORDER BY created DESC")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def remove_bet(self, bet_id: int) -> None:
        self._x("UPDATE bets SET active=0 WHERE id=?", (bet_id,))

    def prune(self, keep_days: int = 60) -> None:
        cutoff = time.time() - keep_days * 86400
        for t in ("decisions", "snapshots"):
            self._x(f"DELETE FROM {t} WHERE ts < ?", (cutoff,))


def _set_aside(path: Path) -> Path:
    """Rename a database that can't be opened (and its -wal/-shm files) to *.corrupt-<time>. Never deletes."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    aside = path.with_name(f"{path.name}.corrupt-{stamp}")
    for suffix in ("", "-wal", "-shm"):
        src = path.with_name(path.name + suffix)
        if src.exists():
            src.replace(aside.with_name(aside.name + suffix))
    return aside
