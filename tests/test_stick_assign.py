"""Per-stick hidden-stick model (redzone/lab/stick_assign.py, standby.ShadowStick sticks=...)."""
from redzone import standby
from redzone.lab.stick_assign import PRETUNE_P50_S, multipliers


def test_multipliers_keep_the_mean_tune_and_order_the_sticks():
    m = multipliers()
    assert m["4k_plus"] < 1 < m["4k_2023"] and abs(sum(m.values()) / 2 - 1) < 0.01
    assert m == multipliers(PRETUNE_P50_S)


def test_the_hidden_stick_swaps_every_switch_and_tunes_at_its_own_speed():
    s = standby.ShadowStick(app_s={"youtubetv": 5.0}, sticks={"fast": 0.5, "slow": 1.5}, first_hidden="fast")
    s.preload("youtubetv:a", None, 0.0)
    assert s.ready_at == 2.5                                     # the fast stick is hidden
    assert s.switch("youtubetv:a", None, "youtubetv:b", 10.0)    # hit, TV now on "fast"
    assert s.hidden == "slow" and s.ready_at == 10.0 + 7.5       # the slow one re-tunes the game we left
    assert not s.switch("youtubetv:b", None, "youtubetv:a", 12.0)   # still loading on the slow stick
    assert s.by_stick == {"fast": {"switches": 1, "hits": 1, "loading": 0},
                          "slow": {"switches": 1, "hits": 0, "loading": 1}}
    assert s.hidden == "fast" and s.card()["by_stick"] == s.by_stick


def test_one_speed_is_the_old_model():
    s = standby.ShadowStick(app_s={"youtubetv": 5.0})
    assert s.hidden is None and s.tune_for("youtubetv:a") == 5.0 and "by_stick" not in s.card()
