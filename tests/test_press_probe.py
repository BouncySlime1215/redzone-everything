import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def test_pick_warm_is_the_ready_standby_and_cold_is_another_live_game():
    import press_probe as pp
    st = {"now": {"game_id": "a"}, "standby": {"game_id": "b", "ready": True},
          "games": [{"id": g, "phase": ph} for g, ph in (("a", "live"), ("b", "live"), ("c", "live"), ("d", "pre"))]}
    assert pp.pick(st, "warm", None) == ("b", "warm")
    assert pp.pick(st, "cold", None) == ("c", "cold")
    assert pp.pick(st, "cold", "c") is None and pp.pick(st, "any", "c") == ("b", "warm")
    st["standby"]["ready"] = False
    assert pp.pick(st, "warm", None) is None and pp.pick(st, "any", None) == ("c", "cold")
