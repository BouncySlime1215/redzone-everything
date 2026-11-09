"""Queue wiring (WP-12): behind queue.enabled the queue owns the hidden stick, replayed on a ShadowStick.

Why: ten code paths wrote to the hidden stick; these prove one owner per tick, that the other writers hold a named
lease the queue respects, and that the flag off changes nothing. No TV, no adb: every stick here is modeled.
"""
import random
import time
import types

import pytest

from redzone import standby
from redzone.config import Config
from redzone.decider.decider import Candidate, Decision, State
from redzone.engine import Engine
from redzone.models import League
from redzone.noggin import gamequeue as gq
from redzone.scoring.engine import Scored
from redzone.store import Store

GAMES = ("a", "b", "c", "d", "e", "f")


def target(gid):
    return f"youtubetv:ch{gid}"


def sc(gid, score, is_break=False):
    return Scored(gid, League.NFL, score, score, score, 1.0, 0.0, f"reason {gid}", [], is_break, None, None, 1.0)


def cands_of(scores):
    return {g: Candidate(sc(g, s), target(g)) for g, s in scores.items()}


class ShadowControl:
    """The controller as the queue sees it, backed by the modeled hidden stick. No flip method on purpose: a queue
    that tried to cut would raise."""
    two_stick, live_control, input_unknown, hidden_ready = True, True, False, True
    preload_why, on_target = "", None

    def __init__(self):
        self.t = 1000.0
        self.stick = standby.ShadowStick(tune_s=7, keep_s=2, flip_s=2)
        self.calls = []
        self.job = ""
        self.prime_plan = ()
        self.scrubbed = {}
        self.hidden = self.main = types.SimpleNamespace(scrubbed=self.scrubbed.get)
        self.latency = types.SimpleNamespace(median=lambda app: None)

    @property
    def standby(self):
        return self.stick.standby

    @property
    def standby_m(self):
        return types.SimpleNamespace(state="ready" if self.stick.target and self.t >= self.stick.ready_at else "loading")

    def stick_job(self):
        return self.job

    def can_tune(self, t):
        return bool(t)

    def slow_tune(self, t):
        return False

    def tune_estimate(self, t, stick=None):
        return self.stick.tune_for(t)

    def standby_ready(self, t):
        return bool(t) and self.stick.target == t and self.t >= self.stick.ready_at

    def preload(self, t, back_s=None, replay_id=None, *, user=False, urgent=False, rotate=False):
        self.calls.append((t, replay_id, user, urgent, rotate))
        self.stick.preload(t, replay_id, self.t)
        return True

    def drop_stuck_standby(self, *a):
        return False

    def buffer_standby(self, *a):
        return False

    def snap_parked(self, *a):
        return None


@pytest.fixture
def rig(tmp_path):
    def make(on=True):
        cfg = Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist")
        cfg.update({"mode": {"dry_run": False}, "queue": {"enabled": on}})
        e = Engine(cfg, Store(tmp_path / "rz.db"))
        e.control = ShadowControl()
        e.planner = standby.Planner(cfg, clock=lambda: e.control.t)
        e.decider.current = "a"
        e.where = {g: types.SimpleNamespace(target=target(g), label=g) for g in GAMES}
        e.owners = []
        real = e._plan_owner
        e._plan_owner = lambda state, why: (e.owners.append((state, why)), real(state, why))[1]
        return e
    return make


def tick(e, scores=None):
    e.control.t += 1
    e._preload_standby(cands_of(scores or {"a": 70, "b": 50, "c": 60}))


def want(e, gid):
    e.last_decision = Decision(e.control.t, "stay", "a", "x", State.WATCHING, want=gid)


def night(e, seed=7, ticks=600, script=True):
    """A seeded night: scores drift, flips happen, and (scripted) pins, wants, locks and leases come and go."""
    rng, c, moves = random.Random(seed), e.control, []
    scores = {g: rng.randint(30, 80) for g in GAMES}
    for i in range(ticks):
        c.t += 1
        for g in GAMES:
            scores[g] = max(0, min(100, scores[g] + rng.randint(-4, 4)))
        if i % 40 == 39:   # a switch lands: the stick we left holds the game we left
            to = rng.choice([g for g in GAMES if g != e.decider.current])
            c.stick.switch(target(to), None, target(e.decider.current), c.t)
            e.decider.current = to
        if script and i % 25 == 0:
            roll = rng.random()
            e.queue_pins[:] = [rng.choice(GAMES)] if roll < 0.3 else []
            e.decider.user.locked_game = rng.choice(GAMES) if 0.3 <= roll < 0.4 else None
            want(e, rng.choice(GAMES) if 0.4 <= roll < 0.7 else None)
            c.job = rng.choice(["verify", "refresh", "lock:scan", ""]) if roll >= 0.85 else ""
        before, calls = len(e.owners), len(c.calls)
        e._preload_standby(cands_of(scores))
        moves.append((len(e.owners) - before, len(c.calls) - calls))
    return moves


# ---- the exit check ---------------------------------------------------------------------------------------------


def test_replay_exactly_one_owner_per_tick_in_standby_m(rig):
    """Flag on, a whole seeded night of pins, locks, committed switches and leases: one standby_m owner every tick,
    at most one preload, and every owner a state the machine knows. ShadowControl has no flip: 0 cuts come from here."""
    e = rig()
    moves = night(e)
    assert all(owners == 1 for owners, _ in moves)
    assert all(preloads <= 1 for _, preloads in moves)
    assert sum(p for _, p in moves) > 5
    assert e.standby_m.state in {"idle", "peek", "rotation", "buffered", "planner", "queue"}


def test_replay_flag_off_matches_flag_on_with_nothing_above_the_planner(rig):
    """With no pin, lock, committed switch or lease the queue only watches: the same preloads as the flag off."""
    off, on = rig(on=False), rig(on=True)
    assert night(off, script=False) == night(on, script=False)
    assert off.control.calls == on.control.calls and on.control.calls
    assert on._game_queue is not None and off._game_queue is None


def test_flag_off_never_reaches_the_queue(rig, monkeypatch):
    e = rig(on=False)
    for name in ("_queue_plan", "_queue_standby", "_queue_refill", "_stick_lease"):
        monkeypatch.setattr(e, name, lambda *a, **k: pytest.fail(f"{name} ran with the flag off"))
    tick(e)
    assert e.action("pin", "b") == (False, "Unknown action pin")
    assert e.queue_pins == [] and "queue_pins" not in e._capture_user_state()


# ---- authority ----------------------------------------------------------------------------------------------------


def test_pin_takes_the_stick_as_your_own_word(rig):
    e = rig()
    assert e.action("pin", "b")[0]
    tick(e)
    assert e.control.calls == [(target("b"), None, True, False, True)]
    assert e.owners[-1][0] == "queue" and e.owners[-1][1].startswith("queue pin")
    assert e._game_queue["hidden"] is None and e._game_queue["want"] == "b"


def test_committed_next_beats_a_pin_and_a_lock_beats_both(rig):
    e = rig()
    e.queue_pins[:] = ["b"]
    want(e, "c")
    tick(e)
    assert e.control.calls[-1][:3] == (target("c"), None, False)
    e.decider.user.locked_game = "d"
    tick(e, {"a": 70, "b": 50, "c": 60, "d": 40})
    assert e.control.calls[-1][:3] == (target("d"), None, True)


def test_a_held_game_is_not_tuned_again(rig):
    e = rig()
    e.queue_pins[:] = ["b"]
    for _ in range(30):
        tick(e)
    assert len(e.control.calls) == 1


def test_a_dropped_game_is_never_wanted(rig):
    e = rig()
    e.action("mute", "b")
    e.queue_pins[:] = ["b"]
    want(e, "b")
    tick(e)
    assert e.control.calls == [] or e.control.calls[0][0] != target("b")
    assert e._game_queue["want"] != "b"
    assert e.action("pin", "b") == (False, f"{e._name('b')} is dropped: un-drop it first")


def test_nothing_above_the_planner_rung_falls_back(rig):
    e = rig()
    tick(e)
    assert e.control.calls == [(target("c"), None, False, False, False)]   # standby.Planner's pick, called its way
    assert e.owners[-1][0] == "planner" and not e.owners[-1][1].startswith("queue")


def test_a_broken_queue_falls_back_to_the_planner(rig, monkeypatch):
    e = rig()
    e.queue_pins[:] = ["b"]
    monkeypatch.setattr(gq, "tick", lambda *a, **k: 1 / 0)
    tick(e)
    assert e.control.calls == [(target("c"), None, False, False, False)] and e._game_queue is None


# ---- leases -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name,job", [("plan_check", "verify"), ("refresh_left", "refresh"), ("lock:scan", "lock:scan")])
def test_a_named_lease_keeps_the_queue_off_the_stick(rig, name, job):
    e = rig()
    e.queue_pins[:] = ["b"]
    e.control.job = job
    tick(e)
    assert e.control.calls == [] and e.owners[-1] == ("idle", f"lease: {name}")


def test_sunday_ticket_scan_lease(rig):
    e = rig()
    e._st_scan_task = types.SimpleNamespace(done=lambda: False)
    want(e, "b")
    tick(e)
    assert e.control.calls == [] and e.owners[-1] == ("idle", "lease: sunday_ticket_scan")
    e._st_scan_task = types.SimpleNamespace(done=lambda: True)
    tick(e)
    assert e.control.calls[-1][0] == target("b")


def test_pregame_warmup_lease_ends_at_the_window(rig):
    e = rig()
    e.cfg.update({"warmup": {"pregame": True}})
    c = e.control
    c.stick.preload(target("c"), None, c.t)
    e.tonight.plan = {"games": [], "warmup": {"state": "ready", "target": target("c"),
                                              "window": time.time() + 60}}
    assert e._stick_lease() == "pregame_warmup"
    e.tonight.plan["warmup"]["window"] = time.time() - 1
    assert e._stick_lease() == ""


def test_prime_multiview_lease(rig):
    e = rig()
    e.cfg.update({"tuners": {"prime_multiview": True}})
    c = e.control
    c.stick.preload("prime:x", None, c.t)
    c.prime_plan, c.job = ["prime:x", "prime:y"], "preload"
    assert e._stick_lease() == "prime_multiview"


def test_peek_still_owns_the_stick(rig):
    e = rig()
    e.queue_pins[:] = ["b"]
    e.peek = ("c", target("c"), time.time() + 60)
    tick(e)
    assert e.control.calls == [] and e.owners[-1][0] == "peek"


# ---- the urgent refill ---------------------------------------------------------------------------------------------


def test_urgent_refill_is_queue_first(rig):
    e = rig()
    e.queue_pins[:] = ["b"]
    assert e._queue_refill(cands_of({"a": 70, "b": 50})) is True
    assert e.control.calls == [(target("b"), None, True, True, True)]


def test_urgent_refill_falls_back_and_respects_a_lease(rig):
    e = rig()
    cands = cands_of({"a": 70, "b": 50})
    assert e._queue_refill(cands) is False and e.control.calls == []
    e.queue_pins[:] = ["b"]
    e.control.job = "verify"
    assert e._queue_refill(cands) is True and e.control.calls == []


# ---- Pin and Move, the snapshot ------------------------------------------------------------------------------------


def test_pin_move_unpin(rig):
    e = rig()
    for g in ("b", "c", "d"):
        assert e.action("pin", g)[0]
    assert e.queue_pins == ["b", "c", "d"]
    e.action("move", "d", "up")
    assert e.queue_pins == ["b", "d", "c"]
    e.action("move", "b", "down")
    assert e.queue_pins == ["d", "b", "c"]
    e.action("move", "c", 0)
    assert e.queue_pins == ["c", "d", "b"]
    e.action("move", "c", 99)
    assert e.queue_pins == ["d", "b", "c"]
    assert e.action("move", "c", "sideways") == (False, "Move it where?")
    e.action("pin", "d", False)
    e.action("unpin", "b")
    assert e.queue_pins == ["c"]
    assert e.action("pin", None) == (False, "Pick a game")
    assert e._capture_user_state()["queue_pins"] == ["c"]


def test_the_top_pin_is_the_first_you_can_reach(rig):
    e = rig()
    e.queue_pins[:] = ["z", "b", "c"]   # z has no candidate
    tick(e)
    assert e.control.calls[-1][0] == target("b")
    assert e._game_queue["want"] == "b"


def test_a_finished_game_loses_its_pin(rig):
    from redzone.models import Phase
    e = rig()
    e.queue_pins[:] = ["b"]
    e.merger.games["b"] = types.SimpleNamespace(phase=Phase.POST)
    e._standby_options = lambda *a: []
    e._queue_plan(cands_of({"a": 70, "b": 50}))
    assert e.queue_pins == []


def test_snapshot_game_queue_only_with_the_flag(tmp_path):
    def snap(on):
        cfg = Config(user_path=tmp_path / f"{on}.yaml", history_dir=tmp_path / f"hist{on}")
        cfg.update({"queue": {"enabled": on}})
        return Engine(cfg, Store(tmp_path / f"{on}.db")).snapshot()
    assert "game_queue" not in snap(False)
    assert "game_queue" in snap(True)


def test_the_phone_sees_no_spoilers_and_r3_is_not_flippable(rig):
    e = rig()
    c = e.control
    c.stick.preload(target("b"), None, c.t)
    keep = {"a": 70, "b": 65, "c": 10}
    tick(e, keep)
    tick(e, keep)
    c.t += 30
    tick(e, keep)
    card = e._game_queue["hidden"]
    assert card["game"] == "b" and card["level"] == "R3" and card["flippable"] is False
    assert not {"tier", "join_in_s", "value"} & set(card)
    c.scrubbed[target("b")] = 70.0
    tick(e, keep)
    assert e._game_queue["hidden"]["level"] == "R4" and e._game_queue["hidden"]["flippable"] is True


def test_the_real_controller_answers_every_question_the_queue_asks(tmp_path, monkeypatch, caplog):
    """ShadowControl could hide a misnamed attribute, and _queue_plan swallows errors on purpose: run it on the real one."""
    import inspect

    from redzone.channels import ChannelMap
    from test_hot_standby import FakeStick

    cfg = Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist")
    cfg.update({"mode": {"dry_run": False}, "queue": {"enabled": True}})
    e = Engine(cfg, Store(tmp_path / "rz.db"))
    e.channels = ChannelMap(tmp_path / "channels.json")
    e.channels.codes.update({"fox": "BBBBBBBBBBB", "cbs": "CCCCCCCCCCC", "nbc": "DDDDDDDDDDD"})
    c = e.control
    c.channels, c.sticks, c.hdmi = e.channels, [FakeStick("a:5555"), FakeStick("b:5555")], {"a:5555": "HDMI1", "b:5555": "HDMI2"}
    c.tv.st_device = "explicit-test-display"
    assert c.two_stick
    sig, calls = inspect.signature(c.preload), []
    monkeypatch.setattr(c, "preload", lambda *a, **k: (sig.bind(*a, **k), calls.append((a, k)), True)[2])
    e.decider.current = "a"
    e.queue_pins[:] = ["b"]
    e._preload_standby({"a": Candidate(sc("a", 70), "youtubetv:fox"), "b": Candidate(sc("b", 50), "youtubetv:cbs")})
    assert "game queue plan failed" not in caplog.text
    assert e._game_queue["want"] == "b" and e._game_queue["up_next"][0]["reason"] == "pinned"
    assert calls == [(("youtubetv:cbs",), {"user": True, "urgent": False, "rotate": True})]
