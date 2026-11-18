"""V7 -> V8 Mind fixes (overseer, Nov 4): crunch = stay prior, unsafe leaves split by leverage (the 3:15 rule),
crunch/sticky rows weighted up, a uniform train side of the gap, and a league-balanced teacher queue."""
from agent.distill import evaluate as E
from agent.distill.teacher import balance_by_league
from agent.kaggle import student_data as SD


def _row(i, crunch=False, late=False, diff=10, crunch_next=False, safe="yes", garbage=False, league="nba"):
    return {"id": f"{league}:g:{i}", "league": league, "crunch": crunch, "late": late, "abs_diff": diff,
            "crunch_next": crunch_next, "garbage": garbage, "y": int(safe == "no"),
            "target": {"lock_in": "none", "safe_to_leave": safe}}


def test_sticky_is_observable_and_league_aware():
    assert E.sticky(_row(1, crunch=True))
    assert E.sticky(_row(2, late=True, diff=6)) and not E.sticky(_row(3, late=True, diff=7))
    assert E.sticky(_row(4, late=True, diff=1, league="nhl")) and not E.sticky(_row(5, late=True, diff=2, league="nhl"))
    assert not E.sticky(_row(6, crunch=True, garbage=True))
    assert not E.sticky(_row(7, crunch_next=True))          # crunch_next is hindsight: never an input


def test_crunch_floor_is_off_by_default_and_forced_only_when_asked():
    assert E.CRUNCH_STAY_PRIOR is False             # Nick 4:15 PM: crunch is learned; the floor is the decider's
    rows = [_row(1, crunch=True)]
    ans = {"nba:g:1": {"lock_in": "watch", "safe_to_leave": "yes", "confidence": 0.9, "stay_s": 60, "storyline": "s",
                       "why": "w", "next": ""}}
    assert E.answers_for("student", rows, ans)["nba:g:1"]["safe_to_leave"] == "yes"
    assert E.answers_for("student", rows, ans, priors=True)["nba:g:1"]["safe_to_leave"] == "no"


def test_crunch_stay_prior_overrides_a_leave():
    leave = {"lock_in": "watch", "safe_to_leave": "yes", "confidence": 0.9, "storyline": "s", "why": "w"}
    assert E.apply_priors(_row(1, crunch=True), leave) == dict(leave, safe_to_leave="no", lock_in="must_see")
    assert E.apply_priors(_row(2, late=True, diff=3), leave)["safe_to_leave"] == "no"
    assert E.apply_priors(_row(3), leave) is leave
    assert E.apply_priors(_row(4, crunch=True), None) is None


def test_unsafe_leaves_split_by_leverage():
    rows = [_row(1, crunch_next=True, safe="no"), _row(2, safe="no"), _row(3), _row(4)]
    preds = {r["id"]: {"lock_in": "none", "safe_to_leave": "yes", "next": ""} for r in rows}
    s = E.score(rows, preds)
    assert s["unsafe_leave_pct"] == 50.0 and s["unsafe_leave_hi_pct"] == 25.0 and s["unsafe_leave_lo_pct"] == 25.0


def test_crunch_and_sticky_rows_weigh_most():
    assert SD.weight(_row(1, crunch=True)) > SD.weight(_row(2, late=True, diff=2)) > SD.weight(_row(3)) == 1.0


def test_teacher_queue_round_robins_leagues_in_order():
    q = [{"id": f"nfl:a:{i}", "task": "reasoner_grounded"} for i in range(4)] + \
        [{"id": "cbb:b:0", "task": "reasoner_grounded"}, {"id": "mlb:c:0", "task": "jump"}, {"id": "w1", "task": "wd_x"}]
    out = balance_by_league(q)
    assert [r["id"] for r in out[:4]] == ["nfl:a:0", "cbb:b:0", "mlb:c:0", "w1"]
    assert [r["id"] for r in out if r["id"].startswith("nfl")] == [f"nfl:a:{i}" for i in range(4)]
    assert sorted(map(str, out), key=str) == sorted(map(str, q), key=str)
