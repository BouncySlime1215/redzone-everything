"""[RC7 P0] A BENCHED source (merge.py: its copy regressed against a faster feed) still answers, so it is
not a failing feed. RC6.1 rolled back twice on Nov 4 ("N sports feeds failing vs 0 before") when deploy.py
counted benches as failures. The engine keeps ok = reachable + fresh and reports benched on its own; the
deploy watch reads both the old shape (bench -> ok:false) and the new one the same way."""
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import deploy as D  # noqa: E402

from redzone.engine import Engine  # noqa: E402
from redzone.sources.merge import SourceHealth  # noqa: E402


def _status(health: dict) -> dict:
    e = SimpleNamespace(merger=SimpleNamespace(health=health), source_status={})
    Engine._source_health(e)
    return e.source_status


def test_engine_reports_a_benched_but_fresh_source_as_ok_and_benched():
    now = time.time()
    st = _status({"espn_summary": SourceHealth(updates=9, last_ok=now - 3, benched=True),
                  "nba": SourceHealth(updates=9, last_ok=now - 2),
                  "nhl": SourceHealth(updates=1, last_ok=now - 600)})
    assert st["espn_summary"]["ok"] is True and st["espn_summary"]["benched"] is True
    assert st["nba"]["ok"] is True and st["nba"]["benched"] is False
    assert st["nhl"]["ok"] is False                       # really unreachable for 10 min


def _health(sources):
    return D.Health.of({"health": {"sources": sources, "sticks": []}})


def test_tonights_case_a_bench_is_not_a_failing_feed_in_either_engine_shape():
    old_shape = [{"name": "espn_summary", "ok": False, "benched": True, "age_s": 4},   # RC6.1's engine
                 {"name": "nba", "ok": True, "benched": False, "age_s": 2}]
    new_shape = [{"name": "espn_summary", "ok": True, "benched": True, "age_s": 4},    # RC7's engine
                 {"name": "nba", "ok": True, "benched": False, "age_s": 2}]
    before, after = _health(old_shape), _health(new_shape)
    assert before.sources_bad == after.sources_bad == 0
    # the watch's rule: more failing feeds after than before -> worse. A bench alone never trips it.
    assert not (after.sources_bad > before.sources_bad)


def test_a_really_unreachable_source_still_counts():
    assert _health([{"name": "nhl", "ok": False, "benched": False, "age_s": 600}]).sources_bad == 1
    assert _health([{"name": "nhl", "ok": False, "benched": True, "age_s": 600}]).sources_bad == 1   # benched AND stale
    assert _health([{"name": "nhl", "ok": False, "benched": True, "age_s": None}]).sources_bad == 1  # never answered
    assert _health([{"name": "nhl", "ok": False}]).sources_bad == 1


def test_source_failing_rule():
    assert not D.source_failing({"ok": True})
    assert not D.source_failing({"ok": True, "benched": True, "age_s": 3})
    assert not D.source_failing({"ok": False, "benched": True, "age_s": 3})
    assert D.source_failing({"ok": False, "benched": False, "age_s": 3})
