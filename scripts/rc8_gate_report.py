#!/usr/bin/env python3
r"""[RC8] Per-league report of the pre-registered buffer gate (docs/plans/2026-11-05-rc8-buffer-gate.md) for Nick.

    python scripts/rc8_gate_report.py \
        --sweep /mnt/project-files/lane3/buffer-sweep/verdict.json \   # lane 3's sweep verdict (gate items 1-2)
        --shadow shadow_grade.json \        # scripts/buffer_shadow_grade.py data/buffer_shadow.jsonl --json (1, 3)
        [--shadow-card card.json]           # shadow vs live lines on the night's pbp (descriptive, item 3)
        [--sync sync.json]                  # {league: {p95_err_s, n}} real behind-live vs assumed B (item 5)
        [--oracle summary.json ...]         # lane 1 windowed-oracle ceilings (descriptive, never a bar)
        [--locked locked.json]              # {league: {entries_mid_play, nights, decisions}} at the cut (item 4)
        [--md report.md] [--json report.json]

Every missing input reads PENDING, never PASS. It applies the gate as written and never loosens it: one shadow
night can only veto, < 8 sweep nights read PENDING. The report states the evidence; the decision to flip a league
(scripts/rc8_flip.py) is Nick's alone. Exit 0 always (a report, not a check), unless an input is unreadable (2).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone import buffer_rc8 as R  # noqa: E402
from redzone.config import Config  # noqa: E402

NEEDS: dict = {}   # flags a verdict may need beyond R.COMPANIONS (start_timed now always ships with RC8)
LEAGUES = ("nba", "nfl", "nhl")   # graded tonight (NBA first, then NFL); others stay live
ITEM_NAMES = {"1_rule1": "Rule #1 (0 mid-play, every run + shadow)", "2_beats_live": "Beats live (sweep, >= 8 nights)",
              "3_shadow": "Tonight's shadow (veto only)", "4_locked": "Locked read at the cut",
              "5_sync": "Sync honesty (p95 <= 2 s)"}


def _load(p: Path | None):
    if p is None:
        return None
    if not p.exists():
        return None
    return json.loads(p.read_text())


def shadow_rows(shadow: dict | None) -> list[dict]:
    """buffer_shadow_grade.py --json -> rows for gate_league (label = cutgrade, clock_truth)."""
    if not shadow:
        return []
    return [{"league": (e.get("league") or "").lower() or None, "label": e.get("cutgrade"),
             "clock_truth": e.get("clock_truth"), "to": e.get("to"), "arena_at": e.get("arena_at")}
            for e in shadow.get("entry_grades") or []]


def sweep_runs(lg_verdict: dict | None, chosen_only: bool = False) -> list[dict]:
    runs = list((lg_verdict or {}).get("runs") or [])
    b = (lg_verdict or {}).get("chosen_b")
    if chosen_only and b is not None:
        needs = (lg_verdict or {}).get("needs") or []
        arm = "st" if ("start_timed" in needs or "decider.entry.start_timed" in R.COMPANIONS) else "main"
        runs = [r for r in runs if r.get("b") == b and r.get("arm", "main") == arm]
    return runs


def flip_cmd(lg: str, v: dict | None, b: float | None) -> str | None:
    """The command that ships B (gate v28's max rule) with any flag the verdict needs."""
    if not v or not v.get("chosen_b") or b is None:
        return None
    extra = "".join(f" --set {NEEDS[n]}" for n in (v.get("needs") or []) if n in NEEDS)
    return f"python scripts/rc8_flip.py {lg} on --seconds {float(b):g}{extra}"


def oracle_line(oracles: dict | None, lg: str) -> str:
    """'p90: 45 21%, 90 70% | primary: ...' from lane 1 summary.json files (window % of full-hindsight big live)."""
    parts = []
    for name, summ in (oracles or {}).items():
        rows = (summ or {}).get(lg) or {}
        cells = [f"{k} {v.get('window_pct_of_full_big_live')}%" for k, v in rows.items()
                 if k.isdigit() and int(k) > 0 and v.get("window_pct_of_full_big_live") is not None]
        if cells:
            lag = next((v.get("feed_lag_s") for v in rows.values() if v.get("feed_lag_s") is not None), None)
            parts.append(f"{name} (lag {lag:g} s): " + ", ".join(cells) if lag is not None else f"{name}: " + ", ".join(cells))
    return " | ".join(parts)


def report(cfg, sweep: dict | None, shadow: dict | None, card: dict | None = None, sync: dict | None = None,
           locked: dict | None = None, leagues=LEAGUES, oracles: dict | None = None) -> dict:
    sv = (sweep or {}).get("leagues") or {}
    rows = shadow_rows(shadow)
    shadow_ok_struct = not shadow or (shadow.get("S1") != "FAIL" and shadow.get("S2") != "FAIL")
    out = {"sweep_sha": (sweep or {}).get("code_sha"), "grader_freeze": (sweep or {}).get("grader_freeze"),
           "shadow_rows": (shadow or {}).get("rows"), "shadow_S1": (shadow or {}).get("S1"),
           "shadow_S2": (shadow or {}).get("S2"), "leagues": {}}
    for lg in leagues:
        v = sv.get(lg)
        # Rule #1 at the B that would ship (the sweep's pick); the whole grid is shown alongside
        state, detail = R.gate_league(lg, sweep_runs(v, chosen_only=True) if v else [], rows if shadow else None,
                                      sweep=v, sync=(sync or {}).get(lg), locked=(locked or {}).get(lg))
        if not shadow_ok_struct:   # the editor touched a stick or the log is malformed: veto everywhere
            detail["items"]["3_shadow"]["state"] = R.FAIL
            detail["items"]["3_shadow"]["note"] = "shadow log failed S1/S2 (schema or the editor touched a stick)"
            state = R.FAIL
        grid = sweep_runs(v)
        b, parts = R.buffer_for(cfg, lg)
        detail.update({"state": state, "rc8_on_now": R.is_on(cfg, lg), "adaptive": parts,
                       "grid_mid_play_total": sum(int(r.get("entries_mid_play") or 0) for r in grid),
                       "grid_runs": len(grid), "shadow_card": (card or {}).get(lg),
                       "oracle": oracle_line(oracles, lg),
                       "sweep_b": (v or {}).get("chosen_b"), "guardrails": (v or {}).get("guardrails")})
        sb, why = R.ship_b(cfg, lg, (v or {}).get("chosen_b"),
                           mid_play_night=detail["items"]["1_rule1"]["shadow_live"] > 0)
        detail.update({"ship_b": sb if (v or {}).get("chosen_b") else None, "ship_why": why,
                       "flip_cmd": flip_cmd(lg, v, sb)})
        out["leagues"][lg] = detail
    return out


def to_md(r: dict) -> str:
    L = ["# RC8 buffer gate, per league", "",
         f"Sweep code {r.get('sweep_sha') or 'n/a'}, grader freeze {r.get('grader_freeze') or 'n/a'}; "
         f"shadow rows {r.get('shadow_rows') if r.get('shadow_rows') is not None else 'none yet'} "
         f"(S1 {r.get('shadow_S1') or '-'}, S2 {r.get('shadow_S2') or '-'}).", "",
         "The gate is applied as pre-registered (docs/plans/2026-11-05-rc8-buffer-gate.md). PENDING never counts as "
         "PASS. One shadow night can only veto. Flipping a league is Nick's call (scripts/rc8_flip.py).", "",
         "| League | Gate | " + " | ".join(ITEM_NAMES[k].split(" (")[0] for k in ITEM_NAMES) + " | Sweep B | Ship B | Formula B |",
         "|---|---|" + "---|" * len(ITEM_NAMES) + "---|---|---|"]
    for lg, d in r["leagues"].items():
        it = d["items"]
        a = d["adaptive"]
        L.append(f"| {lg.upper()} | **{d['state']}** | " + " | ".join(it[k]["state"] for k in ITEM_NAMES)
                 + f" | {d['sweep_b'] if d['sweep_b'] is not None else '-'}"
                 + f" | {d['ship_b'] if d['ship_b'] is not None else '-'} | {a['buffer_s']:g} s |")
    L.append("")
    for lg, d in r["leagues"].items():
        it, a = d["items"], d["adaptive"]
        i1, i2 = it["1_rule1"], it["2_beats_live"]
        L += [f"## {lg.upper()}: {d['state']}", "",
              f"- Rule #1 at the shipped B: {i1['sim_mid_play']} mid-play over {i1['sim_runs']} sweep runs "
              f"(per harness {i1['runs_per_harness']}), clock-truth live {i1['clock_truth_live']}; whole grid: "
              f"{d['grid_mid_play_total']} mid-play over {d['grid_runs']} runs. First picks (own line): "
              f"{'MISSING (PENDING)' if i1['first_pick_mid_play'] is None else i1['first_pick_mid_play']} mid-play in the sweep, {i1['shadow_first_pick_live']} live in the shadow. Shadow: {i1['shadow_entries']} "
              f"entries, {i1['shadow_live']} live.",
              f"- Sweep: verdict {i2.get('verdict') or 'not in yet'}, B {i2.get('chosen_b')}, needs "
              f"{i2.get('needs') or 'nothing'}, {i2.get('fit_nights') or 0} fit nights, primary "
              f"{json.dumps(i2.get('primary')) if i2.get('primary') else 'n/a'}.",
              "- Guardrails: " + (", ".join(f"{k} {'ok' if g.get('ok') else 'MISS'} ({g.get('value')})"
                                             for k, g in (d.get("guardrails") or {}).items()) or "n/a") + ".",
              f"- Oracle ceiling by B (lane 1, % of hindsight big plays, descriptive): {d['oracle'] or 'n/a'}.",
              f"- Shadow vs live tonight: {json.dumps(d['shadow_card']) if d['shadow_card'] else 'not graded yet'}.",
              f"- Locked: {it['4_locked']['state']}. Sync: {it['5_sync']['state']}"
              + (f" (p95 {it['5_sync'].get('p95_err_s')} s over {it['5_sync'].get('n')})"
                 if it['5_sync'].get('p95_err_s') is not None else "") + ".",
              f"- Adaptive B = feed lag p95 {a['feed_lag_p95_s']:g} + sync {a['sync_err_s']:g} + tune/seek "
              f"{a['tune_seek_s']:g} + margin {a['margin_s']:g} - credit {a['stream_delay_credit_s']:g} = "
              f"{a['formula_s']:g} s; ratchet floor {a['ratchet_min_s']:g} s ({a['ratchet_source']}); hard floor "
              f"{a['floor_s']:g} s, cap {a['cap_s']:g} s -> **{a['buffer_s']:g} s**.",
              f"- Ship B: {d['ship_b'] if d['ship_b'] is not None else 'n/a'} ({d['ship_why']}).",
              f"- RC8 on now: {'yes' if d['rc8_on_now'] else 'no'}."
              + (f" To flip: `{d['flip_cmd']}` (rollback: `python scripts/rc8_flip.py {lg} off`)."
                 if d["flip_cmd"] else ""), ""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sweep", type=Path, default=Path("/mnt/project-files/lane3/buffer-sweep/verdict.json"))
    ap.add_argument("--shadow", type=Path, default=None)
    ap.add_argument("--shadow-card", type=Path, default=None)
    ap.add_argument("--sync", type=Path, default=None)
    ap.add_argument("--locked", type=Path, default=None)
    ap.add_argument("--oracle", type=Path, nargs="*", default=[Path("/mnt/project-files/lane1/oracle/summary.json"),
                                                               Path("/mnt/project-files/lane1/oracle/primary/summary.json")])
    ap.add_argument("--leagues", default=",".join(LEAGUES))
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--md", type=Path, default=None)
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args(argv)
    try:
        ins = {k: _load(getattr(a, k)) for k in ("sweep", "shadow", "shadow_card", "sync", "locked")}
    except (OSError, json.JSONDecodeError) as e:
        print(f"unreadable input: {e}", file=sys.stderr)
        return 2
    cfg = Config(a.config) if a.config else Config()
    try:
        oracles = {("primary feed" if p.parent.name == "primary" else "p90 feed"): _load(p) for p in a.oracle}
    except (OSError, json.JSONDecodeError) as e:
        print(f"unreadable oracle summary: {e}", file=sys.stderr)
        return 2
    r = report(cfg, ins["sweep"], ins["shadow"], ins["shadow_card"], ins["sync"], ins["locked"],
               tuple(x for x in a.leagues.split(",") if x), oracles)
    md = to_md(r)
    if a.md:
        a.md.write_text(md)
    if a.json:
        a.json.write_text(json.dumps(r, indent=1))
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
