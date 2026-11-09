"""A simulated Prime Video NBA player with multiview, for rehearsing scripts/prime_multiview_probe.py
without a stick (`--fake`) and for its tests.

It answers the two calls the probe makes on a Stick: `key(*codes)` and `run("shell", UI_DUMP)`, and
draws a `uiautomator dump` from its state:

    player      one game full screen; DOWN shows the overlay (Start over, Multiview, Details), LEFT/RIGHT
                move along it, OK on Multiview opens multiview with that game main
    multiview   tiles (slot 0 main, drawn big); DOWN focuses "Add game", OK opens the picker; from the main
                tile RIGHT focuses side tile 1, DOWN from there side tile 2, OK makes the focused tile
                main (tile order per `after`: swap or rotate), BACK closes the picker or the overlay
    picker      the games Prime offers as focusable rows; DOWN/UP move, OK adds the focused game

Knobs make the rehearsal fail the way a real night can: `readable=False` (DRM player gives an empty
tree), `offered` (games the picker lists), `after` (tile order model), `drop_every` (every Nth
promote is ignored, so a timed round misses).

Why: first added in 8aca6bcc (2026-11-04) as "Prime multiview: stick-free rehearsal of the probe + grade, fix 0.0 s switches graded slow"; no separate decision is recorded for this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from xml.sax.saxutils import quoteattr

from redzone.tuners import prime_multiview as pmv

OVERLAY = ("Start over", "Multiview", "Details")
NAME = {v: k for k, v in pmv.KEYCODE.items()}


@dataclass
class _R:
    ok: bool
    out: str = ""


@dataclass
class _T:
    ok: bool = True
    took: float = 3.0
    steps: list = field(default_factory=lambda: ["deep link", "Watch Live", "playing"])
    error: str = ""


class FakePrimeStick:
    def __init__(self, first: str = "Phoenix Suns at Golden State Warriors",
                 offered: tuple[str, ...] = ("Phoenix Suns at Golden State Warriors", "Boston Celtics at Miami Heat",
                                             "Denver Nuggets at Los Angeles Lakers", "New York Knicks at Chicago Bulls"),
                 after: str = "swap", readable: bool = True, drop_every: int = 0):
        self.host, self.name, self.packages = "fake:5555", "fake prime", {"prime": "com.amazon.firebat"}
        self.mode = "player"
        self.tiles = [first]
        self.offered = list(offered)
        self.after, self.readable, self.drop_every = after, readable, drop_every
        self.overlay: int | None = None      # player overlay focus index
        self.focus = 0                       # multiview: focused slot, or -1 = "Add game"
        self.pick = 0                        # picker focus
        self.promotes = 0
        self.presses: list[str] = []

    # --- the Stick surface the probe uses ---------------------------------------------------------
    async def connect(self) -> bool:
        return True

    async def key(self, *codes: str) -> None:
        for c in codes:
            self._press(NAME.get(c, c))

    async def run(self, *args, timeout: float = 10) -> _R:
        return _R(True, self.dump() if self.readable else "")

    async def tune(self, *_a, **_k) -> _T:
        return _T()

    # --- state machine --------------------------------------------------------------------------
    def _press(self, k: str) -> None:
        self.presses.append(k)
        if self.mode == "player":
            if k == "down":
                self.overlay = 0 if self.overlay is None else self.overlay
            elif k in ("left", "right") and self.overlay is not None:
                self.overlay = max(0, min(len(OVERLAY) - 1, self.overlay + (1 if k == "right" else -1)))
            elif k == "ok" and self.overlay is not None and OVERLAY[self.overlay] == "Multiview":
                self.mode, self.focus, self.overlay = "multiview", 0, None
            elif k == "back":
                self.overlay = None
        elif self.mode == "multiview":
            if k == "down" and self.focus == 0:
                self.focus = -1                                   # "Add game"
            elif k == "down" and self.focus == 1 and len(self.tiles) > 2:
                self.focus = 2
            elif k == "up" and self.focus in (-1, 2):
                self.focus = 0 if self.focus == -1 else 1
            elif k == "right" and self.focus == 0 and len(self.tiles) > 1:
                self.focus = 1
            elif k == "left" and self.focus in (1, 2):
                self.focus = 0
            elif k == "ok" and self.focus == -1 and len(self.tiles) < pmv.MAX_TILES:
                self.mode, self.pick = "picker", 0
            elif k == "ok" and self.focus > 0:
                self.promotes += 1
                if not (self.drop_every and self.promotes % self.drop_every == 0):
                    s = pmv.Session("fake", self.tiles, 0)
                    s.promoted(self.focus, self.after)
                    self.tiles = s.tiles
                self.focus = 0
            elif k == "back":
                self.focus = 0
        elif self.mode == "picker":
            if k == "down":
                self.pick = min(len(self.offered) - 1, self.pick + 1)
            elif k == "up":
                self.pick = max(0, self.pick - 1)
            elif k == "ok":
                g = self.offered[self.pick]
                if g not in self.tiles:
                    self.tiles.append(g)
                self.mode, self.focus = "multiview", 0
            elif k == "back":
                self.mode, self.focus = "multiview", 0

    def dump(self) -> str:
        nodes = []

        def node(text: str, x0: int, y0: int, x1: int, y1: int, focusable=False, focused=False) -> None:
            nodes.append(f'<node text={quoteattr(text)} content-desc="" focusable="{str(focusable).lower()}" '
                         f'focused="{str(focused).lower()}" bounds="[{x0},{y0}][{x1},{y1}]" />')
        if self.mode == "player":
            node(self.tiles[0], 0, 0, 1920, 1080)
            if self.overlay is not None:
                for i, t in enumerate(OVERLAY):
                    node(t, 100 + 300 * i, 900, 380 + 300 * i, 980, True, i == self.overlay)
        else:
            mv = self.mode == "multiview"    # under the picker the tiles can't take focus
            node(self.tiles[0], 0, 0, 1440, 810, mv, mv and self.focus == 0)
            for i, t in enumerate(self.tiles[1:], start=1):
                node(t, 1460, 20 + 400 * (i - 1), 1900, 400 * i, mv, mv and self.focus == i)
            if self.mode == "multiview":
                node("Add game", 100, 900, 400, 980, True, self.focus == -1)
            if self.mode == "picker":
                for i, g in enumerate(self.offered):
                    node(g, 400, 200 + 100 * i, 1500, 290 + 100 * i, True, i == self.pick)
        return '<?xml version="1.0"?><hierarchy rotation="0">' + "".join(nodes) + "</hierarchy>"
