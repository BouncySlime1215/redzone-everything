"""Point-in-time standings, online Elo and a playoff-odds Monte Carlo, league-generic (Crew B, worker B3).

Input: one schedule + results table per league (all seasons), columns

    game_id, season, game_type ("REG" | "POST"), kickoff_utc (tz-aware Timestamp), final_utc (when the result is
    known; kickoff + a safe game length), home, away, home_score, away_score (NaN while unplayed), neutral (bool)

Output: `Fact` rows (redzone/context/tokens.py), kind "derived". For every regular-season game g the facts are
computed at a snapshot time `asof(g) < kickoff(g)` from games h with `final_utc(h) <= asof(g)` and a score, so no
result from g or any later game leaks in (tests/test_context_stakes.py checks it). Fields per team:

    wins, losses, win_pct, div_rank, games_back, elo, elo_diff, elo_win_prob, playoff_odds,
    playoff_odds_if_win, playoff_odds_if_loss, playoff_odds_swing, clinch_status (categorical)

and per game (team null): stakes_swing_max, stakes_swing_sum, elo_diff_abs; playoff games get elimination_game=1
plus the Elo fields. `playoff_odds_swing` = |P(make playoffs | win g) - P(make playoffs | lose g)| is the stakes
number. Both conditionals share the same random draws for every other game (common random numbers), so the swing
is low-noise even with a few hundred sims.

Playoff rule per season: `per_div` auto bids per division (the top teams by record) plus `wildcards` per
conference/league (best remaining records). Ties are broken at random per sim (no league tiebreak rules: cheap,
and the odds error that causes is small next to Elo's). `clinch_status` is mathematical and conservative:
"clinched" / "eliminated" only when no outcome of the remaining schedule can change it, else "alive".
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from redzone.context.tokens import Fact

SOURCE = "derived-standings"


@dataclass
class LeagueConfig:
    name: str
    divisions: Callable[[int], dict[str, tuple[str, str]]]   # season -> {team: (conference, division)}
    playoff: Callable[[int], tuple[int, int]]                 # season -> (per_div auto bids, wildcards per conf)
    license: str
    elo_k: float = 20.0
    elo_hfa: float = 48.0
    elo_regress: float = 1 / 3
    elo_mov: bool = True             # 538-style margin-of-victory multiplier
    sims: int = 500
    asof_lead: pd.Timedelta = field(default_factory=lambda: pd.Timedelta(minutes=30))
    snapshot: str = "kickoff"        # "kickoff": asof = kickoff - lead; "day": asof = the game's day start (see day_start)
    day_start_hour_utc: int = 9      # for snapshot == "day": asof = game date at this UTC hour
    # extra finish-line targets per season, e.g. NBA {"top10": (0, 10)} -> top10_odds, top10_odds_swing facts
    extra_targets: Callable[[int], dict[str, tuple[int, int]]] | None = None


# --------------------------------------------------------------------------------------------- Elo

class Elo:
    def __init__(self, cfg: LeagueConfig, init: dict[str, float] | None = None):
        self.cfg = cfg
        self.r: dict[str, float] = dict(init or {})

    def get(self, t: str) -> float:
        return self.r.get(t, 1500.0)

    def new_season(self, teams) -> None:
        for t in teams:
            self.r[t] = 1500.0 + (1 - self.cfg.elo_regress) * (self.get(t) - 1500.0)

    def prob_home(self, home: str, away: str, neutral: bool = False) -> float:
        d = self.get(home) - self.get(away) + (0.0 if neutral else self.cfg.elo_hfa)
        return 1.0 / (1.0 + 10 ** (-d / 400.0))

    def update(self, home, away, hs, as_, neutral=False) -> None:
        p = self.prob_home(home, away, neutral)
        s = 1.0 if hs > as_ else 0.0 if hs < as_ else 0.5
        k = self.cfg.elo_k
        if self.cfg.elo_mov and hs != as_:
            d = self.get(home) - self.get(away) + (0.0 if neutral else self.cfg.elo_hfa)
            w = d if hs > as_ else -d
            k *= np.log(abs(hs - as_) + 1) * 2.2 / (w * 0.001 + 2.2)
        delta = k * (s - p)
        self.r[home] = self.get(home) + delta
        self.r[away] = self.get(away) - delta


# ---------------------------------------------------------------------------------- playoff picker

@dataclass
class Structure:
    teams: list[str]
    div_cols: list[np.ndarray]
    conf_cols: list[np.ndarray]
    per_div: int
    wildcards: int
    conf_of: np.ndarray
    div_of: np.ndarray

    @classmethod
    def build(cls, cfg: LeagueConfig, season: int, teams) -> "Structure":
        dmap = cfg.divisions(season)
        missing = sorted(set(teams) - set(dmap))
        if missing:
            raise KeyError(f"{cfg.name} {season}: no division for {missing}")
        teams = sorted(teams)
        confs = sorted({dmap[t][0] for t in teams})
        divs = sorted({dmap[t] for t in teams})
        conf_of = np.array([confs.index(dmap[t][0]) for t in teams])
        div_of = np.array([divs.index(dmap[t]) for t in teams])
        per_div, wc = cfg.playoff(season)
        return cls(teams, [np.flatnonzero(div_of == i) for i in range(len(divs))],
                   [np.flatnonzero(conf_of == i) for i in range(len(confs))], per_div, wc, conf_of, div_of)

    def made(self, W: np.ndarray) -> np.ndarray:
        """W: (sims, teams) final points with a per-sim tiebreak jitter already added -> bool (sims, teams)."""
        S = W.shape[0]
        out = np.zeros(W.shape, dtype=bool)
        rows = np.arange(S)[:, None]
        for cols in self.div_cols:
            k = min(self.per_div, len(cols))
            top = np.argsort(-W[:, cols], axis=1)[:, :k]
            out[rows, cols[top]] = True
        for cols in self.conf_cols:
            sub = np.where(out[:, cols], -np.inf, W[:, cols])
            k = min(self.wildcards, len(cols))
            if k:
                top = np.argsort(-sub, axis=1)[:, :k]
                out[rows, cols[top]] = True
        return out

    def clinch_status(self, pts: np.ndarray, rem: np.ndarray) -> list[str]:
        """Conservative mathematical clinch/elimination from current points and games left (1 point per win)."""
        mx = pts + rem
        status = []
        for i in range(len(self.teams)):
            conf = self.conf_cols[self.conf_of[i]]
            div = self.div_cols[self.div_of[i]]
            others_c = conf[conf != i]
            others_d = div[div != i]
            # eliminated: >= per_div division rivals already strictly above my max AND enough conference teams
            # strictly above my max to fill every bid (see docstring proof in docs/datasources/context-stakes-weather.md)
            div_out = (pts[others_d] > mx[i]).sum() >= self.per_div
            n_bids = self.per_div * len(self.div_cols_in_conf(self.conf_of[i])) + self.wildcards
            if div_out and (pts[others_c] > mx[i]).sum() >= n_bids:
                status.append("eliminated")
                continue
            div_in = (mx[others_d] >= pts[i]).sum() < self.per_div
            wc_in = (mx[others_c] >= pts[i]).sum() < self.wildcards
            status.append("clinched_div" if div_in else "clinched_playoff" if wc_in else "alive")
        return status

    def div_cols_in_conf(self, c: int) -> list[np.ndarray]:
        return [d for d in self.div_cols if self.conf_of[d[0]] == c]


# ------------------------------------------------------------------------------------- the engine

def _asof(cfg: LeagueConfig, kickoff: pd.Timestamp) -> pd.Timestamp:
    if cfg.snapshot == "day":
        return kickoff.normalize() + pd.Timedelta(hours=cfg.day_start_hour_utc)
    return kickoff - cfg.asof_lead


def _iso(ts: pd.Timestamp) -> str:
    return ts.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_season(games: pd.DataFrame, cfg: LeagueConfig, elo: Elo, *, now_utc: pd.Timestamp | None = None,
                   horizon: pd.Timedelta = pd.Timedelta(days=8), seed: int = 0, sims: int | None = None) -> list[Fact]:
    """Facts for one season. `elo` carries over between seasons (call elo.new_season first). Games without a
    score are emitted only if kickoff is within `horizon` after `now_utc` (asof = now, the live pregame case)."""
    sims = sims or cfg.sims
    rng = np.random.default_rng(seed)
    g = games.sort_values(["kickoff_utc", "game_id"]).reset_index(drop=True)
    season = int(g["season"].iloc[0])
    played = g["home_score"].notna() & g["away_score"].notna()
    reg = g[g["game_type"] == "REG"]
    teams = sorted(set(reg["home"]) | set(reg["away"]))
    st = Structure.build(cfg, season, teams)
    extra = {}
    for name, (pd_, wc_) in (cfg.extra_targets(season) if cfg.extra_targets else {}).items():
        xs = Structure.build(cfg, season, teams)
        xs.per_div, xs.wildcards = pd_, wc_
        extra[name] = xs
    tix = {t: i for i, t in enumerate(st.teams)}
    lic = cfg.license

    g = g.assign(asof=[_asof(cfg, k) for k in g["kickoff_utc"]], played=played)
    if now_utc is not None:
        fut = ~g["played"] & (g["kickoff_utc"] > now_utc)
        keep = ~fut | (g["kickoff_utc"] <= now_utc + horizon)
        g = g[keep & (g["played"] | (g["kickoff_utc"] > now_utc))].copy()
        g.loc[~g["played"], "asof"] = g.loc[~g["played"], "asof"].clip(upper=now_utc)
    else:
        g = g[g["played"]].copy()
    # every game's result is applied (Elo + standings) once its final_utc has passed
    res = games[played.values].sort_values("final_utc")
    res_i = 0
    W = np.zeros(len(teams))
    L = np.zeros(len(teams))
    T = np.zeros(len(teams))
    facts: list[Fact] = []
    reg_all = games[games["game_type"] == "REG"]
    r_home = reg_all["home"].map(tix).to_numpy()
    r_away = reg_all["away"].map(tix).to_numpy()
    r_final = reg_all["final_utc"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ns]")
    r_played = (reg_all["home_score"].notna() & reg_all["away_score"].notna()).to_numpy()
    r_neutral = reg_all["neutral"].to_numpy(bool)
    r_gid = reg_all["game_id"].to_numpy()

    for asof, slot in g.groupby("asof", sort=True):
        while res_i < len(res) and res["final_utc"].iloc[res_i] <= asof:
            row = res.iloc[res_i]
            elo.update(row["home"], row["away"], row["home_score"], row["away_score"], bool(row["neutral"]))
            if row["game_type"] == "REG":
                h, a = tix[row["home"]], tix[row["away"]]
                hs, as_ = row["home_score"], row["away_score"]
                if hs > as_:
                    W[h] += 1; L[a] += 1
                elif hs < as_:
                    W[a] += 1; L[h] += 1
                else:
                    T[h] += 1; T[a] += 1
            res_i += 1
        asof_s = _iso(asof)
        known = r_played & (r_final <= np.datetime64(asof.tz_convert("UTC").tz_localize(None), "ns"))
        remaining = ~known
        pts = W + 0.5 * T
        gp = W + L + T
        rem_n = np.bincount(r_home[remaining], minlength=len(teams)) + np.bincount(r_away[remaining], minlength=len(teams))
        # Monte Carlo of the remaining regular season with Elo frozen at asof
        rh, ra = r_home[remaining], r_away[remaining]
        ph = np.array([elo.prob_home(st.teams[h], st.teams[a], bool(n))
                       for h, a, n in zip(rh, ra, r_neutral[remaining])])
        hw = rng.random((sims, len(rh))) < ph
        Wsim = np.tile(pts, (sims, 1)) + rng.random((sims, len(teams))) * 0.01
        if len(rh):
            onehot_h = np.zeros((len(rh), len(teams)))
            onehot_h[np.arange(len(rh)), rh] = 1
            onehot_a = np.zeros((len(rh), len(teams)))
            onehot_a[np.arange(len(rh)), ra] = 1
            Wsim += hw @ onehot_h + (~hw) @ onehot_a
        status = st.clinch_status(pts, rem_n)
        rem_idx = {gid: j for j, gid in enumerate(r_gid[remaining])}
        wpct = np.where(gp > 0, (W + 0.5 * T) / np.maximum(gp, 1), np.nan)
        div_rank = np.zeros(len(teams))
        gb = np.zeros(len(teams))
        for cols in st.div_cols:
            p = wpct[cols]
            pz = np.nan_to_num(p, nan=0.0)
            div_rank[cols] = pd.Series(-pz).rank(method="min").to_numpy()
            lead = cols[np.argmax(W[cols] - L[cols])]
            gb[cols] = ((W[lead] - W[cols]) + (L[cols] - L[lead])) / 2.0

        for _, gm in slot.iterrows():
            gid, home, away, neutral = gm["game_id"], gm["home"], gm["away"], bool(gm["neutral"])
            p_home = elo.prob_home(home, away, neutral)
            ed = elo.get(home) - elo.get(away)

            def F(team, fld, val):
                facts.append(Fact(gid, team, asof_s, fld, val, "derived", SOURCE, lic))

            for team, sign, pw in ((home, 1, p_home), (away, -1, 1 - p_home)):
                F(team, "elo", round(elo.get(team), 1))
                F(team, "elo_diff", round(sign * ed, 1))
                F(team, "elo_win_prob", round(pw, 4))
            F(None, "elo_diff_abs", round(abs(ed), 1))
            if gm["game_type"] != "REG":
                F(None, "elimination_game", 1)
                continue
            h, a = tix[home], tix[away]
            j = rem_idx.get(gid)
            base = Wsim.copy()
            if j is not None:   # take this game's own sampled result out, then force each outcome
                base[:, h] -= hw[:, j]
                base[:, a] -= ~hw[:, j]
            Hw = base.copy(); Hw[:, h] += 1
            Aw = base.copy(); Aw[:, a] += 1
            mh, ma = st.made(Hw), st.made(Aw)
            odds, po = {}, {}
            for team, i, pw, mw, ml in ((home, h, p_home, mh, ma), (away, a, 1 - p_home, ma, mh)):
                ow, ol = float(mw[:, i].mean()), float(ml[:, i].mean())
                odds[team] = abs(ow - ol)
                F(team, "wins", W[i]); F(team, "losses", L[i])
                F(team, "win_pct", None if np.isnan(wpct[i]) else round(float(wpct[i]), 4))
                F(team, "div_rank", div_rank[i]); F(team, "games_back", gb[i])
                po[team] = pw * ow + (1 - pw) * ol
                F(team, "playoff_odds", round(po[team], 4))
                F(team, "playoff_odds_if_win", round(ow, 4)); F(team, "playoff_odds_if_loss", round(ol, 4))
                F(team, "playoff_odds_swing", round(odds[team], 4))
                F(team, "clinch_status", status[i])
            for name, xs in extra.items():
                xh, xa = xs.made(Hw), xs.made(Aw)
                for team, i, pw, mw, ml in ((home, h, p_home, xh, xa), (away, a, 1 - p_home, xa, xh)):
                    ow, ol = float(mw[:, i].mean()), float(ml[:, i].mean())
                    xo = pw * ow + (1 - pw) * ol
                    F(team, f"{name}_odds", round(xo, 4))
                    if name == "top10":   # P(seed 7-10), the play-in band
                        F(team, "playin_odds", round(max(xo - po[team], 0.0), 4))
                    F(team, f"{name}_odds_swing", round(abs(ow - ol), 4))
            F(None, "stakes_swing_max", round(max(odds.values()), 4))
            F(None, "stakes_swing_sum", round(sum(odds.values()), 4))
    # apply the rest of the season to Elo so it carries over
    while res_i < len(res):
        row = res.iloc[res_i]
        elo.update(row["home"], row["away"], row["home_score"], row["away_score"], bool(row["neutral"]))
        res_i += 1
    return facts


def compute_league(games: pd.DataFrame, cfg: LeagueConfig, *, seasons=None, now_utc=None, sims=None,
                   log=print) -> dict[int, list[Fact]]:
    """All seasons in order, Elo carried across (regressed toward 1500 each new season)."""
    elo = Elo(cfg)
    out = {}
    for season, gs in games.groupby("season", sort=True):
        teams = sorted(set(gs["home"]) | set(gs["away"]))
        elo.new_season(teams)
        facts = compute_season(gs, cfg, elo, now_utc=now_utc, seed=int(season), sims=sims)
        if seasons is None or season in seasons:
            out[int(season)] = facts
            log(f"{cfg.name} {season}: {len(facts)} facts")
    return out


# ------------------------------------------------------------------------------------ league configs

def _divmap(spec: dict[tuple[str, str], list[str]], alias: dict[str, str] | None = None) -> dict[str, tuple[str, str]]:
    out = {t: cd for cd, ts in spec.items() for t in ts}
    for old, new in (alias or {}).items():
        out[old] = out[new]
    return out


_NFL_ALIAS = {"OAK": "LV", "SD": "LAC", "STL": "LA"}
_NFL_2002 = _divmap({
    ("AFC", "East"): ["BUF", "MIA", "NE", "NYJ"], ("AFC", "North"): ["BAL", "CIN", "CLE", "PIT"],
    ("AFC", "South"): ["HOU", "IND", "JAX", "TEN"], ("AFC", "West"): ["DEN", "KC", "LV", "LAC"],
    ("NFC", "East"): ["DAL", "NYG", "PHI", "WAS"], ("NFC", "North"): ["CHI", "DET", "GB", "MIN"],
    ("NFC", "South"): ["ATL", "CAR", "NO", "TB"], ("NFC", "West"): ["ARI", "LA", "SF", "SEA"],
}, _NFL_ALIAS)
_NFL_1999 = _divmap({   # 31 teams, 6 divisions (1999-2001)
    ("AFC", "East"): ["BUF", "IND", "MIA", "NE", "NYJ"], ("AFC", "Central"): ["BAL", "CIN", "CLE", "JAX", "PIT", "TEN"],
    ("AFC", "West"): ["DEN", "KC", "OAK", "SD", "SEA"],
    ("NFC", "East"): ["ARI", "DAL", "NYG", "PHI", "WAS"], ("NFC", "Central"): ["CHI", "DET", "GB", "MIN", "TB"],
    ("NFC", "West"): ["ATL", "CAR", "NO", "STL", "SF"],
})


def nfl_divisions(season: int) -> dict[str, tuple[str, str]]:
    return _NFL_1999 if season <= 2001 else _NFL_2002


def nfl_playoff(season: int) -> tuple[int, int]:
    if season <= 2001:
        return 1, 3          # 3 division winners + 3 wild cards per conference = 12
    return (1, 2) if season <= 2019 else (1, 3)   # 12 teams 2002-2019, 14 since 2020


_MLB_ALIAS = {"FLO": "MIA", "MON": "WAS", "ATH": "OAK"}
_MLB_BASE = {
    ("AL", "East"): ["BAL", "BOS", "NYA", "TBA", "TOR"], ("AL", "Central"): ["CHA", "CLE", "DET", "KCA", "MIN"],
    ("AL", "West"): ["ANA", "OAK", "SEA", "TEX"],
    ("NL", "East"): ["ATL", "MIA", "NYN", "PHI", "WAS"], ("NL", "Central"): ["CHN", "CIN", "MIL", "PIT", "SLN"],
    ("NL", "West"): ["ARI", "COL", "LAN", "SDN", "SFN"],
}


def mlb_divisions(season: int) -> dict[str, tuple[str, str]]:
    spec = {k: list(v) for k, v in _MLB_BASE.items()}
    spec[("AL", "West") if season >= 2013 else ("NL", "Central")].append("HOU")   # Retrosheet codes
    return _divmap(spec, _MLB_ALIAS)


def mlb_playoff(season: int) -> tuple[int, int]:
    if season == 2020:
        return 2, 2          # 16 teams: top 2 per division + 2 wild cards per league
    if season <= 2011:
        return 1, 1          # 8 teams
    return (1, 2) if season <= 2021 else (1, 3)   # 10 teams 2012-2021, 12 since 2022


NFL_LICENSE = "derived from nflverse schedules (CC-BY-4.0); personal-use, not for commercial release"
MLB_LICENSE = ("derived from Retrosheet game logs (free; 'The information used here was obtained free of charge from "
               "and is copyrighted by Retrosheet. Interested parties may contact Retrosheet at www.retrosheet.org.'); "
               "personal-use, not for commercial release")

NFL = LeagueConfig("nfl", nfl_divisions, nfl_playoff, NFL_LICENSE, elo_k=20.0, elo_hfa=48.0, elo_mov=True,
                   sims=1000, snapshot="kickoff")
MLB = LeagueConfig("mlb", mlb_divisions, mlb_playoff, MLB_LICENSE, elo_k=4.0, elo_hfa=24.0, elo_mov=False,
                   sims=500, snapshot="day")

# NBA (ESPN / hoopR abbreviations; season = start year, 2004 = 2004-05). Six divisions unchanged since 2004-05;
# relocations keep the slot: NJ -> BKN (2012), SEA -> OKC (2008), New Orleans/Oklahoma City Hornets = NO.
_NBA_ALIAS = {"NJ": "BKN", "SEA": "OKC"}
_NBA = _divmap({
    ("East", "Atlantic"): ["BOS", "BKN", "NY", "PHI", "TOR"], ("East", "Central"): ["CHI", "CLE", "DET", "IND", "MIL"],
    ("East", "Southeast"): ["ATL", "CHA", "MIA", "ORL", "WSH"],
    ("West", "Northwest"): ["DEN", "MIN", "OKC", "POR", "UTAH"], ("West", "Pacific"): ["GS", "LAC", "LAL", "PHX", "SAC"],
    ("West", "Southwest"): ["DAL", "HOU", "MEM", "NO", "SA"],
}, _NBA_ALIAS)
NBA_TEAMS = frozenset(_NBA)


def nba_divisions(season: int) -> dict[str, tuple[str, str]]:
    return _NBA


def nba_playoff(season: int) -> tuple[int, int]:
    """`playoff_odds` target. Since 2020-21 = a top-6 seed (direct playoff spot; 7-10 go to the play-in).
    2015-16..2019-20: top 8 per conference. Through 2014-15: division winners were guaranteed a spot (+7)."""
    if season >= 2020:
        return 0, 6
    return (1, 7) if season <= 2014 else (0, 8)


def nba_extra(season: int) -> dict[str, tuple[int, int]]:
    """Play-in era: top10_odds = P(top-10 seed: playoffs or play-in). playin_odds = top10 - playoff (derived later)."""
    return {"top10": (0, 10)} if season >= 2020 else {}


NBA_LICENSE = ("derived from sportsdataverse hoopR NBA schedules (MIT; ESPN-derived data); "
               "personal-use, not for commercial release")
# 538-style NBA Elo: K 20 with a MOV multiplier ~1 -> K 10 with this (log) multiplier; home edge ~80 (recent years
# smaller than 538's 100); regress 1/4 to the mean each season.
NBA = LeagueConfig("nba", nba_divisions, nba_playoff, NBA_LICENSE, elo_k=10.0, elo_hfa=80.0, elo_regress=0.25,
                   elo_mov=True, sims=400, snapshot="kickoff", extra_targets=nba_extra)
STAKES_LEAGUES = {"nfl": NFL, "mlb": MLB, "nba": NBA}
