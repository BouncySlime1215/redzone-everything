"""Training rows for the live game reasoner, distilled from the archive (no paid APIs).

For every archived game, every play: rebuild the state the app would have had right then (the
same espn.parse_summary + Scorer + brain path the live service runs), take its brief (facts +
text), and label it from what actually happened next on the wall clock:

  big_next   a scorecard big play (redzone.lab.sportsgrade.is_big) in the next H seconds
  crunch_next a scorecard crunch play in the next H seconds
  y          big_next or crunch_next, and not garbage time: "worth being on live right now"
  t_big      seconds to the next big play (inf if none before the game ends)
  t_break    seconds to the next real break (timeout, end of period, ...), for "how long to stay"
  y_out      [EXP-302] the lock label from REAL outcomes (outcome_label: a lead change/catch or a win
             probability swing in the next few minutes), plus its parts flip, wp_swing, close_finish.
             `y` is defined by the clock and margin the model sees (NBA clock-only AUC 0.996), so grade
             lock calls on y_out (Nick, Nov 5 1:50 AM).

Rows carry their night (ET date of the scoreboard that lists the game) so whole nights are held
out together: the head is cross-fitted by night (each night scored by a model that never saw it),
and the language model's test set is nights it never trained on.
"""
from __future__ import annotations

import json
import math
import zlib
from dataclasses import dataclass
from pathlib import Path

from redzone.config import Config
from redzone.lab import outcome_label, sportsgrade
from redzone.lab.backtest import Replay
from redzone.models import REAL_BREAKS, League, Phase
from redzone.reasoner import brief
from redzone.scoring.engine import Personal, Scorer
from redzone.sources import espn

HORIZON_S = {"nfl": 180.0, "nba": 120.0, "nhl": 180.0, "mlb": 300.0}


class _Clock:
    t = 0.0

    def __call__(self) -> float:
        return self.t


def nights_of(archive: Path) -> dict[str, str]:
    """game id -> night (YYYYMMDD of the scoreboard listing it; the first one wins)."""
    out: dict[str, str] = {}
    for sb in sorted(archive.glob("scoreboard_*.json")):
        try:
            evs = json.loads(sb.read_text()).get("events", [])
        except (OSError, ValueError):
            continue
        day = sb.stem.split("_")[-1]
        for e in evs:
            out.setdefault(str(e.get("id")), day)
    return out


def fold_of(night: str, k: int) -> int:
    return zlib.crc32(night.encode()) % k


@dataclass
class Row:
    night: str
    gid: str
    i: int
    facts: dict
    brief: str
    y: int
    big_next: int
    crunch_next: int
    t_big: float
    t_break: float
    next_text: str
    mine: bool
    garbage: bool
    crunch: bool
    late: bool
    matchup: str
    league: str
    in_break: bool
    y_out: int = 0
    flip: int = 0
    wp_swing: int = 0
    close_finish: int = 0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _plays_meta(lg: League, r: Replay):
    big, crunch, brk = [], [], []
    prev = None
    for p in r.plays:
        big.append(sportsgrade.is_big(lg, p, prev))
        crunch.append(sportsgrade.is_crunch(lg, p))
        brk.append(espn.stoppage_for(lg, p) in REAL_BREAKS)
        prev = p
    return big, crunch, brk


def game_rows(lg: League, r: Replay, night: str, cfg: Config, my_teams: set[str],
              every: int = 1) -> list[Row]:
    """One row per play (every `every`-th), in order; the scorer and brain see every state."""
    clock = _Clock()
    scorer = Scorer(cfg, clock=clock)
    me = Personal(my_teams={x: set(my_teams) for x in League})
    big, crunch, brk = _plays_meta(lg, r)
    out = outcome_label.label_game(lg, r.plays, r.wall)
    H = HORIZON_S[lg.base.value]
    n = len(r.plays)
    rows: list[Row] = []
    nxt_big = [math.inf] * (n + 1)
    nxt_brk = [math.inf] * (n + 1)
    for j in range(n - 1, -1, -1):
        nxt_big[j] = r.wall[j] if big[j] else nxt_big[j + 1]
        nxt_brk[j] = r.wall[j] if brk[j] else nxt_brk[j + 1]
    for i in range(n):
        g = r.state_at(i)
        if g is None or g.phase != Phase.LIVE:
            continue
        clock.t = r.wall[i]
        s = scorer.score(g, me)
        if i % every:
            continue
        w = r.wall[i]
        j = i + 1
        bn = cn = 0
        nt = ""
        while j < n and r.wall[j] <= w + H:
            if big[j] and not bn:
                bn = 1
                nt = (espn._get(r.plays[j], "type", "text") or "")[:40]
            cn = cn or int(crunch[j])
            j += 1
        tr = scorer.brain._track.get(g.game_id)
        reading = scorer.brain.read(g) if scorer.brain_on() else None
        ctx = brief.Context(mine=g.involves(my_teams), garbage=s.garbage, crunch=s.crunch, late=s.late)
        f = brief.facts(g, tr.row if tr else None, reading, ctx)
        y = int((bn or cn) and not s.garbage)
        rows.append(Row(night, r.gid, i, f, brief.text(g, f, ctx), y, bn, cn,
                        nxt_big[i + 1] - w if i + 1 <= n else math.inf,
                        nxt_brk[i + 1] - w if i + 1 <= n else math.inf, nt,
                        ctx.mine, s.garbage, s.crunch, s.late, g.matchup, lg.value,
                        g.stoppage in REAL_BREAKS, **out[i]))
    return rows


def league_rows(lg: League, archive: Path, cfg: Config, my_teams: set[str], every: int = 1,
                limit: int | None = None, progress=None) -> list[Row]:
    nights = nights_of(archive)
    out: list[Row] = []
    files = sorted(archive.glob("summary_*.json"))[:limit]
    for k, f in enumerate(files):
        gid = f.stem.split("_")[-1]
        night = nights.get(gid)
        if night is None:
            continue
        try:
            r = Replay.load(lg, f)
        except (OSError, ValueError):
            continue
        if r is None or len(r.plays) < 20:
            continue
        out.extend(game_rows(lg, r, night, cfg, my_teams, every))
        if progress:
            progress(k + 1, len(files), len(out))
    return out
