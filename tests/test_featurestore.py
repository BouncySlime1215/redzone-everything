"""Feature store (task F-06): parity with the builders it replaced, cut data equals stored data, versions in the registry.

CI has no real nights, so everything here runs on the repo's fixture games and on tiny games built in the test:
  - value_v3 equals the notebook reader's own rows (tests/fixtures/featurestore_value_v3_golden.json, written by
    `scripts/feature_parity.py golden` from stage4_reader v3) to 1e-6;
  - state4 equals the A-03 arm's pre-F-06 Stage6Rows (restated below as the oracle) to 1e-6;
  - features at t from a cut feed (fence.cut_feed) equal features at t from the full stored game, for every set;
  - a planted look-ahead feature is caught, so the equality is not vacuous.
The 250-state real-night parity is scripts/feature_parity.py (run locally; its result is in the PR).
"""
from __future__ import annotations

import json
from bisect import bisect_right
from pathlib import Path

import numpy as np

from redzone import featurestore as fs
from redzone.lab import fence
from redzone.lab.armeval import stage6 as S6
from redzone.lab.backtest import Replay
from redzone.models import League
from redzone.models_rt import registry

FIX = Path(__file__).resolve().parent / "fixtures"
GOLDEN = FIX / "featurestore_value_v3_golden.json"
GAMES = (("nba", "espn_nba_summary_401909860.json"), ("nba", "espn_nba_summary_401909906.json"),
         ("cbb", "espn_cbb_summary_401820788.json"))


def _replay(lg, name):
    r = Replay.load(League(lg), FIX / name)
    assert r is not None and len(r.plays) > 50
    return r


# ---- the old in-app builder, exactly as redzone/lab/armeval/stage6.py had it before F-06 (the oracle) ------------------
_OLD_REG = {"nba": 4, "cbb": 2}
_OLD_WHISTLE = ("foul", "violation", "outofbounds", "substitution", "kickedball", "delay", "technical", "flagrant",
                "jumpball", "freethrow")


def _old_kind(type_text, period, half):
    tn = type_text.lower().replace(" ", "").replace("-", "").replace("\n", "")
    if "timeout" in tn:
        return 3
    if tn in ("endgame", "endofgame"):
        return 6
    if tn == "endperiod":
        return 5 if period == half else 4
    if "challenge" in tn or "review" in tn:
        return 2
    return 1 if any(w in tn for w in _OLD_WHISTLE) else 0


class _OldStage6Rows:
    def __init__(self, league, plays, wall):
        half, per = _OLD_REG.get(league, 4) // 2, 0
        self.wall = [float(w) for w in wall]
        self.kind, self.last_stop, self.last_to = [], [], []
        ls = lt = -1
        for i, p in enumerate(plays):
            try:
                per = int(float(((p.get("period") or {}).get("number")))) or per or 1
            except (TypeError, ValueError):
                per = per or 1
            k = _old_kind(str((p.get("type") or {}).get("text") or ""), per, half)
            ls = i if k > 0 else ls
            lt = i if k == 3 else lt
            self.kind.append(k)
            self.last_stop.append(ls)
            self.last_to.append(lt)

    def features(self, te, lev):
        j = min(max(bisect_right(self.wall, te) - 1, 0), len(self.wall) - 1)

        def since(k):
            return min(te - self.wall[k], 9999.0) if k >= 0 else 9999.0
        return {"lev_now": lev, "since_stop": since(self.last_stop[j]), "last_kind": float(self.kind[j]),
                "since_to": since(self.last_to[j])}


# ---- parity ------------------------------------------------------------------------------------------------------------
def test_value_v3_equals_the_notebook_reader_rows():
    doc = json.loads(GOLDEN.read_text())
    assert doc["reader_sha256"] == fs.FS_NOTEBOOK_READER_SHA256
    assert tuple(doc["features"]) == fs.FS_VALUE_V3_NAMES == fs.FS_SETS["value_v3"].names
    worst, n = 0.0, 0
    for g in doc["games"]:
        r = _replay(g["league"], g["file"])
        P = fs.fs_parse(g["league"], r.data)
        for row in g["rows"]:
            facts = g["facts"] if row["facts"] else None
            x = fs.fs_rows("value_v3", g["league"], P, [row["t"]], facts, row["post"], row["lag"], dtype=np.float32)[0]
            worst = max(worst, float(np.max(np.abs(x.astype(np.float64) - np.asarray(row["x"])))))
            n += 1
    assert n >= 90 and worst < 1e-6, f"value_v3 differs from the notebook reader by {worst} over {n} rows"


def test_the_notebook_drop_in_returns_the_same_float32_rows():
    doc = json.loads(GOLDEN.read_text())
    g = doc["games"][0]
    r = _replay(g["league"], g["file"])
    RD = fs.FS_STAGE4_COMPAT
    P = RD.parse(g["league"], r.data)
    times = [row["t"] for row in g["rows"][:12]]
    X = RD.game_features(g["league"], P, times, None, 0, 0.0)
    assert X.dtype == np.float32 and X.shape == (12, len(RD.FEATURES))
    f = RD.features_at(g["league"], r.data, times[5])
    assert list(f) == list(RD.FEATURES) and f["lev_now"] == RD.MISSING


def test_state4_equals_the_old_in_app_builder_on_fixture_games():
    rng = np.random.default_rng(6)
    worst, n = 0.0, 0
    for lg, name in GAMES:
        r = _replay(lg, name)
        old, new = _OldStage6Rows(lg, r.plays, r.wall), S6.Stage6Rows(lg, r.plays, r.wall)
        P = fs.fs_parse(lg, {"plays": r.plays}, wall=r.wall, min_rows=1)
        for te in rng.uniform(r.wall[0] - 30, r.wall[-1] + 30, 150).tolist() + list(r.wall[:40]):
            lev = float(rng.uniform(0.2, 4.0))
            o = old.features(te, lev)
            a = fs.fs_columns("state4", lg, P, [te], lev=[lev])
            b = new.features(te, lev)
            for k in fs.FS_STATE4_NAMES:
                worst = max(worst, abs(float(a[k][0]) - o[k]), abs(b[k] - o[k]))
            n += 1
    assert n > 500 and worst < 1e-6, f"state4 differs from the old Stage6Rows by {worst}"


def test_state4_from_facts_equals_the_value_v3_columns():
    lg, name = GAMES[0]
    r = _replay(lg, name)
    doc = json.loads(GOLDEN.read_text())
    facts = doc["games"][0]["facts"]
    P = fs.fs_parse(lg, r.data)
    times = np.linspace(r.wall[0], r.wall[-1], 200)
    v3 = fs.fs_columns("value_v3", lg, P, times, facts)
    s4 = fs.fs_columns("state4", lg, P, times, facts)
    for k in fs.FS_STATE4_NAMES:
        assert np.array_equal(v3[k], s4[k]), k


def test_old_and_new_kind_agree_on_every_row_type():
    texts = ["Jump Shot", "Timeout", "Official TV Timeout", "End Period", "End Game", "End of Game", "Coach's Challenge",
             "Instant Replay Review", "Shooting Foul", "Free Throw - 1 of 2", "Substitution", "Lost Ball Turnover",
             "Out-Of-Bounds", "Kicked Ball", "Defensive Rebound", "", "Jumpball", "Delay Technical"]
    for t in texts:
        for per, half in ((1, 2), (2, 2), (3, 2), (1, 1), (2, 1)):
            assert S6.stage6_kind(t, per, half) == _old_kind(t, per, half), (t, per, half)


# ---- cut data equals stored data -----------------------------------------------------------------------------------------
def _cut_at(r, k):
    """The last row sharing row k's stamp: a feed at that second has shown every row stamped then, so a cut between
    two rows with one stamp is not a moment the feed was ever in."""
    while k + 1 < len(r.wall) - 1 and r.wall[k + 1] <= r.wall[k]:
        k += 1
    return k


def _cut_times(r, k):
    """Moments whose head is row k: its own stamp and halfway to the next row."""
    lo = r.wall[k]
    hi = r.wall[k + 1] if k + 1 < len(r.wall) else lo + 40.0
    mid = lo + 0.5 * (hi - lo) if hi > lo else lo
    return [lo, mid]


def test_features_from_a_cut_feed_equal_features_from_the_stored_game():
    for lg, name in GAMES:
        r = _replay(lg, name)
        facts = json.loads(GOLDEN.read_text())["games"][0]["facts"] if lg == "nba" else None
        P_full = fs.fs_parse(lg, r.data)
        n = len(r.plays)
        for k in sorted({_cut_at(r, k) for k in (1, 5, n // 4, n // 2, (3 * n) // 4, n - 3)}):
            cut = fence.cut_feed(League(lg), r.data, k)
            P_cut = fs.fs_parse(lg, cut)
            assert P_cut is not None and P_cut["n"] == k + 1
            for t in _cut_times(r, k):
                for lag in (0.0, 15.0):
                    for name_ in fs.FS_SETS:
                        a = fs.fs_rows(name_, lg, P_cut, [t + lag], facts, 0, lag)
                        b = fs.fs_rows(name_, lg, P_full, [t + lag], facts, 0, lag)
                        assert np.array_equal(a, b), f"{name} {name_} at play {k}: a feature reads past the cut"


def test_rows_after_t_change_nothing_on_a_tiny_built_game():
    def play(i, text, h, a, per=1, clock="11:00", team="1"):
        return {"id": str(i), "type": {"text": text}, "homeScore": h, "awayScore": a, "period": {"number": per},
                "clock": {"displayValue": clock}, "team": {"id": team}, "text": text,
                "wallclock": f"2026-01-01T00:{i:02d}:00Z"}
    head = [play(0, "Jump Shot", 2, 0), play(1, "Shooting Foul", 2, 0), play(2, "Timeout", 2, 0, team="2"),
            play(3, "Three Point Jumper", 2, 3, team="2"), play(4, "Defensive Rebound", 2, 3)]
    tail = [play(5, "End Period", 2, 3, clock="0:00"), play(6, "Dunk", 4, 3, per=2, clock="11:40")]
    summ = {"header": {"competitions": [{"competitors": [{"team": {"id": "1"}, "homeAway": "home"},
                                                           {"team": {"id": "2"}, "homeAway": "away"}]}]}}
    t = fs.fs_wall(head)[-1] + 20.0
    for name_ in fs.FS_SETS:
        short = fs.fs_rows(name_, "nba", fs.fs_parse("nba", {**summ, "plays": head}), [t])
        full = fs.fs_rows(name_, "nba", fs.fs_parse("nba", {**summ, "plays": head + tail}), [t])
        assert np.array_equal(short, full), name_
    f = fs.fs_features_at("state4", "nba", {**summ, "plays": head}, t, lev=2.5)
    assert f == {"lev_now": 2.5, "since_stop": 140.0, "last_kind": 0.0, "since_to": 140.0}
    v = fs.fs_features_at("value_v3", "nba", {**summ, "plays": head + tail}, t)
    assert v["margin"] == -1.0 and v["to_lead"] == 1.0 and v["to_trail"] == 0.0 and v["foul_5"] == 1.0 and v["made3_5"] == 1.0


def test_the_cut_check_catches_a_feature_that_reads_one_row_ahead():
    """Control: a builder reading the row AFTER the head differs between the cut feed and the stored game."""
    lg, name = GAMES[0]
    r = _replay(lg, name)
    k = _cut_at(r, len(r.plays) // 2)

    def leaky(P, t):
        j = int(np.clip(np.searchsorted(P["wall"], t, "right"), 0, P["n"] - 1))   # one row too far
        return P["HS"][j + 1] - P["AS"][j + 1], P["kind"][j]
    cut = fs.fs_parse(lg, fence.cut_feed(League(lg), r.data, k))
    full = fs.fs_parse(lg, r.data)
    assert leaky(cut, r.wall[k]) != leaky(full, r.wall[k])


# ---- versions --------------------------------------------------------------------------------------------------------------
def test_every_set_has_a_version_tag_that_moves_with_its_columns():
    for name_, spec in fs.FS_SETS.items():
        tag = fs.fs_tag(name_)
        assert tag.startswith(f"{name_}@{spec.version}/{fs.FS_STORE_VERSION}/") and len(tag.rsplit("/", 1)[1]) == 12
        assert len(set(spec.names)) == len(spec.names)
    assert fs.fs_tag("value_v3") != fs.fs_tag("state4")
    assert len(fs.FS_VALUE_V3_NAMES) == 62
    assert S6.STAGE6_FEATURES == fs.FS_STATE4_NAMES


def test_the_registry_record_carries_the_feature_set_tag():
    rec = {"run_id": "f06-demo", "git_sha": "0" * 40, "code_hash": "1" * 64, "config_hash": "2" * 64,
           "data_manifest_hash": "3" * 64, "prereg_hash": "4" * 64, "seed": 0, "metrics": {"auc": 0.5},
           "verdict": "INCONCLUSIVE", "artifact_path": "models/x.npz", "artifact_sha256": "5" * 64, "promoted": False,
           "gpu_type": "cpu", "hours": 0.0, **fs.fs_registry_fields("value_v3")}
    clean = registry.validate_record(rec)
    assert clean["feature_set"] == fs.fs_tag("value_v3")


def test_an_unknown_set_is_refused():
    try:
        fs.fs_spec("value_v9")
    except KeyError as e:
        assert "value_v3" in str(e)
    else:
        raise AssertionError("an unknown feature set must raise")
