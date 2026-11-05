"""[T8 tester] The game-context STAY/LEAVE model (Nick, Nov 5 2026, 9:45 AM ET), black-box.

Nick's order: STAY/LEAVE comes from full game context (state + the play's consequence, rollouts beyond the
buffer vs the best alternative + switch cost, with hysteresis), and it NEVER leaves high leverage (Rule #1 and
the crunch rules stand). This file checks four things and nothing about how the model is built:

  1. Hard floor. On every close-and-late, overtime or graded-crunch state the model says STAY, even when the
     alternative is worth 1e9 (any high-leverage leave = FAIL, docs/plans/2026-11-05-watch-value-v2.md §2b).
  2. Hysteresis. An alternative barely above staying never makes it leave; the band never shrinks as recent
     switches pile up; a value that oscillates around the stay value does not flip the decision every step.
  3. No locked nights. The training nights never meet splits.league_split(league, nights).test.
  4. Clean labels. No label source is Nick's viewing or presses, and none is Teacher / distilled output
     (NO TRAINING SIGNAL FROM NICK; Teacher agreement is a secondary grading line only).

Part 1 also checks the fixture states against the shipped floors (redzone.lab.leverage, redzone.crunch), so
the floor table here cannot drift from the app's. The model tests skip until the builder's module lands
(thread "Train the stay or leave model"); set T8_STAY_LEAVE_MODULE to point them at another module path.

Interface (agreed with the builder; adapt here, not there):
  decide(ctx: dict, state=None) -> (decision, new_state)
      decision.action in {"stay", "leave"}; decision.value_stay, decision.value_alt, decision.margin
  training_manifest() -> {"leagues": {lg: {"train_nights": [...], "valid_nights": [...], "all_nights": [...]}},
                          "label_sources": [str, ...]}            (all_nights optional: defaults to train+valid)
"""
from __future__ import annotations

import importlib
import inspect
import os
import re
from pathlib import Path

import pytest

from redzone import crunch
from redzone.lab import leverage, splits
from redzone.models import League

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = [m for m in [os.environ.get("T8_STAY_LEAVE_MODULE"), "redzone.lab.stay_leave",
                          "redzone.noggin.stay_leave", "redzone.decider.stay_leave", "redzone.scoring.stay_leave"]
              if m]

HUGE = 1e9


def _model():
    for name in CANDIDATES:
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as e:
            if e.name and name.startswith(e.name):
                continue
            raise                                    # the module exists but one of ITS imports is broken
    pytest.skip(f"stay/leave model not landed yet (looked for {CANDIDATES})")


def _clock(s: float) -> str:
    return f"{int(s) // 60}:{int(s) % 60:02d}"


def ctx(league: League, period: int, left_s: float, home: int, away: int, *, stay=50.0, alt=HUGE, **extra) -> dict:
    """One game on screen plus one alternative. left_s = seconds left in the period, for every league
    (the ESPN-style `play` carries NHL's count-up clock the way the feed does)."""
    shown = left_s
    if league == League.NHL:
        shown = (300 if period >= 4 else 1200) - left_s
    play = {"period": {"number": period}, "clock": {"displayValue": _clock(shown) if league != League.MLB else ""},
            "homeScore": home, "awayScore": away}
    c = {"league": league.value if hasattr(league, "value") else str(league), "game_id": "on", "period": period,
         "clock_s": left_s, "home_score": home, "away_score": away, "margin": abs(home - away), "play": play,
         "value": stay, "value_stay": stay, "alts": [{"game_id": "alt", "value": alt}], "ts": 0.0}
    c.update(extra)
    return c


def _decide(m, c, state=None):
    out = m.decide(c, state)
    d, st = out if isinstance(out, tuple) else (out, state)
    act = str(getattr(d, "action", d)).lower()
    assert act in ("stay", "leave"), f"unexpected action {act!r}"
    return d, act, st


# ---- 1. the hard floor -------------------------------------------------------------------------------------

HIGH = [  # (league, period, seconds left, home, away): close and late, or overtime
    (League.NFL, 4, 120, 20, 17), (League.NFL, 4, 299, 24, 17), (League.NFL, 4, 5, 14, 14), (League.NFL, 5, 480, 20, 20),
    (League.NBA, 4, 300, 100, 96), (League.NBA, 4, 30, 99, 99), (League.NBA, 4, 359, 88, 94), (League.NBA, 5, 200, 110, 105),
    (League.NHL, 3, 180, 2, 1), (League.NHL, 3, 20, 3, 3), (League.NHL, 4, 60, 2, 2),
    (League.MLB, 8, 0, 3, 2), (League.MLB, 9, 0, 4, 4), (League.MLB, 11, 0, 5, 5),
]
LOW = [  # clearly not high leverage: early, or a blowout late
    (League.NFL, 1, 600, 7, 3), (League.NFL, 4, 120, 38, 10),
    (League.NBA, 2, 400, 50, 48), (League.NBA, 4, 120, 120, 90),
    (League.NHL, 1, 900, 1, 0), (League.MLB, 3, 0, 2, 1),
]


@pytest.mark.parametrize("lg,per,left,h,a", HIGH)
def test_fixture_states_are_high_leverage_under_the_shipped_floor(lg, per, left, h, a):
    assert leverage.high_leverage(lg, ctx(lg, per, left, h, a)["play"])


@pytest.mark.parametrize("lg,per,left,h,a", LOW)
def test_fixture_low_states_are_not_high_leverage(lg, per, left, h, a):
    assert not leverage.high_leverage(lg, ctx(lg, per, left, h, a)["play"])


@pytest.mark.parametrize("lg,per,left,h,a", HIGH)
def test_never_leaves_high_leverage_whatever_the_alternative(lg, per, left, h, a):
    m = _model()
    for alt in (60.0, 1e3, HUGE):
        d, act, _ = _decide(m, ctx(lg, per, left, h, a, stay=0.0, alt=alt))
        assert act == "stay", f"left a high-leverage {lg} state (P{per} {left}s {h}-{a}) for alt={alt}"
        if hasattr(d, "floor"):
            assert d.floor is True, "stayed, but not because of the floor: the floor must not depend on values"


CRUNCH_EDGES = [(kind, lg) for kind in ("window", "graded") for lg in (League.NFL, League.NBA, League.NHL, League.MLB)]


@pytest.mark.parametrize("kind,lg", CRUNCH_EDGES)
def test_never_leaves_inside_the_crunch_window(kind, lg):
    """The crunch rules stand: at the EDGE of every crunch row (clock at its limit, margin at its limit) the
    model stays. The leverage table and the crunch table differ (NBA: CLOSE_LATE margin 6 at 6:00, crunch window
    margin 7 at 3:00), so a floor that only mirrors leverage.high_leverage misses a 7-point NBA game at 2:00."""
    m = _model()
    r = crunch.row(kind, lg)
    if r is None:
        pytest.skip(f"no {kind} crunch row for {lg}")
    per = r.period
    left = 0 if lg == League.MLB else (r.clock_s if r.clock_s is not None else 60)
    h, a = 50 + int(r.margin), 50
    assert _graded_crunch_kind(kind, lg, per, left, h, a)
    _, act, _ = _decide(m, ctx(lg, per, left, h, a, stay=0.0, alt=HUGE, crunch_state="in"))
    assert act == "stay", f"left a {kind}-crunch {lg} state: P{per} {left}s margin {h - a}"


def _graded_crunch_kind(kind, lg, per, left, h, a) -> bool:
    return crunch.inside(crunch.row(kind, lg), per, left, abs(h - a))


def test_floor_holds_even_when_hysteresis_state_says_leave():
    """A stale state from a low-leverage stretch (pending leave, many switches) must not leak through."""
    m = _model()
    st = None
    for i in range(6):                                 # build up a state on a low-leverage blowout
        _, _, st = _decide(m, ctx(League.NBA, 2, 400 - i * 10, 70, 40, stay=0.0, alt=9.0, ts=float(i * 30)), st)
    _, act, _ = _decide(m, ctx(League.NBA, 4, 60, 101, 100, stay=0.0, alt=HUGE, ts=400.0), st)
    assert act == "stay"


@pytest.mark.parametrize("lg,per,left,h,a", LOW[:4])
def test_the_model_is_not_stuck_on_stay(lg, per, left, h, a):
    """Sanity: the floor test means nothing if the model never leaves. A dead low-leverage game with a far
    better alternative must leave."""
    m = _model()
    _, act, _ = _decide(m, ctx(lg, per, left, h, a, stay=0.0, alt=9.0))
    assert act == "leave"


# ---- 2. hysteresis -----------------------------------------------------------------------------------------

def _low(stay, alt, ts, **k):
    return ctx(League.NBA, 2, 500, 50, 47, stay=stay, alt=alt, ts=ts, **k)


def test_an_alternative_barely_above_staying_does_not_trigger_a_leave():
    m = _model()
    _, act, _ = _decide(m, _low(4.0, 4.0 + 1e-6, 0.0))
    assert act == "stay"


def test_margin_never_shrinks_as_recent_switches_pile_up():
    """Offer a huge gap every 10 s for 15 min; whatever dwell the model uses, the band it reports at each
    successive leave must not shrink and must end wider than it started."""
    m = _model()
    margins, st = [], None
    for i in range(90):
        d, act, st = _decide(m, _low(0.0, 9.0, 1000.0 + 10.0 * i), st)
        if getattr(d, "margin", None) is None:
            pytest.skip("decision has no .margin; hysteresis covered by the two-game tests only")
        if act == "leave":
            margins.append(float(d.margin))
    assert len(margins) >= 3, f"only {len(margins)} leaves in 15 min on a 9-point gap"
    assert all(b >= a - 1e-9 for a, b in zip(margins, margins[1:])), margins
    assert margins[-1] > margins[0], f"margin flat in recent switches: {margins}"


def _two_game_switches(m, base: float, amp: float, steps: int = 30, dt: float = 20.0) -> list[float]:
    """Two low-leverage games trade a +-amp lead every dt seconds; the caller swaps the screen on every leave,
    as the app does. Returns the times of the switches."""
    st, on, at = None, "A", []
    for i in range(steps):
        va, vb = (base + amp, base - amp) if i % 2 == 0 else (base - amp, base + amp)
        stay, alt = (va, vb) if on == "A" else (vb, va)
        c = _low(stay, alt, float(i * dt))
        c["game_id"], c["alts"][0]["game_id"] = on, ("B" if on == "A" else "A")
        _, act, st = _decide(m, c, st)
        if act == "leave":
            on = "B" if on == "A" else "A"
            at.append(i * dt)
    return at


MAX_SWITCHES_10MIN = 5   # tester's proposal (about one switch per 2 min at worst); the integrator may set it


# Value scale (builder, 14:37 UTC): the oracle's combined reward over the next 300 s of screen time, typically
# 0-10 (big play 3, crunch play 1). +-0.3 = small noise (tests the band), +-2 = a large swing (tests the dwell).

def test_small_noise_between_two_games_does_not_cause_whiplash():
    """+-0.3 traded every 20 s for 10 min is noise: at most 2 switches (Dixit band of inaction; spec §2b)."""
    at = _two_game_switches(_model(), base=4.0, amp=0.3)
    assert len(at) <= 2, f"{len(at)} switches in 10 min on +-0.3 noise at {at}"


def test_large_swings_still_respect_a_minimum_dwell():
    """+-2 traded every 20 s: the model may follow, but never two voluntary switches within 60 s
    (Zillmann carry-over; builder's State.dwell_s = 60) and no more than 5 in 10 minutes."""
    at = _two_game_switches(_model(), base=4.0, amp=2.0)
    gaps = [b - a for a, b in zip(at, at[1:])]
    assert all(g >= 60.0 for g in gaps), f"switches {gaps} s apart"
    assert len(at) <= MAX_SWITCHES_10MIN, f"{len(at)} switches in 10 min (cap {MAX_SWITCHES_10MIN})"


def test_a_clear_and_lasting_gap_still_leaves():
    """Hysteresis must delay, not forbid: a sustained big gap ends in a leave within a few steps."""
    m = _model()
    st, acts = None, []
    for i in range(10):
        _, act, st = _decide(m, _low(0.5, 9.0, float(i * 20)), st)
        acts.append(act)
    assert "leave" in acts, acts


# ---- 3. no locked nights in training -----------------------------------------------------------------------

NIGHTS = [f"2025{mo:02d}{d:02d}" for mo in (10, 11, 12) for d in (1, 5, 9, 13, 17, 21, 25)]


def _manifest(m):
    """Normalise to {"leagues": {lg: {train_nights, valid_nights, all_nights}}, "label_sources": [...]}.
    Builder's API: manifest(splits_by_league) -> {lg: {...}, "label_sources": [...]}; training_manifest() also ok."""
    if hasattr(m, "manifest"):
        sp = {lg: splits.league_split(lg, NIGHTS) for lg in ("nba", "nfl", "nhl")}
        raw = m.manifest(sp)
        srcs = raw.get("label_sources") or getattr(m, "LABEL_SOURCES", ())
        lgs = {k: dict(v, all_nights=NIGHTS) for k, v in raw.items() if k != "label_sources" and isinstance(v, dict)}
        return {"leagues": lgs, "label_sources": srcs}
    fn = getattr(m, "training_manifest", None)
    if fn is None:
        pytest.skip("model exposes no manifest() / training_manifest() yet")
    return fn()


def test_fit_json_never_lists_a_locked_night():
    """scripts/stay_leave.py fit writes fit.json; any committed copy must keep train/valid off its test nights."""
    import json
    found = sorted(set(ROOT.glob("**/stay_leave*/fit.json")) | set(ROOT.glob("**/stay_leave_fit*.json")))
    if not found:
        pytest.skip("no committed stay/leave fit.json")
    for p in found:
        blob = json.loads(p.read_text())
        for lg, row in {**blob, **(blob.get("manifest") or {})}.items():
            if not isinstance(row, dict) or "train_nights" not in row:
                continue
            used = set(row.get("train_nights") or ()) | set(row.get("valid_nights") or ())
            assert not (used & set(row.get("test_nights") or ())), f"{p}: {lg} trains on locked nights"


def test_training_never_touches_locked_nights():
    man = _manifest(_model())
    leagues = man.get("leagues") or {}
    assert leagues, "training_manifest() lists no leagues"
    for lg, row in leagues.items():
        used = set(row.get("train_nights") or ()) | set(row.get("valid_nights") or ())
        assert used, f"{lg}: no training nights listed"
        pool = set(row.get("all_nights") or ()) | used
        locked = set(splits.league_split(lg, pool).test)
        assert not (used & locked), f"{lg}: trains on locked nights {sorted(used & locked)[:5]}"


def test_check_itself_catches_a_locked_night():
    """Meta-test (runs now): the overlap check above does flag a manifest that includes the newest night."""
    nights = [f"2025{m:02d}{d:02d}" for m in (10, 11, 12) for d in (1, 8, 15, 22)]
    locked = set(splits.league_split("nba", nights).test)
    assert locked and max(nights) in locked


# ---- 4. label sources: never Nick's viewing, never Teacher output ------------------------------------------

BANNED = re.compile(r"(userstate|press(es)?\b|\btaps?\b|nick|my_view|viewing_log|viewer_log|watch_history|"
                    r"teacher|distill|synthetic|syn_|llm_label|reasoner_label)", re.I)


def test_label_sources_exclude_nick_and_teacher():
    man = _manifest(_model())
    srcs = [str(s) for s in man.get("label_sources") or ()]
    assert srcs, "training_manifest() names no label sources"
    bad = [s for s in srcs if BANNED.search(s)]
    assert not bad, f"banned label sources: {bad}"


BANNED_IMPORTS = re.compile(r"^\s*(from|import)\s+(redzone\.userstate|redzone\.lab\.night\.presses|"
                            r"redzone\.lab\.distill|redzone\.reasoner|redzone\.lab\.reasoner_data|scripts\.distill|"
                            r"agent\.distill)\b", re.M)


def _stay_leave_sources() -> list[Path]:
    out = [p for p in ROOT.glob("redzone/**/*stay_leave*.py")]
    out += [p for p in ROOT.glob("scripts/*stay_leave*.py")]
    return sorted(set(out))


def test_model_and_training_code_import_no_nick_or_teacher_label_module():
    files = _stay_leave_sources()
    if not files:
        pytest.skip("no stay/leave source files yet")
    bad = {str(p.relative_to(ROOT)): BANNED_IMPORTS.findall(p.read_text()) for p in files}
    bad = {k: v for k, v in bad.items() if v}
    assert not bad, f"stay/leave code imports a Nick-viewing or Teacher module: {bad}"


def test_nicks_teams_enter_as_stakes_only_not_as_label():
    """Nick's teams may be a stakes INPUT (his 9:45 AM order) but never a label column."""
    m = _model()
    src = inspect.getsource(m)
    for lab in re.findall(r"label[s_]?\w*\s*=\s*[^\n]+", src):
        assert not re.search(r"my_team|personal|nick", lab, re.I), f"label built from Nick's teams: {lab.strip()}"


# ---- 5. the offline policy (solve_policy) on real archived nights ------------------------------------------

ARCHIVE = ROOT / "data/archive"


def _script():
    import sys
    for d in (str(ROOT), str(ROOT / "scripts")):
        if d not in sys.path:
            sys.path.insert(0, d)
    m = _model()
    if not hasattr(m, "solve_policy") or not (ROOT / "scripts/stay_leave.py").exists():
        pytest.skip("no solve_policy / scripts/stay_leave.py yet")
    return importlib.import_module("stay_leave"), m


def _train_nights(S, lg: League, k: int):
    """The first k TRAIN nights only: a test never reads a locked night."""
    import itertools
    if not (ARCHIVE / lg.value).exists():
        pytest.skip("pinned eval data not unpacked (scripts/fetch_eval_data.py)")
    return list(itertools.islice(S.nights(lg, "train"), k))


@pytest.mark.parametrize("lg", [League.NFL, League.NHL, League.NBA])
def test_solve_policy_never_leaves_under_the_floor_on_real_nights(lg):
    """0 high-leverage leaves on any night (EXP-1712's hard line). The leave is checked against the floor
    table AND against redzone.lab.leverage on the play that was on screen, so the check does not rest only on
    the model's own floor table. Even with the band at 0, which tempts it most."""
    S, m = _script()
    for day, n, tab, F, HL in _train_nights(S, lg, 2):
        sw = m.solve_policy(n, S.FEED_LAG_S, tab, F, HL, None, margin=0.0)
        assert m.night_metrics(n, tab, HL, sw)["hl_leaves"] == 0, f"{lg} {day}: left under the floor"


def test_the_floor_table_covers_every_high_leverage_play():
    """floor_play (what floor_table marks) must hold on every play leverage.high_leverage marks."""
    S, m = _script()
    if not hasattr(m, "floor_play"):
        pytest.skip("no floor_play")
    seen = 0
    for lg in (League.NFL, League.NHL, League.NBA):
        for _day, n, _tab, _F, _HL in _train_nights(S, lg, 1):
            for r in n.replays:
                for p in r.plays:
                    if leverage.high_leverage(r.league, p):
                        seen += 1
                        assert m.floor_play(r.league, p), f"{r.league} play under high leverage not floored"
    assert seen, "no high-leverage play found on the sample nights"


def test_the_floor_test_has_teeth():
    """With floor=False the same policy does leave under the floor on an NFL night (Nov 5 probe: 11 and 28 on
    the first two train nights), so a 0 above is the floor working, not a policy that never leaves."""
    S, m = _script()
    total = 0
    for _day, n, tab, F, HL in _train_nights(S, League.NFL, 2):
        sw = m.solve_policy(n, S.FEED_LAG_S, tab, F, HL, None, margin=0.0, floor=False)
        total += m.night_metrics(n, tab, HL, sw)["hl_leaves"]
    assert total > 0


def test_fit_reads_train_and_valid_only(monkeypatch, tmp_path):
    """scripts/stay_leave.py fit must load only the train and valid splits, never test (locked) or all."""
    S, _m = _script()
    asked = []

    class Stop(Exception):
        pass

    def spy(leagues, split, cache=None):
        asked.append(split)
        if len(asked) >= 2:
            raise Stop
        return []

    monkeypatch.setattr(S, "load", spy)
    with pytest.raises(Stop):
        S.main(["fit", "--out", str(tmp_path), "--leagues", "nba"])
    assert asked and set(asked) <= {"train", "valid"}, asked
