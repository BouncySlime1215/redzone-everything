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
    # [grader v47] no locked scorecard yet for these; the gate shows a PENDING row per league, never nothing
    "cfb": "PENDING: no locked scorecard (CFB plays NFL rules; scripts/ci_scorecard.py --leagues cfb)",
    "cbb": "PENDING: no locked scorecard (scripts/ci_scorecard.py --leagues cbb)",
    "mlb": "PENDING: no locked scorecard (scripts/ci_scorecard.py --leagues mlb)",
}
# Nick's leagues, in his order (WCBB is off, Nick Nov 4 / Nov 8). A league with no scorecard gets a PENDING
# row so the report says "not measured" instead of saying nothing; a gate that has no row cannot fail.
GATED_LEAGUES = ("nfl", "cfb", "nba", "cbb", "nhl", "mlb")
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


# [eval honesty, research 05 §2.2] The NIGHT is the unit of evidence: plays and switches within a night
# share the stream delay, the slate and the decider's state, so a rate gate gets a 95 % interval from a
# cluster bootstrap over nights. PASS needs >= MIN_NIGHTS nights, >= MIN_DECISIONS decisions in the slice
# and the interval's LOWER bound at the bar; a slice whose upper bound is under the bar FAILS; anything
# in between is PENDING (not enough evidence either way). Pending never counts as passing.
MIN_NIGHTS = 8
MIN_DECISIONS = 60
BOOT_DRAWS = 2000


def night_ci(rows: list[tuple[int, int]], draws: int = BOOT_DRAWS, seed: int = 7) -> tuple[float, float, float]:
    """rows: (good, total) per night. Pooled rate and its 95 % cluster-bootstrap interval, in %."""
    import random
    rows = [(g, n) for g, n in rows if n]
    tot = sum(n for _, n in rows)
    if not tot:
        return float("nan"), float("nan"), float("nan")
    point = 100.0 * sum(g for g, _ in rows) / tot
    if len(rows) == 1:
        return point, *wilson(rows[0][0], rows[0][1])
    rng = random.Random(seed)
    rates = []
    for _ in range(draws):
        pick = [rows[rng.randrange(len(rows))] for _ in rows]
        n = sum(x[1] for x in pick)
        rates.append(100.0 * sum(x[0] for x in pick) / n)
    rates.sort()
    return point, rates[int(0.025 * draws)], rates[min(draws - 1, int(0.975 * draws))]


def wilson(good: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 95 % interval for good/n, in % (used for a single night)."""
    if not n:
        return float("nan"), float("nan")
    p = good / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return 100.0 * max(0.0, c - h), 100.0 * min(1.0, c + h)


def rate_gate(rows: list[tuple[int, int]], bar: float) -> tuple[str, str]:
    """(status, detail) for 'rate >= bar %' judged by nights: FAIL when the point estimate (or the
    whole CI) is under the bar, PASS only with enough nights and decisions and a lower bound at the bar."""
    nights = [r for r in rows if r[1]]
    n = sum(t for _, t in nights)
    point, lo, hi = night_ci(nights)
    if not n:
        return PENDING, "no decisions in this slice (0/0)"
    detail = (f"{point:.1f}% ({sum(g for g, _ in nights)}/{n}), 95% CI {lo:.1f}-{hi:.1f} over "
              f"{len(nights)} night{'s' if len(nights) != 1 else ''}")
    if hi < bar:
        return FAIL, detail
    if point < bar:     # too few nights to prove a pass is no excuse for a miss we can already see
        return FAIL, detail + f"; point estimate under {bar:g}"
    if len(nights) >= MIN_NIGHTS and n >= MIN_DECISIONS and lo >= bar:
        return PASS, detail
    short = [f"{len(nights)}/{MIN_NIGHTS} nights" if len(nights) < MIN_NIGHTS else "",
             f"{n}/{MIN_DECISIONS} decisions" if n < MIN_DECISIONS else "",
             f"lower bound {lo:.1f} < {bar:g}" if lo < bar else ""]
    return PENDING, detail + "; needs " + ", ".join(x for x in short if x)


def _ratio(s) -> tuple[int, int] | None:
    """'3/12 (25%)' -> (3, 12); None when the card carries no such count."""
    try:
        a, b = str(s).split(" ")[0].split("/")
        return int(a), int(b)
    except (ValueError, IndexError):
        return None


def _counts(m: dict, per: list[dict], done: str, total: str, key: str) -> tuple[int, int] | None:
    """(done, total) the card MEASURED: per-night counts first, else its 'a/b' string; None when it emits none."""
    if per and any(total in x for x in per):
        return sum(int(x.get(done) or 0) for x in per), sum(int(x.get(total) or 0) for x in per)
    return _ratio(m.get(key))


def zero_bound(n: int) -> str:
    """Rule of three: 0 events in n trials bounds the rate at <= 3/n with 95 % confidence."""
    return f"<= {300.0 / n:.2f}% (rule of three, {n} trials)" if n else "no trials"


def gates(cards: dict, baseline: dict | None = None, slice_info: dict | None = None) -> list[tuple[str, str, str, str]]:
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
        if not name.startswith("demo"):
            # [grader v47, F4] the gate is judged ONLY on the locked split; a card from `--split all` or
            # a train split (or one that doesn't say) can't pass it. ci_scorecard stamps `split` on each card.
            sp = m.get("split", (slice_info or {}).get("split"))
            add("graded on the locked split", name, sp == HELDOUT["split"], f"split={sp!r}, need {HELDOUT['split']!r}")
        cuts = m.get("mid_play_cuts")
        n, mid = _entries(m)
        add("mid-play cuts = 0", name, cuts == 0,
            f"{cuts}" + (f"; true rate {zero_bound(n)}" if cuts == 0 and n else ""))
        per = [x for x in m.get("per_night") or [] if isinstance(x, dict)]
        if per:
            rows_n = [(int(x.get("entries") or 0) - int(x.get("entries_mid_play") or 0), int(x.get("entries") or 0))
                      for x in per]
            st, det = rate_gate(rows_n, 95.0)
            rows.append(("entries at a dead ball >= 95%", name, st, det))
            # <= 5 % mid-play is the same rows read the other way (good = not mid-play): no second verdict
            # that could disagree; it prints the mid-play share for the report.
            if n:
                rows.append(("entries mid-play <= 5%", name, st, f"{100.0 * mid / n:.1f}% ({mid}/{n}), judged with the row above"))
            # [grader v47, R1/F3] Rule #1: 0 mid-play entries is HARD on this path too. The 95% row above
            # is the softer statistic (it would let 5% through); this one is the rule.
            add("entries mid-play = 0", name, mid == 0,
                f"{mid}" + (f" in {n} entries; true rate {zero_bound(n)}" if mid == 0 and n else ""))
            bn, bmid = _entries(_m(base.get(name, {})))
            if bmid is not None and n:
                add("entries mid-play never above today", name, mid <= bmid, f"{mid} vs today {bmid}")
            if not name.startswith("demo"):
                # [grader v47, R9] Nick's pass bar: 0 commercial seconds. Entries that land mid-commercial and
                # the share of watched time spent in a break were measured but never gated.
                if any("entries_mid_break" in x for x in per):
                    mb = sum(int(x.get("entries_mid_break") or 0) for x in per)
                    add("entries mid-commercial = 0", name, mb == 0, f"{mb} of {n} entries landed with > 10 s of break left")
                else:
                    add("entries mid-commercial = 0", name, False, "card has no entries_mid_break count", pending=True)
                dd = [(float(x.get("dead_s") or 0), float(x.get("watched_s") or 0)) for x in per if x.get("watched_s")]
                if dd:
                    pt, lo, _hi = night_ci(dd)
                    add("dead air (commercials on screen) = 0", name, lo <= 0.0 and pt <= 0.0,
                        f"{pt:.1f}% of watched time (95% CI lower bound {lo:.1f})", pending=(pt > 0.0 and lo <= 0.0))
                else:
                    add("dead air (commercials on screen) = 0", name, False, "card has no dead_s / watched_s", pending=True)
        elif n:
            dead = 100.0 * (n - mid) / n
            add("entries at a dead ball >= 95%", name, dead >= 95.0, f"{dead:.1f}% ({n - mid}/{n}); no per-night counts: point estimate")
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
            add("crunch live >= 95% or feasible ceiling", name, cr is not None and cr >= 95,   # [grader v47] measured under 95 FAILS
                f"{cr:.1f}%" if cr is not None else "n/a", pending=cr is None)
            continue
        if per and any(x.get("big") for x in per):
            st, det = rate_gate([(int(x.get("big_seen") or 0), int(x.get("big") or 0)) for x in per], 95.0)
            rows.append(("big plays seen >= 95%", name, st, det))
        elif m.get("big_seen_pct") is not None:
            add("big plays seen >= 95%", name, m["big_seen_pct"] >= 95.0, f"{m['big_seen_pct']}%")
        if per and any("big_seen_10m" in x for x in per) and any(x.get("big") for x in per):
            # [catch-up the RedZone way] Nick, Nov 4: never miss a big play for long. Live, or replayed
            # within 10 min (backtest.CATCHUP_S); your team's all of them; no crunch play given up for one.
            st, det = rate_gate([(int(x.get("big_seen_10m") or 0), int(x.get("big") or 0)) for x in per], 99.0)
            rows.append(("catch-up: big plays seen live or replayed within 10 min >= 99%", name, st, det))
            mb = sum(int(x.get("my_big") or 0) for x in per)
            ms = sum(int(x.get("my_big_seen_10m") or 0) for x in per)
            add("catch-up: your-team big plays within 10 min = 100%", name, ms >= mb,
                f"{ms}/{mb}" if mb else "no your-team big plays in this slice (0/0)", pending=not mb)
            cor = sum(int(x.get("crunch_on_replay") or 0) for x in per)
            add("catch-up: crunch plays aired during a replay = 0", name, cor == 0, f"{cor}")
        if m.get("your_team_big_seen_pct") is not None:
            add("your-team big plays seen = 100%", name, m["your_team_big_seen_pct"] >= 100.0, f"{m['your_team_big_seen_pct']}%")
        if m.get("your_team_crunch_live_pct") is not None:
            v = m["your_team_crunch_live_pct"]
            # [grader v47, F5/R9] The scorecards print 0/0 as 0.0, so a real 0% hid as "no plays". Read the
            # counts (per-night my_crunch / my_crunch_live, or the 'a/b' string): n == 0 is PENDING, a measured
            # 0% is a FAIL. A card with no count at all and a 0.0 can't be told apart: PENDING, and says why.
            c = _counts(m, per, "my_crunch_live", "my_crunch", "your_team_crunch_live")
            if c is not None and c[1] > 0:
                add("your-team crunch live = 100%", name, c[0] >= c[1], f"{c[0]}/{c[1]} ({100.0 * c[0] / c[1]:.1f}%)")
            elif c is not None:
                add("your-team crunch live = 100%", name, False, "no your-team crunch plays in this slice (0/0)", pending=True)
            else:
                add("your-team crunch live = 100%", name, v >= 100.0,
                    f"{v}%" if v else "0.0 with no play count on the card: cannot tell 0% from 0/0", pending=v == 0.0)
        cr = m.get("crunch_live_pct")
        if cr is not None:
            # [grader v47, F5/R9] Under the league order (NFL > CFB > NBA > rest) a lower-priority overlap that
            # gets a replay is not a miss, but that ceiling is not built: until a card carries a computed
            # `crunch_live_ceiling_pct`, a MEASURED value under the bar FAILS. PENDING only when n == 0.
            n_cr = m.get("crunch_plays")
            if n_cr is None and per and any("crunch" in x for x in per):
                n_cr = sum(int(x.get("crunch") or 0) for x in per)
            bar = min(95.0, float(m["crunch_live_ceiling_pct"])) if m.get("crunch_live_ceiling_pct") is not None else 95.0
            if n_cr == 0 or (n_cr is None and not cr):
                add("crunch live >= 95% or feasible ceiling", name, False, "no crunch plays measured (n = 0)", pending=True)
            else:
                add("crunch live >= 95% or feasible ceiling", name, cr >= bar,
                    f"{cr}%" + (f" of {n_cr} plays" if n_cr else "") + ("" if cr >= bar else f"; under {bar:g}, no feasible ceiling explains it"))
    # [grader v47, R2] every one of Nick's leagues gets a row: PENDING (not absent) when it has no scorecard
    for lg in GATED_LEAGUES:
        if lg not in cards:
            add("league scorecard present", lg, False, HELDOUT.get(lg, "no scorecard"), pending=True)
            add("entries mid-play = 0", lg, False, "not measured: Rule #1 has no card to grade", pending=True)
    for gate, why in (("reels >= 95% in dead time, 0 live big plays missed on a reel", "ESPN clip reels are not simulated; rewind replays are graded by the catch-up rows and the cut grade"),
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



# [per-league promotion] Nick, Nov 5 2026 8:20 PM ET: "the gains are big; the gates are goals and targets, keep
# working to them, but take the wins along the way." A candidate change may go live PER LEAGUE when, in THAT
# league, it has 0 mid-play entries on dev AND locked and beats live on both: no line worse, at least one better.
# Never-join-mid-play stays absolute. The full gates above still define "done".
PROMOTE_HIGHER = ("big_live_pct", "crunch_live_pct", "big_seen_pct", "big_seen_10m_pct", "your_team_crunch_live_pct")
PROMOTE_LOWER = ("dead_air_pct", "plays_missed_entry")


def league_promotion(live: dict, cand: dict, league: str) -> tuple[str, str]:
    """(status, detail) for promoting `cand` over `live` in one league. live / cand: {"dev": cards, "locked": cards}
    (scorecards graded with the EXACT settings set that would go live). PASS = promote in this league."""
    notes, better, worse = [], 0, []
    for split in ("dev", "locked"):
        lm, cm = _m((live.get(split) or {}).get(league, {})), _m((cand.get(split) or {}).get(league, {}))
        if not lm or not cm:
            return PENDING, f"{split}: no {league} scorecard for both profiles"
        mid = cm.get("entries_mid_play")
        if mid is None:
            return PENDING, f"{split}: candidate card has no entries_mid_play"
        if mid != 0:
            return FAIL, f"{split}: candidate joins mid-play {mid} times in {league} (never-join-mid-play is absolute)"
        # [grader v47, R10] Leaves too: no PR may raise mid-play leaves, and Nick's pass bar is 0 (every sport).
        cuts = cm.get("mid_play_cuts")
        if cuts is None:
            return PENDING, f"{split}: candidate card has no mid_play_cuts (leaves unmeasured)"
        if cuts != 0:
            return FAIL, f"{split}: candidate leaves mid-play {cuts} times in {league} (pass bar: 0)"
        for k in PROMOTE_HIGHER + PROMOTE_LOWER:
            a, b = _pct(lm.get(k)), _pct(cm.get(k))
            if a is None and b is None:
                continue
            if b is None:   # [grader v47, R10] a line live has and the candidate dropped is not "no change"
                return PENDING, f"{split}: candidate card is missing {k} (judged on fewer lines)"
            if a is None:
                continue
            d = (b - a) if k in PROMOTE_HIGHER else (a - b)
            if d < -1e-9:
                worse.append(f"{split} {k} {a:g} -> {b:g}")
            elif d > 1e-9:
                better += 1
            notes.append(f"{split} {k} {a:g}->{b:g}")
    if worse:
        return FAIL, "worse than live: " + "; ".join(worse)
    if not better:
        return FAIL, "no line better than live"
    return PASS, f"0 mid-play entries on dev + locked; {better} lines better, none worse ({'; '.join(notes)})"


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
    doc = json.loads(a.scorecards.read_text())
    cards = doc.get("cards", {})
    base = json.loads(a.baseline.read_text()) if a.baseline and a.baseline.exists() else None
    rows = gates(cards, base, doc.get("slice"))
    md = markdown(rows)
    print(md)
    if a.md:
        a.md.write_text(md + "\n")
    return 1 if any(r[2] == FAIL for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
