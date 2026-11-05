"""[one brain, step 1] One league order (docs/onebrain-migration.md): redzone.models.LEAGUE_ORDER, read
through league_order(cfg), is the only definition; the decision core, Noggin Priority and the plan
derive their ranks from it. Exact parity: every rank equals what the old copies returned."""
import ast
from pathlib import Path

import yaml

from redzone import plan
from redzone.decider import core
from redzone.models import LEAGUE_ORDER, League, league_index, league_order
from redzone.noggin import priority as P

ROOT = Path(__file__).resolve().parents[1]
OLD = ("nfl", "cfb", "nba", "cbb", "nhl", "mlb")   # the copies step 1 replaced, minus wcbb (Nick, Nov 4: off, unlisted)


class Cfg(dict):
    def get(self, k, d=None):
        return super().get(k, d)


def test_the_order_is_the_north_star_order_and_the_config_default():
    assert tuple(x.value for x in LEAGUE_ORDER) == OLD == league_order()
    shipped = yaml.safe_load((ROOT / "redzone" / "default_config.yaml").read_text())["core"]["league_priority"]
    assert tuple(shipped) == OLD
    assert set(LEAGUE_ORDER) == set(League) - {League.WCBB}


def test_core_and_priority_ranks_are_unchanged():
    k = core.Knobs()
    rules = P.HardRules()
    for lg in OLD + ("xfl", "", "wcbb"):
        old_core = len(OLD) - OLD.index(lg) if lg in OLD else 0
        old_pri = OLD.index(lg) if lg in OLD else len(OLD)
        assert core.league_rank(lg, k) == old_core
        assert rules.league_rank(lg) == old_pri


def test_a_config_override_reaches_every_reader():
    cfg = Cfg({"core.league_priority": ["NBA", "nfl"]})
    assert league_order(cfg) == ("nba", "nfl")
    assert core.Knobs.from_cfg(cfg).league_priority == ("nba", "nfl")
    assert P.HardRules.from_config(cfg).league_order == ("nba", "nfl")
    assert league_index(League.NFL, cfg) == 1 and league_index("mlb", cfg) == 2


def test_the_plan_orders_college_by_the_same_order():
    pos = plan.LEAGUE_POS
    assert sorted(pos, key=pos.get) == list(LEAGUE_ORDER) and League.WCBB not in pos
    # pro leagues keep their old relative order (NFL > NBA > NHL > MLB); college now slots in by the
    # North Star order instead of after everything (the one listed behaviour change, plan only)
    assert pos[League.NFL] < pos[League.NBA] < pos[League.NHL] < pos[League.MLB]
    assert pos[League.CFB] < pos[League.NBA]


def test_no_other_runtime_module_keeps_its_own_copy():
    # an order lists the college leagues too (a 4-league tuple like web/server.py:290 is a support list)
    for p in (ROOT / "redzone").rglob("*.py"):
        if "lab" in p.parts:
            continue
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.Tuple) and len(n.elts) >= 5 and all(
                    isinstance(e, ast.Constant) and e.value in OLD for e in n.elts):
                raise AssertionError(f"{p.relative_to(ROOT)}:{n.lineno} keeps its own league order")


def test_league_weight_follows_the_order_except_the_logged_exception():
    w = yaml.safe_load((ROOT / "redzone" / "default_config.yaml").read_text())["score"]["league_weight"]
    known = set()   # wcbb (0.9 < nhl 0.95) left the order with Nick's Nov 4 ruling
    bad = {(a, b) for i, a in enumerate(OLD) for b in OLD[i + 1:] if w[a] < w[b]}
    assert bad == known, bad


def test_wcbb_is_off_by_default_and_ranks_last():
    """[Nick, Nov 4 7:55 PM ET] Women's basketball isn't one of his leagues: off, out of the order."""
    cfg = yaml.safe_load((ROOT / "redzone" / "default_config.yaml").read_text())
    assert cfg["mode"]["leagues"]["wcbb"] is False
    assert "wcbb" not in cfg["core"]["league_priority"]
    assert core.league_rank("wcbb", core.Knobs()) == 0 and league_index(League.WCBB) == len(LEAGUE_ORDER)
