"""The arbiter (WP-17): a pure pick among legal options, a must-show floor no brain can lower, Rule #1 above all of it."""
import random
from dataclasses import replace

from redzone.contracts import brain
from redzone.decider.decider import Candidate, Decider, Decision, State
from redzone.decider.screen import DelayBook, Event
from redzone.models import League
from redzone.noggin import arbiter as A
from redzone.noggin import legal, priority as P
from redzone.scoring.engine import Scored
from tests.brains import ATTACKS, AdversarialBrain, NullBrain, RandomBrain

NOW = 1000.0


def cut(g, cls=2, prep=0.0):
    return brain.Option(id=f"cut:{g}", verb="cut", game_id=g, l1_class=cls, needs_prep_s=prep)


def inp(*opts):
    return brain.BrainInput(tick=7, now=NOW, options=(legal.STAY, *opts))


def must(g, kind="td", air=NOW + 30.0, league="nfl"):
    return A.must_of(league, kind, g, air)


def scores(i, **by):
    return brain.BrainOutput(tick=i.tick, made_at=NOW, scores={k.replace("__", ":"): v for k, v in by.items()})


# ------------------------------------------------------------ the floor and Nick's must-show list

def test_nicks_football_must_show_list_is_all_on_the_floor():
    for k in ("td", "turnover", "gain10", "fg", "big3rd", "4th_try", "wp5"):
        assert A.trigger_rank("nfl", k) != A.NOT_FLOOR and A.trigger_rank("cfb", k) != A.NOT_FLOOR
    assert A.trigger_rank("nfl", "nonsense") == A.NOT_FLOOR
    assert A.trigger_rank("xfl", "td") == A.NOT_FLOOR     # a league we don't show is never owed


def test_a_must_carries_the_moment_tier_and_only_an_unlisted_kind_gets_the_highlight_tier():
    """Must.tier orders the floor: a touchdown is tier 1 and a field goal tier 2, not everything at the highlight tier."""
    from redzone.moments import tiers
    assert [A.must_of("nfl", k, "g", NOW).tier for k in ("td", "fg", "gain10")] == [1, 2, 3]
    assert A.must_of("nfl", "td", "g", NOW).tier == tiers.tier_of("nfl", "td")
    assert tiers.tier_of("nba", "rz_trip") is None
    assert A.must_of("nba", "rz_trip", "g", NOW).tier == tiers.TIER_HIGHLIGHT


def test_trigger_order_is_score_then_red_zone_then_turnover_then_late():
    r = [A.trigger_rank("nfl", k) for k in ("td", "fg", "rz_trip", "turnover", "wp5")]
    assert r == sorted(r) and len(set(r)) == 5


def test_trigger_order_can_be_turned_off():
    p = replace(A.DEFAULT_PARAMS, trigger_order=False)
    assert A.trigger_rank("nfl", "td", p) == A.trigger_rank("nfl", "turnover", p)      # both tier 1
    assert A.trigger_rank("nfl", "td", p) < A.trigger_rank("nfl", "gain10", p)


def test_floor_beats_the_baseline_and_says_why():
    i = inp(cut("a"), cut("b"))
    c = A.choose(i, NullBrain().think(i), "cut:b", musts=[must("a")])
    assert c.option.id == "cut:a" and c.why == "floor:td"


def test_null_brain_with_nothing_owed_is_exactly_the_baseline():
    i = inp(cut("a"), cut("b"))
    for base in ("cut:b", "cut:a", "stay"):
        assert A.choose(i, NullBrain().think(i), base).option.id == base


def test_a_decision_outside_the_legal_list_is_left_to_the_app():
    i = inp(cut("a"))
    assert A.choose(i, NullBrain().think(i), "cut:gone").option is None


def test_a_brain_can_never_lower_the_floor():
    i = inp(cut("a"), cut("b"))
    assert A.choose(i, scores(i, cut__b=100.0, stay=100.0, cut__a=-100.0), "stay", musts=[must("a")]).option.id == "cut:a"


def test_a_brain_only_orders_inside_the_floor_when_triggers_tie():
    i = inp(cut("a"), cut("b"))
    assert A.choose(i, scores(i, cut__a=1.0, cut__b=50.0), "stay", musts=[must("a"), must("b")]).option.id == "cut:b"
    assert A.choose(i, scores(i, cut__a=50.0, cut__b=1.0), "stay", musts=[must("a"), must("b")]).option.id == "cut:a"
    assert A.choose(i, scores(i, cut__b=100.0), "stay", musts=[must("a", "td"), must("b", "gain10")]).option.id == "cut:a"


def test_touchdown_on_the_screen_keeps_the_screen_over_a_lesser_must():
    i = inp(cut("b"))
    c = A.choose(i, None, "cut:b", musts=[must("on", "td"), must("b", "gain10")], show=A.Show(current="on"))
    assert c.option.id == "stay"


# ------------------------------------------------------------ per-route deadline

def test_route_deadline_comes_from_that_routes_prep():
    assert A.route_deadline(100.0, 15.0) == 100.0 - 15.0 - A.DEFAULT_PARAMS.flip_margin_s
    m = must("a", air=NOW + 20.0)
    assert A.owed(m, 15.0, NOW) and not A.owed(m, 25.0, NOW)


def test_a_route_too_slow_for_its_moment_drops_off_the_floor():
    i = inp(cut("slow", prep=40.0), cut("b"))
    c = A.choose(i, None, "cut:b", musts=[must("slow", air=NOW + 30.0)])
    assert c.option.id == "cut:b"
    i2 = inp(cut("fast", prep=5.0))
    assert A.choose(i2, None, "stay", musts=[must("fast", air=NOW + 30.0)]).why == "floor:td"


def test_preload_takes_a_reachable_must_and_only_moves_for_a_higher_trigger():
    ms = [must("a", "gain10"), must("b", "td")]
    assert A.preload_target(ms, {"a": 5.0, "b": 5.0}, None, NOW) == "b"
    assert A.preload_target([must("a", "td")], {"a": 5.0}, "a", NOW) is None                # already holds it
    assert A.preload_target([must("b", "gain10"), must("a", "td")], {"b": 5.0}, "a", NOW) is None   # holds the higher one
    assert A.preload_target([must("a", air=NOW + 3.0)], {"a": 15.0}, None, NOW) is None     # past its route's deadline
    assert A.preload_target([must("a", "gain10")], {"a": 5.0}, None, NOW) == "a"            # an empty stick takes any must
    assert A.preload_target([must("a", "gain10")], {"a": 5.0}, "b", NOW) is None            # a parked stick moves for a big one only
    assert A.preload_target([must("a", "td")], {"a": 5.0}, "b", NOW) == "a"


# ------------------------------------------------------------ L1

def test_l1_class_is_never_crossed_by_the_floor_or_the_brain():
    i = inp(cut("open", cls=2), cut("crunch", cls=1))
    assert A.choose(i, None, "stay", musts=[must("open")]).why != "floor:td"
    assert A.choose(i, None, "stay", musts=[must("crunch")]).option.id == "cut:crunch"
    assert A.choose(i, scores(i, cut__open=99.0, cut__crunch=-5.0, stay=-50.0), "stay").option.id == "cut:crunch"


def test_a_must_show_never_pulls_the_tv_off_your_teams_game():
    i = inp(cut("open", cls=2))
    assert A.choose(i, None, "stay", musts=[must("open")], show=A.Show(current_class=0)).option.id == "stay"


# ------------------------------------------------------------ the copied rules, each with its own switch

def test_hard_ceiling_three_cuts_a_minute():
    i = inp(cut("a"))
    assert A.choose(i, None, "cut:a", musts=[must("a")], show=A.Show(cuts_60s=3)).why == "ceiling"
    assert A.choose(i, None, "cut:a", show=A.Show(cuts_60s=3), p=replace(A.DEFAULT_PARAMS, ceiling_per_min=0)).option.id == "cut:a"


def test_last_game_standing_stops_whipping():
    i = inp(cut("reel"))
    assert A.choose(i, None, "cut:reel", show=A.Show(live_games=1)).why == "last_game"
    assert A.choose(i, None, "cut:reel", show=A.Show(live_games=1), p=replace(A.DEFAULT_PARAMS, last_game=False)).option.id == "cut:reel"


def test_stay_for_the_replay_unless_a_higher_trigger_waits():
    i = inp(cut("a"))
    fg = A.trigger_rank("nfl", "fg")
    p = replace(A.DEFAULT_PARAMS, stay_replay_s=6.8)
    assert A.choose(i, None, "cut:a", show=A.Show(replay_rank=fg), p=p).why == "stay_replay"
    assert A.choose(i, None, "cut:a", musts=[must("a", "gain10")], show=A.Show(replay_rank=fg), p=p).why == "stay_replay"
    assert A.choose(i, None, "stay", musts=[must("a", "td")], show=A.Show(replay_rank=fg), p=p).option.id == "cut:a"
    assert A.choose(i, None, "cut:a", show=A.Show(replay_rank=fg)).option.id == "cut:a"    # off by default: measured


def test_late_narrowing_keeps_close_games_and_the_floor():
    i = inp(cut("close"), cut("blowout"))
    s = A.Show(late=True, close=frozenset({"close"}))
    assert A.choose(i, None, "cut:blowout", show=s).option.id == "stay"
    assert A.choose(i, None, "cut:close", show=s).option.id == "cut:close"
    assert A.choose(i, None, "cut:blowout", musts=[must("blowout")], show=s).option.id == "cut:blowout"
    assert A.choose(i, None, "cut:blowout", show=s, p=replace(A.DEFAULT_PARAMS, late_narrow=False)).option.id == "cut:blowout"


def test_visit_budget_keeps_one_for_the_final_two_minutes():
    i = inp(cut("a"))
    p = replace(A.DEFAULT_PARAMS, visit_budget=3, visit_reserve=1)
    assert A.choose(i, None, "cut:a", show=A.Show(visits={"a": 1}), p=p).option.id == "cut:a"
    assert A.choose(i, None, "cut:a", show=A.Show(visits={"a": 2}), p=p).option.id == "stay"
    assert A.choose(i, None, "cut:a", show=A.Show(visits={"a": 2}, final_two=frozenset({"a"})), p=p).option.id == "cut:a"
    assert A.choose(i, None, "cut:a", show=A.Show(visits={"a": 3}, final_two=frozenset({"a"})), p=p).option.id == "stay"
    assert A.choose(i, None, "cut:a", musts=[must("a")], show=A.Show(visits={"a": 99}), p=p).option.id == "cut:a"


def test_decide_last_does_not_commit_to_a_window_that_has_not_opened():
    o = replace(cut("a"), earliest_s=5.0)
    i = inp(o)
    assert A.choose(i, None, "cut:a", musts=[must("a")]).option is None
    assert A.choose(i, None, "cut:a", musts=[must("a")], p=replace(A.DEFAULT_PARAMS, decide_last=False)).why == "floor:td"


# ------------------------------------------------------------ Rule #1: only ever a legal option, never resume or skip

def test_forbidden_kinds_and_unknown_verbs_fail_closed():
    bad = (brain.Option(id="r", verb="resume", game_id="a"), brain.Option(id="s", verb="skip", game_id="a"),
           brain.Option(id="x", verb="teleport", game_id="a"))
    i = brain.BrainInput(tick=1, now=NOW, options=(legal.STAY, *bad))
    for attempt in (scores(i, r=100.0, s=100.0, x=100.0), None):
        c = A.choose(i, attempt, "r", musts=[A.must_of("nfl", "td", "a", NOW + 30.0)])
        assert c.option is None or c.option.verb == "stay"


def test_no_legal_cut_means_no_cut_however_big_the_moment():
    facts = legal.Facts(now=NOW, current="on", gate_ok=False, since=0.0, games=(legal.GameRow("a", app="yt"),))
    i = brain.BrainInput(tick=1, now=NOW, options=legal.legal_options(facts))
    assert A.choose(i, None, "stay", musts=[must("a")], show=A.Show(current="on")).option.id == "stay"


def _hostile_pool(rng):
    games = [f"g{n}" for n in range(rng.randint(1, 8))]
    opts = tuple(brain.Option(id=f"cut:{g}", verb=rng.choice(["cut", "reel", "fill"]), game_id=g, l1_class=rng.randint(0, 2),
                              needs_prep_s=rng.uniform(0, 60)) for g in games)
    return games, brain.BrainInput(tick=3, now=NOW, options=(legal.STAY, *opts))


def test_hostile_brains_on_two_thousand_slates_never_escape_the_rules():
    rng = random.Random(17)
    brains = [NullBrain(), RandomBrain(seed=5), *(AdversarialBrain(a) for a in ATTACKS)]
    for n in range(2000):
        games, i = _hostile_pool(rng)
        ms = [m for g in games if rng.random() < .4
              for m in [A.must_of("nfl", rng.choice(["td", "turnover", "gain10"]), g, NOW + rng.uniform(-5, 60))]]
        show = A.Show(current_class=rng.randint(0, 2))
        out = brains[n % len(brains)].think(i)
        c = A.choose(i, out, "stay", musts=ms, show=show)
        assert c.option in i.options and legal.kind_of(c.option.verb) not in legal.FORBIDDEN_KINDS
        floor = A.must_show_floor(A.lowest_l1_class(i.options, show.current_class), ms, None, NOW)
        if floor:                                  # whatever the brain said, a floor option wins
            assert c.option.id in floor and c.why.startswith("floor:")
        cuts = [o for o in i.options if o.verb != "stay"]
        if c.option.verb != "stay" and c.why.startswith(("floor", "brain")):
            assert c.option.l1_class == min([o.l1_class for o in cuts] + [show.current_class])     # L1 never crossed


def test_choose_is_pure():
    i = inp(cut("a"), cut("b"))
    runs = {A.choose(i, RandomBrain(seed=1).think(i), "stay", musts=[must("a")]) for _ in range(5)}
    assert len(runs) == 1


def test_params_read_each_rule_and_keep_defaults_on_bad_values():
    cfg = {"arbiter.visit_budget": 2, "arbiter.late_narrow": False, "arbiter.ceiling_per_min": "lots",
           "arbiter.floor_extra": ["fg"], "arbiter.stay_replay_s": True}
    p = A.Params.from_config(lambda k, d=None: cfg.get(k, d))
    assert p.visit_budget == 2 and p.late_narrow is False and p.floor_extra == ("fg",)
    assert p.ceiling_per_min == A.DEFAULT_PARAMS.ceiling_per_min and p.stay_replay_s == A.DEFAULT_PARAMS.stay_replay_s


# ------------------------------------------------------------ the tier in Ranked.key

def _g(gid, **kw):
    return P.GameInput(gid, "nfl", ("X",), live={"score": kw.pop("score", 10.0)}, **kw)


def test_moment_tier_orders_inside_a_class_and_never_across_one():
    rules = P.HardRules({"nfl": frozenset()})
    prefs = P.Preferences()
    plain = P.rank([_g("a", score=90.0), _g("b", score=10.0, moment_tier=1)], rules, prefs)
    assert [r.game_id for r in plain] == ["b", "a"]
    crossing = P.rank([_g("crunch", crunch=True), _g("open", score=99.0, moment_tier=1)], rules, prefs)
    assert [r.game_id for r in crossing] == ["crunch", "open"]
    assert not P.violations(crossing, {"crunch": _g("crunch", crunch=True), "open": _g("open")}, rules)


def test_no_moment_ranks_as_it_did_before_the_tier_existed():
    rules, prefs = P.HardRules({"nfl": frozenset()}), P.Preferences()
    assert P.NO_MOMENT > 3
    r = P.rank([_g("a", score=5.0), _g("b", score=6.0)], rules, prefs)
    assert [x.game_id for x in r] == ["b", "a"]
    assert r[0].key[2] == P.NO_MOMENT


# ------------------------------------------------------------ the decider stage (flag gated, fakes only)

class Cfg:
    def __init__(self, **v):
        self.v = {"arbiter.enabled": True, "decider.crunch.hold_personal": 5, "decider.dwell.min_s": 0, **v}

    def get(self, k, d=None):
        return self.v.get(k, d)


def make(on=True):
    d = Decider.__new__(Decider)
    d.cfg = Cfg(**{"arbiter.enabled": on})
    d.current, d.replaying, d.after_replay, d.safe_mode = "on", False, False, False
    d.user = type("U", (), {"paused": False})()
    d.log, d.history = [], __import__("collections").deque()
    d.timelines, d._yours, d.entry_cost = {}, set(), None
    d.arbiter_brain, d.arbiter_log = A.NullBrain(), __import__("collections").Counter()
    d.delays = DelayBook.__new__(DelayBook)
    d.delays.get = lambda t: type("D", (), {"mu": 30.0})()
    d._home_restore = lambda *a: None
    d._clock = lambda: NOW
    return d


def cand(gid):
    s = Scored.__new__(Scored)
    s.__dict__.update(personal=0, crunch=False, high_leverage=False, late=False, lock=None)
    x = Candidate.__new__(Candidate)
    x.__dict__.update(scored=s, target=f"yt:{gid}")
    return x


def stage(d, cands, opts, base):
    d.legal_facts = lambda c, n: None
    real = legal.legal_options
    legal.legal_options = lambda f: opts
    try:
        return d._arbiter(base, cands, NOW, (None, None))
    finally:
        legal.legal_options = real


def test_flag_off_the_stage_is_a_no_op_and_reads_nothing():
    d = make(on=False)
    base = Decision(NOW, "stay", "on", "x", State.WATCHING)
    assert d._arbiter(base, None, NOW, None) is base
    assert not d._arbiter_on()


def test_flag_defaults_off():
    d = make()
    d.cfg = Cfg()
    d.cfg.v.pop("arbiter.enabled")
    assert not d._arbiter_on()


def test_hard_kinds_are_never_rewritten():
    d = make()
    for kind in ("user", "crunch", "ended", "resume", "skip", "chase"):
        base = Decision(NOW, "switch", "x", "x", State.WATCHING, kind=kind)
        assert d._arbiter(base, {}, NOW, None) is base


def test_stage_cuts_for_the_floor_as_an_ordinary_better_switch_so_the_gate_still_judges_it():
    d = make()
    d.timelines["a"] = type("T", (), {"league": League.NFL, "events": [Event(NOW + 40.0, "play", "TD", pid="p1", play_kind="td")]})()
    base = Decision(NOW, "stay", "on", "x", State.WATCHING)
    out = stage(d, {"on": cand("on"), "a": cand("a")}, (legal.STAY, cut("a")), base)
    assert out.action == "switch" and out.target == "a" and out.kind == "better" and out.kind not in legal.FORBIDDEN_KINDS


def test_stage_with_no_legal_cut_leaves_the_decision_alone():
    d = make()
    d.timelines["a"] = type("T", (), {"league": League.NFL, "events": [Event(NOW + 40.0, "play", "TD", pid="p1", play_kind="td")]})()
    base = Decision(NOW, "stay", "on", "x", State.WATCHING)
    assert stage(d, {"on": cand("on"), "a": cand("a")}, (legal.STAY,), base) is base


def test_stage_null_brain_nothing_owed_changes_nothing():
    d = make()
    base = Decision(NOW, "switch", "a", "x", State.WATCHING, kind="better")
    assert stage(d, {"on": cand("on"), "a": cand("a")}, (legal.STAY, cut("a")), base) is base


def test_a_brain_that_raises_counts_as_no_opinion():
    d = make()

    class Boom:
        def think(self, inp):
            raise RuntimeError("bad model")
    d.arbiter_brain = Boom()
    base = Decision(NOW, "stay", "on", "x", State.WATCHING)
    assert stage(d, {"on": cand("on"), "a": cand("a")}, (legal.STAY, cut("a")), base) is base


def test_a_must_show_already_aired_is_not_owed():
    d = make()
    d.timelines["a"] = type("T", (), {"league": League.NFL, "events": [Event(NOW - 60.0, "play", "TD", pid="p1", play_kind="td")]})()
    assert d._arbiter_musts({"a": cand("a")}, NOW, A.DEFAULT_PARAMS) == ()


def test_timeline_words_match_the_floor():
    assert A.timeline_word("nfl", "td", False) == "td"
    assert A.timeline_word("nfl", "safety", False) == "turnover"
    assert A.timeline_word("nfl", "rush", True) == "gain10"
    assert A.timeline_word("nfl", "rush", False) == ""
    assert A.timeline_word("nba", "made", True) == "clutch_fg"
    assert A.timeline_word("nhl", "goal", False) == "goal"
    assert A.timeline_word("mlb", "hr", False) == "hr"


def _td_timeline(gid_air=NOW + 40.0):
    return type("T", (), {"league": League.NFL, "events": [Event(gid_air, "play", "TD", pid="p1", play_kind="td")]})()


def test_rotation_floor_overrides_the_value_pick_among_games_that_land_on_a_dead_ball():
    d = make()
    d.timelines["b"] = _td_timeline()
    rows = [(90.0, "a", {"who": "a"}), (10.0, "b", {"who": "b"})]
    got = d._arbiter_rotation(rows, "a", {"on": cand("on"), "a": cand("a"), "b": cand("b")}, NOW)
    assert got == ("b", {"who": "b"})


def test_rotation_never_picks_a_game_outside_its_proven_rows():
    d = make()
    d.timelines["c"] = _td_timeline()
    rows = [(90.0, "a", {"who": "a"})]
    got = d._arbiter_rotation(rows, "a", {"on": cand("on"), "a": cand("a"), "c": cand("c")}, NOW)
    assert got == ("a", {"who": "a"})


def test_rotation_holds_at_the_ceiling_and_counts_why():
    d = make()
    d.jump_holds = __import__("collections").Counter()
    d.history.extend((NOW - i, "x", "jump") for i in range(3))
    rows = [(90.0, "a", {})]
    assert d._arbiter_rotation(rows, "a", {"on": cand("on"), "a": cand("a")}, NOW) is None
    assert d.jump_holds["arbiter_ceiling"] == 1 and d.arbiter_log["ceiling"] == 1


def test_hidden_stick_goes_for_a_reachable_touchdown():
    d = make()
    d._deadline_cost = lambda t: 5.0
    d.timelines["b"] = _td_timeline()
    assert d._arbiter_preload({"a": cand("a"), "b": cand("b")}, None, NOW) == "b"
    d._deadline_cost = lambda t: 60.0
    assert d._arbiter_preload({"a": cand("a"), "b": cand("b")}, None, NOW) is None


def test_best_ranks_a_must_show_first_inside_its_class():
    from redzone.models import League as L
    d = make()
    d.timelines["b"] = _td_timeline()

    def scored(gid):
        x = cand(gid)
        x.scored.__dict__.update(league=L.NFL, score=10.0, pre_moment=False, lock=None)
        return x
    w = {"a": scored("a"), "b": scored("b")}
    assert d._arbiter_rank(w, ["a", "b"], set(), {"a": 90.0, "b": 1.0}, {}) == "a"
    assert d._arbiter_rank(w, ["a", "b"], set(), {"a": 90.0, "b": 1.0}, d._arbiter_moment_tiers(w, NOW)) == "b"


def test_the_budget_never_stops_a_break_visit_or_counts_one():
    d = make()
    d.cfg.v.update({"arbiter.visit_budget": 2})
    d.history.extend([(NOW - 65, "a", "better"), (NOW - 70, "a", "break_visit"), (NOW - 90, "a", "better")])
    assert d._arbiter_show({"on": cand("on"), "a": cand("a")}, NOW, A.Params.from_config(d.cfg.get)).visits["a"] == 2
    base = Decision(NOW, "switch", "a", "fill the break", State.WATCHING, kind="break_visit")
    assert stage(d, {"on": cand("on"), "a": cand("a")}, (legal.STAY, cut("a")), base) is base
    other = Decision(NOW, "switch", "a", "better game", State.WATCHING, kind="better")
    assert stage(d, {"on": cand("on"), "a": cand("a")}, (legal.STAY, cut("a")), other).action == "stay"
