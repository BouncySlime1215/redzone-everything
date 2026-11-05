"""[EXP-803] Delay precision grader, as pre-registered in #296: A4 gate, per-seed guardrails, Holm over A1-A3."""
import json

from redzone.lab import delaygrade as G

FAM = {G.FAMILY: {"name": G.FAMILY, "arms": ["EXP-803:A1", "EXP-803:A2", "EXP-803:A3"]}}


def night(n, entries=10, midcom=5, mid_play=0, missed=10, crunch_live=10, big_live=10, dead=100.0, sigma=9.0,
          cover=1.0):
    return {"night": n, "entries": entries, "entries_mid_break": midcom, "entries_mid_play": mid_play,
            "plays_missed_entry": missed, "crunch": 10, "crunch_live": crunch_live, "big": 10, "big_live": big_live,
            "dead_s": dead, "watched_s": 1000.0, "sync_honesty": {"n": 100, "within_2sigma": cover, "sigma_p50": sigma}}


def runs(arm, nights=25, seeds=(0, 1, 2), harness="plain", **kw):
    return [{"harness": harness, "seed": s, "arm": arm, "per_night": [night(f"n{i:02d}", **kw) for i in range(nights)]}
            for s in seeds]


def wobble(rs, midcom_by_night):
    """Give each night its own mid-commercial count so the night bootstrap has spread."""
    for r in rs:
        for x in r["per_night"]:
            x["entries_mid_break"] = midcom_by_night(int(x["night"][1:]))
    return rs


def grade(*rs, **kw):
    return G.grade([r for x in rs for r in x], families=kw.pop("families", FAM), **kw)


def test_a4_passes_only_at_the_gate_with_its_upper_bound_below_a0s_lower_bound():
    a0 = wobble(runs("A0"), lambda i: 4 + i % 3)                  # ~50 per 100
    a4 = wobble(runs("A4"), lambda i: i % 2)                      # ~5 per 100
    t = grade(a0, a4)["plain"]["arms"]["A4"]
    assert t["midcom_per_100"] <= G.MIDCOM_GATE and t["ci_rate"][1] < grade(a0, a4)["plain"]["arms"]["A0"]["ci_rate"][0]
    assert t["verdict"] == "PASS" and t["holm"] is None           # A4 is the single combined arm: unadjusted
    t = grade(a0, wobble(runs("A4"), lambda i: 1 + i % 2))["plain"]["arms"]["A4"]   # ~15 per 100: over 10
    assert t["verdict"] == "NO_PASS"


def test_a1_to_a3_read_lift_only_through_holm_over_the_declared_family():
    a0 = wobble(runs("A0"), lambda i: 4 + i % 3)
    rep = grade(a0, wobble(runs("A1"), lambda i: 1 + i % 2), runs("A2"), runs("A3"))["plain"]
    assert rep["arms"]["A1"]["verdict"] == "LIFT" and rep["arms"]["A1"]["holm"]["family_size"] == 3
    assert rep["arms"]["A2"]["verdict"] == "NO_LIFT"
    rep = grade(a0, wobble(runs("A1"), lambda i: 1 + i % 2), families={})["plain"]   # family not declared yet
    assert rep["arms"]["A1"]["verdict"] == "NO_LIFT" and not rep["family_declared"]
    assert "not declared" in G.markdown({"plain": rep})


def test_a_narrow_band_that_stops_covering_the_truth_fails():
    t = grade(runs("A0"), runs("A4", midcom=0, sigma=2.0, cover=0.80))["plain"]["arms"]["A4"]
    assert t["band_dishonest"] and t["verdict"] == "FAIL"


def test_one_added_mid_play_entry_on_one_seed_fails_whatever_the_lift():
    arm = runs("A4", midcom=0)
    arm[1]["per_night"][0]["entries_mid_play"] = 1
    assert grade(runs("A0"), arm)["plain"]["arms"]["A4"]["verdict"] == "FAIL"


def test_every_guardrail_is_read_per_seed():
    a0 = wobble(runs("A0"), lambda i: 4 + i % 3)
    good = (lambda **kw: wobble(runs("A4", **kw), lambda i: i % 2))
    assert grade(a0, good())["plain"]["arms"]["A4"]["verdict"] == "PASS"
    for kw, key in (({"big_live": 9}, "big_seen_live"), ({"crunch_live": 9}, "crunch_live"),
                    ({"dead": 106.0}, "dead_air"), ({"missed": 11}, "plays_missed")):
        arm = good()
        arm[2] = good(**kw)[2]                                    # only seed 2 breaks it
        t = grade(a0, arm)["plain"]["arms"]["A4"]
        assert t["guardrails"][key] is False and t["verdict"] == "NO_PASS", key
    arm = good()
    arm[2] = good(dead=104.0)[2]                                  # +0.4 points: inside the 0.5 allowance
    assert grade(a0, arm)["plain"]["arms"]["A4"]["guardrails"]["dead_air"] is True


def test_too_few_nights_is_pending_and_harnesses_are_never_pooled():
    rep = grade(runs("A0", nights=13), runs("A4", nights=13, midcom=0), runs("A0", harness="resync"),
                runs("A4", harness="resync", midcom=9))
    assert rep["plain"]["arms"]["A4"]["verdict"] == "PENDING" and rep["resync"]["arms"]["A4"]["verdict"] == "NO_PASS"


def test_load_dir_reads_eval_sports_outputs_by_name(tmp_path):
    for r in runs("A0", nights=3, seeds=(0,)) + runs("A2", nights=3, seeds=(0,)):
        (tmp_path / f"plain-{r['seed']}-{r['arm']}.json").write_text(json.dumps({"per_night": r["per_night"]}))
    (tmp_path / "notes.json").write_text("{}")
    got = G.load_dir(tmp_path)
    assert sorted(r["arm"] for r in got) == ["A0", "A2"] and got[0]["harness"] == "plain"
    assert "| A2 |" in G.markdown(G.grade(got, min_nights=2, families=FAM))
