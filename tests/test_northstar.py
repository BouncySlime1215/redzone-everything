"""scripts/northstar.py: the North Star table, its statuses, the PR delta and the roadmap ranking."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import northstar as ns  # noqa: E402


def night(i, *, cuts=0, mid=0, land=40, dead_land=40, big=10, live=10, seen=10, my_big=0, my_live=0, crunch_live=10):
    return {"night": f"2026100{i}", "cuts": cuts, "entries_mid_play": mid, "landings": land,
            "landings_dead": dead_land, "big": big, "big_live": live, "big_seen": seen,
            "my_big": my_big, "my_big_live": my_live, "dead_s": 100.0, "watched_s": 1000.0,
            "crunch": 10, "crunch_live": crunch_live, "my_crunch": 3, "my_crunch_live": 3}


def cards(n=9, **kw):
    per = [night(i, **kw) for i in range(n)]
    return {lg: {"metrics": {"per_night": per, "dead_air_pct": 10.0}} for lg in ns.LEAGUES}


def rows_by(rows):
    return {r["key"]: r for r in rows}


def test_perfect_slice_passes_where_evidence_allows():
    r = rows_by(ns.table(cards(9, my_big=2, my_live=2)))
    assert r["mid_play"]["status"] == ns.PASS and r["mid_play"]["value"] == 0.0   # 1080 landings, 27 nights
    assert r["landings_dead"]["status"] == ns.PASS
    assert r["big_live_nba"]["status"] == ns.PASS
    # live-or-replayed without replay age graded is an upper bound: never PASS
    assert r["big_seen_10min"]["status"] == ns.PENDING
    c = cards(9)
    for lg in ns.LEAGUES:
        for x in c[lg]["metrics"]["per_night"]:
            x["big_seen_10m"] = x["big"]
            x["my_big_seen_10m"] = x["my_big"] = 3
    r2 = rows_by(ns.table(c))
    assert r2["big_seen_10min"]["status"] == ns.PASS and r2["your_team_big"]["status"] == ns.PASS
    assert r["switch_p95"]["status"] == ns.PENDING        # only the 10 Nov 3 samples
    assert r["recovery"]["status"] == ns.PENDING          # no night run
    assert r["dead_air"]["status"] == ns.FAIL             # 10 % with a CI above 0


def test_one_mid_play_entry_fails_rule_one():
    r = rows_by(ns.table(cards(9, mid=1)))
    assert r["mid_play"]["status"] == ns.FAIL
    assert r["mid_play"]["value"] == pytest.approx(2.5)


def test_too_few_nights_is_pending_not_pass():
    r = rows_by(ns.table(cards(2)))
    assert r["landings_dead"]["status"] == ns.PENDING
    assert r["mid_play"]["status"] == ns.PENDING


def test_missing_scorecard_is_named():
    c = cards(9)
    c["nba"] = {"error": "exit 1"}
    r = rows_by(ns.table(c))
    assert "missing: nba" in r["landings_dead"]["detail"]
    assert r["big_live_nba"]["status"] == ns.PENDING


def test_switch_samples(tmp_path):
    log = tmp_path / "tv_input.jsonl"
    log.write_text("\n".join(json.dumps({"total_s": 1.9 + (i % 5) * 0.05, "verified": True}) for i in range(80))
                   + "\n" + json.dumps({"total_s": 9.0, "verified": False}) + "\nnot json\n")
    xs = ns.switch_samples([log])
    assert len(xs) == 80
    r = ns.switch_row(xs)
    assert r["status"] == ns.PASS and r["value"] <= 2.1
    assert ns.switch_row([4.0] * 80)["status"] == ns.FAIL


def test_recovery_from_night_reports():
    good = {"checks": [{"name": c, "ok": True} for c in ns.FAULT_CHECK.values()],
            "faults": [{"name": f} for f in ns.FAULT_CHECK] * 2, "seed": 1}
    bad = {"checks": [{"name": c, "ok": c != "feed_outage_handled"} for c in ns.FAULT_CHECK.values()],
           "faults": [{"name": f} for f in ns.FAULT_CHECK] * 2, "seed": 2}
    assert ns.recovery_row([dict(good, seed=s) for s in range(8)])["status"] == ns.PASS
    r = ns.recovery_row([dict(good, seed=s) for s in range(5)] + [dict(bad, seed=s) for s in range(3)])
    assert r["status"] == ns.FAIL and "feed_outage" in r["detail"]
    assert ns.recovery_row([{"error": "timed out", "seed": 3}])["value"] == 0.0


def test_delta_and_history(tmp_path):
    head = {"rows": ns.table(cards(9))}
    base = {"rows": ns.table(cards(9, mid=1))}
    md = ns.delta_md(head, base)
    assert "Mid-play" in md and "🟢" in md and "Moved:" in md
    hist = tmp_path / "h.jsonl"
    hist.write_text(json.dumps(ns.history_row("dev", base["rows"], "abc", "t")) + "\n")
    page = ns.render({"dev": {"cards": cards(9), "rows": head["rows"]}}, "def", "t2", hist)
    assert "-2.50 🟢" in page


def test_roadmap_rank_orders_priority_then_score():
    rows = ns.table(cards(9, mid=1, live=5))
    items = [{"id": "a", "metric": "big_live_nfl", "expected_delta": 40, "effort_days": 1, "owner": "x", "status": "queued"},
             {"id": "b", "metric": "mid_play", "expected_delta": 2, "effort_days": 1, "owner": "x", "status": "queued"},
             {"id": "c", "metric": None, "effort_days": 1, "owner": "x", "status": "queued", "priority": 1},
             {"id": "d", "metric": "landings_dead", "expected_delta": 1, "effort_days": 1, "owner": "x", "status": "done"}]
    got = [x["id"] for x in ns.rank(items, rows)]
    assert got[0] == "c" and got[-1] == "d"
    with pytest.raises(ValueError):
        ns.rank([{"id": "z", "metric": "nope", "effort_days": 1}], rows)


def test_committed_roadmap_is_valid():
    src = json.loads((ns.ROOT / "docs/roadmap.json").read_text())
    ranked = ns.rank(src["items"], ns.table(cards(9)))
    assert len(ranked) == len(src["items"])
    md = ns.render_roadmap(src, ns.table(cards(9)), "x", "t")
    assert "P1" in md and "Planning loop" in md


def test_priority_rows():
    r = rows_by(ns.table(cards(9, crunch_live=8)))
    assert r["crunch_live_nfl"]["status"] == ns.PENDING        # 80 % but the feasible ceiling isn't computed
    assert r["crunch_live_nfl"]["value"] == pytest.approx(80.0)
    assert r["your_team_crunch"]["status"] == ns.PASS
    assert r["league_order"]["status"] == ns.PENDING and r["right_game"]["status"] == ns.PENDING
    assert rows_by(ns.table(cards(9)))["crunch_live_nba"]["status"] == ns.PASS


def test_sync_rows():
    assert ns.settle_row(None)["status"] == ns.PENDING
    reps = [{"seed": i, "settle_book": {"n": 20, "ok": 20}} for i in range(8)]
    assert ns.settle_row(reps)["status"] == ns.PASS
    assert ns.settle_row([{"seed": i, "settle_book": {"n": 20, "ok": 17}} for i in range(8)])["status"] == ns.FAIL
    assert rows_by(ns.table(cards(9)))["delay_err_p95"]["status"] == ns.PENDING


def test_profile_run_keeps_its_candidate_column(tmp_path, monkeypatch):
    """Regression: a later results[split] assignment dropped the --profile column (cycle 01)."""
    calls = []

    def fake_run(root, split, timeout=1800, profile=None):
        calls.append(profile)
        return cards(mid=1 if profile is None else 0)

    monkeypatch.setattr(ns, "run_cards", fake_run)
    out = tmp_path / "ns.json"
    ns.main(["--split", "dev", "--profile", "candidate", "--no-write", "--json", str(out)])
    res = json.loads(out.read_text())["dev"]
    assert calls == [None, "candidate"]
    assert "candidate" in res and res["candidate"]["profile"] == "candidate"
    assert rows_by(res["candidate"]["rows"])["mid_play"]["value"] == 0
    assert rows_by(res["rows"])["mid_play"]["value"] > 0


def test_right_game_reads_store_taps(tmp_path, monkeypatch):
    import time as _time
    from redzone.store import Store
    assert ns.load_taps(tmp_path / "none.db") is None and not (tmp_path / "none.db").exists()
    db = tmp_path / "redzone.db"
    st = Store(db)
    base = 1_793_000_000.0                       # an evening, Eastern
    for night in range(9):
        for k in range(8):
            monkeypatch.setattr(_time, "time", lambda t=base + night * 86400 + k * 60: t)
            st.feedback("g1", 1 if k else -1, "switch", {"reason": "crunch", "switch": k})
    monkeypatch.setattr(_time, "time", lambda: base + 10 * 86400)
    st.feedback("g1", 0, "missed", {})
    st.feedback("g1", 0, "switch", {})          # cleared: not counted
    monkeypatch.undo()
    taps = ns.load_taps(db)
    assert len(taps) == 74
    r = ns.right_game_row(taps)
    assert r["key"] == "right_game" and r["nights"] == 10
    assert abs(r["value"] - 100 * 63 / 73) < 0.01 and r["status"] == ns.FAIL      # 86 % < 95 %
    assert ns.right_game_row(None)["status"] == ns.PENDING
