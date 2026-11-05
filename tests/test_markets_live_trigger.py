import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "markets"))

import poly_live_trigger as L  # noqa: E402

MK = {"tokH": {"invert": False, "game_key": "nfl:20261105:MIA@BUF"},
      "tokA": {"invert": True, "game_key": "nfl:20261105:NYJ@NE"}}


def _trade(asset, price, ts=1782753357257):
    return json.dumps({"event_type": "last_trade_price", "asset_id": asset, "price": str(price), "size": "10",
                       "side": "BUY", "timestamp": str(ts), "market": "0xabc"})


def test_jump_fires_once_then_cooldown():
    d = L.Detector()
    assert d.update("m", 0.0, 0.50) is None
    assert d.update("m", 5.0, 0.51) is None
    f = d.update("m", 10.0, 0.56)
    assert f and abs(f["delta"] - 0.06) < 1e-9 and f["window_s"] == 10.0
    assert d.update("m", 15.0, 0.62) is None          # cooldown


def test_slow_drift_does_not_fire():
    d = L.Detector()
    assert all(d.update("m", t * 20.0, 0.50 + 0.01 * t) is None for t in range(10))


def test_handle_invert_trigger_and_no_wallet():
    det = L.Detector()
    rows = L.handle(_trade("tokA", 0.40), MK, det, 100.0) + L.handle(_trade("tokA", 0.30), MK, det, 110.0)
    assert [r["row"] for r in rows] == ["tick", "tick", "trigger"]
    assert rows[0]["p_home"] == 0.6 and rows[-1]["delta"] == 0.1          # away token inverted
    assert rows[0]["server_ts"] == 1782753357.257
    assert not any("transaction_hash" in r or "maker" in r for r in rows)
    assert L.handle("PONG", MK, det, 1.0) == [] and L.handle(_trade("other", 0.5), MK, det, 1.0) == []


def test_price_change_mid():
    ev = {"event_type": "price_change", "market": "0x", "timestamp": "1",
          "price_changes": [{"asset_id": "tokH", "price": "0.5", "size": "1", "side": "BUY",
                             "best_bid": "0.48", "best_ask": "0.52"}]}
    assert L.p_from_event(ev, "tokH") == 0.5
