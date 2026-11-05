"""EXP-025: the pre-big stage (redzone/noggin/prebig.py), its build-up measure (redzone/lab/buildup.py) and
the experiment's analysis (scripts/exp025_prebig.py). Synthetic inputs only: no archive, no backtest run."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from redzone.decider.decider import Decision, State
from redzone.decider.screen import Gate
from redzone.lab import backtest, buildup
from redzone.models import League
from redzone.noggin import prebig

ROOT = Path(__file__).resolve().parent.parent


class Cfg(dict):
    def get(self, k, default=None):
        return super().get(k, default)


def scored(league=League.NFL, pre=None, lock=None, garbage=False, crunch=False, crunch_now=False):
    return SimpleNamespace(league=league, pre_moment=pre, lock=lock, garbage=garbage, crunch=crunch, crunch_now=crunch_now)


def cand(s, target="youtubetv:x"):
    return SimpleNamespace(scored=s, target=target, stale=False)


def fake_dec(on=True, current="a", safe=True, yours=(), history=()):
    return SimpleNamespace(cfg=Cfg({prebig.FLAG: on}), replaying=None, after_replay=False,
                           user=SimpleNamespace(paused=False), safe_mode=False, current=current, timelines={},
                           _yours=set(yours), history=list(history), _screen_break=set(), since=0.0,
                           _gate=lambda c, now: Gate(safe, "dead ball" if safe else "play on screen"))


def stay(cur="a"):
    return Decision(1000.0, "stay", cur, "nothing better", State.WATCHING)


# ---- the signal ---------------------------------------------------------------------------------------

def test_signal_reads_the_scorers_pre_moment_and_locks():
    assert prebig.signal(scored(pre="3rd & goal")).hazard >= 0.5
    assert prebig.signal(scored(lock="red zone drive")).hazard == 0.5
    assert prebig.signal(scored(League.NHL, pre="power play")).hazard == 0.3
    assert prebig.signal(scored()).hazard == 0.0


def test_garbage_time_has_no_signal():
    assert prebig.signal(scored(pre="3rd & goal", garbage=True)).hazard == 0.0


def test_nba_crunch_runup_from_the_timeline():
    tl = SimpleNamespace(clocks=[(0.0, 4, 200.0)], _last_score=(100, 96))
    assert prebig.signal(scored(League.NBA), tl).label == "crunch run-up"
    tl_far = SimpleNamespace(clocks=[(0.0, 4, 500.0)], _last_score=(100, 96))
    tl_blowout = SimpleNamespace(clocks=[(0.0, 4, 200.0)], _last_score=(110, 90))
    assert prebig.signal(scored(League.NBA), tl_far).hazard == 0.0
    assert prebig.signal(scored(League.NBA), tl_blowout).hazard == 0.0


# ---- the stage ----------------------------------------------------------------------------------------

def test_flag_off_is_a_no_op():
    dec = fake_dec(on=False)
    d = stay()
    cands = {"a": cand(scored()), "b": cand(scored(pre="3rd & goal"))}
    assert prebig.apply(dec, d, cands, 1000.0) is d


def test_default_config_keeps_it_off():
    import yaml
    cfg = yaml.safe_load((ROOT / "redzone/default_config.yaml").read_text())
    assert not ((cfg.get("noggin") or {}).get("prebig") or {}).get("enabled", False)


def test_live_decider_never_builds_the_stage():
    assert "prebig" not in (ROOT / "redzone/decider/decider.py").read_text()


def test_switches_to_the_game_about_to_have_a_big_snap():
    dec = fake_dec()
    cands = {"a": cand(scored()), "b": cand(scored(pre="3rd & goal"))}
    out = prebig.apply(dec, stay(), cands, 1000.0)
    assert out.action == "switch" and out.target == "b" and out.kind == prebig.PREBIG_KIND


def test_waits_for_the_leave_gate():
    dec = fake_dec(safe=False)
    cands = {"a": cand(scored()), "b": cand(scored(pre="3rd & goal"))}
    out = prebig.apply(dec, stay(), cands, 1000.0)
    assert out.action == "stay" and out.want == "b"


def test_never_leaves_your_team_crunch_or_a_lock():
    for dec, here in ((fake_dec(yours={"a"}), scored()), (fake_dec(), scored(crunch=True)),
                      (fake_dec(), scored(lock="final two minutes"))):
        cands = {"a": cand(here), "b": cand(scored(pre="3rd & goal"))}
        assert prebig.apply(dec, stay(), cands, 1000.0).action == "stay"


def test_only_when_the_app_would_stay_and_not_too_soon():
    cands = {"a": cand(scored()), "b": cand(scored(pre="3rd & goal")), "c": cand(scored())}
    sw = Decision(1000.0, "switch", "c", "better", State.WATCHING, kind="better")
    assert prebig.apply(fake_dec(), sw, cands, 1000.0) is sw
    assert prebig.apply(fake_dec(history=[(990.0, "a", "better")]), stay(), cands, 1000.0).action == "stay"


def test_needs_a_margin_over_what_is_on_screen():
    cands = {"a": cand(scored(League.NHL, pre="power play")), "b": cand(scored(League.NHL, pre="power play"))}
    assert prebig.apply(fake_dec(), stay(), cands, 1000.0).action == "stay"


def test_holds_the_buildup_but_not_against_crunch():
    dec = fake_dec(current="b")
    dec._prebig = ("b", 900.0, "3rd & goal")
    dec.since = 905.0
    cands = {"b": cand(scored(pre="3rd & goal")), "c": cand(scored()), "k": cand(scored(crunch=True))}
    better = Decision(1000.0, "switch", "c", "better", State.WATCHING, kind="better")
    assert prebig.apply(dec, better, cands, 1000.0).action == "stay"
    crunch = Decision(1000.0, "switch", "k", "crunch", State.WATCHING, kind="crunch")
    assert prebig.apply(dec, crunch, cands, 1000.0) is crunch
    # the drive ended: the hold ends with it
    cands["b"] = cand(scored())
    assert prebig.apply(dec, better, cands, 1000.0) is better and dec._prebig is None


def test_with_prebig_runs_after_pick():
    class Base:
        cfg = Cfg({prebig.FLAG: False})

        def _pick(self, d, cands, now):
            return d
    sub = prebig.with_prebig(Base)()
    d = stay()
    assert sub._pick(d, {}, 0.0) is d


def test_prebig_runs_restores_the_backtest_decider():
    orig = backtest.Decider
    with buildup.prebig_runs():
        assert backtest.Decider is not orig and issubclass(backtest.Decider, orig)
    assert backtest.Decider is orig


# ---- build-up seen --------------------------------------------------------------------------------------

def test_visit_before_counts_seconds_on_screen_before_the_play():
    watching = [(0.0, "a"), (100.0, "b"), (300.0, "a")]
    assert buildup.visit_before(watching, None, "b", 160.0, 167.0, 5.0) == 55.0
    assert buildup.visit_before(watching, [0.0, 15.0, 0.0], "b", 160.0, 167.0, 5.0) == 70.0   # a rewind shows more
    assert buildup.visit_before(watching, None, "b", 290.0, 310.0, 5.0) is None                # left mid-play


def test_summarize_counts_unseen_as_zero_in_the_mean():
    s = buildup.summarize([30.0, None, 10.0])
    assert s["big"] == 3 and s["big_live"] == 2 and s["buildup_median_s"] == 20.0
    assert s["buildup_mean_all_s"] == round(40 / 3, 1) and s["arrived_before"] == 1


# ---- the experiment's analysis ------------------------------------------------------------------------------

def _script():
    spec = importlib.util.spec_from_file_location("exp025", ROOT / "scripts/exp025_prebig.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _row(big, live, **kw):
    r = {"big": big, "big_live": live, "landings": 10, "landings_dead": 10, "entries": 10, "entries_mid_play": 0,
         "cuts": 0, "cuts_high": 0, "plays_missed_entry": 0, "dead_s": 0.0, "watched_s": 100.0, "crunch": 1,
         "crunch_live": 1, "switches": 5, "prebig_switches": 1, "buildup": [10.0], "buildup_n_big": big,
         "arrived_before": 0}
    r.update(kw)
    return r


def test_analysis_is_pending_under_eight_nights_and_passes_a_clear_lift():
    m = _script()
    few = {"rows": {"control:0": {f"d{i}": _row(10, 5) for i in range(4)},
                    "prebig:0": {f"d{i}": _row(10, 8) for i in range(4)}}, "nights": [f"d{i}" for i in range(4)]}
    many = {"rows": {"control:0": {f"d{i}": _row(10, 5) for i in range(9)},
                     "prebig:0": {f"d{i}": _row(10, 8) for i in range(9)}}, "nights": [f"d{i}" for i in range(9)]}
    an = m.analyse({"nba": few, "nhl": many, "nfl": many}, [0])
    assert an["leagues"]["nba"]["status"] == "PENDING"
    assert an["leagues"]["nhl"]["status"] == "PASS" and an["leagues"]["nhl"]["big_live_delta"][1] > 0
    assert an["overall"] == "PASS"


def test_analysis_fails_on_a_mid_play_entry():
    m = _script()
    res = {"rows": {"control:0": {f"d{i}": _row(10, 5) for i in range(9)},
                    "prebig:0": {f"d{i}": _row(10, 8, entries_mid_play=int(i == 0)) for i in range(9)}},
           "nights": [f"d{i}" for i in range(9)]}
    an = m.analyse({"nhl": res}, [0])
    assert an["leagues"]["nhl"]["status"] == "FAIL" and an["overall"] == "FAIL"


def test_the_script_refuses_the_locked_split():
    m = _script()
    try:
        m.main(["--split", "test"])
    except SystemExit as e:
        assert "locked" in str(e)
    else:
        raise AssertionError("read the locked split")
