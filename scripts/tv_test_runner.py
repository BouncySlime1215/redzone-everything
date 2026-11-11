"""TV test runner: walks docs/test-queue.md and the Wave T1 hidden-stick measurements, one at a time.

    python scripts/tv_test_runner.py --plan                # what each test would do, plain English, with time
    python scripts/tv_test_runner.py                       # run (needs data/.agent_hw_ok; hidden stick only)
    python scripts/tv_test_runner.py --nobody-watching     # also allow tests that change what the TV shows
    python scripts/tv_test_runner.py --status | --reset

Why: build-plan Wave T1/T2 and docs/test-queue.md: the first TV night must be fast, and a hand-run list
loses time and results. This walks the list in order, runs preflight first, stops on the first failure,
records the numbers against each pass bar, and resumes where it stopped (a failed test is retried).

Safety, all enforced in code and proved in tests/test_tv_test_runner.py:
- No data/.agent_hw_ok (Nick creates it) = refuse before anything else: no preflight, no device call.
- Only the hidden stick: the stick object must say role "hidden". Tests that need the visible stick or the
  app run by Nick are "manual": the runner lists them and skips them. A test that changes what the TV
  shows is "disruptive": it waits for --nobody-watching.
- --plan and --status touch nothing (no device, no preflight, no write).
- It never reads, stores or prints a stick address or a secret: it only accepts numbers and true/false
  back from the stick, and an error is recorded by its class name only (a message could hold an address).
- The real stick driver is not wired here (load_hidden_stick fails closed); tests use a fake stick.
"""
from __future__ import annotations

import argparse
import json
import operator
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

ROOT = Path(__file__).resolve().parent.parent
QUEUE_DOC = "docs/test-queue.md"
PERMISSION = "data/.agent_hw_ok"
STATE = "data/tv_test_state.json"
RESULTS = "data/tv_test_results.jsonl"

_OPS: dict[str, tuple[Callable[[Any, Any], bool], str]] = {
    "<=": (operator.le, "at most"), "<": (operator.lt, "under"),
    ">=": (operator.ge, "at least"), "==": (operator.eq, "exactly"),
}


@dataclass(frozen=True)
class Bar:
    """One pass bar: metric OP limit. op 'info' records the number and never fails."""
    metric: str
    op: str
    limit: float | bool | None = None
    unit: str = ""

    def text(self) -> str:
        if self.op == "info":
            return f"{self.metric}: recorded, no bar"
        return f"{self.metric} {_OPS[self.op][1]} {self.limit}{self.unit}"

    def check(self, value: Any) -> bool:
        if self.op == "info":
            return value is not None
        if value is None or isinstance(value, str):
            return False
        return bool(_OPS[self.op][0](value, self.limit))


@dataclass(frozen=True)
class Spec:
    """A test the runner can do on the hidden stick."""
    id: str
    title: str
    does: str
    minutes: int
    bars: tuple[Bar, ...]
    disruptive: bool = False       # changes what the TV shows: needs --nobody-watching


@dataclass
class Item:
    id: str
    title: str
    minutes: int
    spec: Spec | None = None
    bar_text: str = ""
    source: str = "queue"          # queue | t1
    note: str = ""

    @property
    def manual(self) -> bool:
        return self.spec is None


class Stick(Protocol):
    role: str

    def measure(self, test_id: str) -> dict:
        """Run the test's measurements on the stick; return {metric: number or bool}."""


# What the runner does by itself. Everything else in the queue is manual (needs the app run by Nick,
# the visible stick, or a game on both sticks). Numbers come from docs/test-queue.md 3a/3b and Wave T1.
SPECS: tuple[Spec, ...] = (
    Spec("3a", "Stick speed numbers per app and HDMI (P1-MAXRATE)",
         "Seek the hidden stick by short, medium and long distances and time each; park it and measure how far "
         "the position lands from the target; time a game change, a flip and PLAY-to-picture; hold a pause for "
         "30 s; try Prime in steps. Every number goes in the ledger.", 10,
         (Bar("seek_latency_s", "<=", 8, " s"), Bar("seek_fail_rate", "<", 0.10),
          Bar("park_error_sd_s", "<=", 1.5, " s"), Bar("game_change_prep_s", "<=", 15, " s"),
          Bar("play_to_picture_s", "<=", 2.5, " s"), Bar("pause_hold_30s_ok", "==", True),
          Bar("prime_seek_in_steps", "info"))),
    Spec("3b", "YouTube TV label anchor at the live edge (WP-03 review)",
         "On a live YouTube TV game, press RIGHT then read the time label, press LEFT then read it again, and "
         "compare both to the offset the code expects.", 3, (Bar("labels_agree_with_offset", "==", True),)),
    Spec("T1-espn", "ESPN Unlimited rows and codes in YouTube TV",
         "Open the ESPN rows in YouTube TV on the hidden stick and read which rows and channel codes appear. "
         "Sets the WP-22 config.", 10, (Bar("rows_found", ">=", 1), Bar("codes_resolve", "==", True))),
    Spec("T1-leaguepass", "League Pass on the hidden stick",
         "Open League Pass on the hidden stick and check it signs in, lists tonight's games and starts one.",
         10, (Bar("signed_in", "==", True), Bar("game_starts", "==", True))),
    Spec("T1-localflip", "Local flip success without SmartThings",
         "Flip the TV between the two HDMI inputs 5 times using the local key and CEC only, counting how many "
         "land on the right picture. Sets WP-04.", 8, (Bar("flip_success_rate", ">=", 0.95),), disruptive=True),
    Spec("T1-L1", "L1 Prime steps",
         "On a Prime game, step back in small jumps and record how far each step really moves.", 8,
         (Bar("step_error_s", "<=", 2.0, " s"),)),
    Spec("T1-L2", "L2 banner clear",
         "Wait for the Prime banner, clear it and time how long the picture takes to come back clean.", 4,
         (Bar("banner_clear_s", "<=", 3.0, " s"),)),
    Spec("T1-L3", "L3 band read and pause",
         "Read the progress band on the hidden stick, pause, and check the band and the position agree.", 6,
         (Bar("band_read_ok", "==", True), Bar("pause_holds", "==", True))),
    Spec("T1-L6", "L6 Prime seek",
         "Seek a Prime game by a long distance and by short steps; record latency and miss.", 8,
         (Bar("seek_latency_s", "<=", 8, " s"), Bar("seek_fail_rate", "<", 0.10))),
    Spec("T1-L10", "L10 HDMI list",
         "Read the list of HDMI inputs the stick sees (names only, nothing is changed).", 3,
         (Bar("inputs_listed", ">=", 2),)),
    Spec("T1-L11", "L11 left-stick drift",
         "Park the stick for 5 minutes and measure how far its position drifts from the clock.", 8,
         (Bar("drift_s_per_5min", "<=", 1.5, " s"),)),
    Spec("T1-L12", "L12 DIAL read-only",
         "Ask the stick's DIAL service what app is running; read only, nothing is launched.", 3,
         (Bar("dial_reads", "==", True),)),
    Spec("T2-cap", "Prime capture offset",
         "30 alternating raw-band and PNG captures of a running game clock on the hidden Prime stick; the offset between "
         "the two conventions is the capture offset. Replaces the 1.0 s margin in delay.timing.app_sigma.", 3,
         (Bar("capture_offset_s", "info"), Bar("capture_offset_sd_s", "<=", 0.5, " s"))),
    Spec("T2-k", "Prime K per game",
         "20 bug reads per game across 3 games and both sticks: K = delay - E, per game and per stick.", 12,
         (Bar("k_sd_within_game_s", "<=", 0.5, " s"), Bar("k_spread_across_games_s", "info"),
          Bar("k_spread_across_sticks_s", "<=", 0.5, " s"))),
    Spec("T2-pos", "Prime position form",
         "Which content reports the wall-clock position and which a counter; pause and seek in each and check the meter.",
         5, (Bar("epoch_content_share", "info"), Bar("meter_survives_seek", "==", True))),
    Spec("T2-edge", "YouTube TV live edge",
         "The label probe (3b) on 5 channels, 3 rounds an hour apart, both sticks. Is the live edge one number, and how "
         "stable.", 15, (Bar("edge_mean_s", "info"), Bar("edge_sd_s", "<=", 1.0, " s"))),
)
SPEC_BY_ID = {s.id: s for s in SPECS}
T1_IDS = tuple(s.id for s in SPECS if s.id.startswith("T1-"))


def parse_queue(text: str) -> list[Item]:
    """Rows of the docs/test-queue.md table, in order: id, test, time, pass bar (Status is ignored)."""
    items: list[Item] = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 7 or not re.fullmatch(r"\d+[a-z]?", cells[0]):
            continue
        m = re.search(r"(\d+)\s*min", cells[4])
        spec = SPEC_BY_ID.get(cells[0])
        items.append(Item(cells[0], re.sub(r"`", "", cells[1])[:110], int(m.group(1)) if m else 0, spec,
                          cells[5]))
    return items


def build_items(root: Path = ROOT) -> list[Item]:
    """The queue rows in order, then the Wave T1 measurements."""
    items = parse_queue((root / QUEUE_DOC).read_text())
    for it in items:
        if it.spec:
            it.title, it.minutes = it.spec.title, it.spec.minutes
            it.bar_text = "; ".join(b.text() for b in it.spec.bars)
        else:
            it.note = "manual: needs the app run by Nick, a game pair or both sticks"
    for sid in T1_IDS:
        s = SPEC_BY_ID[sid]
        items.append(Item(s.id, s.title, s.minutes, s, "; ".join(b.text() for b in s.bars), "t1"))
    return items


def plan_text(items: list[Item]) -> str:
    """Plain-English plan. Pure: no device, no preflight, no file written."""
    out = ["TV test plan (nothing is touched by this command)", ""]
    for n, it in enumerate(items, 1):
        if it.spec:
            tag = "runner, hidden stick" + (", needs --nobody-watching" if it.spec.disruptive else "")
            out.append(f"{n:>2}. [{it.id}] {it.title}  ({it.minutes} min, {tag})")
            out.append(f"      Does: {it.spec.does}")
        else:
            out.append(f"{n:>2}. [{it.id}] {it.title}  ({it.minutes} min, manual, the runner skips it)")
            out.append(f"      Does: Nick or the coordinator runs this by hand; {it.note}.")
        out.append(f"      Passes when: {it.bar_text or 'see docs/test-queue.md'}")
    auto = sum(i.minutes for i in items if i.spec)
    total = sum(i.minutes for i in items)
    out += ["", f"Runner tests: {auto} min. Manual tests: {total - auto} min. TOTAL: {total} min.",
            "Before any runner test: data/.agent_hw_ok must exist, then preflight must say GO."]
    return "\n".join(out)


def evaluate(spec: Spec, got: Any) -> tuple[bool, dict, list[str]]:
    """Pure: numbers and bools only come through; one line per failed bar."""
    nums = {k: v for k, v in (got or {}).items()
            if isinstance(k, str) and isinstance(v, (int, float, bool))} if isinstance(got, dict) else {}
    fails = [f"{b.text()} (got {nums.get(b.metric, 'nothing')})" for b in spec.bars
             if not b.check(nums.get(b.metric))]
    return not fails, nums, fails


def _load(path: Path) -> dict:
    try:
        d = json.loads(path.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    tmp.replace(path)


def load_hidden_stick() -> Stick:
    """Fails closed: the real driver is not wired in this file. The coordinator supplies one that reports
    role 'hidden' and returns numbers only."""
    raise RuntimeError("no real stick driver is wired into tv_test_runner; pass one in")


def run(root: Path = ROOT, *, stick_factory: Callable[[], Stick] = load_hidden_stick,
        preflight: Callable[[], dict] | None = None, nobody_watching: bool = False,
        clock: Callable[[], float] = time.time, out: Callable[[str], None] = print) -> int:
    """Walk the list. 0 = everything done, 1 = a failure or a NO-GO (stopped), 2 = refused or blocked."""
    if not (root / PERMISSION).is_file():
        out(f"REFUSED: {PERMISSION} does not exist. Nick creates it to allow hardware tests. Nothing was run.")
        return 2
    if preflight is None:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import preflight as _pf
        preflight = lambda: _pf.preflight(root)  # noqa: E731
    pre = preflight()
    if not pre.get("go"):
        out("NO-GO from preflight: " + ", ".join(pre.get("failed", [])) + ". Fix those and run again.")
        return 1
    out("Preflight GO.")
    state_path = root / STATE
    state = _load(state_path)
    items = build_items(root)
    stick: Stick | None = None
    for it in items:
        if it.manual:
            continue
        if state.get(it.id, {}).get("status") == "pass":
            out(f"[{it.id}] already passed, skipping.")
            continue
        assert it.spec is not None
        if it.spec.disruptive and not nobody_watching:
            out(f"BLOCKED at [{it.id}]: it changes what the TV shows. Run again with --nobody-watching "
                "when nobody is watching. Progress is saved.")
            return 2
        if stick is None:
            try:
                stick = stick_factory()
            except Exception as e:  # the message could name a device: class only
                out(f"REFUSED: no stick driver ({type(e).__name__}). Nothing was run.")
                return 2
            if getattr(stick, "role", None) != "hidden":
                out("REFUSED: the stick is not the hidden one. The runner only acts on the hidden stick.")
                return 2
        t0 = clock()
        try:
            ok, nums, fails = evaluate(it.spec, stick.measure(it.id))
        except Exception as e:
            ok, nums, fails = False, {}, [f"the stick call failed ({type(e).__name__})"]
        rec = {"id": it.id, "status": "pass" if ok else "fail", "numbers": nums, "failed_bars": fails,
               "seconds": round(clock() - t0, 1), "at": int(clock())}
        state[it.id] = rec
        _save(state_path, state)
        with (root / RESULTS).open("a") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
        out(f"[{it.id}] {'PASS' if ok else 'FAIL'} {nums}")
        if not ok:
            out("STOPPED on a failure: " + "; ".join(fails) + ". Fix it and run again: it resumes here.")
            return 1
    out("All runner tests passed. Manual tests in docs/test-queue.md are still Nick's.")
    return 0


def status_text(root: Path = ROOT) -> str:
    state = _load(root / STATE)
    lines = [f"[{i.id}] {state.get(i.id, {}).get('status', 'manual' if i.manual else 'not run')}"
             for i in build_items(root)]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Walk the TV test queue on the hidden stick.")
    ap.add_argument("--plan", action="store_true", help="print what each test does and its time; touches nothing")
    ap.add_argument("--status", action="store_true", help="print saved progress; touches nothing")
    ap.add_argument("--reset", action="store_true", help="forget saved progress")
    ap.add_argument("--nobody-watching", action="store_true", help="allow tests that change what the TV shows")
    a = ap.parse_args(argv)
    if a.plan:
        print(plan_text(build_items()))
        return 0
    if a.status:
        print(status_text())
        return 0
    if a.reset:
        _save(ROOT / STATE, {})
        print("progress cleared")
        return 0
    return run(nobody_watching=a.nobody_watching)


if __name__ == "__main__":
    sys.exit(main())
