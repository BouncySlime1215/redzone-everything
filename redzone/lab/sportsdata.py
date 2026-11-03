"""Turn free public play-by-play releases into labeled game-state snapshots.

Each ``norm_*`` function reads one source into a common *events frame* (one row per
logged event, sorted by game then event order) with these columns:

    league season game_id seq etype period clock_s t_game wall_s score_home score_away
    wp_home stoppage big clutch [situational columns, NaN when not applicable]

``derive`` then adds the shared features and labels documented in
``redzone.lab.datasets``. Win probability comes from the source when it has one
(nflverse, cfbfastR, ESPN); otherwise from the small analytic models below.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from redzone.scoring.gameflow import SPEC, _RE24, game_elapsed, phi, wp_baseball, wp_basketball, wp_hockey  # noqa: F401
from redzone.lab.datasets import BREAK_HORIZON_S, BREAKS, FEATURES, KEY_WINDOW_S, LABELS, META, \
    STOPPAGE_CODE

SITUATIONAL = ["possession_home", "yardline_100", "down", "ydstogo", "to_home", "to_away",
               "fouls_home", "fouls_away", "bonus_home", "bonus_away", "home_skaters",
               "away_skaters", "outs", "on_1b", "on_2b", "on_3b", "half_bottom", "balls", "strikes"]


# ---------------------------------------------------------------------------
# small math helpers

def parse_clock(s: pd.Series) -> pd.Series:
    """'MM:SS' / 'SS.s' display clocks -> seconds."""
    s = s.astype(object).where(s.notna(), "").astype(str)
    mm = s.str.extract(r"^(\d+):(\d+(?:\.\d+)?)$")
    out = pd.to_numeric(mm[0], errors="coerce").astype("float64") * 60 \
        + pd.to_numeric(mm[1], errors="coerce").astype("float64")
    plain = pd.to_numeric(s.where(~s.str.contains(":"), ""), errors="coerce").astype("float64")
    return out.fillna(plain).astype("float64")


def iso_seconds(s: pd.Series) -> pd.Series:
    """ISO timestamps -> epoch seconds (NaN when missing)."""
    t = pd.to_datetime(s, utc=True, errors="coerce", format="ISO8601")
    out = (t - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds()
    return out.astype("float64")


def _wall_rel(wall_abs: pd.Series, game: pd.Series) -> pd.Series:
    """Epoch seconds -> seconds since the game's first stamped event, made monotone."""
    first = wall_abs.groupby(game, sort=False).transform("min")
    rel = wall_abs - first
    return rel.groupby(game, sort=False).cummax()


# ---------------------------------------------------------------------------
# source normalizers -> events frame

def _blank(df: pd.DataFrame) -> pd.DataFrame:
    for c in SITUATIONAL:
        if c not in df:
            df[c] = np.nan
    return df


def norm_nfl(paths: list[Path]) -> pd.DataFrame:
    cols = ["game_id", "play_id", "season", "home_team", "away_team", "posteam", "qtr",
            "quarter_seconds_remaining", "total_home_score", "total_away_score", "home_wp",
            "home_wp_post", "play_type_nfl", "desc", "time_of_day", "touchdown", "interception",
            "fumble_lost", "safety", "field_goal_result", "replay_or_challenge", "yards_gained",
            "yardline_100", "down", "ydstogo", "home_timeouts_remaining", "away_timeouts_remaining",
            "fourth_down_converted", "result", "season_type"]
    df = pd.concat([pd.read_parquet(p, columns=cols) for p in paths], ignore_index=True)
    df = df[df.qtr.notna()].sort_values(["game_id", "play_id"], kind="stable").reset_index(drop=True)
    e = pd.DataFrame({"league": "nfl", "season": df.season.astype(int), "game_id": df.game_id})
    e["seq"] = df.groupby("game_id").cumcount()
    e["etype"] = df.play_type_nfl.fillna("UNSPECIFIED").astype(str)
    e["period"] = df.qtr.astype(int)
    e["clock_s"] = df.quarter_seconds_remaining.astype(float)
    ot = np.where(df.season_type.eq("POST"), 900.0, 600.0)
    e["t_game"] = game_elapsed("nfl", e.period.values, e.clock_s.values, ot)
    e["wall_s"] = _wall_rel(iso_seconds(df.time_of_day), df.game_id)
    e["score_home"] = df.total_home_score.astype(float)
    e["score_away"] = df.total_away_score.astype(float)
    wp = df.home_wp_post.fillna(df.home_wp)
    wp = wp.groupby(df.game_id).ffill()
    last = ~df.game_id.duplicated(keep="last")
    wp[last] = np.sign(df.result[last]).map({1.0: 1.0, -1.0: 0.0, 0.0: 0.5})
    e["wp_home"] = wp.astype(float)
    desc = df.desc.fillna("").str.lower()
    t = df.play_type_nfl.fillna("")
    score = (df.touchdown == 1) | (df.field_goal_result == "made") | (df.safety == 1)
    st = np.select(
        [t.eq("END_GAME"), t.eq("END_QUARTER") & e.period.eq(2), t.eq("END_QUARTER"),
         t.eq("TIMEOUT"), score, df.replay_or_challenge.eq(1), desc.str.contains("injur")],
        ["game_end", "halftime", "end_period", "timeout", "score_break", "review", "injury"], "none")
    # nflverse drops the two-minute-warning rows: mark the play after which the clock
    # first shows <= 2:00 in Q2/Q4.
    prev_clock = e.groupby("game_id").clock_s.shift(1)
    prev_per = e.groupby("game_id").period.shift(1)
    tmw = e.period.isin([2, 4]) & (e.clock_s <= 120) & (prev_clock > 120) & (prev_per == e.period)
    nxt = tmw.groupby(e.game_id).shift(-1, fill_value=False).astype(bool)
    st = np.where(nxt & (st == "none"), "two_minute_warning", st)
    e["stoppage"] = st
    e["big"] = (df.touchdown == 1) | (df.interception == 1) | (df.fumble_lost == 1) | (df.safety == 1) \
        | (df.yards_gained.fillna(0) >= 20)
    e["clutch"] = (df.field_goal_result == "made") | (df.fourth_down_converted == 1)
    e["possession_home"] = np.where(df.posteam.isna(), np.nan, (df.posteam == df.home_team).astype(float))
    e["yardline_100"] = df.yardline_100
    e["down"] = df.down
    e["ydstogo"] = df.ydstogo
    e["to_home"] = df.home_timeouts_remaining
    e["to_away"] = df.away_timeouts_remaining
    return _blank(e)


def norm_cfb(paths: list[Path]) -> pd.DataFrame:
    cols = ["game_id", "id_play", "season", "period", "clock_minutes", "clock_seconds", "pos_team",
            "home", "away", "pos_team_score", "def_pos_team_score", "play_type", "play_text",
            "home_wp_after", "home_wp_before", "wallclock", "pos_team_timeouts_rem_before",
            "def_pos_team_timeouts_rem_before", "yards_to_goal", "down", "distance", "yards_gained"]
    df = pd.concat([pd.read_parquet(p, columns=cols) for p in paths], ignore_index=True)
    df = df[df.period.notna() & df.play_type.ne("placeholder")]
    df = df.sort_values(["game_id", "id_play"], kind="stable").reset_index(drop=True)
    gid = df.game_id.astype(str)
    e = pd.DataFrame({"league": "cfb", "season": df.season.astype(int), "game_id": gid})
    e["seq"] = df.groupby(gid).cumcount()
    e["etype"] = df.play_type.fillna("").astype(str)
    e["period"] = df.period.astype(int)
    clock = df.clock_minutes.astype(float) * 60 + df.clock_seconds.astype(float)
    e["clock_s"] = np.where(e.period > 4, np.nan, clock)
    t = game_elapsed("cfb", np.minimum(e.period.values, 4), clock.values)
    # College OT is untimed: give each OT play 20 synthetic game seconds after regulation.
    ot_k = (e.period > 4).groupby(gid).cumsum()
    e["t_game"] = np.where(e.period > 4, 3600 + 20.0 * ot_k, t)
    e["t_game"] = e.groupby("game_id").t_game.cummax()
    e["wall_s"] = _wall_rel(iso_seconds(df.wallclock), gid)
    home_pos = df.pos_team == df.home
    e["score_home"] = np.where(home_pos, df.pos_team_score, df.def_pos_team_score).astype(float)
    e["score_away"] = np.where(home_pos, df.def_pos_team_score, df.pos_team_score).astype(float)
    e["score_home"] = e.groupby("game_id").score_home.cummax()
    e["score_away"] = e.groupby("game_id").score_away.cummax()
    wp = df.home_wp_after.fillna(df.home_wp_before).groupby(gid).ffill()
    last = ~gid.duplicated(keep="last")
    fd = (e.score_home - e.score_away)[last]
    wp[last] = np.where(fd > 0, 1.0, np.where(fd < 0, 0.0, 0.5))
    e["wp_home"] = wp.astype(float)
    pt = df.play_type.fillna("")
    text = df.play_text.fillna("").str.lower()
    score = pt.str.contains("Touchdown") | pt.eq("Field Goal Good") | pt.str.contains("Safety")
    st = np.select(
        [pt.eq("End of Game"), pt.eq("End of Half"), pt.isin(["End Period", "End of Regulation"]),
         pt.eq("Timeout"), score, text.str.contains(r"\breview|overturned|upheld|challenge"),
         text.str.contains("injur")],
        ["game_end", "halftime", "end_period", "timeout", "score_break", "review", "injury"], "none")
    e["stoppage"] = st
    scrimmage = pt.isin(["Rush", "Pass Reception", "Pass Completion", "Sack"])
    e["big"] = pt.str.contains("Touchdown|Interception|Fumble Recovery \\(Opponent\\)|Blocked|Safety") \
        | (scrimmage & (df.yards_gained.fillna(0) >= 20))
    e["clutch"] = pt.eq("Field Goal Good")
    e["possession_home"] = np.where(df.pos_team.isna(), np.nan, home_pos.astype(float))
    e["yardline_100"] = df.yards_to_goal
    e["down"] = df.down
    e["ydstogo"] = df.distance
    e["to_home"] = np.where(home_pos, df.pos_team_timeouts_rem_before, df.def_pos_team_timeouts_rem_before)
    e["to_away"] = np.where(home_pos, df.def_pos_team_timeouts_rem_before, df.pos_team_timeouts_rem_before)
    return _blank(e)


_BB_RULES = {  # timeouts per game, team-foul bonus threshold, foul reset unit
    "nba": dict(timeouts=7, bonus=5, half_period=2),
    "cbb": dict(timeouts=4, bonus=7, half_period=1),
    "wcbb": dict(timeouts=4, bonus=5, half_period=2),
}


def norm_basketball(league: str, paths: list[Path], max_games: int | None = None) -> pd.DataFrame:
    cols = ["game_id", "season", "sequence_number", "type_text", "text", "period_number",
            "clock_display_value", "home_score", "away_score", "scoring_play", "score_value",
            "team_id", "home_team_id", "away_team_id", "wallclock", "game_spread", "home_favorite"]
    df = pd.concat([pd.read_parquet(p, columns=cols) for p in paths], ignore_index=True)
    df = df[df.type_text.ne("Substitution") & df.period_number.notna()]
    if max_games:
        keep = _sample_games(df.game_id, max_games)
        df = df[df.game_id.isin(keep)]
    df["sq"] = pd.to_numeric(df.sequence_number, errors="coerce")
    df = df.sort_values(["game_id", "sq"], kind="stable").reset_index(drop=True)
    gid = df.game_id.astype(str)
    rules = _BB_RULES[league]
    e = pd.DataFrame({"league": league, "season": df.season.astype(int), "game_id": gid})
    e["seq"] = df.groupby(gid).cumcount()
    tt = df.type_text.fillna("").astype(str)
    e["etype"] = tt
    e["period"] = df.period_number.astype(int)
    e["clock_s"] = parse_clock(df.clock_display_value).astype(float)
    e["t_game"] = game_elapsed(league, e.period.values, e.clock_s.values)
    e["t_game"] = e.groupby("game_id").t_game.cummax()
    e["wall_s"] = _wall_rel(iso_seconds(df.wallclock), gid)
    e["score_home"] = df.home_score.astype(float).groupby(gid).cummax()
    e["score_away"] = df.away_score.astype(float).groupby(gid).cummax()
    low = tt.str.lower()
    text = df.text.fillna("").str.lower()
    is_home = df.team_id.astype("float64") == df.home_team_id.astype("float64")
    is_away = df.team_id.astype("float64") == df.away_team_id.astype("float64")
    ft = low.str.contains("free throw") | low.str.contains("freethrow")
    ft_last = low.str.contains(r"1 of 1|2 of 2|3 of 3") | (ft & ~low.str.contains(r"\d of \d"))
    timeout = low.str.contains("timeout") & ~low.str.contains("excess timeout")
    st = np.select(
        [low.isin(["end game", "end of game"]),
         low.eq("end period") & e.period.eq(rules["half_period"]),
         low.eq("end period"),
         timeout,
         low.str.contains("challenge|review"),
         ft & ~ft_last,
         text.str.contains("team rebound") | low.str.contains("dead ball rebound"),
         low.str.contains("foul|violation|out of bounds|kicked ball|delay|traveling|jumpball|ejection")],
        ["game_end", "halftime", "end_period", "timeout", "review", "whistle", "whistle", "whistle"], "none")
    e["stoppage"] = st
    made = df.scoring_play.fillna(False).astype(bool) & ~ft
    three = made & (df.score_value.fillna(0) == 3)
    dunk = made & low.str.contains("dunk")
    e["big"] = False
    e["clutch"] = three | dunk
    # Possession after the event (home = 1): a made shot or turnover gives it to the other side.
    turnover = low.str.contains("turnover") | low.str.contains("steal")
    reb = low.str.contains("rebound") & ~text.str.contains("team rebound")
    steal = low.str.contains("steal")
    pos = np.where(made | (ft & ft_last & df.scoring_play.fillna(False).astype(bool)) | (turnover & ~steal),
                   np.where(is_home, 0.0, np.where(is_away, 1.0, np.nan)),
                   np.where(reb | steal, np.where(is_home, 1.0, np.where(is_away, 0.0, np.nan)), np.nan))
    e["possession_home"] = pd.Series(pos).groupby(gid).ffill().values
    # Team fouls per foul-reset unit (quarter, or half for men's college) and timeouts used.
    foul = low.str.contains("foul") & ~low.str.contains("offensive|technical|flagrant")
    unit = e.period if rules["half_period"] == 2 else np.where(e.period <= 1, 1, np.where(e.period == 2, 2, e.period))
    key = gid + "_" + pd.Series(unit).astype(str)
    fh = (foul & is_home).astype(int).groupby(key).cumsum()
    fa = (foul & is_away).astype(int).groupby(key).cumsum()
    e["fouls_home"], e["fouls_away"] = fh.values, fa.values
    e["bonus_home"] = (fa >= rules["bonus"]).astype(float).values  # home shoots bonus FTs
    e["bonus_away"] = (fh >= rules["bonus"]).astype(float).values
    team_to = timeout & ~low.str.contains("official|tv")
    e["to_home"] = rules["timeouts"] - (team_to & is_home).astype(int).groupby(gid).cumsum()
    e["to_away"] = rules["timeouts"] - (team_to & is_away).astype(int).groupby(gid).cumsum()
    e["to_home"] = e.to_home.clip(lower=0)
    e["to_away"] = e.to_away.clip(lower=0)
    spread = df.game_spread.astype(float).abs()
    exp_margin = np.where(df.home_favorite.astype("boolean").fillna(False), spread, -spread)
    e["_exp_margin"] = exp_margin
    e["wp_home"] = np.nan
    return _blank(e)


_NHL_STOP = [
    ("tv-timeout", "timeout"), ("home-timeout", "timeout"), ("visitor-timeout", "timeout"),
    ("video-review", "review"), ("chlg", "review"), ("player-injury", "injury"),
]


def norm_nhl(paths: list[Path]) -> pd.DataFrame:
    cols = ["game_id", "season", "season_type", "event_idx", "event_type", "description", "period",
            "period_type", "period_seconds_remaining", "game_seconds", "home_score", "away_score",
            "home_skaters", "away_skaters", "event_team_type", "xg"]
    df = pd.concat([pd.read_parquet(p, columns=cols) for p in paths], ignore_index=True)
    df = df[df.game_id.notna() & df.event_type.ne("CHANGE") & df.period_type.ne("SHOOTOUT")
            & df.event_type.ne("SHOOTOUT_COMPLETE")]
    df = df.sort_values(["game_id", "event_idx"], kind="stable").reset_index(drop=True)
    gid = df.game_id.astype("int64").astype(str)
    season = df.season.astype("int64")
    season = np.where(season > 10000, season % 10000, season)  # 20242025 -> 2025
    e = pd.DataFrame({"league": "nhl", "season": season, "game_id": gid})
    e["seq"] = df.groupby(gid).cumcount()
    et = df.event_type.fillna("").astype(str)
    e["etype"] = et
    e["period"] = df.period.astype(int)
    e["clock_s"] = df.period_seconds_remaining.astype(float)
    e["t_game"] = df.game_seconds.astype(float).groupby(gid).cummax()
    e["wall_s"] = np.nan
    e["score_home"] = df.home_score.astype(float).groupby(gid).cummax()
    e["score_away"] = df.away_score.astype(float).groupby(gid).cummax()
    desc = df.description.fillna("").str.lower()
    reason = desc.str.extract(r"\(([^)]*)\)")[0].fillna("")
    stop_kind = pd.Series("whistle", index=df.index)
    for needle, kind in reversed(_NHL_STOP):
        stop_kind = stop_kind.mask(reason.str.contains(needle, regex=False), kind)
    st = np.select(
        [et.eq("GAME_END"), et.eq("PERIOD_END") & e.period.isin([1, 2]), et.eq("PERIOD_END"),
         et.eq("STOP"), et.eq("GOAL"), et.eq("PENALTY")],
        ["game_end", "intermission", "end_period", stop_kind, "score_break", "whistle"], "none")
    e["stoppage"] = st
    e["big"] = et.eq("GOAL")
    e["clutch"] = et.isin(["SHOT", "MISSED_SHOT"]) & (df.xg.fillna(0) >= 0.2)
    e["home_skaters"] = df.home_skaters.astype(float)
    e["away_skaters"] = df.away_skaters.astype(float)
    to = st == "timeout"
    e["to_home"] = 1 - (to & reason.str.contains("home-timeout")).groupby(gid).cumsum().clip(upper=1)
    e["to_away"] = 1 - (to & reason.str.contains("visitor-timeout")).groupby(gid).cumsum().clip(upper=1)
    e["_playoff"] = df.season_type.eq("P").values
    e["wp_home"] = np.nan
    return _blank(e)


def norm_mlb(paths: list[Path], pitch_paths: list[Path] | None = None) -> pd.DataFrame:
    """One row per pitch (state after it) plus one per plate-appearance result.

    Plate appearances carry real start/end timestamps; pitches inside one are spaced
    evenly between them (the pitch feed has no clock of its own).
    """
    df = pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)
    df = df[df.event_type.ne("game_advisory")]
    df = df.sort_values(["game_pk", "at_bat_index"], kind="stable").reset_index(drop=True)
    gid = df.game_pk.astype(str)
    start, end = iso_seconds(df.start_time), iso_seconds(df.end_time)
    end = end.fillna(start)
    start = start.fillna(end)
    half = df.inning.astype(str) + df.half_inning
    same_prev = (half == half.groupby(gid).shift(1))
    prev = lambda s: s.groupby(gid).shift(1)  # noqa: E731
    pre = pd.DataFrame({
        "score_home": prev(df.home_score).fillna(0), "score_away": prev(df.away_score).fillna(0),
        "outs": np.where(same_prev, prev(df.outs), 0),
        "on_1b": np.where(same_prev, prev(df.post_on_first_id).notna(), False),
        "on_2b": np.where(same_prev, prev(df.post_on_second_id).notna(), False),
        "on_3b": np.where(same_prev, prev(df.post_on_third_id).notna(), False)})
    nxt_pitcher = df.groupby(gid).pitcher_id.shift(-1)
    same_next = half.groupby(gid).shift(-1) == half
    last = ~gid.duplicated(keep="last")
    st = np.select([last, df.outs >= 3, same_next & nxt_pitcher.notna() & (nxt_pitcher != df.pitcher_id)],
                   ["game_end", "end_period", "pitching_change"], "none")
    pa = pd.DataFrame({
        "game_id": gid, "ab": df.at_bat_index, "sub": 999, "abs_t": end, "etype": df.event_type.fillna(""),
        "period": df.inning.astype(int), "half_bottom": df.half_inning.eq("bottom").astype(float),
        "score_home": df.home_score.astype(float), "score_away": df.away_score.astype(float),
        "outs": df.outs.astype(float), "on_1b": df.post_on_first_id.notna().astype(float),
        "on_2b": df.post_on_second_id.notna().astype(float), "on_3b": df.post_on_third_id.notna().astype(float),
        "balls": np.nan, "strikes": np.nan, "stoppage": st, "clutch": df.event_type.eq("home_run")})
    parts = [pa]
    if pitch_paths:
        p = pd.concat([pd.read_parquet(x, columns=["game_pk", "at_bat_index", "pitch_number", "balls",
                                                   "strikes"]) for x in pitch_paths], ignore_index=True)
        p["game_id"] = p.game_pk.astype(str)
        n = p.groupby(["game_id", "at_bat_index"]).pitch_number.transform("max")
        p = p[p.pitch_number < n].assign(n=n[p.pitch_number < n])  # the last pitch is the PA row
        info = pd.DataFrame({"game_id": gid, "at_bat_index": df.at_bat_index, "start": start, "end": end,
                             "period": df.inning.astype(int),
                             "half_bottom": df.half_inning.eq("bottom").astype(float)}).join(pre.astype(float))
        p = p.merge(info, on=["game_id", "at_bat_index"], how="inner")
        parts.append(pd.DataFrame({
            "game_id": p.game_id, "ab": p.at_bat_index, "sub": p.pitch_number,
            "abs_t": p.start + (p.end - p.start) * p.pitch_number / p.n, "etype": "pitch",
            "period": p.period, "half_bottom": p.half_bottom, "score_home": p.score_home,
            "score_away": p.score_away, "outs": p.outs, "on_1b": p.on_1b, "on_2b": p.on_2b, "on_3b": p.on_3b,
            "balls": p.balls.astype(float), "strikes": p.strikes.astype(float).clip(upper=2),
            "stoppage": "none", "clutch": False}))
    e = pd.concat(parts, ignore_index=True).sort_values(["game_id", "ab", "sub"], kind="stable")
    e = e.reset_index(drop=True)
    e.insert(0, "league", "mlb")
    yr = pd.to_datetime(df.start_time, utc=True, errors="coerce", format="ISO8601").dt.year
    season = yr.groupby(gid).min()
    e["season"] = e.game_id.map(season).fillna(0).astype(int)
    e["seq"] = e.groupby("game_id").cumcount()
    e["clock_s"] = np.nan
    e["wall_s"] = _wall_rel(e.pop("abs_t"), e.game_id)
    e["wall_s"] = e.groupby("game_id").wall_s.transform(lambda s: s.interpolate(limit_direction="both"))
    e["t_game"] = e.wall_s  # baseball has no game clock: game time is wall time
    e["big"] = False
    e["wp_home"] = np.nan
    return _blank(e.drop(columns=["ab", "sub"]))


def norm_espn_archive(league: str, archive: Path) -> pd.DataFrame:
    """Our own recorded ESPN summaries, classified exactly as the live service does."""
    from redzone.models import League
    from redzone.sources import espn

    lg = League(league)
    rows = []
    for f in sorted(archive.glob("summary_*.json")):
        try:
            data = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        plays = espn.flatten_plays(lg, data)
        if len(plays) < 20:
            continue
        wpm = {str(w.get("playId")): w.get("homeWinPercentage") for w in data.get("winprobability") or []}
        gid = f.stem.split("_")[-1]
        season = espn._int(espn._get(data, "header", "season", "year")) or 0
        for i, p in enumerate(plays):
            per = espn._int(espn._get(p, "period", "number")) or 0
            clock = espn._clock_s(espn._get(p, "clock", "displayValue"))
            if lg == League.NHL and clock is not None:
                clock = max(0.0, (300 if per >= 4 else 1200) - clock)  # ESPN NHL clock counts up
            ttxt = (espn._get(p, "type", "text") or "")
            st = espn.stoppage_for(lg, p).value
            rows.append(dict(
                game_id=gid, season=season, seq=i, etype=ttxt, period=per,
                clock_s=np.nan if lg == League.MLB else clock,
                wall_abs=espn._ts(p.get("wallclock")),
                score_home=espn._int(p.get("homeScore")), score_away=espn._int(p.get("awayScore")),
                wp_home=wpm.get(str(p.get("id"))),
                stoppage=st, big=_espn_big(lg, ttxt, p), clutch=_espn_clutch(lg, ttxt, p),
                half_bottom=(1.0 if "bottom" in (espn._get(p, "period", "type") or "").lower() else 0.0)
                if lg == League.MLB else np.nan,
                outs=espn._int(p.get("outs")) if lg == League.MLB else np.nan,
            ))
        if rows and rows[-1]["game_id"] == gid:
            rows[-1]["stoppage"] = "game_end"
    e = pd.DataFrame(rows)
    if e.empty:
        return e
    e.insert(0, "league", f"espn_{league}")
    for c in ("score_home", "score_away"):
        e[c] = e[c].astype(float).groupby(e.game_id).ffill().fillna(0).groupby(e.game_id).cummax()
    e["wall_s"] = _wall_rel(e.pop("wall_abs").astype(float), e.game_id)
    e["wall_s"] = e.groupby("game_id").wall_s.transform(lambda s: s.interpolate(limit_direction="both"))
    if lg == League.MLB:
        e["t_game"] = e.wall_s
    else:
        e["clock_s"] = e.clock_s.astype(float).groupby(e.game_id).ffill()
        e["t_game"] = game_elapsed(league, e.period.values.astype(float), e.clock_s.values)
        e["t_game"] = pd.Series(e.t_game).groupby(e.game_id).cummax().values
    e["wp_home"] = e.wp_home.astype(float)
    if lg == League.MLB:
        e["outs"] = e.outs.astype(float)
    return _blank(e)


def _espn_big(lg, t: str, p: dict) -> bool:
    t = t.lower()
    if lg.value == "nfl":
        scrimmage = t in ("rush", "pass reception", "pass", "sack") or t.startswith("pass")
        return "touchdown" in t or "interception" in t or "fumble recovery (opponent)" in t \
            or "safety" in t or bool(p.get("isTurnover")) or (scrimmage and espn_yards(p) >= 20)
    if lg.value == "nhl":
        return t == "goal"
    return False


def _espn_clutch(lg, t: str, p: dict) -> bool:
    t = t.lower()
    if lg.value == "nba":
        return bool(p.get("scoringPlay")) and ("dunk" in t or (p.get("scoreValue") == 3))
    if lg.value == "mlb":
        return "home run" in t
    if lg.value == "nfl":
        return "field goal good" in t
    return False


def espn_yards(p: dict) -> float:
    try:
        return float(p.get("statYardage") or 0)
    except (TypeError, ValueError):
        return 0.0


def _sample_games(game_ids: pd.Series, n: int) -> set:
    """Deterministic subset of n games (stable across rebuilds)."""
    import zlib
    u = pd.Series(game_ids.unique())
    h = u.astype(str).map(lambda g: zlib.crc32(g.encode()))
    return set(u[h.argsort()[:n]])


# ---------------------------------------------------------------------------
# shared features + labels

def fill_wp(league: str, e: pd.DataFrame) -> pd.DataFrame:
    base = league.removeprefix("espn_")
    sp = SPEC[base]
    diff = (e.score_home - e.score_away).values
    if base in ("nba", "cbb", "wcbb"):
        total = sp["period_s"] * sp["n_reg"]
        reg_left = np.clip(total - e.t_game.values, 0, None)
        ot_left = np.where(e.period > sp["n_reg"], np.nan_to_num(e.clock_s.values), 0)
        model = wp_basketball(base, diff, reg_left + ot_left, e.get("_exp_margin", pd.Series(0, index=e.index)).values)
    elif base == "nhl":
        reg_left = np.clip(3600 - e.t_game.values, 0, None)
        model = wp_hockey(diff, reg_left, e.home_skaters.values, e.away_skaters.values)
        ot = e.period.values > 3
        model = np.where(ot, np.where(diff > 0, 1.0, np.where(diff < 0, 0.0, 0.52)), model)
    elif base == "mlb":
        bases = (e.on_1b.fillna(0) + 2 * e.on_2b.fillna(0) + 4 * e.on_3b.fillna(0)).values
        model = wp_baseball(diff, e.period.values, e.half_bottom.fillna(0).values,
                            e.outs.fillna(0).values, bases)
    else:  # football without a source WP (ESPN archive gaps): normal model, sigma 13.5
        left = np.clip(3600 - e.t_game.values, 0, None)
        model = wp_basketball("nba", diff, left * 2880 / 3600, np.zeros(len(e)))
    src = e.wp_home.astype(float)
    e["wp_home"] = src.where(src.notna(), model)
    e["wp_home"] = e.wp_home.clip(0, 1)
    last = ~e.game_id.duplicated(keep="last")
    fin = np.where(diff > 0, 1.0, np.where(diff < 0, 0.0, 0.5))
    e.loc[last, "wp_home"] = fin[last.values]
    return e


def _next_after(mask: np.ndarray) -> np.ndarray:
    """For each i, the smallest j > i with mask[j] (len(mask) if none)."""
    n = len(mask)
    idx = np.where(mask, np.arange(n), n)
    nxt_from = np.minimum.accumulate(idx[::-1])[::-1]
    return np.append(nxt_from[1:], n)


def _last_upto(mask: np.ndarray) -> np.ndarray:
    """For each i, the largest j <= i with mask[j] (-1 if none)."""
    idx = np.where(mask, np.arange(len(mask)), -1)
    return np.maximum.accumulate(idx)


def _window_sum(t: np.ndarray, x: np.ndarray, w: float) -> tuple[np.ndarray, np.ndarray]:
    """Trailing sum over (t-w, t] and upcoming sum over (i, t+w] of x."""
    c = np.concatenate([[0.0], np.cumsum(x)])
    i = np.arange(len(t))
    j = np.searchsorted(t, t - w, side="right")
    k = np.searchsorted(t, t + w, side="right")
    back = c[i + 1] - c[j]
    fwd = c[np.maximum(k, i + 1)] - c[i + 1]
    return back, fwd


# Rows where a WP jump is a model/bookkeeping artifact, not something that happened on the field.
_ADMIN = {"timeout", "two_minute_warning", "end_period", "halftime", "intermission", "game_end"}


def derive(league: str, e: pd.DataFrame) -> pd.DataFrame:
    """Add shared features and labels to a sorted events frame."""
    base = league.removeprefix("espn_")
    sp = SPEC[base]
    e = e.reset_index(drop=True)
    e["t_game"] = e.t_game.astype(float).groupby(e.game_id).ffill().fillna(0)
    e["t_game"] = e.groupby("game_id").t_game.cummax()
    # The end of the final period is the end of the game, not a break.
    nxt_st = e.groupby("game_id").stoppage.shift(-1)
    final = e.stoppage.isin(["end_period", "halftime"]) & (nxt_st.isna() | nxt_st.eq("game_end"))
    e.loc[final, "stoppage"] = "game_end"
    n_tot = len(e)
    out = {k: np.full(n_tot, np.nan) for k in (
        "wp_delta", "wp_vol_8", "wp_vol_w", "lead_changes", "ties", "run_home", "run_away", "pts_w",
        "s_since_break", "ev_since_break", "wall_since_break", "next_break_s", "next_break_wall_s",
        "next_key_s", "back", "fwd", "lead_change")}
    sd_all = (e.score_home - e.score_away).values.astype(float)
    if base == "mlb":
        frac_left = np.clip(1 - (e.period.values - 1 + 0.5 * e.half_bottom.fillna(0).values) / 9, 0, 1)
        is_ot = e.period.values > 9
        crunch = (e.period.values >= 8) & (np.abs(sd_all) <= 2)
    else:
        total = sp["period_s"] * sp["n_reg"]
        frac_left = np.clip(1 - e.t_game.values / total, 0, 1)
        is_ot = e.period.values > sp["n_reg"]
        cs, cm = sp["crunch"]
        crunch = is_ot | ((e.period.values == sp["n_reg"]) & (e.clock_s.values <= cs)
                          & (np.abs(sd_all) <= cm))
    is_break = e.stoppage.isin(BREAKS).values
    wp = e.wp_home.values.astype(float)
    t_all = e.t_game.values.astype(float)
    w_all = e.wall_s.values.astype(float)
    starts = np.flatnonzero(np.r_[True, e.game_id.values[1:] != e.game_id.values[:-1]])
    ends = np.r_[starts[1:], n_tot]
    for a, b in zip(starts, ends):
        sl = slice(a, b)
        t, w, p = t_all[sl], w_all[sl], wp[sl]
        n = b - a
        d = np.abs(np.diff(p, prepend=p[0]))
        out["wp_delta"][sl] = np.diff(p, prepend=p[0])
        c = np.cumsum(d)
        out["wp_vol_8"][sl] = c - np.r_[np.zeros(min(8, n)), c[:-8]] if n > 8 else c
        back, fwd = _window_sum(t, d, sp["vol_w"])
        out["wp_vol_w"][sl], out["back"][sl], out["fwd"][sl] = back, back, fwd
        sd = sd_all[sl]
        sgn = np.sign(sd)
        nz = _last_upto(sgn != 0)  # last event (<= i) with someone leading
        prev_sign = np.r_[0, np.where(nz[:-1] >= 0, sgn[np.maximum(nz[:-1], 0)], 0)]
        lc = (sgn != 0) & (prev_sign != 0) & (sgn != prev_sign)
        out["lead_change"][sl] = lc
        out["lead_changes"][sl] = np.cumsum(lc)
        tie = (sd == 0) & (np.r_[0, sd[:-1]] != 0)
        out["ties"][sl] = np.cumsum(tie)
        dh = np.clip(np.diff(e.score_home.values[sl], prepend=e.score_home.values[a]), 0, None)
        da = np.clip(np.diff(e.score_away.values[sl], prepend=e.score_away.values[a]), 0, None)
        ch, ca = np.cumsum(dh), np.cumsum(da)
        la, lh = _last_upto(da > 0), _last_upto(dh > 0)
        out["run_home"][sl] = ch - np.where(la >= 0, ch[np.maximum(la, 0)], 0)
        out["run_away"][sl] = ca - np.where(lh >= 0, ca[np.maximum(lh, 0)], 0)
        pb, _ = _window_sum(t, dh + da, sp["vol_w"])
        out["pts_w"][sl] = pb
        brk = is_break[sl]
        lb = _last_upto(brk)
        i = np.arange(n)
        out["s_since_break"][sl] = t - t[np.maximum(lb, 0)]
        out["ev_since_break"][sl] = np.where(lb >= 0, i - lb, i)
        out["wall_since_break"][sl] = w - w[np.maximum(lb, 0)]
        nb = _next_after(brk)
        ok = nb < n
        out["next_break_s"][sl] = np.where(ok, t[np.minimum(nb, n - 1)] - t, np.nan)
        out["next_break_wall_s"][sl] = np.where(ok, w[np.minimum(nb, n - 1)] - w, np.nan)
    e["frac_left"] = frac_left
    e["is_ot"] = is_ot.astype(float)
    e["crunch"] = crunch.astype(float)
    for k, v in out.items():
        e[k] = v
    e["score_diff"] = sd_all
    e["abs_diff"] = np.abs(sd_all)
    e["wp_lev"] = 4 * wp * (1 - wp)
    scored = (e.groupby("game_id").score_home.diff().fillna(0) + e.groupby("game_id").score_away.diff().fillna(0)) > 0
    key = (e.big.astype(bool).values
           | (e.clutch.astype(bool).values & crunch)
           | ((np.abs(e.wp_delta.values) >= sp["wp_key"]) & ~e.stoppage.isin(_ADMIN).values)
           | (e.lead_change.values.astype(bool) & (frac_left <= sp["late"]))
           | (scored.values & crunch))
    e["key_now"] = key
    nk = np.full(n_tot, np.nan)
    for a, b in zip(starts, ends):
        k = _next_after(key[a:b])
        n = b - a
        t = t_all[a:b]
        nk[a:b] = np.where(k < n, t[np.minimum(k, n - 1)] - t, np.nan)
    e["next_key_s"] = nk
    e["break_soon"] = (e.next_break_s <= BREAK_HORIZON_S).astype("int8")
    bw = e.next_break_wall_s <= BREAK_HORIZON_S
    e["break_soon_wall"] = bw.astype(float).where(e.wall_s.notna(), np.nan)
    e["key_soon"] = (e.next_key_s <= KEY_WINDOW_S[base]).astype("int8")
    e["in_break"] = is_break.astype(float)
    e["stoppage"] = e.stoppage.map(STOPPAGE_CODE).fillna(STOPPAGE_CODE["unknown"]).astype("int8")
    e["hot_raw"] = hot_raw(e, sp, frac_left, is_ot)
    return e


def hot_raw(e: pd.DataFrame, sp: dict, frac_left: np.ndarray, is_ot: np.ndarray) -> np.ndarray:
    """Pre-percentile game flow: trailing + upcoming WP movement and late-game leverage."""
    back, fwd = e.back.values, e.fwd.values
    q = max(np.nanquantile(back, 0.95), 1e-6)
    late = np.where(is_ot, 1.0, 0.35 + 0.65 * (1 - frac_left))
    tension = e.wp_lev.values * late
    raw = 0.35 * np.clip(back / q, 0, 1) + 0.35 * np.clip(fwd / q, 0, 1) + 0.30 * tension
    sm = pd.Series(raw).groupby(e.game_id.values).transform(lambda s: s.ewm(alpha=0.25).mean())
    return sm.values


def finalize(league: str, e: pd.DataFrame) -> tuple[pd.DataFrame, list[float]]:
    """Percentile-scale hotness, keep the published columns, shrink dtypes."""
    knots = np.nanquantile(e.hot_raw.values, np.linspace(0, 1, 101))
    e["hotness"] = (np.searchsorted(knots, e.hot_raw.values, side="right") - 1).clip(0, 100).astype("int8")
    cols = META + FEATURES + LABELS
    for c in cols:
        if c not in e:
            e[c] = np.nan
    e = e[cols].copy()
    for c in FEATURES + ["hot_raw", "next_break_s", "next_break_wall_s", "next_key_s", "break_soon_wall"]:
        if c != "stoppage":
            e[c] = e[c].astype("float32")
    e["key_now"] = e.key_now.astype("int8")
    e["seq"] = e.seq.astype("int32")
    e["season"] = e.season.astype("int16")
    for c in ("league", "etype", "game_id"):
        e[c] = e[c].astype(str).astype("category") if c != "game_id" else e[c].astype(str)
    return e, [round(float(k), 5) for k in knots]


def build(league: str, raw_dir: Path, archive_dir: Path | None = None,
          max_games: int | None = None) -> tuple[pd.DataFrame, list[float]]:
    raw = lambda pat: sorted(raw_dir.glob(pat))  # noqa: E731
    if league == "nfl":
        e = norm_nfl(raw("nfl_*.parquet"))
    elif league == "cfb":
        e = norm_cfb(raw("cfb_*.parquet"))
    elif league in ("nba", "cbb", "wcbb"):
        e = norm_basketball(league, raw(f"{league}_*.parquet"), max_games=max_games)
    elif league == "nhl":
        e = norm_nhl(raw("nhl_*.parquet"))
    elif league == "mlb":
        e = norm_mlb(raw("mlb_pbp_*.parquet"), raw("mlb_pitches_*.parquet"))
    elif league.startswith("espn_"):
        e = norm_espn_archive(league.removeprefix("espn_"), archive_dir / league.removeprefix("espn_"))
    else:
        raise ValueError(league)
    if e.empty:
        raise ValueError(f"no events for {league}")
    e = fill_wp(league, e)
    e = derive(league, e)
    return finalize(league, e)
