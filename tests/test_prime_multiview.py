"""Prime NBA multiview (redzone/tuners/prime_multiview.py + PrimeMultiviewTuner), driven against a
fake Prime player whose accessibility tree draws the main tile big and the side tiles small. No
real stick, no network; team labels are made-up placeholders."""
import asyncio
import json
from pathlib import Path

import pytest

from redzone import control as ctl
from redzone.channels import ChannelMap
from redzone.tuners import prime_multiview as pmv
from redzone.tuners import tuners as T
from redzone.tuners.adb import RunResult, Stick

A, B, C, D = "prime:gA", "prime:gB", "prime:gC", "prime:gD"
NAMES = {A: ["Aaa", "Alphas"], B: ["Bbb", "Betas"], C: ["Ccc", "Gammas"], D: ["Ddd", "Deltas"]}
KEYS = {"version": 1, "open": ["up", "ok"],
        "add": {"open": ["right", "ok"], "next": "down", "select": "ok", "slot": "append", "picker_readable": True},
        "promote": {"0>1": ["right", "ok"], "0>2": ["right", "down", "ok"]},
        "after": "swap", "focus_check": "uiautomator", "timings": {"promote_s": {"0>1": [0.8, 0.9, 1.1]}}}


def node(text, x0, y0, x1, y1, focused=False, focusable=False):
    return (f'<node text="{text}" content-desc="" focused="{str(focused).lower()}" '
            f'focusable="{str(focusable).lower()}" bounds="[{x0},{y0}][{x1},{y1}]" />')


class FakePrime(Stick):
    """Prime's multiview: tiles[0] is main (big, left); the rest are stacked small on the right.
    Promote keys follow KEYS with the swap model. `deaf` = keys do nothing (unverifiable switch)."""

    def __init__(self, host, tiles=(A, B, C), deaf=False, readable=True):
        super().__init__(host, name=host, os="Fire OS 8")
        self.state = "connected"
        self.packages = {"prime": "com.amazon.firebat"}
        self.tiles, self.deaf, self.readable = list(tiles), deaf, readable
        self.keys: list[str] = []
        self.picker: list[str] | None = None
        self.picker_focus = 0
        self.offered = [D]

    async def key(self, *codes):
        self.keys.extend(codes)
        await asyncio.sleep(0.06)               # a real key press takes a moment
        if self.deaf:
            return RunResult(True, "", 0.01)
        seq = [c.removeprefix("KEYCODE_DPAD_").removeprefix("KEYCODE_").lower() for c in codes]
        if self.picker is not None:
            for k in seq:
                if k == "down":
                    self.picker_focus += 1
                elif k == "back":
                    self.picker = None
                elif k == "center":
                    self.tiles.append(self.picker[self.picker_focus])
                    self.picker = None
            return RunResult(True, "", 0.01)
        if seq == ["right", "center"] and len(self.tiles) > 1:
            self.tiles[0], self.tiles[1] = self.tiles[1], self.tiles[0]
        elif seq == ["right", "down", "center"] and len(self.tiles) > 2:
            self.tiles[0], self.tiles[2] = self.tiles[2], self.tiles[0]
        elif seq == ["right", "center"] or seq == ["up", "center"]:
            pass
        return RunResult(True, "", 0.01)

    def xml(self) -> str:
        if self.picker is not None:
            rows = "".join(node(f"{NAMES[t][1]} at Home LIVE", 0, 100 * i, 500, 100 * i + 90,
                                focused=i == self.picker_focus, focusable=True) for i, t in enumerate(self.picker))
            return f"<hierarchy>{rows}</hierarchy>"
        big = node(NAMES[self.tiles[0]][1], 0, 0, 1400, 800)
        small = "".join(node(NAMES[t][1], 1400, 300 * i, 1920, 300 * i + 290) for i, t in enumerate(self.tiles[1:]))
        return f"<hierarchy>{big}{small}</hierarchy>"

    async def run(self, *args, timeout=8.0):
        if "uiautomator dump" in " ".join(args):
            return RunResult(True, self.xml() if self.readable else "ERROR: could not get idle state", 0.01)
        return RunResult(True, "", 0.01)

    async def shell(self, cmd, timeout=8.0):
        return RunResult(True, "", 0.01)

    async def playback(self, package=None):
        return {"state": "playing", "pkg": "com.amazon.firebat", "position": 1000, "updated": 1000}


def tuner(keys=KEYS, tiles=(A, B, C), host="p:5555"):
    t = T.PrimeMultiviewTuner(pmv.KeyMap.from_dict(keys), verify_s=0.5, poll_s=0.0)
    t.enabled = True
    t.names = lambda target: NAMES.get(target, [])
    t.sessions[host] = pmv.Session(host, list(tiles), 0)
    return t


# --- the key map ---------------------------------------------------------------------------------

def test_key_map_round_trips_and_rejects_typos(tmp_path):
    km = pmv.KeyMap.from_dict(KEYS)
    assert km.can_switch and km.can_build and km.path(0, 2) == ["right", "down", "ok"]
    assert km.tile_cost() == 0.9
    assert pmv.KeyMap.from_dict(km.to_dict()).to_dict() == km.to_dict()
    bad = tmp_path / "k.json"
    bad.write_text(json.dumps({**KEYS, "promote": {"0>1": ["rigth", "ok"]}}))
    assert pmv.load_keys(bad) is None          # a typo never becomes a key press
    assert pmv.load_keys(tmp_path / "missing.json") is None


def test_no_focus_check_means_no_switching():
    assert not pmv.KeyMap.from_dict({**KEYS, "focus_check": "none"}).can_switch


# --- reading the screen ----------------------------------------------------------------------------

def test_main_tile_is_the_biggest_label():
    xml = FakePrime("x").xml()
    assert pmv.main_tile(pmv.parse_nodes(xml), {t: NAMES[t] for t in (A, B, C)}) == A


def test_main_tile_unknown_when_dump_unreadable_or_tied():
    assert pmv.main_tile(pmv.parse_nodes("ERROR: could not get idle state"), NAMES) is None
    tie = f"<hierarchy>{node('Alphas', 0, 0, 10, 10)}{node('Betas', 0, 0, 10, 10)}</hierarchy>"
    assert pmv.main_tile(pmv.parse_nodes(tie), NAMES) is None


# --- which games go in -----------------------------------------------------------------------------

def cand(t, score, teams=("AAA", "BBB"), league="nba"):
    return pmv.TileCandidate(t, league, teams, score)


def test_plan_takes_top_three_eligible_and_sends_the_rest_to_the_other_stick():
    pool = [cand(A, 50), cand(B, 80, ("XXX", "YYY")), cand(C, 70), cand(D, 60),
            cand("prime:nhl1", 99, league="nhl"), cand("youtubetv:espn", 99)]
    tiles, others = pmv.plan_tiles(pool, exclude=["nba:XXX"])
    assert tiles == [C, D, A]                  # B has an excluded team; NHL and YTTV never join
    assert set(others) == {B, "prime:nhl1"}


def test_pin_goes_first_and_open_tiles_keep_their_slots():
    pool = [cand(A, 50, ("PIN", "OOO")), cand(B, 80), cand(C, 70), cand(D, 60)]
    assert pmv.plan_tiles(pool, pin=["nba:pin"])[0] == [A, B, C]
    assert pmv.plan_tiles(pool, current=[C, D])[0] == [C, D, B]


def test_rebuild_only_when_a_top_game_is_missing():
    s = pmv.Session("h", [A, B, C])
    assert not pmv.needs_rebuild(s, [B, A, D])
    assert pmv.needs_rebuild(s, [D, A, B])
    assert pmv.needs_rebuild(None, [A, B])
    assert not pmv.needs_rebuild(None, [A])


# --- the tile switch -------------------------------------------------------------------------------

async def test_switch_presses_the_probed_path_and_verifies():
    t, s = tuner(), FakePrime("p:5555")
    r = await t.switch(s, C)
    assert r.ok and r.path == "tile" and r.took < pmv.GATE_UNDER_S
    assert s.keys == ["KEYCODE_DPAD_RIGHT", "KEYCODE_DPAD_DOWN", "KEYCODE_DPAD_CENTER"]
    assert t.sessions["p:5555"].tiles == [C, B, A]          # swap model, same as the fake
    r = await t.switch(s, A)
    assert r.ok and t.sessions["p:5555"].main_target == A
    assert all(x["ok"] for x in t.results)


async def test_unverified_switch_fails_and_forgets_the_session():
    t, s = tuner(), FakePrime("p:5555", deaf=True)
    r = await t.switch(s, B)
    assert not r.ok and r.screen_changed is True
    assert "p:5555" not in t.sessions                       # next switch is a normal tune
    t2, s2 = tuner(), FakePrime("p:5555", readable=False)
    assert not (await t2.switch(s2, B)).ok


async def test_closed_cut_window_presses_nothing():
    t, s = tuner(), FakePrime("p:5555")
    r = await t.switch(s, B, may_cut=lambda: False)
    assert not r.ok and r.screen_changed is False and s.keys == []


async def test_tune_uses_the_tile_and_falls_back_to_a_deep_link(monkeypatch):
    linked = []

    async def deep_link(self, stick, ref, target, **kw):
        linked.append(target)
        return T.TuneResult(True, target, 30.0, ["playing"], "", True)
    monkeypatch.setattr(T.PrimeTuner, "tune", deep_link)
    t, s = tuner(), FakePrime("p:5555")
    assert (await t.tune(s, "gB", B)).path == "tile" and linked == []
    r = await t.tune(s, "gD", D)                             # not a tile: normal tune
    assert r.ok and linked == [D] and "p:5555" not in t.sessions


async def test_off_it_is_plain_prime(monkeypatch):
    linked = []

    async def deep_link(self, stick, ref, target, **kw):
        linked.append(target)
        return T.TuneResult(True, target, 30.0, [], "", True)
    monkeypatch.setattr(T.PrimeTuner, "tune", deep_link)
    t, s = tuner(), FakePrime("p:5555")
    t.enabled = False
    assert not t.has_tile("p:5555", B)
    await t.tune(s, "gB", B)
    assert linked == [B] and s.keys == []


async def test_build_adds_picker_games_and_verifies():
    t, s = tuner(tiles=()), FakePrime("p:5555", tiles=(A,))
    t.sessions.clear()
    orig = s.key

    async def key(*codes):
        if list(codes) == ["KEYCODE_DPAD_RIGHT", "KEYCODE_DPAD_CENTER"] and s.picker is None and len(s.tiles) < 3:
            s.picker, s.picker_focus = list(s.offered), 0
            s.keys.extend(codes)
            return RunResult(True, "", 0.01)
        return await orig(*codes)
    s.key = key
    s.offered = [C, B]
    assert await t.build(s, [A, B])
    assert t.sessions["p:5555"].tiles == [A, B] and s.tiles == [A, B]


# --- the controller ------------------------------------------------------------------------------

@pytest.fixture
def ctl_mv(cfg, tmp_path, monkeypatch):
    cfg.update({"mode": {"dry_run": False}, "tuners": {"prime_multiview": True}})
    c = ctl.Controller(cfg, ChannelMap(tmp_path / "ch.json"))
    stick = FakePrime("p:5555")
    c.sticks = [stick]
    t = tuner()
    monkeypatch.setitem(ctl.TUNERS, "prime", t)
    return c, stick, t


def test_tile_on_screen_costs_about_a_second(ctl_mv):
    c, _, _ = ctl_mv
    assert c.deadline_cost(B) == 1.1 and c.deadline_cost(A) == 0.0
    assert c.deadline_cost(D) >= 8.0                          # not a tile: Prime's normal cost


def test_flag_off_keeps_prime_costs(ctl_mv):
    c, _, _ = ctl_mv
    c.cfg.update({"tuners": {"prime_multiview": False}})
    assert c.prime_mv is None and c.deadline_cost(B) >= 8.0


async def test_execute_switches_the_tile_at_a_safe_moment(ctl_mv):
    c, stick, t = ctl_mv
    c.on_target = A
    out = await c.execute(B, may_cut=lambda: True)
    assert out.ok and out.path == "prime_tile" and c.on_target == B
    assert c.latency.median("prime_tile") is not None and c.latency.median("prime") is None


# --- the product rule ----------------------------------------------------------------------------

def test_shipped_defaults_off_and_carry_no_personal_teams(cfg):
    assert cfg.get("tuners.prime_multiview") is False
    assert cfg.get("score.personal.multiview_exclude") == [] and cfg.get("score.personal.multiview_pin") == []
    root = Path(__file__).resolve().parent.parent
    code = "".join((root / p).read_text() for p in ("redzone/tuners/prime_multiview.py",
                                                     "scripts/prime_multiview_probe.py"))
    for abbr in {a for v in (cfg.defaults.get("teams") or {}).values() for a in v}:
        assert f'"{abbr}"' not in code and f"'{abbr}'" not in code and f":{abbr.lower()}" not in code.lower()


def test_gate():
    good = [{"ok": True, "took": 1.0}] * 19 + [{"ok": True, "took": 2.5}]
    assert pmv.gate(good, 0)["good_pct"] == 95.0 and pmv.gate(good, 0)["pass"]
    assert not pmv.gate(good, 1)["pass"] and not pmv.gate(good[:5], 0)["pass"]
    assert not pmv.gate(good, None)["pass"]


def test_probe_order_models_and_labels():
    import importlib.util
    p = Path(__file__).resolve().parent.parent / "scripts" / "prime_multiview_probe.py"
    spec = importlib.util.spec_from_file_location("pmv_probe", p)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    assert probe.predict("swap", ["a", "b", "c"], 0, 2) == (["c", "b", "a"], 0)
    assert probe.predict("rotate", ["a", "b", "c"], 0, 2) == (["c", "a", "b"], 0)
    assert probe.words("Gammas at Home LIVE") == ["Gammas", "Home"]
