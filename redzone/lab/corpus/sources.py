"""Read games from the B-01 history folder and from the recorded archive, and keep fenced nights out before anything is tokenised.

Why: B-02 reads three kinds of local files (B-01 packed ESPN shards or per-game files, hoopR and wehoop season parquet files, the nightly
archive of ESPN summaries) and must apply the leak fence (F-09) before a play is tokenised or counted. The fence is a gate object each
adapter asks before it opens a file or converts a game: the archive adapter knows a summary's night from the scoreboards, so a fenced
night's summary file is never opened; a history shard line or a parquet game has to be parsed to learn its night, is dropped at once
and only tallied.

Folders are read-only here. Roots may be symlinks (a worktree links the Mac's data/archive); symlinks, hidden files and names that
look like secrets inside a root are skipped, like redzone/lab/datamanifest.py does. No network, no writes.

    history root   shards/espn_<league>_<season>.jsonl.gz   (else espn/<league>/<season>/<id>.json.gz)
                   datasets/<hoopr_nba|hoopr_mbb|wehoop_wbb>/play_by_play_<season>.parquet
    archive root   <league>/scoreboard_<YYYYMMDD>.json and <league>/summary_<id>.json
"""
from __future__ import annotations

import gzip
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from redzone.lab import fence
from redzone.lab.corpus.records import CORPUS_PROVIDERS, GameRec, night_class, season_of
from scripts.history.leagues import LEAGUES as CORPUS_LEAGUE_TABLE
from scripts.history.leagues import night_of

CORPUS_DATASET_KEYS = {"hoopr_nba": ("hoopr", "nba"), "hoopr_mbb": ("hoopr", "cbb"), "wehoop_wbb": ("wehoop", "wcbb")}
CORPUS_HOOPR_COLUMNS = ("id", "sequence_number", "type_text", "text", "period_number", "clock_display_value", "wallclock",
                        "scoring_play", "score_value", "home_score", "away_score", "game_id", "home_team_abbrev", "away_team_abbrev",
                        "game_date")
_SHARD_NAME = re.compile(r"^espn_([a-z0-9]+)_(\d{4})\.jsonl\.gz$")
_GAME_NAME = re.compile(r"^(\d{4,15})\.json\.gz$")
_DATASET_NAME = re.compile(r"^play_by_play_(\d{4})\.parquet$")
_BOARD_NAME = re.compile(r"^scoreboard_(\d{8})\.json$")
_SUMMARY_NAME = re.compile(r"^summary_(\d{4,15})\.json$")
_SECRETISH = re.compile(r"(\.env($|\.)|token|secret|passw|credential|oauth|api[_-]?key|cookie|id_rsa|\.pem$|\.key$)", re.I)


class FenceGate:
    """The one place that says whether a game may enter the corpus. `hold_tuning` lets tuning-window nights through (they end up in
    shards flagged train_ok=false); locked, fresh and undated nights never pass. Refusals are tallied per league and verdict."""

    def __init__(self, hold_tuning: bool = False, count: bool = True) -> None:
        self.hold_tuning = hold_tuning
        self.count = count
        self.refused: dict[tuple[str, str], int] = {}

    def admit(self, league: str, night: str | None, exact: bool = True) -> bool:
        klass = night_class(league, night, exact)
        if klass == "clear" or (klass == "tuning" and self.hold_tuning):
            return True
        if self.count:
            self.refused[(league, klass)] = self.refused.get((league, klass), 0) + 1
        return False

    def tally(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for (lg, klass), n in sorted(self.refused.items()):
            out.setdefault(lg, {})[klass] = n
        return out


@dataclass(frozen=True)
class SourceUnit:
    """One file (or one folder of per-game files) that holds one league-season from one provider."""
    provider: str
    origin: str          # history-shard, history-games, dataset or archive
    league: str
    season: int
    path: str            # relative to the source root, always with forward slashes
    root: str            # the source root's real path (kept out of every manifest)

    @property
    def name(self) -> str:
        return f"{self.origin}:{self.path}"


def _entries(path: str) -> tuple[list[os.DirEntry], list[os.DirEntry]]:
    """(directories, regular files) of a folder, sorted; symlinks, hidden and secret-looking names are left out."""
    dirs, files = [], []
    if not os.path.isdir(path):
        return dirs, files
    with os.scandir(path) as it:
        for e in sorted(it, key=lambda x: x.name):
            if e.is_symlink() or e.name.startswith(".") or _SECRETISH.search(e.name):
                continue
            if e.is_dir(follow_symlinks=False):
                dirs.append(e)
            elif e.is_file(follow_symlinks=False):
                files.append(e)
    return dirs, files


def _gid(x: object) -> str:
    s = str(x).strip()
    return s[:-2] if s.endswith(".0") else s


def _abbr(x: object) -> str:
    return x if isinstance(x, str) and x else "?"


def _rel(root: str, path: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


# --- the B-01 history folder ---------------------------------------------------------------------------------------

def _record_to_game(rec: dict, unit: SourceUnit, ref: str) -> GameRec | None:
    plays = rec.get("plays")
    if not isinstance(plays, list) or not rec.get("id"):
        return None
    night = str(rec.get("night") or "").strip() or None
    exact = night is not None
    if night is None:
        for p in plays:
            if isinstance(p, dict) and p.get("wallclock"):
                night, exact = night_of(p["wallclock"]), True
                break
    if night is None:
        d = str(rec.get("date") or "").replace("-", "")[:8]
        night, exact = (d if len(d) == 8 and d.isdigit() else None), False
    home = (rec.get("home") or {}).get("abbr") if isinstance(rec.get("home"), dict) else None
    away = (rec.get("away") or {}).get("abbr") if isinstance(rec.get("away"), dict) else None
    if not home or not away:
        a, _, h = str(rec.get("matchup") or "? @ ?").partition(" @ ")
        home, away = home or h or "?", away or a or "?"
    try:
        season = int(rec.get("season") or unit.season)
    except (TypeError, ValueError):
        season = unit.season
    return GameRec(unit.provider, unit.origin, unit.league, season, _gid(rec["id"]), night, exact, str(home), str(away),
                   [p for p in plays if isinstance(p, dict)], ref)


class HistorySource:
    """The B-01 history folder: packed ESPN shards or per-game files, plus hoopR and wehoop season files."""

    def __init__(self, root: str | Path) -> None:
        self.root = os.path.realpath(root)

    def units(self, leagues: set[str] | None = None, seasons: set[int] | None = None) -> list[SourceUnit]:
        out: list[SourceUnit] = []
        packed: set[tuple[str, int]] = set()
        _, files = _entries(os.path.join(self.root, "shards"))
        for e in files:
            m = _SHARD_NAME.match(e.name)
            if m and m.group(1) in CORPUS_LEAGUE_TABLE:
                lg, season = m.group(1), int(m.group(2))
                packed.add((lg, season))
                out.append(SourceUnit("espn", "history-shard", lg, season, _rel(self.root, e.path), self.root))
        for lg_dir in _entries(os.path.join(self.root, "espn"))[0]:
            if lg_dir.name not in CORPUS_LEAGUE_TABLE:
                continue
            for sd in _entries(lg_dir.path)[0]:
                if sd.name.isdigit() and (lg_dir.name, int(sd.name)) not in packed and any(
                        _GAME_NAME.match(f.name) for f in _entries(sd.path)[1]):
                    out.append(SourceUnit("espn", "history-games", lg_dir.name, int(sd.name), _rel(self.root, sd.path), self.root))
        for key_dir in _entries(os.path.join(self.root, "datasets"))[0]:
            if key_dir.name not in CORPUS_DATASET_KEYS:
                continue
            provider, lg = CORPUS_DATASET_KEYS[key_dir.name]
            for e in _entries(key_dir.path)[1]:
                m = _DATASET_NAME.match(e.name)
                if m:
                    out.append(SourceUnit(provider, "dataset", lg, int(m.group(1)), _rel(self.root, e.path), self.root))
        keep = [u for u in out if u.provider in CORPUS_PROVIDERS and (not leagues or u.league in leagues)
                and (not seasons or u.season in seasons)]
        return sorted(keep, key=lambda u: (u.league, u.season, u.provider, u.origin, u.path))

    def games(self, unit: SourceUnit, gate: FenceGate, reads: dict[str, str] | None = None) -> Iterator[GameRec]:
        """Games of one unit that the gate admits. `reads` (optional) receives {relative path: sha256} of every file opened."""
        full = os.path.join(unit.root, *unit.path.split("/"))
        if unit.origin == "history-shard":
            yield from self._shard(unit, full, gate, reads)
        elif unit.origin == "history-games":
            for e in _entries(full)[1]:
                if _GAME_NAME.match(e.name):
                    yield from self._one_file(unit, e.path, gate, reads)
        else:
            yield from self._dataset(unit, full, gate, reads)

    def _shard(self, unit: SourceUnit, full: str, gate: FenceGate, reads: dict[str, str] | None) -> Iterator[GameRec]:
        if reads is not None:
            reads[unit.path] = fence.file_sha256(Path(full))
        with gzip.open(full, "rt", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                g = _record_to_game(rec, unit, unit.path) if isinstance(rec, dict) else None
                if g is not None and gate.admit(g.league, g.night, g.night_exact):
                    yield g

    def _one_file(self, unit: SourceUnit, path: str, gate: FenceGate, reads: dict[str, str] | None) -> Iterator[GameRec]:
        rel = _rel(unit.root, path)
        if reads is not None:
            reads[rel] = fence.file_sha256(Path(path))
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                rec = json.loads(f.read())
        except (OSError, ValueError, EOFError):
            return
        g = _record_to_game(rec, unit, rel) if isinstance(rec, dict) else None
        if g is not None and gate.admit(g.league, g.night, g.night_exact):
            yield g

    def _dataset(self, unit: SourceUnit, full: str, gate: FenceGate, reads: dict[str, str] | None) -> Iterator[GameRec]:
        import pandas as pd
        import pyarrow.parquet as pq

        from redzone.noggin import factory as F
        if reads is not None:
            reads[unit.path] = fence.file_sha256(Path(full))
        have = set(pq.read_schema(full).names)
        df = pd.read_parquet(full, columns=[c for c in CORPUS_HOOPR_COLUMNS if c in have])
        for c in CORPUS_HOOPR_COLUMNS:
            if c not in df.columns:
                df[c] = None
        df["sq"] = pd.to_numeric(df.sequence_number, errors="coerce")
        for gid, g in df.sort_values(["game_id", "sq"], kind="stable").groupby("game_id", sort=False):
            night, exact = None, True
            stamped = [w for w in g.wallclock.tolist() if isinstance(w, str) and w]
            if stamped:
                night = night_of(stamped[0])
            if night is None:
                d = g.game_date.iloc[0]
                night, exact = (None if d is None or d != d else pd.Timestamp(d).strftime("%Y%m%d")), False
            if not gate.admit(unit.league, night, exact):
                continue
            yield GameRec(unit.provider, unit.origin, unit.league, unit.season, _gid(gid), night, exact,
                          _abbr(g.home_team_abbrev.iloc[0]), _abbr(g.away_team_abbrev.iloc[0]), F._plays_basketball(g), unit.path)


# --- the recorded archive -------------------------------------------------------------------------------------------

class ArchiveSource:
    """data/archive: nightly ESPN scoreboards and summaries. The night of a summary is the first scoreboard that lists its game."""

    def __init__(self, root: str | Path, nights: set[str] | None = None) -> None:
        self.root = os.path.realpath(root)
        self.nights = set(nights) if nights else None       # only these nights, when given
        self._nights: dict[str, dict[str, str]] = {}

    def _game_nights(self, league: str) -> dict[str, str]:
        if league not in self._nights:
            found: dict[str, str] = {}
            for e in _entries(os.path.join(self.root, league))[1]:
                m = _BOARD_NAME.match(e.name)
                if not m:
                    continue
                try:
                    with open(e.path, "rb") as f:
                        events = json.load(f).get("events", [])
                except (OSError, ValueError, AttributeError):
                    continue
                for ev in events:
                    if isinstance(ev, dict) and ev.get("id"):
                        found.setdefault(_gid(ev["id"]), m.group(1))
            self._nights[league] = found
        return self._nights[league]

    def units(self, leagues: set[str] | None = None, seasons: set[int] | None = None) -> list[SourceUnit]:
        """One unit per league and season label; summaries whose game is on no scoreboard form the season-0 unit (undated), left out
        when a night list was given."""
        out: list[SourceUnit] = []
        for d in _entries(self.root)[0]:
            lg = d.name
            if lg not in CORPUS_LEAGUE_TABLE or (leagues and lg not in leagues):
                continue
            found: set[int] = set()
            nmap = self._game_nights(lg)
            for e in _entries(d.path)[1]:
                m = _SUMMARY_NAME.match(e.name)
                if not m:
                    continue
                n = nmap.get(m.group(1))
                if n is None:
                    if not self.nights:
                        found.add(0)
                elif not self.nights or n in self.nights:
                    found.add(season_of(lg, n))
            out += [SourceUnit("espn", "archive", lg, s, lg, self.root) for s in sorted(found) if not seasons or s in seasons]
        return out

    def games(self, unit: SourceUnit, gate: FenceGate, reads: dict[str, str] | None = None) -> Iterator[GameRec]:
        from redzone.models import League
        from redzone.sources import espn
        lg = unit.league
        nmap = self._game_nights(lg)
        for e in _entries(os.path.join(self.root, lg))[1]:
            m = _SUMMARY_NAME.match(e.name)
            if not m:
                continue
            gid = m.group(1)
            n = nmap.get(gid)
            if n is None:
                if unit.season != 0 or self.nights:
                    continue
            elif unit.season == 0 or season_of(lg, n) != unit.season or (self.nights and n not in self.nights):
                continue
            if not gate.admit(lg, n, True):             # judged from the scoreboard name; the summary file stays closed
                continue
            rel = _rel(self.root, e.path)
            if reads is not None:
                reads[rel] = fence.file_sha256(Path(e.path))
            try:
                with open(e.path, "rb") as f:
                    summary = json.load(f)
                comp = (summary.get("header", {}).get("competitions") or [{}])[0]
                if not (comp.get("status", {}).get("type", {}) or {}).get("completed"):
                    continue
                sides = {c.get("homeAway"): (c.get("team") or {}).get("abbreviation") or "?" for c in comp.get("competitors") or []}
                plays = [p for p in espn.flatten_plays(League(lg), summary) if isinstance(p, dict)]
            except (OSError, ValueError, AttributeError, TypeError):
                continue
            yield GameRec("espn", "archive", lg, unit.season, gid, n, True, sides.get("home", "?"), sides.get("away", "?"), plays, rel)

