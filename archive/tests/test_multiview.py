"""YouTube TV multiview switch path (redzone/tuners/multiview.py), driven against a fake 2x2
multiview app: tile planning, the cost model as SwitchPaths, and a driver that verifies every step
and never presses CENTER on a full-screen player."""
import pytest

from redzone.contracts.switching import KINDS, MULTIVIEW, deadline
from redzone.tuners import multiview as M

ESPN, FOX, CBS, NBC, ABC = (f"youtubetv:{c}" for c in ("espn", "fox", "cbs", "nbc", "abc"))


class FakeMultiview:
    """YouTube TV's multiview as the sticks would show it: a 2x2 grid with a focused tile; CENTER
    expands it, BACK returns to the grid. Full screen, the media session's subtitle names the
    channel; in the grid it names none (what the probe will check on the real app)."""

    def __init__(self, tiles, clock, key_s=0.1, expand_s=0.6):
        self.tiles, self.clock = list(tiles), clock
        self.focus, self.full = 0, None
        self.key_s, self.expand_s = key_s, expand_s
        self.pending_at = None
        self.keys: list[str] = []
        self.center_on_player = 0

    async def key(self, *codes):
        for c in codes:
            self.keys.append(c)
            self.clock.t += self.key_s
            name = c.removeprefix("KEYCODE_").lower()
            if self.full is not None:
                if name == "back":
                    self.full = None
                elif name == "dpad_center":
                    self.center_on_player += 1          # would pause / pop up the player controls
                continue
            r, col = divmod(self.focus, 2)
            if name == "dpad_right" and col == 0:
                self.focus += 1
            elif name == "dpad_left" and col == 1:
                self.focus -= 1
            elif name == "dpad_down" and r == 0:
                self.focus += 2
            elif name == "dpad_up" and r == 1:
                self.focus -= 2
            elif name == "dpad_center":
                self.full = self.tiles[self.focus]
                self.pending_at = self.clock.t + self.expand_s

    async def playback(self, pkg=None):
        self.clock.t += 0.05
        if self.full and self.clock.t >= (self.pending_at or 0):
            return {"state": "playing", "subtitle": f"Game, {self.full.split(':')[1].upper()}, null", "read_ok": True}
        return {"state": "playing", "subtitle": "", "read_ok": True}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def matches(target, report):
    sub = (report.get("subtitle") or "").lower()
    if not sub:
        return None
    return target.split(":")[1] in sub


async def no_sleep(_):
    return None


@pytest.fixture
def mv(monkeypatch):
    monkeypatch.setattr(M.asyncio, "sleep", no_sleep)
    clk = Clock()
    app = FakeMultiview([ESPN, FOX, CBS, NBC], clk)
    s = M.MultiviewSession("a:5555", (ESPN, FOX, CBS, NBC), grid_confirmed=True)

    async def grid():
        return app.full is None
    d = M.MultiviewDriver(app, "pkg", s, matches, grid_check=grid, clock=clk, poll_s=0.1)
    return app, s, d, clk


def test_the_kind_is_part_of_the_contract():
    assert MULTIVIEW in KINDS and M.MULTIVIEW == MULTIVIEW


def test_moves_on_a_2x2_grid():
    assert M.moves(0, 3) == ["down", "right"]
    assert M.moves(3, 0) == ["up", "left"]
    assert M.moves(1, 2) == ["down", "left"]
    assert M.moves(2, 2) == []


def test_plan_keeps_open_tiles_in_place():
    assert M.plan_tiles([FOX, "prime:tnf", ESPN, CBS, NBC, ABC]) == (FOX, ESPN, CBS, NBC)
    # NBC drops out, ABC comes in: it takes NBC's slot, the others don't move
    assert M.plan_tiles([ESPN, ABC, FOX, CBS], current=(FOX, ESPN, CBS, NBC)) == (FOX, ESPN, CBS, ABC)
    assert M.plan_tiles([ESPN, ESPN]) == (ESPN,)


def test_reopen_only_when_a_top_game_is_missing():
    s = M.MultiviewSession("a", (ESPN, FOX, CBS, NBC))
    assert not M.needs_reopen(s, [FOX, ESPN, ABC])          # ABC is 3rd: not worth a reopen
    assert M.needs_reopen(s, [ABC, ESPN])
    assert M.needs_reopen(None, [ESPN])
    assert not M.needs_reopen(None, ["prime:tnf"])


async def test_expand_collapse_and_hop_between_tiles(mv):
    app, s, d, clk = mv
    r = await d.expand(NBC)
    assert r.ok and app.full == NBC and s.expanded == NBC and s.focus == 3
    r = await d.expand(FOX)                                 # BACK, up, CENTER
    assert r.ok and app.full == FOX and s.focus == 1
    assert app.keys[-3:] == ["KEYCODE_BACK", "KEYCODE_DPAD_UP", "KEYCODE_DPAD_CENTER"]
    assert (await d.expand(FOX)).detail == "already full screen"
    assert app.center_on_player == 0
    assert d.costs.n("mv_expand") == 2 and d.costs.n("mv_collapse") == 1


async def test_never_center_without_a_confirmed_grid(mv):
    app, s, d, clk = mv
    await d.expand(ESPN)
    s.expanded = None                                       # our idea of the screen is wrong
    s.grid_confirmed = False
    r = await d.expand(CBS)
    assert not r.ok and "grid not confirmed" in r.detail
    assert app.center_on_player == 0


async def test_an_expand_that_never_shows_is_reported_and_forgets_the_screen(mv, monkeypatch):
    app, s, d, clk = mv
    app.expand_s = 99.0                                     # the tile never comes up full screen
    r = await d.expand(CBS)
    assert not r.ok and s.expanded is None and not s.grid_confirmed


async def test_not_a_tile():
    s = M.MultiviewSession("a", (ESPN,))
    d = M.MultiviewDriver(None, "pkg", s, matches)
    assert not (await d.expand(ABC)).ok


def test_costs_as_switch_paths():
    c = M.MultiviewCosts()
    s = M.MultiviewSession("b:5555", (ESPN, FOX, CBS, NBC), focus=0, expanded=ESPN)
    # visible stick showing ESPN full screen: FOX is collapse + 1 step + expand, no flip
    p = c.path(s, FOX, on_screen=True)
    assert p.kind == MULTIVIEW and p.stick == "b:5555" and p.samples == 0
    assert p.cost_s == pytest.approx(1.0 + 0.35 + 1.5)
    assert c.path(s, ESPN, on_screen=True) is None          # already on screen
    assert c.path(s, ABC, on_screen=True) is None           # not a tile
    # hidden stick: expand off screen, then flip; only the flip is dead air, the deadline has both
    h = c.path(s, NBC, on_screen=False, flip_s=1.2, flip_p90_s=2.0)
    assert h.cost_s == 1.2 and h.cost_p90_s > 2.0 and h.preloaded
    assert c.path(s, ESPN, on_screen=False, flip_s=1.2).cost_p90_s == 1.2   # already expanded there
    assert deadline((p,)) == p.cost_p90_s


def test_measured_costs_replace_the_defaults():
    c = M.MultiviewCosts()
    for x in (0.8, 0.9, 1.0, 5.0):
        c.record("mv_expand", x)
    assert c.get("mv_expand") == 1.0 and c.get("mv_expand", .9) == 5.0
    s = M.MultiviewSession("b", (ESPN, FOX), focus=0)
    assert c.path(s, FOX, on_screen=True).samples == 4


def test_multiview_is_off_by_default(cfg):
    assert cfg.get("multiview.enabled") is False
    assert set(cfg.get("multiview.costs")) == set(M.DEFAULT_COSTS)
