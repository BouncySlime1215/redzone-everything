"""scripts/noggin_selfplay.py + the Kaggle factory notebook: self-play shards are deterministic, chunked,
resumable after a cut, and never see the locked/eval nights."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("pyarrow")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import noggin_selfplay as SP  # noqa: E402
import train_noggin_pick as T  # noqa: E402
from redzone.lab.backtest import Replay  # noqa: E402
from redzone.models import League  # noqa: E402
from redzone.noggin import pick as PK  # noqa: E402


def _rep(gid, t0):
    return Replay(League("nba"), gid, {}, [{}] * 3, [t0, t0 + 60, t0 + 120], "A @ B")


@pytest.fixture
def fake(monkeypatch):
    nights = {f"2025010{i}": [_rep(f"g{i}{j}", 1.736e9 + i * 86400 + j * 900) for j in range(3)] for i in range(5)}
    seen = []
    monkeypatch.setattr(T, "season_nights", lambda league, raw: nights)
    monkeypatch.setattr(SP, "_pack", lambda reps, lag, T_: (seen.append(len(reps)), None, None, None, None)[1:])
    monkeypatch.setattr(T, "labels", lambda n, steps, tab: (None, None))
    monkeypatch.setattr(T, "rows", lambda X, tgt, every: (np.ones((4, len(PK.PICK_FEATURES))), np.zeros(4)))
    monkeypatch.setattr(SP, "CHUNK", 3)
    return nights, seen


def test_shards_chunk_and_resume_after_a_cut(fake, tmp_path, monkeypatch):
    nights, seen = fake
    info = SP.build("nba", tmp_path / "raw.parquet", 2025, tmp_path, 2, 7, 1, budget_s=-1)   # cut at once
    assert info["complete"] is False and info["parts"] == 0 and info["rows"] == 0
    clock = iter(range(10_000))
    monkeypatch.setattr(SP.time, "time", lambda: next(clock))
    info = SP.build("nba", tmp_path / "raw.parquet", 2025, tmp_path, 2, 7, 1, budget_s=8)      # some parts
    assert 0 < info["parts"] < info["parts_total"] and not info["complete"]
    first = sorted(p.name for p in (tmp_path / "nba" / "2025").glob("part-*.parquet"))
    monkeypatch.setattr(SP.time, "time", __import__("time").time)
    info = SP.build("nba", tmp_path / "raw.parquet", 2025, tmp_path, 2, 7, 1)
    assert info["complete"] and info["slates"] == info["slates_total"] == 5 * 3      # 5 real + 10 synthetic
    assert info["parts_total"] == 5 and info["new_parts"] == 5 - len(first)
    t = pd.concat(pd.read_parquet(p) for p in sorted((tmp_path / "nba" / "2025").glob("part-*.parquet")))
    assert len(t) == info["rows"] == 15 * 4 and t.slate.nunique() == 15
    assert set(t.synthetic) == {0, 1} and set(t.lag[t.synthetic == 1]) <= set(np.float16(SP.LAGS))
    assert SP.build("nba", tmp_path / "raw.parquet", 2025, tmp_path, 2, 7, 1) == json.loads(
        (tmp_path / "nba" / "2025.json").read_text())                                  # done: not rebuilt


def test_synthetic_slates_are_seeded_and_stay_in_the_season():
    nights = {f"2025010{i}": [_rep(f"g{i}{j}", 1.736e9 + i * 86400 + j * 900) for j in range(4)] for i in range(3)}
    a = [(s, [r.gid for r in reps], lag) for s, reps, lag in SP.synthetic_slates(nights, 3, np.random.default_rng([7, 2025]))]
    b = [(s, [r.gid for r in reps], lag) for s, reps, lag in SP.synthetic_slates(nights, 3, np.random.default_rng([7, 2025]))]
    assert a == b and len(a) == 9
    pool = {r.gid for reps in nights.values() for r in reps}
    for sid, gids, lag in a:
        assert {g.split("~")[0] for g in gids} <= pool and lag in SP.LAGS


def test_notebook_converts_and_packs(tmp_path):
    import tarfile

    import pack_factory_code as P
    assert P.main([str(tmp_path)]) == 0
    nb = json.loads((tmp_path / "kaggle_noggin_factory.ipynb").read_text())
    assert nb["cells"][0]["cell_type"] == "markdown" and sum(c["cell_type"] == "code" for c in nb["cells"]) >= 4
    names = tarfile.open(tmp_path / "rz_factory_code.tar.gz").getnames()
    assert "rz/scripts/noggin_selfplay.py" in names and "rz/redzone/noggin/factory.py" in names
    assert not any(n.endswith((".db", ".env", ".pyc")) or "__pycache__" in n for n in names)


def test_espn_nights_loader_for_nhl(tmp_path):
    """[NHL self-play] factory espn_game records -> whole nights of Replays, eval window dropped."""
    import gzip

    from redzone.noggin import factory as F

    def game(gid, t0, matchup):
        plays = [{"id": str(i), "wallclock": f"2025-01-0{t0}T0{i}:00:00Z", "type": {"text": "Shot"},
                  "period": {"number": 1}, "clock": {"displayValue": "10:00"}} for i in range(1, 5)]
        return {"id": gid, "season_type": 2, "plays": plays, "matchup": matchup}
    p = tmp_path / "espn_2025.jsonl.gz"
    with gzip.open(p, "wt") as f:
        for g in (game("1", 3, "WSH @ NYR"), game("2", 3, "BOS @ TOR"), game("3", 4, "A @ B")):
            f.write(json.dumps(g) + "\n")
    nights = T.season_nights("nhl", p)
    assert list(nights) and all(len(v) >= 2 for v in nights.values())   # the lone game's night is dropped
    reps = next(iter(nights.values()))
    assert {r.matchup for r in reps} == {"WSH @ NYR", "BOS @ TOR"} and all(r.wall == sorted(r.wall) for r in reps)
    assert all(n < F.EVAL_FIRST_NIGHT["nhl"] for n in nights)
    hdr = {"header": {"id": "9", "season": {"type": 2}, "competitions": [{"competitors": [
        {"homeAway": "home", "team": {"abbreviation": "WSH"}}, {"homeAway": "away", "team": {"abbreviation": "PIT"}}]}]},
        "plays": [{"id": "1", "wallclock": "2025-01-03T01:00:00Z"}]}
    assert F.espn_game(hdr)["matchup"] == "PIT @ WSH"
