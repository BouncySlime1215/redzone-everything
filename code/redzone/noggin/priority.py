"""Noggin Priority: which game deserves the TV, in four layers applied in order (docs/noggin.md § Priority).

    L1  Nick's hard rules (config only, never overridable by AI)
            your team's big moments first (WSH: Commanders/Wizards/Capitals/Nationals; UNC), then
            crunch time, ranked by league order NFL > CFB > NBA > CBB > NHL > MLB; never Peacock.
    L2  Nick's preferences (phone page): bets weight, rivalries, NHL power-play/OT boost, reels on/off,
            switch frequency. Learned suggestions from 👍/👎 are returned for display, never applied.
    L3  Mind read (every 30-60 s): narrative and stakes numbers, bounded to +-mind_cap points and
            expired after its TTL. It can reorder games only inside the same L1 tier.
    L4  Reflex live value (every tick): red zone, close-late, power play, win-prob swing, restart countdown.

L1 is impossible to violate by construction: games are ranked by the tuple
(tier, league rank inside crunch, score), and only the score is touched by L2-L4. A game on a banned app
is never ranked at all. Everything is a pure function of its inputs (no clock, no randomness): replaying
a night through `rank` gives the same order and the same reason strings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from redzone.models import league_index, league_order

DEFAULT_LEAGUE_ORDER = league_order()     # one definition: redzone.models.LEAGUE_ORDER
DEFAULT_BANNED_APPS = ("peacock",)

TIER_YOUR_TEAM = 0   # your team in a big moment or crunch time
TIER_CRUNCH = 1      # anyone else's crunch time
TIER_OPEN = 2        # everything else: ranked by score only
TIER_NAMES = {TIER_YOUR_TEAM: "your team", TIER_CRUNCH: "crunch", TIER_OPEN: "open"}


# ---------------------------------------------------------------- inputs

@dataclass(frozen=True)
class HardRules:
    """L1. Built only from config (`from_config`), never from a model."""
    your_teams: Mapping[str, frozenset[str]] = field(default_factory=dict)  # league -> team abbreviations
    league_order: tuple[str, ...] = DEFAULT_LEAGUE_ORDER
    banned_apps: frozenset[str] = frozenset(DEFAULT_BANNED_APPS)

    @classmethod
    def from_config(cls, cfg) -> "HardRules":
        teams = cfg.get("teams") or {}
        order = league_order(cfg)
        banned = set(DEFAULT_BANNED_APPS) | {str(a).lower() for a in (cfg.get("noggin.priority.banned_apps") or [])}
        return cls({str(k): frozenset(str(t).upper() for t in (v or [])) for k, v in teams.items()},
                   tuple(str(x) for x in order), frozenset(banned))

    def league_rank(self, league: str) -> int:
        return league_index(league, order=self.league_order)

    def is_yours(self, league: str, teams: Iterable[str]) -> bool:
        mine = self.your_teams.get(league, frozenset())
        return any(t.upper() in mine for t in teams)


@dataclass(frozen=True)
class Preferences:
    """L2. Nick's phone-page settings. Multipliers are clamped to their ranges in `clamped`."""
    bets_weight: float = 1.0            # x the bet term
    rivalry_points: float = 6.0         # added when the game is one of `rivalries`
    rivalries: frozenset[frozenset[str]] = frozenset()   # {frozenset({"UNC","DUKE"}), ...}
    nhl_pp_ot_boost: float = 1.0        # x the NHL power-play / overtime term
    reels: bool = True                  # catch-up reels allowed
    switch_frequency: float = 1.0       # 0.5 calmer .. 2.0 busier: scales the margin a challenger must clear

    RANGES = {"bets_weight": (0.0, 2.0), "rivalry_points": (0.0, 15.0), "nhl_pp_ot_boost": (0.0, 2.0),
              "switch_frequency": (0.5, 2.0)}

    def clamped(self) -> "Preferences":
        kw = {k: min(hi, max(lo, float(getattr(self, k)))) for k, (lo, hi) in self.RANGES.items()}
        return Preferences(rivalries=self.rivalries, reels=bool(self.reels), **kw)

    @classmethod
    def from_config(cls, cfg) -> "Preferences":
        p = cfg.get("noggin.priority.prefs") or {}
        riv = frozenset(frozenset(str(t).upper() for t in pair) for pair in (p.get("rivalries") or []))
        return cls(bets_weight=p.get("bets_weight", 1.0), rivalry_points=p.get("rivalry_points", 6.0),
                   rivalries=riv, nhl_pp_ot_boost=p.get("nhl_pp_ot_boost", 1.0), reels=p.get("reels", True),
                   switch_frequency=p.get("switch_frequency", 1.0)).clamped()


@dataclass(frozen=True)
class MindRead:
    """L3. One Mind cycle's numbers for a game, each term in [-1, 1] (e.g. comeback_brewing 0.8)."""
    terms: Mapping[str, float]
    at: float
    ttl_s: float = 90.0


@dataclass(frozen=True)
class GameInput:
    game_id: str
    league: str                         # League.value
    teams: tuple[str, ...]              # abbreviations, e.g. ("UNC", "DUKE")
    app: str = ""                       # provider / app, e.g. "youtubetv", "peacock"
    crunch: bool = False                # crunch time (the scorer's definition)
    big_moment: bool = False            # a big moment is live or imminent (red zone, PP, close-late)
    live: Mapping[str, float] = field(default_factory=dict)   # L4 terms in points, e.g. {"red_zone": 30}
    bet: float = 0.0                    # L2 bet stake term in points (your over/under is live)
    label: str = ""                     # short name for reasons, e.g. "UNC"


# ---------------------------------------------------------------- output

@dataclass(frozen=True)
class Ranked:
    game_id: str
    tier: int
    league_rank: int
    score: float
    terms: tuple[tuple[str, float], ...]     # every contribution, largest |points| first
    reason: str

    @property
    def key(self) -> tuple:
        """Sort key: lower is better. L1 fields come first, so L2-L4 can never cross a tier."""
        crunch_rank = self.league_rank if self.tier <= TIER_CRUNCH else 0
        return (self.tier, crunch_rank, -self.score, self.game_id)


# ---------------------------------------------------------------- the four layers

MIND_TERM_POINTS = {   # points per unit of each Mind term before the cap
    "comeback_brewing": 10.0, "star_heating_up": 6.0, "playoff_race": 6.0, "upset_alert": 8.0,
    "bet_decisive_next": 8.0, "rivalry_heat": 4.0,
}
MIND_CAP = 15.0         # |L3 total| never exceeds this many points
REASON_TERMS = 3        # top contributors shown on the phone page
PRETTY = {"comeback_brewing": "comeback brewing", "star_heating_up": "star heating up",
          "playoff_race": "playoff race", "upset_alert": "upset alert", "bet_decisive_next": "bet decides next",
          "bet": "your bet", "rivalry": "rivalry", "red_zone": "red zone", "close_late": "close late",
          "power_play": "power play", "overtime": "overtime", "wp_swing": "win-prob swing",
          "restart_soon": "restart soon", "run": "run"}


def tier_of(g: GameInput, rules: HardRules) -> int:
    if rules.is_yours(g.league, g.teams) and (g.big_moment or g.crunch):
        return TIER_YOUR_TEAM
    return TIER_CRUNCH if g.crunch else TIER_OPEN


def mind_points(read: MindRead | None, now: float, cap: float = MIND_CAP) -> dict[str, float]:
    """L3 terms in points, scaled down together so |sum| <= cap. Stale or missing: nothing."""
    if read is None or now - read.at > read.ttl_s or now < read.at - 5.0:
        return {}
    pts = {}
    for k, v in sorted(read.terms.items()):
        try:
            x = float(v)
        except (TypeError, ValueError):
            continue
        if x != x:          # NaN
            continue
        pts[f"mind:{k}"] = max(-1.0, min(1.0, x)) * MIND_TERM_POINTS.get(k, 3.0)
    total = sum(pts.values())
    if abs(total) > cap:
        s = cap / abs(total)
        pts = {k: v * s for k, v in pts.items()}
    return pts


def score_terms(g: GameInput, prefs: Preferences, mind: dict[str, float]) -> dict[str, float]:
    """L4 live value, then L2 preference terms, then the bounded L3 terms."""
    terms = {k: float(v) for k, v in sorted(g.live.items()) if v == v}
    if g.league == "nhl" and prefs.nhl_pp_ot_boost != 1.0:
        for k in ("power_play", "overtime"):
            if k in terms:
                terms[k] *= prefs.nhl_pp_ot_boost
    if g.bet:
        terms["bet"] = g.bet * prefs.bets_weight
    if prefs.rivalries and frozenset(t.upper() for t in g.teams) in prefs.rivalries:
        terms["rivalry"] = prefs.rivalry_points
    terms.update(mind)
    return terms


def _reason(g: GameInput, tier: int, terms: dict[str, float]) -> str:
    top = [k for k, v in sorted(terms.items(), key=lambda kv: (-abs(kv[1]), kv[0])) if v > 0][:REASON_TERMS]
    words = [PRETTY.get(k.split(":", 1)[-1], k.split(":", 1)[-1].replace("_", " ")) for k in top]
    if tier == TIER_YOUR_TEAM:
        words.insert(0, "your team")
    elif tier == TIER_CRUNCH:
        words.append("crunch")
    name = g.label or g.game_id
    return f"{name} " + " + ".join(words) if words else name


def rank(games: Iterable[GameInput], rules: HardRules, prefs: Preferences,
         minds: Mapping[str, MindRead] | None = None, now: float = 0.0) -> list[Ranked]:
    """Every allowed game, best first. Banned apps are dropped (L1)."""
    prefs = prefs.clamped()
    out = []
    for g in games:
        if g.app and g.app.lower() in rules.banned_apps:
            continue
        tier = tier_of(g, rules)
        terms = score_terms(g, prefs, mind_points((minds or {}).get(g.game_id), now))
        score = sum(terms.values())
        ordered = tuple(sorted(terms.items(), key=lambda kv: (-abs(kv[1]), kv[0])))
        out.append(Ranked(g.game_id, tier, rules.league_rank(g.league), round(score, 6), ordered,
                          _reason(g, tier, terms)))
    return sorted(out, key=lambda r: r.key)


def margin(base_margin: float, prefs: Preferences) -> float:
    """L2 switch frequency: busier lowers the points a challenger must clear, calmer raises them."""
    return base_margin / prefs.clamped().switch_frequency


def violations(ranked: list[Ranked], games: Mapping[str, GameInput], rules: HardRules) -> list[str]:
    """L1 audit used by tests and the night lab: empty when every hard rule holds."""
    bad = []
    for i, a in enumerate(ranked):
        ga = games[a.game_id]
        if ga.app and ga.app.lower() in rules.banned_apps:
            bad.append(f"{a.game_id}: banned app {ga.app}")
        for b in ranked[i + 1:]:
            if b.tier < a.tier:
                bad.append(f"{b.game_id} ({TIER_NAMES[b.tier]}) below {a.game_id} ({TIER_NAMES[a.tier]})")
            if a.tier == b.tier == TIER_CRUNCH and b.league_rank < a.league_rank:
                bad.append(f"crunch {b.game_id} ({games[b.game_id].league}) below crunch {a.game_id}")
    return bad
