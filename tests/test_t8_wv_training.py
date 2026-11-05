"""[T8 tester] Watch Value training on real engagement labels (EXP-1704, PRs #314 / #317), black-box where possible.

Checks, each against docs/prereg/exp-1704.md and Nick's rules:
  1. Labels are joined to TRAIN nights only: on valid and locked rows the fit target stays the swing label `wv`.
  2. The night split is the shared one (`splits.league_split`, which honours a pin's `dev_before`), so the
     locked block is the same here as in every scorecard.
  3. No engagement label is a feature: the Watch Value feature list names no engagement column.
  4. No label source is Nick's viewing or presses, or Teacher / distilled output.
  5. The drama-math arm (A3) fits its suspense table on train games only.
Tests skip until the training code they need is on the branch.
"""
from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

BANNED = re.compile(r"(userstate|press(es)?\b|\btaps?\b|nick|viewing|viewer_log|watch_history|teacher|distill|"
                    r"synthetic|syn_|llm_label|reasoner_label)", re.I)
ENGAGEMENT = re.compile(r"(bsky|bluesky|wiki|pageview|edit_rate|velocity|mkt_vol|market_vol|attention|twitch|"
                        r"viewers|engagement|aux)", re.I)


def _tw():
    try:
        import train_watch_value as TW
    except ModuleNotFoundError:
        pytest.skip("scripts/train_watch_value.py not on this branch")
    if not hasattr(TW, "aux_targets"):
        pytest.skip("no EXP-1704 aux_targets yet (#317)")
    return TW


def _al():
    try:
        import attention_labels as AL
    except ModuleNotFoundError:
        pytest.skip("scripts/attention_labels.py not on this branch (#314)")
    return AL


def _games(split_of_game: dict[str, str], n: int = 30):
    rows = []
    for g, (gid, _s) in enumerate(split_of_game.items()):
        for i in range(n):
            rows.append({"gid": gid, "night": f"2025110{g}", "matchup": "LAL @ SAC", "wall": 1000.0 + 60 * i,
                         "wp_home": 0.5 + 0.3 * np.sin(i / 4 + g)})
    by_game: dict[str, list[int]] = {}
    for k, r in enumerate(rows):
        by_game.setdefault(r["gid"], []).append(k)
    split = np.array([split_of_game[r["gid"]] for r in rows])
    return rows, by_game, split


def _series(AL):
    s = AL.Series("bsky")
    for t in range(900, 3000, 60):
        s.add("LAL", t, float((t // 60) % 7))
    return [s.finish()]


@pytest.mark.parametrize("arm", ["A1", "A2", "A3"])
def test_engagement_and_drama_labels_touch_train_rows_only(arm):
    """Prereg: 'Labels are joined to train nights only.' Valid rows early-stop the fit, so a mixed target there
    is a label joined to valid; locked rows must never carry one."""
    TW, AL = _tw(), _al()
    rows, by_game, split = _games({"g_train": "train", "g_valid": "valid", "g_test": "test"})
    wv = np.linspace(0.05, 0.95, len(rows))
    y, _info = TW.aux_targets(rows, by_game, wv, split == "train", arm, _series(AL), seed=1)
    off = split != "train"
    assert not np.array_equal(y[~off], wv[~off]), "the arm changed nothing on train rows: test is not exercising it"
    bad = np.flatnonzero(off & ~np.isclose(y, wv))
    assert bad.size == 0, f"{arm}: fit target differs from wv on {bad.size} valid/locked rows"


def test_training_uses_the_shared_league_split():
    """splits.split_nights ignores a pin's dev_before; league_split does not. Every model must use the split
    every scorecard uses, or a night locked elsewhere can land in train here (splits.py docstring)."""
    TW = _tw()
    src = inspect.getsource(TW.league)
    assert "league_split" in src and "split_nights(" not in src, \
        "train_watch_value.league splits nights with split_nights, not league_split"


def test_no_engagement_column_is_a_feature():
    watch = pytest.importorskip("redzone.contracts.watch", reason="Watch Value contract (#166) not on this branch")
    from redzone.models import League
    for lg in (League.NBA, League.NFL, League.NHL):
        feats = watch.features_for(lg.base.value)
        bad = [f for f in feats if ENGAGEMENT.search(f)]
        assert not bad, f"{lg}: engagement columns in Watch Value features: {bad}"


def test_label_sources_exclude_nick_and_teacher():
    AL = _al()
    srcs = list(AL.WINDOWS)
    assert srcs
    assert not [s for s in srcs if BANNED.search(s)], srcs
    with pytest.raises(ValueError):                    # an unknown source is refused, not silently loaded
        AL.load_spec({"source": "teacher_labels", "paths": []})


def test_label_code_imports_no_nick_or_teacher_module():
    pat = re.compile(r"^\s*(from|import)\s+(redzone\.userstate|redzone\.lab\.night\.presses|redzone\.lab\.distill|"
                     r"redzone\.reasoner|redzone\.lab\.reasoner_data|distill|agent\.distill)\b", re.M)
    files = [ROOT / "scripts" / f for f in ("attention_labels.py", "wv_aux_label.py")]
    files = [p for p in files if p.exists()]
    if not files:
        pytest.skip("no engagement label code yet")
    bad = {p.name: pat.findall(p.read_text()) for p in files}
    assert not any(bad.values()), bad


def test_drama_suspense_table_fits_on_train_games_only(monkeypatch):
    TW, AL = _tw(), _al()
    rows, by_game, split = _games({"g_train": "train", "g_valid": "valid", "g_test": "test"})
    wv = np.linspace(0.05, 0.95, len(rows))
    seen = []
    real = AL.fit_suspense

    def spy(games):
        seen.extend(len(g) for g in games)
        return real(games)

    monkeypatch.setattr(AL, "fit_suspense", spy)
    TW.aux_targets(rows, by_game, wv, split == "train", "A3", None, seed=0)
    assert seen == [len(by_game["g_train"])], f"suspense table fit on {len(seen)} games, expected the 1 train game"
