"""[time to see, Nick Nov 5 2026 12:28 AM ET] The catch-up window ratchet (docs/catchup_ratchet.json).

After each release's locked read:
  python scripts/catchup_ratchet.py --record RC7 --json ns.json   # a northstar.py --json output (locked split)
records, per league, whether the release held the NEXT tighter window (>= 99% of big plays seen within it, a PASS
by release_gate.rate_gate over locked nights). A league whose last 2 records both held its next window steps down
(3 min -> 90 s -> 30 s -> live) and never steps back up.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import release_gate as rg  # noqa: E402

STATE = ROOT / "docs/catchup_ratchet.json"
BAR = 99.0


def seen_within(nights: list[dict], window: float) -> list[tuple[int, int]] | None:
    """Per night (seen within window, big plays). None if a night has no time-to-see list (old scorecard)."""
    out = []
    for n in nights:
        xs = n.get("big_see_s")
        if not isinstance(xs, list):
            return None
        out.append((sum(1 for x in xs if isinstance(x, (int, float)) and 0 <= x <= window), len(xs)))
    return out


def next_window(state: dict, lg: str) -> float | None:
    ladder = [float(x) for x in state["ladder_s"]]
    w = float(state["window_s"][lg])
    tighter = [x for x in ladder if x < w]
    return max(tighter) if tighter else None


def record(state: dict, release: str, cards: dict) -> dict:
    """Append this release's per-league verdicts, then step down every league that held its next window twice."""
    held = {}
    for lg in state["window_s"]:
        nw = next_window(state, lg)
        per = (rg._m(cards.get(lg, {})) or {}).get("per_night") or []
        rows = seen_within(per, nw) if nw is not None and per else None
        held[lg] = {"window_s": nw, "held": bool(rows) and rg.rate_gate(rows, BAR)[0] == rg.PASS}
    state["releases"].append({"release": release, "held": held})
    for lg in state["window_s"]:
        last = [r["held"].get(lg) for r in state["releases"][-2:]]
        nw = next_window(state, lg)
        if (nw is not None and len(last) == 2
                and all(x and x["held"] and x["window_s"] == nw for x in last)):
            state["window_s"][lg] = int(nw) if float(nw).is_integer() else nw   # never back up: only a step down
    return state


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--record", required=True, help="the release name, e.g. RC7")
    ap.add_argument("--json", type=Path, required=True, help="scripts/northstar.py --json output with a locked split")
    ap.add_argument("--state", type=Path, default=STATE)
    a = ap.parse_args(argv)
    state = json.loads(a.state.read_text())
    before = dict(state["window_s"])
    cards = json.loads(a.json.read_text())["locked"]["cards"]
    state = record(state, a.record, cards)
    assert all(float(state["window_s"][k]) <= float(before[k]) for k in before), "the ratchet never loosens"
    a.state.write_text(json.dumps(state, indent=2) + "\n")
    for lg, w in state["window_s"].items():
        print(f"{lg}: {before[lg]} s -> {w} s" if w != before[lg] else f"{lg}: {w} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
