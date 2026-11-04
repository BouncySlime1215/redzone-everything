"""RAM/CPU line for the PR comment: the live decide() tick on a synthetic slate, startup, and peak RSS.

    python scripts/perf_probe.py                        # this checkout
    python scripts/perf_probe.py --root base --json p.json

Runs the real Engine (no network, no TV): builds it, loads N live games across leagues, then calls
decide() K times while the clocks run, so scoring + the decider + candidates all run every tick.
It measures the decision path, not the poll loop or the Mind. Numbers are only comparable between
runs on the same machine (CI's PR-vs-base pair).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import resource
import sys
import tempfile
import time
from pathlib import Path


async def probe(root: Path, n_games: int, n_ticks: int) -> dict:
    sys.path.insert(0, str(root.resolve()))
    t0 = time.perf_counter()
    from redzone.config import Config
    from redzone.engine import Engine
    from redzone.models import GameState, League, Phase, Team
    from redzone.store import Store
    tmp = Path(tempfile.mkdtemp(prefix="rz-perf-"))
    e = Engine(Config(user_path=tmp / "c.yaml", history_dir=tmp / "h"), Store(tmp / "db"))
    e.toast = lambda *x, **k: None
    startup_s = time.perf_counter() - t0
    lgs = [League.NFL, League.NBA, League.NHL, League.NFL]
    teams = [("WSH", "DAL"), ("BOS", "NYK"), ("CAR", "PIT"), ("KC", "BUF"), ("LAL", "GSW"),
             ("NYR", "TOR"), ("PHI", "NYG"), ("MIA", "DEN")]
    games = []
    for i in range(n_games):
        h, w = teams[i % len(teams)]
        games.append(GameState(league=lgs[i % len(lgs)], game_id=f"p{i}", home=Team(h, score=10 + i),
                               away=Team(w, score=12), phase=Phase.LIVE, period=4, clock_s=600.0))
    times = []
    for k in range(n_ticks):
        for i, g in enumerate(games):
            g.clock_s = max(0.0, 600.0 - k * 2 - i)
            g.home.score = 10 + i + (k // 40)
            e.merger.update("perf", g)
        t = time.perf_counter()
        e.decide()
        times.append((time.perf_counter() - t) * 1000)
        await asyncio.sleep(0)
    try:
        await e.close()
    except Exception:
        pass
    times.sort()
    q = lambda p: round(times[min(len(times) - 1, int(p * len(times)))], 2)   # noqa: E731
    ru = resource.getrusage(resource.RUSAGE_SELF)
    rss_mb = ru.ru_maxrss / (1024 * 1024) if sys.platform == "darwin" else ru.ru_maxrss / 1024
    return {"games": n_games, "ticks": n_ticks, "tick_ms_p50": q(.5), "tick_ms_p99": q(.99),
            "startup_s": round(startup_s, 2), "peak_rss_mb": round(rss_mb, 1),
            "cpu_s": round(ru.ru_utime + ru.ru_stime, 2)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--games", type=int, default=8)
    ap.add_argument("--ticks", type=int, default=300)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    out = asyncio.run(probe(a.root, a.games, a.ticks))
    print(json.dumps(out))
    if a.json:
        a.json.write_text(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
