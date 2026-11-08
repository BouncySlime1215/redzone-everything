import pytest

from redzone import fsm
from redzone.fsm import ANY, IllegalTransition, MachineMap, Spec

DOOR = Spec("test_door", owner="tests", doc="a door",
            states={"shut": "closed", "open": "open", "gone": "removed"},
            edges={"shut": ("open",), "open": ("shut",), "gone": (), },
            initial="shut")
LOST = Spec("test_lost", owner="tests", doc="any-edge", keyed=True,
            states={"idle": "", "busy": "", "lost": ""},
            edges={"idle": ("busy",), "busy": ("idle", "lost"), "lost": (ANY,)}, initial="idle")


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_moves_records_history_and_age():
    c = Clock()
    m = DOOR.new(clock=c)
    assert m.is_("shut") and m.since == 100
    c.t = 105
    assert m.to("open", why="knock") is True
    assert m.state == "open" and m.why == "knock" and m.age(110) == 5
    assert list(m.history) == [(105, "shut", "open", "knock")]


def test_same_state_keeps_since_but_updates_why():
    c = Clock()
    m = DOOR.new(clock=c)
    c.t = 200
    assert m.to("shut", why="still") is False
    assert m.since == 100 and m.why == "still" and not m.history


def test_illegal_raises_when_strict():
    m = DOOR.new()
    with pytest.raises(IllegalTransition):
        m.to("gone", why="x")


def test_illegal_logs_and_moves_in_production(monkeypatch, caplog):
    monkeypatch.setattr(fsm, "STRICT", False)
    m = DOOR.new()
    m.to("gone", why="x")
    m.to("shut", why="reset") if False else None
    assert m.state == "gone" and m.illegal == 1
    assert "not declared" in caplog.text
    assert m.view()["illegal"] == 1


def test_reset_skips_edge_check():
    m = DOOR.new()
    m.reset("gone", why="restore")
    assert m.state == "gone" and m.history[-1][3] == "restore"


def test_unknown_state_is_a_bug_even_in_production(monkeypatch):
    monkeypatch.setattr(fsm, "STRICT", False)
    with pytest.raises(ValueError):
        DOOR.new().to("ajar")


def test_spec_rejects_unknown_states():
    with pytest.raises(ValueError):
        Spec("test_bad", owner="t", doc="", states={"a": ""}, edges={"a": ("b",)}, initial="a")


def test_any_edge_and_keyed_map():
    mm = MachineMap(LOST)
    mm["x"].to("busy", why="w")
    mm["x"].to("lost", why="w")
    mm["x"].to("busy", why="w")  # lost -> ANY
    assert mm.state("x") == "busy" and mm.state("y") == "idle"
    assert mm.in_state("busy") == ["x"]
    mm["z"]
    mm.prune()
    assert "z" not in mm and "x" in mm


def test_observer_errors_never_break_a_move():
    def boom(*a):
        raise RuntimeError("observer")
    m = DOOR.new(on_change=boom)
    assert m.to("open", why="w") and m.state == "open"


def test_snapshot_all_lists_live_machines_and_skips_idle_keyed():
    m = DOOR.new()
    mm = MachineMap(LOST)
    mm["idle_one"]
    names = [(v["machine"], v.get("key")) for v in fsm.snapshot_all()]
    assert ("test_door", None) in names
    assert ("test_lost", "idle_one") not in names
    del m


def test_every_declared_spec_is_well_formed():
    import redzone.machines as machines
    for name, spec in machines.ALL.items():
        assert spec.owner and spec.doc, name
        assert set(spec.edges) <= set(spec.states), name
        for s, meaning in spec.states.items():
            assert isinstance(meaning, str), (name, s)


def test_a_move_without_why_fails_in_tests():
    with pytest.raises(ValueError):
        DOOR.new().to("open")


def test_a_move_without_why_is_labelled_in_production(monkeypatch):
    monkeypatch.setattr(fsm, "STRICT", False)
    m = DOOR.new()
    m.to("open")
    assert m.why == "unspecified"


def test_transitions_are_saved_and_survive_a_restart(tmp_path):
    p = tmp_path / "transitions.jsonl"
    fsm.log_transitions(p, commit="abc123")
    try:
        m = DOOR.new()
        m.to("open", why="knock")
        mm = MachineMap(LOST)
        mm["k"].to("busy", why="work")
    finally:
        fsm.stop_logging()
    rows = fsm.read_transitions(p)
    assert [r["new"] for r in rows] == ["open", "busy"]
    assert rows[0] == {"ts": rows[0]["ts"], "machine": "test_door", "old": "shut", "new": "open",
                       "why": "knock", "commit": "abc123"}
    assert rows[1]["key"] == "k"
    assert fsm.read_transitions(p, "test_lost") == [rows[1]]


def test_transition_log_rotates(tmp_path):
    p = tmp_path / "t.jsonl"
    lg = fsm.log_transitions(p)
    lg.max_bytes = 200
    try:
        m = DOOR.new()
        for i in range(20):
            m.to("open" if m.is_("shut") else "shut", why=f"flip {i}")
    finally:
        fsm.stop_logging()
    assert (tmp_path / "t.jsonl.1").exists()
    assert fsm.read_transitions(p, limit=500)[-1]["why"] == "flip 19"
