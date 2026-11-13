"""One arm on one real night: the show's own replay, the frozen grader's counts, the scorecard and the raw per-jump log.

    loaded = load_night("20261021:4")                 # NIGHT[:GAMES][+nflI]: a dev night, its slate of GAMES games
    card, raw = run_night(make_arm("planner"), loaded, minutes=0, seed=0)

The episode is `park_replay.build` + `park_replay.drive` exactly as scripts/park_replay.py runs it (fake sticks, fake
clock), with the arm's hook installed before the first tick. `+nflI` adds the i-th one-game NFL dev night time-shifted
onto the slate (park_replay's --nfl: the football + NBA mix). The card's numbers come from park_replay's own `result`,
`graded` (the FROZEN cutgrade.grade), `seen_card` and `picture_grade`, plus high-leverage mid-play leaves counted with
the frozen grading.dead_at and leverage.high_leverage. The raw log holds every cut, the screen timeline and every
graded item shown, so recompute.py can recount the card without running the show.
Why: WORKPLAN v2 F-04: a verdict is only as good as its per-night numbers and the logs that let anyone recount them.
"""
from __future__ import annotations

import asyncio
import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from redzone.lab.gym2 import env as gym

ARMEVAL_SPEC = re.compile(r"^(\d{8})(?::(\d+))?(?:\+nfl(\d+))?$")
ARMEVAL_MIX_CLASS = (({"nba"}, "nba_only"), ({"nba", "cbb"}, "nba_college"))


def parse_spec(spec: str, games: int = 4) -> tuple[str, int, int]:
    """'20261021' | '20261021:3' | '20261021:4+nfl0' -> (night, games, nfl index or -1)."""
    m = ARMEVAL_SPEC.match(str(spec).strip())
    if not m:
        raise ValueError(f"night spec {spec!r}: want NIGHT[:GAMES][+nflI], like 20261021 or 20261021:4+nfl0")
    return m.group(1), int(m.group(2) or games), int(m.group(3)) if m.group(3) is not None else -1


def mix_class(leagues) -> str:
    """The L105 mix a slate belongs to: nba_only, nba_college, football_nba, or other."""
    s = {str(x) for x in leagues}
    for want, name in ARMEVAL_MIX_CLASS:
        if s == want:
            return name
    if "nba" in s and s & {"nfl", "cfb"}:
        return "football_nba"
    return "other"


def census_mix(census: Path | None, night: str) -> str | None:
    """The census's peak mix on that real night (models/mix-census-v1/nights.csv, universe 'owner'), or None."""
    if census is None:
        return None
    day = f"{night[:4]}-{night[4:6]}-{night[6:]}"
    with open(census, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("night") == day and row.get("universe") == "owner":
                return row.get("peak_mix") or ""
    return None


@dataclass
class Loaded:
    """A night ready to replay: its recorded games (rs), the slate, and the archive file behind each game."""
    spec: str
    night: str
    games: int
    nfl: int
    rs: list
    slate: list
    files: list = field(default_factory=list)          # one (relative name, path) per game, nfl last
    extra_nights: list = field(default_factory=list)   # the nfl game's own night (it is fenced like any other)


def load_night(spec: str, games: int = 4, archive: Path | None = None) -> Loaded:
    """A dev night through park_replay.load_replay (it refuses non-dev nights), and the files behind it."""
    pr = gym.replay_module()
    night, n, nfl = parse_spec(spec, games)
    rs, slate = pr.load_replay(night, n, "nba")
    root = Path(archive) if archive is not None else pr.ROOT / "data/archive"
    files = [(f"nba/summary_{r.gid}.json", root / "nba" / f"summary_{r.gid}.json") for r in rs]
    extra = []
    if nfl >= 0:
        from redzone.lab import splits
        d = root / "nfl"
        fn = pr.nfl_nights()[nfl]
        gid = splits.archive_nights(d)[fn][0]
        files.append((f"nfl/summary_{gid}.json", d / f"summary_{gid}.json"))
        extra.append(fn)
    return Loaded(str(spec), night, n, nfl, list(rs), list(slate), files, extra)


def _runs(pairs) -> list:
    """[(t, value)] sorted by t -> [[t0, t1, value]] runs of consecutive seconds with one value (t1 inclusive)."""
    out: list = []
    for t, v in pairs:
        if out and out[-1][2] == v and out[-1][1] == t - 1:
            out[-1][1] = t
        else:
            out.append([t, t, v])
    return out


def hl_leaves_mid_play(rs: list, base: int, cuts: list) -> int:
    """Leaves made while play was live (frozen grading.dead_at) at a high-leverage moment (frozen leverage.high_leverage)."""
    from redzone.grading import dead_at, idx
    from redzone.lab.leverage import high_leverage
    n = 0
    for g1, pi, _g2, _pj in cuts:
        r, st = rs[g1], base + pi
        if not (r.wall[0] <= st <= r.wall[-1]) or dead_at(r.league, r.wall, r.plays, st)[0]:
            continue
        i = idx(r.wall, st)
        n += int(i >= 0 and high_leverage(r.league, r.plays[i]))
    return n


def _ratio(a, b, nd: int = 4):
    return round(a / b, nd) if b else None


def run_night(arm, loaded: Loaded, *, minutes: float = 0.0, seed: int = 0, census: Path | None = None) -> tuple[dict, dict]:
    """(the night's scorecard, its raw log). Deterministic: the same arm, night, minutes and seed give the same bytes."""
    pr = gym.replay_module()
    rec = gym.Recorder()
    b = pr.build(loaded.night, loaded.games, seed=seed, minutes=minutes, loaded=(loaded.rs, loaded.slate), nfl=loaded.nfl,
                 say=rec.say, emit=rec.emit, **arm.replay_kw())
    show = b.show
    arm.attach(show, loaded.night)
    asyncio.run(b.clock.run(pr.drive(show, b.clock, b.rows, b.a, b.b)))
    digest = gym.digest(gym.decision_list(show, rec))
    row, sc, j = pr.result(show), pr.seen_card(show), show.judge
    rs, base = show.replays, show.night.base
    leagues = [str(r.league.value) for r in rs]
    a = show.night.span[0]
    end = (show.last_t + 1) if show.last_t is not None else a
    hl_mid = hl_leaves_mid_play(rs, base, j.cuts)
    wn, wok = row["warrant_n"], row["warranted"]
    football = any(r.league.base.value == "nfl" for r in rs)
    pic = pr.picture_grade(show) if football else None
    big_total, big_seen = sc["big_total"], sc["big_seen_live"]
    card = {
        "night": loaded.spec, "games": [r.gid for r in rs], "leagues": leagues, "mix": mix_class(leagues),
        "census_mix": census_mix(census, loaded.night), "minutes": row["minutes"], "decisions_sha256": digest,
        "rule1": {"joins_mid_play": row["grader_entries_mid_play"], "joins_mid_play_strict": row["grader_entries_mid_play_strict"],
                  "leaves_mid_play": row["grader_leaves_mid_play"], "hl_leaves_mid_play": hl_mid,
                  "joins": row["grader_entries"], "leaves": row["grader_leaves"],
                  "judge_mid_entry": row["mid_entry"], "judge_mid_leave": row["mid_leave"], "judge_hl_leave": row["hl_leave"],
                  "joins_after_miss": row["joins_after_miss"]},
        "value": {"live_points": round(j.v2, 3), "live_share": row["v2_seen"] if row["v2_seen"] != "" else None,
                  "catchup_points": None, "catchup_note": "no catch-up engine in the replay yet (E-01)"},
        "big": {"seen_live": big_seen, "total": big_total, "must_sees_missed": big_total - big_seen},
        "freshness": {"worst_unseen_s": sc["worst_unseen_s"], "mean_unseen_s": sc["mean_unseen_s"],
                      "worst_unseen_live_s": sc["worst_unseen_live_s"]},
        "pace": {"jumps": len(j.jumps), "flips": len(j.cuts), "jumps_per_min": row["jumps_per_min"], "peak_1m": row["peak_1m"],
                 "warrant_n": wn, "warranted": wok, "warranted_share": _ratio(wok, wn)},
        "catchup": {"coverage": None, "lateness_s": None, "note": "no catch-up engine in the replay yet (E-01)"},
        "yes_rate": {"value": None, "note": "no show-time labels yet (C-track); the planner's own yes_rate is not Nick's"},
        "cost": {"planner_searches": show.pl.searches, "show_seconds": end - a,
                 "note": "deterministic proxies; CPU seconds are in the run's timing.json, outside the verdict"},
        "picture": pic,
    }
    games = []
    for g, r in enumerate(rs):
        x = j.A[g]
        name, path = loaded.files[g] if g < len(loaded.files) else (f"{r.league.value}/summary_{r.gid}.json", None)
        lo, hi = max(a, x.a0), min(end - 1, x.a1)
        live = _runs((t, 1) for t in range(lo, hi + 1) if not x.dead[min(x.n - 1, t)])
        from redzone.lab.fence import file_sha256
        games.append({"g": g, "gid": r.gid, "league": str(r.league.value), "file": name,
                      "sha256": file_sha256(path) if path is not None else None, "a0": x.a0, "a1": x.a1,
                      "live_runs": [[s, e] for s, e, _ in live], "big": sorted(i for i in x.mom if a <= i < end)})
    shown = sorted([g, ti, float(j.A[g].mom.get(ti, 0.0)), max(0.0, float(j.A[g].v2.get(ti, 0.0)))]
                   for g, ti in j.shown)
    raw = {
        "header": {"night": loaded.spec, "arm": arm.name, "seed": seed, "minutes_arg": minutes, "base": base,
                   "span": [a, end], "minutes": row["minutes"], "games": games, "decisions_sha256": digest,
                   "v2_total": float(show.night.v2_total)},
        "cuts": [{"n": n, "t": f[0], "from": c[0], "leave_s": c[1], "to": c[2], "join_s": c[3], "judge_bad": f[2]}
                 for n, (c, f) in enumerate(zip(j.cuts, j.flips))],
        "jumps": list(j.jumps),
        "warrants": [None if w is None else bool(w[0]) for w in show.warrants],
        "screen": _runs(sorted(j.tl.items())),
        "shown": shown,
    }
    return card, raw
