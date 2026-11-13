import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "markets"))

import market_attention as A  # noqa: E402


def test_minutes_and_games(tmp_path):
    d = tmp_path / "in" / "polymarket" / "nba"
    d.mkdir(parents=True)
    ps = [0.50, 0.50, 0.55, 0.56, 0.40]
    ticks = [{"source": "polymarket", "league": "nba", "game_key": "nba:1:A@B", "market_id": "m", "ts": 1000 + 60 * i,
              "p_home": p, "phase": "pregame" if i == 0 else "live"} for i, p in enumerate(ps)]
    (d / "1.jsonl").write_text("\n".join(json.dumps(t) for t in ticks))
    (d / "markets.jsonl").write_text(json.dumps({"game_key": "nba:1:A@B", "market_id": "m", "start_ts": 1060,
                                                 "volume_usd": 12345.0}))
    c = A.run(tmp_path / "in", tmp_path / "out")
    assert c == {"games": 1, "labeled_minutes": 5, "with_volume": 1, "tag": A.TAG}
    m = list(csv.DictReader(open(tmp_path / "out" / "market_minutes.csv")))
    assert m[2]["abs_dp_1m"] == "0.05" and m[4]["abs_dp_5m"] == "0.16" and m[1]["min_from_start"] == "0.0"
    g = list(csv.DictReader(open(tmp_path / "out" / "market_games.csv")))[0]
    assert g["volume_usd"] == "12345.0" and g["swings_3pt"] == "2" and g["live_range"] == "0.16"
    assert "p_home" not in m[0]                      # derived only: no price level written


def test_p_home_pre_takes_last_price_ten_minutes_before_start():
    ticks = [{"ts": 1000.0, "p_home": 0.40}, {"ts": 1300.0, "p_home": 0.45}, {"ts": 1500.0, "p_home": 0.60}]
    assert A.p_pre(ticks, 1900.0) == 0.45          # 1500 is only 400 s before the start
    assert A.p_pre(ticks, None) is None
    assert A.p_pre(ticks, 1000.0 + 25 * 3600) is None   # nothing within 24 h
