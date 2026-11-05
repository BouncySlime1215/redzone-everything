"""[shadow] EXP-004: the candidate decider beside live, silent. Serves: EXP-004. Owner: lane 3."""
from __future__ import annotations

import json

from redzone.config import Config
from redzone.shadow import ReadOnlyDelays, Shadow, ShadowConfig


def test_the_profile_overrides_sit_on_top_of_todays_config_without_touching_it(tmp_path):
    cfg = Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h")
    sc = ShadowConfig(cfg, ["noggin.priority.enabled=true", "replay.crunch_block_mine_leagues=[nfl]",
                            "decider.entry.max_wait_s={}"])
    assert sc.get("noggin.priority.enabled") is True and sc.get("replay.crunch_block_mine_leagues") == ["nfl"]
    assert sc.get("decider.entry.max_wait_s") == {} and sc.get("no.such.key", 7) == 7
    assert cfg.get("noggin.priority.enabled") is not True            # live config untouched
    assert sc.get("two_stick.hold_s") == cfg.get("two_stick.hold_s")   # everything else is today's
    cfg.update({"two_stick": {"hold_s": 33}})                         # the phone edits live config...
    assert sc.get("two_stick.hold_s") == 33 and sc.get("noggin.priority.enabled") is True   # ...the shadow follows


def test_the_shadow_never_writes_the_live_delay_book():
    class Book:
        held = {"x": 1.0}

        def get(self, t):
            return ("delay", t)

        def set_behind(self, *a, **k):
            raise AssertionError("the shadow wrote live's delays")
    ro = ReadOnlyDelays(Book())
    ro.set_behind("youtubetv:espn", 90.0)
    assert ro.get("youtubetv:espn") == ("delay", "youtubetv:espn") and ro.held == {"x": 1.0}


def test_an_error_turns_the_shadow_off_for_the_night_and_says_so(tmp_path):
    cfg = Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h")
    cfg.update({"shadow": {"enabled": True}})
    sh = Shadow(cfg, ReadOnlyDelays(object()), tmp_path / "shadow.jsonl")
    sh.tick({"g": object()}, None, None, "stay")   # not a Candidate: the decider raises
    assert not sh.alive and sh.view()["alive"] is False
    sh.tick({}, None, None, "stay")               # off: nothing runs, nothing raises
    rows = [json.loads(x) for x in (tmp_path / "shadow.jsonl").read_text().splitlines()]
    assert rows == [{"ts": rows[0]["ts"], "action": "shadow_off", "reason": "error in decide"}]


def test_a_quiet_night_logs_only_the_changes_not_every_tick(tmp_path):
    from types import SimpleNamespace
    cfg = Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h")
    sh = Shadow(cfg, ReadOnlyDelays(object()), tmp_path / "shadow.jsonl")
    stay = SimpleNamespace(action="stay", target=None, kind="", reason="")
    sh.decider.decide = lambda *a, **k: stay
    sh.decider.current = "a"
    for live in ["b"] * 50 + ["a"] * 50:          # a seed-1 replay logged 17k rows like this before
        sh.tick({}, None, live, "stay")
    rows = [json.loads(x) for x in (tmp_path / "shadow.jsonl").read_text().splitlines()]
    assert [(r["on"], r["live_on"]) for r in rows] == [("a", "b"), ("a", "a")] and sh.calls == 100


def test_the_demo_engines_own_shadow_stick_is_not_mistaken_for_the_shadow_decider(tmp_path):
    # CI scorecard on #187: DemoEngine.shadow is a standby.ShadowStick, so Engine.decide must use its own name.
    from redzone.engine import Engine
    from redzone.store import Store
    e = Engine(Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h"), Store(tmp_path / "t.db"))
    e.shadow = object()   # what DemoEngine puts there
    assert e.shadow_decider is None
    e.decide()            # raised AttributeError: 'ShadowStick' object has no attribute 'tick'
