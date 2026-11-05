"""two_stick.hand_back_s: the game you just left by hand keeps the hidden stick, so a tap back is a flip.

Nov 4 live: 16 phone presses to Prime ~2 min apart, 11 of them cold (~30 s tunes): the hidden stick
held a planner guess instead of the Prime game you had just left."""
import time

from redzone import standby
from redzone.config import Config
from redzone.decider.decider import Candidate
from redzone.engine import Engine
from redzone.scoring.engine import Scored
from redzone.models import League
from redzone.standby import HOME, MOMENT, NEXT


def cand(gid, score, target, pre=None):
    s = Scored(gid, League.NBA, score, score, score, 1.0, 0.0, f"reason {gid}", [], False, None, pre, 1.0, False)
    return Candidate(s, target)


def pool():
    return {"prime": cand("prime", 40, "prime:p1"), "hot": cand("hot", 90, "youtubetv:hot", pre="final minute"),
            "wsh": cand("wsh", 60, "youtubetv:wsh")}


def test_game_left_by_hand_outranks_a_guessed_moment():
    opts = standby.options(pool(), current="wsh", back_to="prime", hand_back="prime",
                           save_s=lambda t: 30.0 if t.startswith("prime:") else 5.0)
    assert (opts[0].game_id, opts[0].cls) == ("prime", HOME)
    # Without it, the "about to happen" guess takes the slot and the tap back to Prime is a ~30 s tune.
    opts = standby.options(pool(), current="wsh", back_to="prime",
                           save_s=lambda t: 30.0 if t.startswith("prime:") else 5.0)
    assert (opts[0].game_id, opts[0].cls) == ("hot", MOMENT)


def test_the_deciders_own_next_and_your_focus_game_still_come_first():
    opts = standby.options(pool(), current="wsh", hand_back="prime", want="hot")
    assert (opts[0].game_id, opts[0].cls) == ("hot", NEXT)
    opts = standby.options(pool(), current="hot", home="wsh", hand_back="prime")
    assert opts[0].game_id == "wsh" and [o.game_id for o in opts if o.cls == HOME] == ["wsh"]


def test_planner_keeps_the_kept_prime_game_instead_of_retuning(tmp_path):
    cfg = Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h")
    pl = standby.Planner(cfg)
    opts = standby.options(pool(), current="wsh", back_to="prime", hand_back="prime")
    assert pl.plan(opts, ("prime:p1", None)) is None          # already there: leave it
    assert pl.plan(opts, ("youtubetv:hot", None)).target == "prime:p1"   # at once, no hold_s wait


def test_engine_window(tmp_path):
    cfg = Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h")
    e = Engine(cfg)
    dec = e.decider
    dec.current, dec.stack = "wsh", ["prime"]
    e._hand_left = ("prime", time.time())
    assert e._hand_back() == "prime"                   # on by default (600 s)
    cfg.update({"two_stick": {"hand_back_s": 0}})
    assert e._hand_back() is None                      # 0 = off
    cfg.update({"two_stick": {"hand_back_s": 600}})
    e._hand_left = ("prime", time.time() - 601)
    assert e._hand_back() is None                      # window over
    e._hand_left = ("prime", time.time())
    dec.stack = ["other"]
    assert e._hand_back() is None                      # you've moved on: it's no longer the way back
    dec.stack, dec.current = ["wsh"], "prime"
    assert e._hand_back() is None                      # already back on it
