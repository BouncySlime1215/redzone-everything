"""Feed race scorer (tracker items 4 + 5): lag, head to head, screen lead, score-bug race with Polymarket."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import feed_race_score as FR  # noqa: E402

W0 = 1_800_000_000.0


def _night(tmp_path):
    rec = tmp_path / "rec"
    rec.mkdir()
    rows = []
    for i in range(40):                              # one play every 30 s; cdn sees it at +8 s, ESPN at +30 s
        w = W0 + 30 * i
        rows.append({"t": w + 8, "wall": w, "league": "nba", "game": "g1", "play": f"c{i}", "type": "Jump Shot",
                     "source": "nba_cdn"})
        rows.append({"t": w + 30, "wall": w + 0.4, "league": "nba", "game": "g1", "play": f"e{i}",
                     "type": "Jump Shot", "source": "espn_summary"})
    (rec / "plays.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    sync = [{"t": W0 + 30 * 10 + 45, "target": "prime:x", "kind": "score", "z": 45.0, "mu": 45.0},
            {"t": W0 + 30 * 10 + 20, "target": "prime:live", "kind": "score", "z": 20.0, "mu": 20.0},
            {"t": W0 + 30 * 20 + 45, "target": "prime:x", "kind": "score", "z": 45.0, "mu": 45.0}]
    (tmp_path / "sync.jsonl").write_text("\n".join(json.dumps(r) for r in sync) + "\n")
    poly = [{"row": "tick", "recv_ts": W0}, {"row": "trigger", "recv_ts": W0 + 30 * 10 + 5}]
    (tmp_path / "poly.jsonl").write_text("\n".join(json.dumps(r) for r in poly) + "\n")
    return rec


def test_lag_and_head_to_head(tmp_path):
    r = FR.score(_night(tmp_path))
    assert r["lag"]["nba"]["nba_cdn"]["p50"] == 8.0 and r["lag"]["nba"]["espn_summary"]["p50"] == 29.6
    h = r["head_to_head"]["nba"]["espn_summary>nba_cdn"]
    assert h["p50"] == -22.0 and h["n"] >= 30          # cdn 22 s sooner on the same play


def test_score_bug_race_with_polymarket_and_live_edge(tmp_path):
    rec = _night(tmp_path)
    r = FR.score(rec, tmp_path / "sync.jsonl", tmp_path / "poly.jsonl", screen="prime:x")
    b = r["score_bug"]
    assert b["reads"] == 2 and b["screen"] == "prime:x"
    assert b["lead_s"]["nba_cdn"]["p50"] == 37.0                   # 45 s screen delay - 8 s cdn lag
    assert b["lead_s"]["polymarket_trigger"]["n"] == 1 and b["lead_s"]["polymarket_trigger"]["p50"] == 40.0
    assert b["lead_s"]["screen:prime:live"]["p50"] == 25.0          # the live-edge stick shows it 25 s sooner
    assert {s: v["n"] for s, v in b["first"].items()} == {"polymarket_trigger": 1, "nba_cdn": 1}
    assert r["screen_lead"]["prime:x"]["nba"]["nba_cdn"] == 37.0
    assert "Score-bug race on prime:x" in FR.render(r)


def test_empty_logs_do_not_crash(tmp_path):
    rec = tmp_path / "empty"
    rec.mkdir()
    (rec / "plays.jsonl").write_text("")
    r = FR.score(rec, tmp_path / "missing.jsonl")
    assert r["plays"] == 0 and r["score_bug"] == {"reads": 0}
    assert "No score-bug reads" in FR.render(r)
