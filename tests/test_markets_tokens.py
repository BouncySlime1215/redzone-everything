import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "markets"))

import tokens as T  # noqa: E402


def _tick(ts, p, **kw):
    return {"source": "polymarket", "league": "nba", "game_key": "nba:20251101:BOS@WAS", "market_id": "m1",
            "market_kind": "moneyline", "phase": kw.pop("phase", "live"), "ts": ts, "p_home": p,
            "line": None, "volume": kw.pop("volume", 1.0), "tag": T.TAG, **kw}


def test_flat_minutes_are_dropped_jumps_kept():
    ticks = [_tick(i * 60, 0.50 + (0.001 * i)) for i in range(4)] + [_tick(240, 0.60)]
    evs = T.emit(ticks)
    assert [e["ts"] for e in evs] == [0, 240]          # first tick, then the 10-pt jump
    assert abs(evs[1]["jump"] - (0.60 - 0.50)) < 1e-9
    assert evs[1]["volume"] == 4.0                       # volume accumulates over dropped ticks


def test_heartbeat_and_phase_change():
    ticks = [_tick(0, 0.5, phase="pregame"), _tick(60, 0.5, phase="live"), _tick(400, 0.505)]
    assert [e["ts"] for e in T.emit(ticks)] == [0, 60, 400]


def test_tokens_in_block_and_width():
    ev = T.emit([_tick(0, 0.73)])[0]
    toks = T.tokens(ev)
    assert len(toks) == T.TOKENS_PER_EVENT
    assert all(0 <= t < T.BLOCK_SIZE for t in toks)
    assert T.tokens({**ev, "jump": 0.2})[3] > T.tokens({**ev, "jump": -0.2})[3]


def test_run_counts_new_info_and_tag(tmp_path):
    d = tmp_path / "in" / "polymarket" / "nba"
    d.mkdir(parents=True)
    (d / "20251101.jsonl").write_text("\n".join(json.dumps(_tick(i * 60, 0.5 + 0.03 * i)) for i in range(5)))
    c = T.run(tmp_path / "in", tmp_path / "out")
    assert c["ticks"] == 5 and c["ticks_missing_tag"] == 0
    assert c["new_info_tokens"] == 5 * T.TOKENS_PER_EVENT and c["derived_tokens"] == 0
