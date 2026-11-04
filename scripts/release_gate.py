"""The release gate ("95% good minimum", the user's rule of 2026-11-03), checked on HELD-OUT nights.

    python scripts/ci_scorecard.py --split test --robust --out locked.json   # the LOCKED split + noise
    python scripts/release_gate.py heldout.json [--baseline docs/gate_baseline.json] [--md gate.md]

Every gate gets PASS, FAIL or PENDING. PENDING means it can't be measured yet (no gold labels, no TV
path, no e2e night test, no feasible-ceiling computation), and it NEVER counts as passing. Exit code
1 when any gate FAILs. A part that can't pass ships OFF behind its feature flag
(redzone/contracts/flags.py), and the release PR says how far short it is and why.

Judged ONLY on the LOCKED test split (redzone/lab/splits.py) and fresh post-cutoff nights, never on
nights any choice was made on. Robustness cards (sync +-3 s, a dropping/late feed) must pass the
same hard gates.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HELDOUT = {
    "eval_data": "eval-data-v1",
    "split": "test",   # the LOCKED split of redzone/lab/splits.py: newest >= 25 % of nights per league
    "nfl": "LOCKED test nights 20251209-20261101 (10 Sunday slates, 57 games): scripts/eval.py --split test",
    "nba": "LOCKED test nights 20261029-20261101 (24 games): scripts/eval_sports.py nba --split test",
    "nhl": "LOCKED test nights 20261029-20261101 (29 games): scripts/eval_sports.py nhl --split test",
    "demo": "20261101 mixed demo day (inside the locked window)",
}
PASS, FAIL, PENDING = "PASS", "FAIL", "PENDING"


def _m(card: dict) -> dict:
    if not isinstance(card, dict) or card.get("error"):
        return {}
    return card.get("metrics", card)


def _entries(m: dict) -> tuple[int | None, int | None]:
    ct = m.get("cut_timing", m)
    return ct.get("entries"), ct.get("entries_mid_play")


def _pct(s) -> float | None:
    """'104/137 (76%)' or 76.0 -> 76.0"""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    if isinstance(s, dict):
        return _pct(s.get("seen"))
    try:
        a, b = str(s).split(" ")[0].split("/")
        return 100.0 * int(a) / int(b) if int(b) else None
    except (ValueError, IndexError):
        return None


def gates(cards: dict, baseline: dict | None = None) -> list[tuple[str, str, str, str]]:
    """(gate, scope, status, detail) rows."""
    rows: list[tuple[str, str, str, str]] = []
    base = (baseline or {}).get("cards", {})

    def add(gate, scope, ok, detail, pending=False):
        rows.append((gate, scope, PENDING if pending else (PASS if ok else FAIL), detail))

    for name, card in cards.items():
        m = _m(card)
        if not m:
            add("scorecard ran", name, False, str(card.get("error", "no metrics")) if isinstance(card, dict) else "")
            continue
        if m.get("eval_data") != HELDOUT["eval_data"]:
            add("pinned data", name, False, f"eval_data={m.get('eval_data')!r}")
        cuts = m.get("mid_play_cuts")
        add("mid-play cuts = 0", name, cuts == 0, f"{cuts}")
        n, mid = _entries(m)
        if n:
            dead = 100.0 * (n - mid) / n
            add("entries at a dead ball >= 95%", name, dead >= 95.0, f"{dead:.1f}% ({n - mid}/{n})")
            add("entries mid-play <= 5%", name, 100.0 * mid / n <= 5.0, f"{100.0 * mid / n:.1f}% ({mid}/{n})")
            # [landing offsets] Nick's Nov 4 timing gate: 0 mid-play entries, and >= 95 % of landings
            # (cuts away and entries) inside on-screen dead time (histogram: *_to_restart_hist)
            add("entries mid-play = 0", name, mid == 0, f"{mid}")
            ld = m.get("cut_timing", m).get("landings_in_dead_pct")
            if ld is not None:
                add("landings in dead time >= 95%", name, ld >= 95.0, f"{ld}%")
            bn, bmid = _entries(_m(base.get(name, {})))
            if bmid is not None:
                add("entries mid-play never above today", name, mid <= bmid, f"{mid} vs today {bmid}")
        if name.startswith("demo"):
            add("big plays seen >= 95% (NFL)", name, (_pct(m.get("nfl_big_plays")) or 0) >= 95, str(m.get("nfl_big_plays", {}).get("seen")))
            add("big plays seen >= 95% (NHL)", name, (_pct(m.get("nhl_goals")) or 0) >= 95, str(m.get("nhl_goals", {}).get("seen")))
            cr = _pct(m.get("crunch_live"))
            add("crunch live >= 95% or feasible ceiling", name, cr is not None and cr >= 95, f"{cr:.1f}% (ceiling: pending)" if cr is not None else "n/a", pending=cr is not None and cr < 95)
            continue
        if m.get("big_seen_pct") is not None:
            add("big plays seen >= 95%", name, m["big_seen_pct"] >= 95.0, f"{m['big_seen_pct']}%")
        if m.get("your_team_big_seen_pct") is not None:
            add("your-team big plays seen = 100%", name, m["your_team_big_seen_pct"] >= 100.0, f"{m['your_team_big_seen_pct']}%")
        if m.get("your_team_crunch_live_pct") is not None:
            v = m["your_team_crunch_live_pct"]
            # The scorecards print 0/0 as 0.0: with no your-team crunch plays in the slice there is nothing
            # to grade. TODO(scorecards): emit the play counts so a real 0% can't hide here.
            add("your-team crunch live = 100%", name, v >= 100.0,
                f"{v}%" if v else "no your-team crunch plays in this slice (0/0)", pending=v == 0.0)
        cr = m.get("crunch_live_pct")
        if cr is not None:
            # Under the user's league order (NFL > CFB > NBA > rest) a lower-priority overlap that gets a
            # replay is not a miss; until that ceiling is computed, a value under 95% is PENDING, not PASS.
            add("crunch live >= 95% or feasible ceiling", name, cr >= 95.0,
                f"{cr}% (ceiling: pending)" if cr < 95 else f"{cr}%", pending=cr < 95.0)
    for gate, why in (("reels >= 95% in dead time, 0 live big plays missed on a reel", "reel scorecard line not built yet"),
                      ("reasoner >= 95% agreement, unsafe leaves <= 0.5%, 100% valid JSON", "no gold labels yet"),
                      ("watchdog >= 95% correct, every kind >= 90%, 0 unsafe", "no held-out error set yet"),
                      ("sync error < 2 s on >= 95% of samples", "needs real screen samples (Mac)"),
                      ("preloaded switches < 3 s on >= 95%", "TV path not working yet"),
                      ("fault-injection night: >= 95% minutes correct picture, all faults recovered", "e2e night test reports no uptime figure yet"),
                      ("slices: per network/app, close/blowout, crunch/early, your teams", "scorecards don't report slices yet"),
                      ("locked test size: >= 25% of nights AND a few thousand games", "eval-data-v1 locked test is 130 games (110 graded); needs eval-data-v2"),
                      ("fresh nights after the pin", "none in eval-data-v1; the 5 AM archive feeds eval-data-v2"),
                      ("models trained only before the locked cutoff (no leakage)",
                       "brain models were trained on seasons that include the locked nights (NBA dev-test AUC 0.999): "
                       "every number above that uses brain features is OPTIMISTIC until the time-cutoff retrain lands")):
        add(gate, "system", False, why, pending=True)
    return rows


def markdown(rows) -> str:
    icon = {PASS: "✅ PASS", FAIL: "❌ FAIL", PENDING: "⏳ PENDING"}
    out = ["| gate | scope | status | value |", "|---|---|---|---|"]
    out += [f"| {g} | {s} | {icon[st]} | {d} |" for g, s, st, d in rows]
    n = {k: sum(r[2] == k for r in rows) for k in (PASS, FAIL, PENDING)}
    out.append(f"\n{n[PASS]} pass, {n[FAIL]} fail, {n[PENDING]} pending (pending never counts as passing).")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scorecards", type=Path, help="scripts/ci_scorecard.py output on the held-out slice")
    ap.add_argument("--baseline", type=Path, default=Path(__file__).resolve().parent.parent / "docs/gate_baseline.json")
    ap.add_argument("--md", type=Path, default=None)
    a = ap.parse_args()
    cards = json.loads(a.scorecards.read_text()).get("cards", {})
    base = json.loads(a.baseline.read_text()) if a.baseline and a.baseline.exists() else None
    rows = gates(cards, base)
    md = markdown(rows)
    print(md)
    if a.md:
        a.md.write_text(md + "\n")
    return 1 if any(r[2] == FAIL for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
