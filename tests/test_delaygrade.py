"""[EXP-803] Delay precision grader: honest band, mid-commercial on paired nights, EXP-728's guardrails."""
import json

from redzone.lab import delaygrade as G


def night(n, entries=10, midcom=5, mid_play=0, missed=0, crunch_live=10, big_live=10, dead=100.0, sigma=9.0, cover=1.0):
    return {"night": n, "entries": entries, "entries_mid_break": midcom, "entries_mid_play": mid_play,
            "plays_missed_entry": missed, "crunch": 10, "crunch_live": crunch_live, "big": 10, "big_live": big_live,
            "dead_s": dead, "watched_s": 1000.0, "sync_honesty": {"n": 100, "within_2sigma": cover, "sigma_p50": sigma}}


def runs(arm, nights=25, seeds=(0, 1, 2), harness="plain", **kw):
    return [{"harness": harness, "seed": s, "arm": arm, "per_night": [night(f"n{i:02d}", **kw) for i in range(nights)]}
            for s in seeds]


def test_a_tighter_honest_band_that_cuts_mid_commercial_wins_and_reports_the_gates():
    rep = G.grade(runs("base") + runs("tight", midcom=1, sigma=4.0))["plain"]
    t = rep["arms"]["tight"]
    assert rep["nights"] == 25 and t["verdict"] == "WIN" and t["midcom_per_100"] == 10.0
    assert t["bar_728"] is False and t["gate_803"] is False          # a WIN that misses the bar still ships OFF


def test_a_narrow_band_that_stops_covering_the_truth_fails():
    t = G.grade(runs("base") + runs("liar", midcom=0, sigma=2.0, cover=0.80))["plain"]["arms"]["liar"]
    assert t["band_dishonest"] and t["verdict"] == "FAIL"


def test_one_added_mid_play_entry_fails_whatever_the_lift():
    arm = runs("tight", midcom=0, sigma=4.0)
    arm[0]["per_night"][0]["entries_mid_play"] = 1
    assert G.grade(runs("base") + arm)["plain"]["arms"]["tight"]["verdict"] == "FAIL"


def test_guardrails_block_a_win_per_seed():
    arm = runs("tight", midcom=1, sigma=4.0)
    for r in arm[2:]:                                                 # one seed loses crunch live
        r["per_night"] = [night(x["night"], midcom=1, sigma=4.0, crunch_live=9) for x in r["per_night"]]
    t = G.grade(runs("base") + arm)["plain"]["arms"]["tight"]
    assert t["guardrails"]["crunch_live"] is False and t["verdict"] == "NO_WIN"
    t = G.grade(runs("base") + runs("tight", midcom=1, sigma=4.0, missed=1))["plain"]["arms"]["tight"]
    assert t["guardrails"]["plays_missed"] is False and t["verdict"] == "NO_WIN"


def test_too_few_nights_is_pending_and_harnesses_are_never_pooled():
    rep = G.grade(runs("base", nights=13) + runs("tight", nights=13, midcom=0, sigma=4.0)
                  + runs("base", harness="resync") + runs("tight", harness="resync", midcom=9))
    assert rep["plain"]["arms"]["tight"]["verdict"] == "PENDING"
    assert rep["resync"]["arms"]["tight"]["verdict"] == "LOSE"


def test_load_dir_reads_eval_sports_outputs_by_name(tmp_path):
    for r in runs("base", nights=3, seeds=(0,)) + runs("tick5", nights=3, seeds=(0,)):
        (tmp_path / f"plain-{r['seed']}-{r['arm']}.json").write_text(json.dumps({"per_night": r["per_night"]}))
    (tmp_path / "notes.json").write_text("{}")
    got = G.load_dir(tmp_path)
    assert sorted(r["arm"] for r in got) == ["base", "tick5"] and got[0]["harness"] == "plain"
    assert "| tick5 |" in G.markdown(G.grade(got, min_nights=2))
