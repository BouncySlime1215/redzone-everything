"""The brief: one game's live situation as numbers (for the head) and as compact text (for the model).

Why: unknown (needs owner)

Built from what the app already has on every poll: the GameState, the brain's running features
(its Tracker row) and reading, plus your context (your team, a bet, what's on screen). Stdlib +
numpy; nothing here can raise on a strange feed (every field is optional, see redzone.models).

Why: first added in f79f070a (2026-11-03) as "Live game reasoner: brief builder, hindsight labels from the archive, a fast lock-in head (cross-..."; no separate decision is recorded for this module.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from redzone.models import REAL_BREAKS, GameState, League, Phase, Stoppage, pro_period

# Play kinds that count as a big play in the brief's "last plays" and the "since big" counter.
BIG_KINDS = {"td", "turnover", "goal", "hr", "safety"}


@dataclass
class Context:
    """What the brief knows about you (all optional)."""
    mine: bool = False            # your team is playing
    bet: str = ""                 # a short bet label, e.g. "WSH -3.5"
    on_screen: bool = False       # this game is on the TV now
    garbage: bool = False         # the scorer says garbage time
    crunch: bool = False          # the scorer says crunch time
    late: bool = False            # final period
    screen_game: str = ""         # what's on the TV now (matchup), for the brief text


def _f(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return float("nan")
    return v if math.isfinite(v) else float("nan")


def _skaters(s: str | None) -> tuple[float, float]:
    """'5v4' -> (5, 4); anything else -> (nan, nan)."""
    try:
        a, b = (s or "").lower().split("v")
        return float(a), float(b)
    except (ValueError, AttributeError):
        return float("nan"), float("nan")


# --- numeric facts (the head's features) --------------------------------------------------------

COMMON = ["period", "clock_s", "frac_left", "is_ot", "abs_diff", "wp_lev", "wp_delta", "wp_vol_8",
          "wp_vol_w", "lead_changes", "run_max", "pts_w", "in_break", "s_since_break", "ev_since_break",
          "crunch", "garbage", "late", "mine", "brain_key", "brain_break", "brain_hot",
          "recent_scores", "recent_big", "plays_since_big", "events"]
EXTRA = {
    "nfl": ["possession_trailing", "yardline_100", "down", "ydstogo", "red_zone", "goal_to_go",
            "two_min", "drive_plays", "def_timeouts"],
    "nba": ["trail_timeouts"],
    "nhl": ["pp", "pp_5v3", "empty_net"],
    "mlb": ["outs", "runners"],
}
BASE = {"nfl": "nfl", "cfb": "nfl", "nba": "nba", "cbb": "nba", "wcbb": "nba", "nhl": "nhl", "mlb": "mlb"}


def features_for(league: str) -> list[str]:
    return COMMON + EXTRA[BASE[league]]


def facts(g: GameState, row: dict | None, reading=None, ctx: Context | None = None) -> dict:
    """Every number the head and the brief use, for this state. NaN = unknown."""
    ctx = ctx or Context()
    row = row or {}
    lg = BASE.get(g.league.value, g.league.base.value)
    s = g.situation
    hs, as_ = g.home.score, g.away.score
    diff = (hs - as_) if hs is not None and as_ is not None else float("nan")
    rd = reading.as_dict() if hasattr(reading, "as_dict") else (reading or {})
    plays = [p for p in (g.recent_plays or []) if p is not None][-8:]
    big_idx = [i for i, p in enumerate(plays) if (p.kind or "") in BIG_KINDS]
    out = dict(
        period=_f(g.period), clock_s=_f(g.clock_s), frac_left=_f(row.get("frac_left")),
        is_ot=_f(row.get("is_ot")), abs_diff=abs(diff) if not math.isnan(diff) else float("nan"),
        wp_lev=_f(row.get("wp_lev")), wp_delta=_f(row.get("wp_delta")), wp_vol_8=_f(row.get("wp_vol_8")),
        wp_vol_w=_f(row.get("wp_vol_w")), lead_changes=_f(row.get("lead_changes")),
        run_max=max(_f(row.get("run_home")), _f(row.get("run_away"))) if row else float("nan"),
        pts_w=_f(row.get("pts_w")), in_break=float(g.stoppage in REAL_BREAKS),
        s_since_break=_f(row.get("s_since_break")), ev_since_break=_f(row.get("ev_since_break")),
        crunch=float(ctx.crunch), garbage=float(ctx.garbage), late=float(ctx.late), mine=float(ctx.mine),
        brain_key=_f(rd.get("key_soon_p")), brain_break=_f(rd.get("break_soon_p")),
        brain_hot=_f(rd.get("hotness")),
        recent_scores=float(sum(1 for p in plays if p.scoring)), recent_big=float(len(big_idx)),
        plays_since_big=float(len(plays) - 1 - big_idx[-1]) if big_idx else 8.0,
        events=_f(getattr(reading, "events", None) if reading is not None else row.get("events")),
    )
    if lg == "nfl":
        poss_home = s.possession == g.home.abbr if s.possession else None
        lead_of_poss = (diff if poss_home else -diff) if poss_home is not None and not math.isnan(diff) else float("nan")
        per = pro_period(g.league, g.period, g.clock_s)
        def_to = (s.timeouts_away if poss_home else s.timeouts_home) if poss_home is not None else None
        drive = 0
        for p in reversed(plays):     # plays since the last change of possession / score / kickoff
            t = (p.type_text or p.text or "").lower()
            if "timeout" in t or "end of" in t or "two-minute" in t:
                continue
            if p.scoring or "kickoff" in t or "punt" in t or (p.kind or "") in BIG_KINDS:
                break
            drive += 1
        out.update(
            possession_trailing=float(lead_of_poss <= 0 and lead_of_poss >= -8) if not math.isnan(lead_of_poss) else float("nan"),
            yardline_100=_f(s.yards_to_goal), down=_f(s.down), ydstogo=_f(s.distance),
            red_zone=float(s.yards_to_goal is not None and s.yards_to_goal <= 20),
            goal_to_go=float(bool(s.goal_to_go)),
            two_min=float(per in (2, 4) and g.clock_s is not None and g.clock_s <= 120),
            drive_plays=float(drive), def_timeouts=_f(def_to))
    elif lg == "nba":
        trail_home = (not math.isnan(diff)) and diff < 0
        out["trail_timeouts"] = _f(s.timeouts_home if trail_home else s.timeouts_away)
    elif lg == "nhl":
        a, b = _skaters(s.strength)
        if math.isnan(a):
            a, b = _f(row.get("home_skaters")), _f(row.get("away_skaters"))
        uneven = not math.isnan(a) and not math.isnan(b) and a != b
        out.update(pp=float(bool(s.power_play) or uneven),
                   pp_5v3=float(not math.isnan(a) and abs(a - b) >= 2), empty_net=float(bool(s.empty_net)))
    elif lg == "mlb":
        out.update(outs=_f(s.outs), runners=float(sum(bool(x) for x in s.runners)) if s.runners else float("nan"))
    return out


def vector(f: dict, features: list[str]) -> np.ndarray:
    return np.array([f.get(k, float("nan")) for k in features], dtype=np.float32)


# --- the brief text (the model's input) ---------------------------------------------------------

def _clock(g: GameState) -> str:
    if g.clock_s is None:
        return ""
    m, s = divmod(int(max(0, g.clock_s)), 60)
    return f"{m}:{s:02d}"


def _period(g: GameState) -> str:
    p = g.period or 0
    b = g.league.base
    reg = {League.NFL: 4, League.NBA: 4, League.NHL: 3, League.MLB: 9}[b]
    if g.league == League.CBB:
        reg = 2
    if b == League.MLB:
        return f"{'Bot' if g.half == 'bottom' else 'Top'} {p}"
    if p > reg:
        return "OT" if p == reg + 1 else f"{p - reg}OT"
    name = {League.NFL: "Q", League.NBA: "Q", League.NHL: "P"}.get(b, "P")
    return f"H{p}" if reg == 2 else f"{name}{p}"


def _pct(x: float) -> str:
    return "?" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{round(100 * x)}%"


def _short(t: str, n: int = 70) -> str:
    t = " ".join((t or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def text(g: GameState, f: dict, ctx: Context | None = None, head_p: float | None = None,
         n_plays: int = 8) -> str:
    """The compact brief the model reads (~150-250 tokens). One fact per line, no prose."""
    ctx = ctx or Context()
    lg = g.league.value
    s = g.situation
    hs = g.home.score if g.home.score is not None else "?"
    as_ = g.away.score if g.away.score is not None else "?"
    stop = g.stoppage.value if g.stoppage not in (Stoppage.NONE, Stoppage.UNKNOWN) else "live"
    lines = [f"{lg.upper()} {g.away.abbr} {as_} @ {g.home.abbr} {hs} | {_period(g)} {_clock(g)} | {stop}"]
    b = g.league.base
    if b == League.NFL and s.possession:
        dd = f"{s.down}&{'goal' if s.goal_to_go else s.distance}" if s.down else ""
        ytg = f"{s.yards_to_goal} yds to goal" if s.yards_to_goal is not None else ""
        lines.append(f"ball {s.possession} {dd} {ytg} | drive {int(f.get('drive_plays', 0))} plays".strip())
    elif b == League.NHL:
        bits = []
        if s.power_play:
            bits.append(f"power play {s.power_play} {s.strength or ''}".strip())
        if s.empty_net:
            bits.append(f"empty net {s.empty_net}")
        if bits:
            lines.append(" | ".join(bits))
    elif b == League.MLB and s.outs is not None:
        on = "".join(str(i + 1) for i, x in enumerate(s.runners or ()) if x) or "none"
        lines.append(f"{s.outs} out, on base: {on}")
    to = []
    if s.timeouts_home is not None and s.timeouts_away is not None:
        to.append(f"timeouts {g.away.abbr} {s.timeouts_away} {g.home.abbr} {s.timeouts_home}")
    wp = g.home_win_prob
    if wp is not None:
        sw = f.get("wp_delta")
        to.append(f"{g.home.abbr} win {_pct(wp)}" + (f" (swing {sw * 100:+.0f})" if sw == sw and sw else ""))
    if to:
        lines.append(" | ".join(to))
    flags = [k for k, v in (("crunch time", ctx.crunch), ("garbage time", ctx.garbage), ("final period", ctx.late))
             if v]
    lc = f.get("lead_changes")
    if lc == lc and lc:
        flags.append(f"{int(lc)} lead changes")
    if flags:
        lines.append(", ".join(flags))
    bk, br, hot = f.get("brain_key"), f.get("brain_break"), f.get("brain_hot")
    if bk == bk or hot == hot:
        lines.append(f"brain: key soon {_pct(bk)}, break soon {_pct(br)}, flow {'?' if hot != hot else round(hot)}/100")
    if head_p is not None:
        lines.append(f"lock-in odds {_pct(head_p)}")
    you = []
    if ctx.mine:
        you.append("your team")
    if ctx.bet:
        you.append(f"bet {ctx.bet}")
    you.append("ON SCREEN" if ctx.on_screen else (f"screen: {ctx.screen_game}" if ctx.screen_game else "not on screen"))
    lines.append("you: " + ", ".join(you))
    plays = [p for p in (g.recent_plays or []) if p is not None][-n_plays:]
    if plays:
        lines.append("last plays:")
        for p in plays:
            sc = f" [{p.score[1]}-{p.score[0]}]" if p.scoring and p.score else ""
            lines.append(f"- {_short(p.text)}{sc}")
    return "\n".join(lines)


def is_live(g: GameState | None) -> bool:
    return g is not None and g.phase == Phase.LIVE
