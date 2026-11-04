"""Prime Video NBA multiview as a switch path (SWITCHING). OFF until a live calibration on a Prime
NBA night passes its gate (`tuners.prime_multiview`, scripts/prime_multiview_probe.py).

Prime Video on Fire TV can show up to three NBA games at once: one big main tile (it has the
audio) and smaller tiles stacked on the right. Making another tile main is remote navigation
inside the player: no app relaunch, no deep link, no detail page. So a switch between tiles on
the same stick should take about a second, against ~32 s for a Prime deep-link tune.

What this module knows is data, not code:

  data/prime_multiview_keys.json   the D-pad key paths and timings the probe recorded on Nick's
                                   sticks (how to open multiview, add a game, make tile j main,
                                   and how the tile order changes afterwards)
  score.personal.multiview_*       which games may not join a multiview and which go first. Team
                                   names never appear in code, so a build we sell carries none of
                                   one person's data.

Prime's player is DRM-protected, so screenshots come back black. Every step is verified from the
accessibility tree instead (`uiautomator dump`): the main tile is the game label drawn biggest,
and the focused node is where a CENTER would land. A step that can't be verified is a failed
step, never a guess.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

KEYS_FILE = Path(__file__).resolve().parents[2] / "data" / "prime_multiview_keys.json"
MAX_TILES = 3
KEYCODE = {"up": "KEYCODE_DPAD_UP", "down": "KEYCODE_DPAD_DOWN", "left": "KEYCODE_DPAD_LEFT",
           "right": "KEYCODE_DPAD_RIGHT", "ok": "KEYCODE_DPAD_CENTER", "back": "KEYCODE_BACK",
           "menu": "KEYCODE_MENU"}
DEFAULT_TILE_S = 1.0     # a tile switch, until measured (tuners.prime_tile_s)
DEFAULT_TILE_P90_S = 2.0
GATE_OK_PCT = 95.0       # >= this share of tile switches land on the right tile...
GATE_UNDER_S = 2.0       # ...in under this many seconds (plus 0 mid-play entries on the live night)
AFTER = ("swap", "rotate", "fixed")
UI_DUMP = "rm -f /sdcard/rz_mv.xml && uiautomator dump /sdcard/rz_mv.xml >/dev/null 2>&1 && cat /sdcard/rz_mv.xml"


def codes(path: Iterable[str]) -> list[str]:
    """Probe names ("right", "ok") -> Android keycodes. Unknown names raise: a typo in the keys file
    must never turn into a stray key press."""
    out = []
    for k in path:
        if k in KEYCODE:
            out.append(KEYCODE[k])
        elif isinstance(k, str) and k.startswith("KEYCODE_"):
            out.append(k)
        else:
            raise ValueError(f"unknown key {k!r} in the prime multiview key map")
    return out


# --- the key map the probe writes ----------------------------------------------------------------

@dataclass
class KeyMap:
    """data/prime_multiview_keys.json. `promote["0>2"]` is the key path that, with slot 0 main, makes
    slot 2 main. `after` is how the tile order changes when it does:
        swap    the promoted game and the old main trade places (slot 0 is always main)
        rotate  the promoted game moves to slot 0, the rest keep their order
        fixed   no tile moves; only which slot is main changes (paths then exist for every i>j)
    """
    open: list[str] = field(default_factory=list)          # playing one game -> multiview with it main
    add_open: list[str] = field(default_factory=list)      # in multiview -> the "add a game" picker
    add_next: str = "down"                                 # one step down the picker list
    add_select: str = "ok"
    add_slot: str = "append"                               # where an added game lands: append | main
    promote: dict[str, list[str]] = field(default_factory=dict)
    after: str = "swap"
    focus_check: str = "none"                              # uiautomator | accessibility | none
    picker_readable: bool = False
    picker_offered: list[str] = field(default_factory=list)
    timings: dict = field(default_factory=dict)
    stick: str = ""
    measured_at: str = ""
    calibration: dict = field(default_factory=dict)        # the live-night gate result, filled in later

    @property
    def can_switch(self) -> bool:
        """Enough to switch tiles: promote paths and a way to check which tile is main."""
        return bool(self.promote) and self.after in AFTER and self.focus_check != "none"

    @property
    def can_build(self) -> bool:
        return self.can_switch and bool(self.open) and bool(self.add_open) and self.picker_readable

    def path(self, main: int, slot: int) -> list[str] | None:
        if main == slot:
            return []
        p = self.promote.get(f"{main}>{slot}")
        return list(p) if p is not None else None

    def tile_cost(self, q: float = 0.5) -> float | None:
        xs = sorted(float(x) for v in (self.timings.get("promote_s") or {}).values() for x in (v or []))
        return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None

    @classmethod
    def from_dict(cls, d: dict) -> "KeyMap":
        add = d.get("add") or {}
        km = cls(open=list(d.get("open") or []), add_open=list(add.get("open") or []),
                 add_next=add.get("next", "down"), add_select=add.get("select", "ok"),
                 add_slot=add.get("slot", "append"),
                 promote={str(k): list(v) for k, v in (d.get("promote") or {}).items()},
                 after=d.get("after", "swap"), focus_check=d.get("focus_check", "none"),
                 picker_readable=bool(add.get("picker_readable", False)),
                 picker_offered=list(d.get("picker_offered") or []), timings=dict(d.get("timings") or {}),
                 stick=d.get("stick", ""), measured_at=d.get("measured_at", ""),
                 calibration=dict(d.get("calibration") or {}))
        if km.after not in AFTER:
            raise ValueError(f"after must be one of {AFTER}, got {km.after!r}")
        for p in [km.open, km.add_open, [km.add_next, km.add_select], *km.promote.values()]:
            codes(p)   # validate every key name now, not at switch time
        return km

    def to_dict(self) -> dict:
        return {"version": 1, "stick": self.stick, "measured_at": self.measured_at, "open": self.open,
                "add": {"open": self.add_open, "next": self.add_next, "select": self.add_select,
                        "slot": self.add_slot, "picker_readable": self.picker_readable},
                "promote": self.promote, "after": self.after, "focus_check": self.focus_check,
                "picker_offered": self.picker_offered, "timings": self.timings, "calibration": self.calibration}


def load_keys(path: Path | str | None = None) -> KeyMap | None:
    """The probe's key map, or None when it's missing or broken (then every switch is a normal tune)."""
    p = Path(path) if path else KEYS_FILE
    try:
        return KeyMap.from_dict(json.loads(p.read_text()))
    except (OSError, ValueError, TypeError, AttributeError):
        return None


# --- what the accessibility tree says ------------------------------------------------------------

@dataclass(frozen=True)
class Node:
    text: str
    focused: bool
    focusable: bool
    area: int


_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


def parse_nodes(xml: str) -> list[Node] | None:
    """Every node of a `uiautomator dump`, in document order. None = not a readable dump."""
    if not xml or "<hierarchy" not in xml:
        return None
    try:
        root = ET.fromstring(xml[xml.index("<hierarchy"):xml.rindex("</hierarchy>") + len("</hierarchy>")])
    except (ET.ParseError, ValueError):
        return None
    out = []
    for n in root.iter("node"):
        m = _BOUNDS.match(n.get("bounds", ""))
        area = max(0, int(m[3]) - int(m[1])) * max(0, int(m[4]) - int(m[2])) if m else 0
        text = " ".join(x for x in (n.get("text", ""), n.get("content-desc", "")) if x).strip()
        out.append(Node(text, n.get("focused") == "true", n.get("focusable") == "true", area))
    return out


def _hits(text: str, labels: Iterable[str]) -> bool:
    t = text.lower()
    return any(len(lb) >= 3 and re.search(rf"\b{re.escape(lb.lower())}\b", t) for lb in labels)


def main_tile(nodes: list[Node] | None, labels: dict[str, list[str]]) -> str | None:
    """Which game is the main tile: the one whose label is drawn biggest. None = can't tell (no dump,
    no label found, or two games tie)."""
    if not nodes:
        return None
    best: dict[str, int] = {}
    for n in nodes:
        if not n.text:
            continue
        for target, names in labels.items():
            if _hits(n.text, names):
                best[target] = max(best.get(target, 0), n.area)
    if not best:
        return None
    ranked = sorted(best.items(), key=lambda kv: -kv[1])
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None
    return ranked[0][0] if ranked[0][1] > 0 else None


def picker_rows(nodes: list[Node] | None) -> tuple[list[str], int | None]:
    """The picker's focusable rows (labels) in screen order, and which one is focused."""
    rows = [n for n in nodes or () if n.focusable and n.text]
    focus = next((i for i, n in enumerate(rows) if n.focused), None)
    return [n.text for n in rows], focus


# --- the tiles on one stick ----------------------------------------------------------------------

@dataclass
class Session:
    """One stick's multiview: `tiles[slot]` = target in that screen slot, `main` = the slot shown big."""
    stick: str
    tiles: list[str]
    main: int = 0

    @property
    def main_target(self) -> str | None:
        return self.tiles[self.main] if 0 <= self.main < len(self.tiles) else None

    def slot(self, target: str) -> int | None:
        return self.tiles.index(target) if target in self.tiles else None

    def promoted(self, slot: int, after: str) -> None:
        """Book what the tile order looks like once `slot` became main."""
        if after == "fixed":
            self.main = slot
        elif after == "swap":
            self.tiles[0], self.tiles[slot] = self.tiles[slot], self.tiles[0]
            self.main = 0
        else:   # rotate
            self.tiles.insert(0, self.tiles.pop(slot))
            self.main = 0


# --- which games go into the multiview ------------------------------------------------------------

@dataclass(frozen=True)
class TileCandidate:
    target: str
    league: str
    teams: tuple[str, ...]   # the feed's abbreviations, e.g. ("AAA", "BBB")
    score: float


def team_keys(league: str, teams: Iterable[str]) -> set[str]:
    return {f"{league}:{t}".lower() for t in teams if t}


def eligible(c: TileCandidate, exclude: Iterable[str], offered: set[str] | None = None) -> bool:
    """A Prime NBA game that no team in it keeps out (score.personal.multiview_exclude, "league:ABBR"),
    and that the picker offers when we know what it offers."""
    if not c.target.startswith("prime:") or c.league != "nba":
        return False
    if team_keys(c.league, c.teams) & {e.lower() for e in exclude}:
        return False
    return offered is None or c.target in offered


def plan_tiles(cands: Iterable[TileCandidate], exclude: Iterable[str] = (), pin: Iterable[str] = (),
               k: int = MAX_TILES, current: Iterable[str] = (),
               offered: set[str] | None = None) -> tuple[list[str], list[str]]:
    """(tiles, others): the top-k eligible Prime NBA games for the multiview, best first, and every
    other Prime game, which belongs on the other stick. A pinned team (score.personal.multiview_pin)
    goes first; then the decider's score. A tile already open keeps its slot while it's still
    wanted, so a small reshuffle doesn't cost a rebuild."""
    k = max(1, min(MAX_TILES, int(k)))
    pins = {p.lower() for p in pin}
    excl = list(exclude)
    pool = list(cands)
    ok = [c for c in pool if eligible(c, excl, offered)]
    ok.sort(key=lambda c: (not (team_keys(c.league, c.teams) & pins), -c.score, c.target))
    want = [c.target for c in ok[:k]]
    cur = [t for t in current if t in want]
    tiles = cur + [t for t in want if t not in cur]
    others = [c.target for c in pool if c.target.startswith("prime:") and c.target not in tiles]
    return tiles, others


def needs_rebuild(session: Session | None, tiles: list[str], must_have: int = 2) -> bool:
    """Rebuild (slow: it runs on the hidden stick) only when one of the top `must_have` planned games
    isn't a tile."""
    top = tiles[:must_have]
    if len(tiles) < 2:
        return False
    return session is None or any(t not in session.tiles for t in top)


def gate(results: list[dict], mid_play_entries: int | None = None) -> dict:
    """The live-calibration gate: >= 95% of tile switches land on the right tile in under 2 s, and
    0 mid-play entries on the night. results = [{"ok": bool, "took": s}, ...]."""
    n = len(results)
    # a missing time counts as slow; a measured 0.0 (rounded) is fast, not missing
    good = sum(1 for r in results if r.get("ok") and float(1e9 if r.get("took") is None else r["took"]) < GATE_UNDER_S)
    pct = round(100.0 * good / n, 1) if n else 0.0
    passed = n >= 20 and pct >= GATE_OK_PCT and mid_play_entries == 0
    return {"switches": n, "good": good, "good_pct": pct, "mid_play_entries": mid_play_entries,
            "need": f">= {GATE_OK_PCT:.0f}% right tile under {GATE_UNDER_S:.0f} s over >= 20 switches, "
                    "0 mid-play entries", "pass": passed}
