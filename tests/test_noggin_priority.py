"""Noggin Priority: L1 hard rules can't be broken by L2-L4, ranking is deterministic, reasons are readable."""
import random
from pathlib import Path

from redzone.config import Config
from redzone.noggin import priority as P

RULES = P.HardRules({"nfl": frozenset({"WSH"}), "nba": frozenset({"WSH"}), "nhl": frozenset({"WSH"}),
                     "mlb": frozenset({"WSH"}), "cbb": frozenset({"UNC"}), "cfb": frozenset({"UNC"})})
LEAGUES = list(P.DEFAULT_LEAGUE_ORDER)
TEAMS = ["WSH", "UNC", "DUKE", "BOS", "NYG", "DAL", "LAL", "TOR"]


def _random_game(rng, i):
    lg = rng.choice(LEAGUES)
    live = {k: rng.uniform(-10, 60) for k in rng.sample(["red_zone", "close_late", "power_play", "wp_swing",
                                                         "restart_soon", "run", "overtime"], rng.randint(0, 4))}
    return P.GameInput(f"g{i}", lg, tuple(rng.sample(TEAMS, 2)), app=rng.choice(["youtubetv", "prime", "peacock", ""]),
                       crunch=rng.random() < 0.3, big_moment=rng.random() < 0.3, live=live,
                       bet=rng.choice([0, 0, 10, 25]))


def _random_mind(rng, now):
    terms = {k: rng.uniform(-3, 3) for k in P.MIND_TERM_POINTS}
    terms["made_up_term"] = float("nan")
    return P.MindRead(terms, at=now - rng.uniform(0, 120), ttl_s=90)


def test_l1_holds_for_any_inputs():
    """Property test: 2,000 random slates with hostile L2-L4 inputs never break an L1 rule."""
    rng = random.Random(7)
    for trial in range(2000):
        now = 1_000_000.0 + trial
        games = [_random_game(rng, i) for i in range(rng.randint(1, 9))]
        minds = {g.game_id: _random_mind(rng, now) for g in games}
        prefs = P.Preferences(bets_weight=rng.uniform(-5, 50), rivalry_points=rng.uniform(-50, 500),
                              rivalries=frozenset({frozenset({"UNC", "DUKE"})}),
                              nhl_pp_ot_boost=rng.uniform(-5, 50), switch_frequency=rng.uniform(-1, 9))
        ranked = P.rank(games, RULES, prefs, minds, now)
        assert P.violations(ranked, {g.game_id: g for g in games}, RULES) == []
        assert all(g.app != "peacock" for g in games if g.game_id in {r.game_id for r in ranked})
        for r in ranked:   # L3 bounded
            mind = sum(v for k, v in r.terms if k.startswith("mind:"))
            assert abs(mind) <= P.MIND_CAP + 1e-9


def test_your_team_big_moment_beats_everything():
    big = P.GameInput("other", "nfl", ("DAL", "NYG"), crunch=True, big_moment=True, live={"red_zone": 90})
    mine = P.GameInput("caps", "nhl", ("WSH", "TOR"), big_moment=True, live={"power_play": 5})
    mind = {"other": P.MindRead({"comeback_brewing": 1, "upset_alert": 1}, at=0)}
    ranked = P.rank([big, mine], RULES, P.Preferences(), mind, now=10)
    assert [r.game_id for r in ranked] == ["caps", "other"]
    assert ranked[0].reason.startswith("caps your team")


def test_higher_league_crunch_over_lower_league_noncrunch_and_crunch():
    nba_crunch = P.GameInput("nba", "nba", ("LAL", "BOS"), crunch=True)
    nhl_crunch = P.GameInput("nhl", "nhl", ("TOR", "BOS"), crunch=True, live={"overtime": 80})
    mlb_hot = P.GameInput("mlb", "mlb", ("NYY", "BOS"), live={"close_late": 99})
    mind = {"mlb": P.MindRead({"upset_alert": 1, "comeback_brewing": 1}, at=0)}
    ranked = P.rank([mlb_hot, nhl_crunch, nba_crunch], RULES, P.Preferences(nhl_pp_ot_boost=2), mind, now=1)
    assert [r.game_id for r in ranked] == ["nba", "nhl", "mlb"]


def test_never_peacock():
    games = [P.GameInput("p", "nfl", ("WSH", "DAL"), app="Peacock", crunch=True, big_moment=True),
             P.GameInput("y", "mlb", ("NYY", "BOS"), app="youtubetv")]
    assert [r.game_id for r in P.rank(games, RULES, P.Preferences())] == ["y"]


def test_mind_reorders_only_inside_a_tier_and_expires():
    a = P.GameInput("a", "nba", ("LAL", "BOS"), live={"run": 20}, label="LAL")
    b = P.GameInput("b", "nba", ("TOR", "NYK"), live={"run": 12}, label="TOR")
    minds = {"b": P.MindRead({"comeback_brewing": 1.0}, at=100)}
    assert [r.game_id for r in P.rank([a, b], RULES, P.Preferences(), minds, now=120)] == ["b", "a"]
    # stale after its TTL: back to the Reflex order
    assert [r.game_id for r in P.rank([a, b], RULES, P.Preferences(), minds, now=500)] == ["a", "b"]


def test_deterministic_and_order_independent():
    rng = random.Random(3)
    games = [_random_game(rng, i) for i in range(8)]
    minds = {g.game_id: _random_mind(rng, 50) for g in games}
    one = P.rank(games, RULES, P.Preferences(), minds, now=50)
    two = P.rank(list(reversed(games)), RULES, P.Preferences(), dict(reversed(list(minds.items()))), now=50)
    assert one == two


def test_reason_string_names_top_terms():
    g = P.GameInput("u", "cbb", ("UNC", "DUKE"), crunch=True, live={"run": 25, "wp_swing": 3}, bet=12, label="UNC")
    r = P.rank([g], RULES, P.Preferences(rivalries=frozenset({frozenset({"UNC", "DUKE"})})))[0]
    assert r.reason == "UNC your team + run + your bet + rivalry"
    other = P.GameInput("o", "cbb", ("KU", "UK"), crunch=True, live={"run": 25}, bet=12, label="KU")
    assert P.rank([other], RULES, P.Preferences())[0].reason == "KU run + your bet + crunch"


def test_prefs_clamped_and_margin():
    p = P.Preferences(bets_weight=99, switch_frequency=0.01).clamped()
    assert p.bets_weight == 2.0 and p.switch_frequency == 0.5
    assert P.margin(10, P.Preferences(switch_frequency=2.0)) == 5.0


def test_nothing_learns_from_nicks_votes():
    """[hard rule, Nov 4] Nick's 👍/👎, taps and overrides are controls, never labels: no learner reads them."""
    assert not hasattr(P, "suggest_from_feedback")


def test_rules_from_default_config():
    cfg = Config(user_path=Path("/nonexistent.yaml"))
    rules = P.HardRules.from_config(cfg)
    assert rules.is_yours("nfl", ["WSH"]) and rules.is_yours("cbb", ["unc"])
    assert rules.league_order[:3] == ("nfl", "cfb", "nba")
    assert "peacock" in rules.banned_apps
