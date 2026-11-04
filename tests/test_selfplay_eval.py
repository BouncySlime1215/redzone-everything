"""scripts/selfplay_eval.py: pct_of_oracle matches northstar's definition, and sources are summarized apart."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import selfplay_eval as SE  # noqa: E402


def _t(big_live, crunch_live, dead_s, big=10, crunch=10, watched_s=1000):
    return dict(big=big, big_live=big_live, crunch=crunch, crunch_live=crunch_live, dead_s=dead_s, watched_s=watched_s)


def test_pct_of_oracle_is_worst_capped_share():
    orc = {"big_live": _t(10, 9, 300), "crunch_live": _t(9, 10, 300), "dead_air": _t(5, 5, 100)}
    r = SE.pct_of_oracle(_t(8, 9, 200), orc)
    assert r["shares"] == {"big_live": 80.0, "crunch_live": 90.0, "dead_air": round(100 * 80 / 90, 1)}
    assert r["pct"] == 80.0 and r["worst"] == "big_live"
    # beating the ceiling caps at 100, never above
    assert SE.pct_of_oracle(_t(10, 10, 50), orc)["pct"] == 100.0


def test_pct_of_oracle_needs_all_three_objectives():
    r = SE.pct_of_oracle(_t(8, 9, 200), {"big_live": _t(10, 9, 300)})
    assert r["pct"] is None and r["shares"] == {"big_live": 80.0}


def test_summarize_keeps_sources_apart():
    rows = []
    for src, live in (("real", 10), ("synthetic", 5)):
        row = {"source": src, "slate": "x", "games": 2, "hours": 3.0, "hand": _t(live, 10, 100)}
        for o in SE.OBJ:
            row[f"oracle:{o}"] = _t(10, 10, 100)
        rows.append(row)
    s = SE.summarize(rows, ["hand"], cue_s=0)
    assert s["real"]["policies"]["hand"]["pct_of_oracle"]["pct"] == 100.0
    assert s["synthetic"]["policies"]["hand"]["pct_of_oracle"]["pct"] == 50.0
    assert s["synthetic"]["policies"]["hand"]["pct_of_oracle_cue"] is None
    assert "| synthetic (1) | hand | 50.0 |" in SE.markdown(s)
