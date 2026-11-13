"""Feature store: the ONE place model features are built from a feed prefix, for training notebooks and the app alike.

Why: WORKPLAN v2 task F-06 and TRAINING-PLAN rule 8: the stage 4/5/6 notebooks built the value-model features in their
own reader (stage4_reader v3) and the stage 6 arm rebuilt the state features in its own code, so training and serving
could drift apart unseen. Both now call this module; a parity check (scripts/feature_parity.py) and a cut-data test
(tests/test_featurestore.py) hold it to the old builders and to causality.

    from redzone import featurestore as fs
    P = fs.fs_parse("nba", summary)                                   # one pass over the rows the feed has shown
    X = fs.fs_rows("value_v3", "nba", P, times, facts=facts)          # float64, one row per time, FS_SETS order
    f = fs.fs_features_at("state4", "nba", summary, now, lev=1.7)     # one moment as a dict
    rec.update(fs.fs_registry_fields("value_v3"))                     # {"feature_set": "value_v3@value-v3.0/fs-1/<sha12>"}
    RD = fs.FS_STAGE4_COMPAT                                          # drop-in for the notebook's stage4_reader module

Causal contract: a feature at time t reads only rows stamped at or before t - lag (and grader facts stamped at or
before it). Rows after t never change it, so features from a feed PREFIX equal features from the stored full game
(the cut-data test). One inherited quirk, kept for parity: a time BEFORE the first row reads the first row.

Feature sets (name, version, columns). A version changes whenever a column's meaning or arithmetic changes; the tag
`fs_tag(name)` = name@version/store-version/sha12(columns) goes into every registry run that trains on the set.

    value_v3  value-v3.0  the 62 stage 4 v3 features (FS_VALUE_V3_NAMES), nba and cbb; origin: the notebook reader
                          stage4_reader.py v3, sha256 1120430239ef..., ported line for line (float32 out, as fitted)
    state4    state4-v1   lev_now, since_stop, last_kind, since_to: the stage 6 state-only (gbm4) reranker inputs;
                          lev_now from an explicit per-time leverage (the show's) or else the grader's facts

numpy + stdlib only, no other repo import, so a notebook can import it from a pinned checkout and the live process
can import it without pandas.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace

import numpy as np

FS_STORE_VERSION = "fs-1"
FS_MISSING = -999.0                                    # a feature the models read as unknown
FS_REG = {"nba": (4, 720.0), "cbb": (2, 1200.0)}       # regulation periods, seconds per period
FS_OT_SECONDS = 300.0
FS_SINCE_CAP = 9999.0
FS_FACT_CAP = 3600.0
FS_BACKFILL = 5.0                                      # a row with no wallclock is stamped 5 s after the one before
FS_NOT_TEAM_FOUL = ("offensive", "technical", "double", "flopping", "delay", "3second", "nofoul")
FS_WHISTLE = ("foul", "violation", "outofbounds", "substitution", "kickedball", "delay", "technical", "flagrant",
              "jumpball", "freethrow")
FS_FACT_NAMES = ("lev_now", "pv60", "pv300", "pv900", "pv_rate", "since_big", "n_big", "since_hl")
FS_WP_NAMES = ("wp_home", "wp_close", "wp_vol_2", "wp_vol_5", "wp_d60w", "wp_pre")
FS_VALUE_V3_NAMES = (
    "league_cbb", "post", "period", "clock_s", "left_s", "ot", "margin", "absmargin", "total", "wall_elapsed",
    "last_kind", "since_row", "since_action", "since_stop", "since_to", "rows_60w", "rows_300w", "gsec_300w", "gsec_60w",
    "lead_pts_2", "trail_pts_2", "lead_pts_5", "trail_pts_5", "tot_pts_2", "tot_pts_5", "d_absmargin_2", "d_absmargin_5",
    "run_pts", "run_by_trailer", "lead_changes_5", "ties_5", "lead_changes",
    "poss_2", "poss_5", "shots_5", "made3_5", "dunk_5", "tov_5", "stocks_5", "foul_5", "ft_5",
    "tf_lead", "tf_trail", "to_lead", "to_trail", "to_period",
    "spread_abs", "over_under") + FS_WP_NAMES + FS_FACT_NAMES
FS_STATE4_NAMES = ("lev_now", "since_stop", "last_kind", "since_to")
FS_NOTEBOOK_READER_SHA256 = "1120430239ef10310b1a082f85b6264f5e190f5ffe99bf6795ee8753ddf35ad2"


@dataclass(frozen=True)
class FeatureSetSpec:
    """One registered feature set: its columns in model order, its version and where it came from."""
    name: str
    version: str
    names: tuple
    leagues: tuple
    origin: str


FS_SETS = {
    "value_v3": FeatureSetSpec("value_v3", "value-v3.0", FS_VALUE_V3_NAMES, ("nba", "cbb"),
                               "stage4_reader.py v3 (models/value-v1/stage4_reader_v3.py), sha256 1120430239ef"),
    "state4": FeatureSetSpec("state4", "state4-v1", FS_STATE4_NAMES, ("nba", "cbb"),
                             "stage 6 state-only export (gbm4) and the A-03 arm's Stage6Rows"),
}


def fs_spec(name: str) -> FeatureSetSpec:
    """The registered set, or KeyError naming the sets that exist."""
    if name not in FS_SETS:
        raise KeyError(f"no feature set {name!r} (have {', '.join(sorted(FS_SETS))})")
    return FS_SETS[name]


def fs_tag(name: str) -> str:
    """name@version/store-version/sha12 of the column list: the string a registry run records."""
    s = fs_spec(name)
    h = hashlib.sha256("\n".join(s.names).encode()).hexdigest()[:12]
    return f"{s.name}@{s.version}/{FS_STORE_VERSION}/{h}"


def fs_registry_fields(name: str) -> dict:
    """The optional registry field(s) a run trained on this set adds to its record (redzone.models_rt.registry)."""
    return {"feature_set": fs_tag(name)}


# ---- reading one row ---------------------------------------------------------------------------------------------------
def _fs_ts(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() if iso else None
    except (ValueError, AttributeError):
        return None


def _fs_clock(d):
    try:
        if d and ":" in str(d):
            m, s = str(d).split(":", 1)
            return int(m) * 60 + float(s)
        return float(d) if d not in (None, "") else None
    except ValueError:
        return None


def _fs_int(v):
    """int(float(v)), or None for anything that is not a number (no repo import: notebooks load this file alone)."""
    out = None
    try:
        out = int(float(v))
    except (TypeError, ValueError):
        pass
    return out


def _fs_get(o, *path):
    for p in path:
        o = o.get(p) if isinstance(o, dict) else None
        if o is None:
            return None
    return o


def fs_norm_type(type_text) -> str:
    """A row's type text the way every kind test reads it: lower case, no spaces, dashes or newlines."""
    return str(type_text or "").lower().replace(" ", "").replace("-", "").replace("\n", "")


def fs_kind(tn: str, period: int, half: int) -> int:
    """What a row says about play (tn = fs_norm_type): 0 action, 1 whistle (foul, violation, substitution, free throw,
    ...), 2 review, 3 timeout, 4 period end, 5 halftime, 6 end of game."""
    if "timeout" in tn:
        return 3
    if tn in ("endgame", "endofgame"):
        return 6
    if tn == "endperiod":
        return 5 if period == half else 4
    if "challenge" in tn or "review" in tn:
        return 2
    return 1 if any(w in tn for w in FS_WHISTLE) else 0


def fs_wall(plays: list) -> list | None:
    """Wall stamps for the rows: wallclock, a missing one 5 s after the last, then made non-decreasing. None when the
    first row has no stamp (the same rule as the replay loader and the notebook reader)."""
    wall, last = [], None
    for p in plays:
        t = _fs_ts(p.get("wallclock"))
        t = t if t is not None else (last + FS_BACKFILL if last is not None else None)
        wall.append(t)
        last = t if t is not None else last
    if not wall or wall[0] is None:
        return None
    for i in range(1, len(wall)):
        wall[i] = max(wall[i], wall[i - 1])
    return wall


# ---- one pass over the feed --------------------------------------------------------------------------------------------
def fs_parse(league: str, summary: dict, wall=None, min_rows: int = 2):
    """Every per-row array the features need, from the rows the summary holds (a feed prefix or a stored game: each
    array entry depends only on that row and the rows before it). `wall` overrides the stamps (the show passes its own
    replay stamps). None when there are fewer than `min_rows` rows or no first stamp."""
    plays = [p for p in (summary.get("plays") or []) if isinstance(p, dict)]
    n = len(plays)
    if n < max(1, min_rows):
        return None
    reg, plen = FS_REG.get(league, FS_REG["nba"])
    comp = _fs_get(summary, "header", "competitions")
    comp = comp[0] if isinstance(comp, list) and comp else {}
    side = {str(_fs_get(c, "team", "id")): c.get("homeAway") for c in comp.get("competitors", []) if isinstance(c, dict)}
    wpm = {}
    for w in summary.get("winprobability") or []:
        if isinstance(w, dict) and w.get("playId") is not None and w.get("homeWinPercentage") is not None:
            try:
                wpm[str(w["playId"])] = float(w["homeWinPercentage"])
            except (TypeError, ValueError):
                pass
    if wall is None:
        wall = fs_wall(plays)
        if wall is None:
            return None
    else:
        wall = [float(x) for x in wall]
        if len(wall) != n:
            raise ValueError(f"{len(wall)} wall stamps for {n} rows")

    def z(dt=np.float64):
        return np.zeros(n, dtype=dt)
    per, clk, gsec, hs, as_, kind, wp = z(), z(), z(), z(), z(), z(np.int64), np.full(n, np.nan)
    c = {k: z() for k in ("shot", "made3", "dunk", "tov", "stocks", "foul", "ft", "poss", "tfh", "tfa", "toh", "toa", "to",
                          "lc", "tie")}
    run_pts, run_side, pstart = z(), np.full(n, -1.0), z(np.int64)
    ph = pa = 0
    pp_, pc_, pw, rs, rp, sign, ps = 0, plen, np.nan, -1, 0.0, 0, 0
    for i, p in enumerate(plays):
        pr = _fs_int(_fs_get(p, "period", "number")) or pp_ or 1
        ck = _fs_clock(_fs_get(p, "clock", "displayValue"))
        if pr != pp_:
            ps = i
        if ck is None:
            ck = pc_ if pr == pp_ else (plen if pr <= reg else FS_OT_SECONDS)
        pp_, pc_ = pr, ck
        per[i], clk[i], pstart[i] = pr, ck, ps
        gsec[i] = ((pr - 1) * plen + (plen - ck) if pr <= reg
                   else reg * plen + (pr - reg - 1) * FS_OT_SECONDS + (FS_OT_SECONDS - ck))
        h, a = _fs_int(p.get("homeScore")), _fs_int(p.get("awayScore"))
        h, a = (ph if h is None else h), (pa if a is None else a)
        dh, da = max(0, h - ph), max(0, a - pa)
        ph, pa = h, a
        hs[i], as_[i] = h, a
        tn = fs_norm_type(_fs_get(p, "type", "text"))
        x = str(p.get("text") or "").lower()
        kind[i] = fs_kind(tn, pr, reg // 2)
        sd = {"home": 0, "away": 1}.get(side.get(str(_fs_get(p, "team", "id"))), -1)
        ft = "freethrow" in tn
        pts = dh + da
        c["ft"][i] = ft
        c["shot"][i] = ((not ft) and (bool(p.get("shootingPlay"))
                                      or any(w in tn for w in ("shot", "layup", "dunk", "jumper", "tipin")))
                        and "block" not in tn)
        c["made3"][i] = (not ft) and pts == 3
        c["dunk"][i] = "dunk" in tn and pts > 0
        tov = "turnover" in tn
        c["tov"][i] = tov
        c["stocks"][i] = "block" in tn or "steal" in tn or "steals" in x or "blocks" in x
        foul = "foul" in tn and "nofoul" not in tn
        c["foul"][i] = foul
        if foul and sd >= 0 and not any(w in tn for w in FS_NOT_TEAM_FOUL):
            c["tfh" if sd == 0 else "tfa"][i] = 1
        if "timeout" in tn:
            c["to"][i] = 1
            if "official" not in x and "official" not in tn and "tv" not in tn and "media" not in x and sd >= 0:
                c["toh" if sd == 0 else "toa"][i] = 1
        c["poss"][i] = (pts > 0 and not ft) or "defensiverebound" in tn or tov
        if pts > 0:
            s_ = 0 if dh >= da else 1
            rp = rp + pts if s_ == rs else float(pts)
            rs = s_
            sg = (h > a) - (h < a)
            if sg == 0:
                c["tie"][i] = 1
            elif sign != 0 and sg != sign:
                c["lc"][i] = 1
            if sg != 0:
                sign = sg
        run_pts[i], run_side[i] = rp, rs
        v = wpm.get(str(p.get("id")))
        pw = v if v is not None else pw
        wp[i] = pw
    gsec = np.maximum.accumulate(gsec)
    ar = np.arange(n)
    P = {"league": league, "n": n, "wall": np.array(wall, dtype=np.float64), "per": per, "clk": clk, "gsec": gsec,
         "kind": kind, "pstart": pstart, "HS": np.concatenate([[0.0], hs]), "AS": np.concatenate([[0.0], as_]),
         "run_pts": run_pts, "run_side": run_side, "wp": wp,
         "last_action": np.maximum.accumulate(np.where(kind == 0, ar, -1)),
         "last_stop": np.maximum.accumulate(np.where(kind > 0, ar, -1)),
         "last_to": np.maximum.accumulate(np.where(kind == 3, ar, -1)),
         "wp_abs": np.concatenate([[0.0], np.cumsum(np.nan_to_num(np.abs(np.diff(wp, prepend=wp[0]))))]),
         "has_wp": bool(np.isfinite(wp).any())}
    for k, v in c.items():
        P["C_" + k] = np.concatenate([[0.0], np.cumsum(v)])
    pk = (summary.get("pickcenter") or [None])[0] if isinstance(summary.get("pickcenter"), list) else None
    try:
        P["spread_abs"] = abs(float(pk["spread"])) if pk and pk.get("spread") is not None else FS_MISSING
    except (TypeError, ValueError):
        P["spread_abs"] = FS_MISSING
    try:
        P["over_under"] = float(pk["overUnder"]) if pk and pk.get("overUnder") is not None else FS_MISSING
    except (TypeError, ValueError):
        P["over_under"] = FS_MISSING
    return P


def fs_facts(facts):
    """The grader's lists {"lev", "v2", "big", "hl"} as sorted arrays (once per game; converted input passes through)."""
    if facts is None or "_lt" in facts:
        return facts
    lev = np.array([x[:2] for x in facts.get("lev") or ()], dtype=np.float64).reshape(-1, 2)
    v2 = np.array([x[:2] for x in facts.get("v2") or () if x[1] > 0], dtype=np.float64).reshape(-1, 2)
    v2 = v2[np.argsort(v2[:, 0], kind="stable")]
    big = np.sort(np.array([x[0] for x in facts.get("big") or ()], dtype=np.float64))
    hl = np.sort(np.array([x[1] for x in facts.get("hl") or ()], dtype=np.float64))
    return {"_lt": lev[:, 0], "_lv": lev[:, 1], "_vt": v2[:, 0], "_cv": np.concatenate([[0.0], np.cumsum(v2[:, 1])]),
            "_bt": big, "_he": hl}


# ---- the columns ---------------------------------------------------------------------------------------------------------
def _fs_head(P, times, lag):
    t = np.atleast_1d(np.asarray(times, dtype=np.float64))
    te = t - float(lag)
    j = np.clip(np.searchsorted(P["wall"], te, "right") - 1, 0, P["n"] - 1)
    return t, te, j


def _fs_since(P, name, j, te):
    k = P[name][j]
    return np.where(k >= 0, np.minimum(te - P["wall"][np.clip(k, 0, None)], FS_SINCE_CAP), FS_SINCE_CAP)


def _fs_fact_lev(fa, te, n):
    if fa is None:
        return np.full(n, FS_MISSING)
    if not len(fa["_lt"]):
        return np.full(n, 1.0)
    k = np.searchsorted(fa["_lt"], te, "right") - 1
    return np.where(k >= 0, fa["_lv"][np.clip(k, 0, None)], 1.0)


def _fs_value_v3(league, P, times, facts, post, lag) -> dict:
    """The 62 value_v3 columns as float64 arrays (rows at or before time - lag only)."""
    t, te, j = _fs_head(P, times, lag)
    n, wall, gs = P["n"], P["wall"], P["gsec"]
    reg, plen = FS_REG.get(league, FS_REG["nba"])
    F = {}
    per, clk = P["per"][j], P["clk"][j]
    ot = per > reg
    hs, as_ = P["HS"][j + 1], P["AS"][j + 1]
    m = hs - as_
    home_lead = m >= 0
    F["league_cbb"] = np.full(len(t), float(league == "cbb"))
    F["post"] = np.full(len(t), float(post))
    F["period"], F["clock_s"], F["ot"] = per, clk, ot.astype(np.float64)
    F["left_s"] = np.where(ot, clk, (reg - per) * plen + clk)
    F["margin"], F["absmargin"], F["total"] = m, np.abs(m), hs + as_
    F["wall_elapsed"] = te - wall[0]
    F["last_kind"] = P["kind"][j].astype(np.float64)
    F["since_row"] = te - wall[j]
    F["since_action"] = _fs_since(P, "last_action", j, te)
    F["since_stop"] = _fs_since(P, "last_stop", j, te)
    F["since_to"] = _fs_since(P, "last_to", j, te)

    def wwin(sec):                      # first row stamped inside the last `sec` wall seconds
        return np.minimum(np.searchsorted(wall, te - sec, "right"), j + 1)

    def gwin(sec):                      # first row inside the last `sec` seconds of game clock
        return np.minimum(np.searchsorted(gs, gs[j] - sec, "left"), j)
    w60, w300, g2, g5 = wwin(60.0), wwin(300.0), gwin(120.0), gwin(300.0)
    F["rows_60w"], F["rows_300w"] = (j + 1 - w60).astype(np.float64), (j + 1 - w300).astype(np.float64)
    F["gsec_300w"] = gs[j] - gs[np.clip(w300 - 1, 0, None)]
    F["gsec_60w"] = gs[j] - gs[np.clip(w60 - 1, 0, None)]
    for tag, g0 in (("2", g2), ("5", g5)):
        ph, pa = hs - P["HS"][g0], as_ - P["AS"][g0]
        F["lead_pts_" + tag] = np.where(home_lead, ph, pa)
        F["trail_pts_" + tag] = np.where(home_lead, pa, ph)
        F["tot_pts_" + tag] = ph + pa
        F["d_absmargin_" + tag] = np.abs(m) - np.abs(P["HS"][g0] - P["AS"][g0])
    F["run_pts"] = P["run_pts"][j]
    rs = P["run_side"][j]
    F["run_by_trailer"] = np.where(rs < 0, 0.0, np.where(m == 0, 0.5, ((rs == 1) == home_lead).astype(np.float64)))

    def cnt(name, j0):
        return P["C_" + name][j + 1] - P["C_" + name][j0]
    F["lead_changes_5"], F["ties_5"], F["lead_changes"] = cnt("lc", g5), cnt("tie", g5), P["C_lc"][j + 1]
    F["poss_2"], F["poss_5"] = cnt("poss", g2), cnt("poss", g5)
    for k in ("shot", "made3", "dunk", "tov", "stocks", "foul", "ft"):
        F[{"shot": "shots_5"}.get(k, k + "_5")] = cnt(k, g5)
    ps = P["pstart"][j]
    tfh, tfa = cnt("tfh", ps), cnt("tfa", ps)
    F["tf_lead"], F["tf_trail"] = np.where(home_lead, tfh, tfa), np.where(home_lead, tfa, tfh)
    toh, toa = P["C_toh"][j + 1], P["C_toa"][j + 1]
    F["to_lead"], F["to_trail"] = np.where(home_lead, toh, toa), np.where(home_lead, toa, toh)
    F["to_period"] = cnt("to", ps)
    F["spread_abs"] = np.full(len(t), float(P["spread_abs"]))
    F["over_under"] = np.full(len(t), float(P["over_under"]))
    wp = P["wp"][j]
    if P["has_wp"]:
        F["wp_home"] = wp
        F["wp_close"] = 1.0 - np.abs(2.0 * wp - 1.0)
        F["wp_vol_2"] = P["wp_abs"][j + 1] - P["wp_abs"][np.minimum(g2 + 1, j + 1)]
        F["wp_vol_5"] = P["wp_abs"][j + 1] - P["wp_abs"][np.minimum(g5 + 1, j + 1)]
        F["wp_d60w"] = np.abs(wp - P["wp"][np.clip(w60 - 1, 0, None)])
        f0 = int(np.flatnonzero(np.isfinite(P["wp"]))[0])     # read the pregame number only once it is in hand
        F["wp_pre"] = np.where(j >= f0, abs(float(P["wp"][f0]) - 0.5), np.nan)
        for k in FS_WP_NAMES:
            F[k] = np.where(np.isfinite(F[k]), F[k], FS_MISSING)
    else:
        for k in FS_WP_NAMES:
            F[k] = np.full(len(t), FS_MISSING)
    fa = fs_facts(facts)
    if fa is not None:
        F["lev_now"] = _fs_fact_lev(fa, te, len(t))
        cv, vt = fa["_cv"], fa["_vt"]
        i1 = np.searchsorted(vt, te, "right")
        for sec in (60, 300, 900):
            F["pv%d" % sec] = cv[i1] - cv[np.searchsorted(vt, te - sec, "right")]
        F["pv_rate"] = cv[i1] / np.maximum(F["wall_elapsed"] / 60.0, 1.0)
        for name, arr in (("since_big", fa["_bt"]), ("since_hl", fa["_he"])):
            k = np.searchsorted(arr, te, "right")
            F[name] = (np.where(k > 0, np.minimum(te - arr[np.clip(k - 1, 0, None)], FS_FACT_CAP), FS_FACT_CAP)
                       if len(arr) else np.full(len(t), FS_FACT_CAP))
        F["n_big"] = np.searchsorted(fa["_bt"], te, "right").astype(np.float64)
    else:
        for k in FS_FACT_NAMES:
            F[k] = np.full(len(t), FS_MISSING)
    return {k: np.asarray(F[k], dtype=np.float64) for k in FS_VALUE_V3_NAMES}


def _fs_state4(league, P, times, facts, lag, lev) -> dict:
    """lev_now, since_stop, last_kind, since_to as float64 arrays. lev: one leverage per time (the show's own number at
    that second) or None to read the grader's facts (FS_MISSING without facts)."""
    t, te, j = _fs_head(P, times, lag)
    if lev is not None:
        lv = np.broadcast_to(np.asarray(lev, dtype=np.float64), t.shape).copy()
    else:
        lv = _fs_fact_lev(fs_facts(facts), te, len(t))
    return {"lev_now": lv, "since_stop": _fs_since(P, "last_stop", j, te),
            "last_kind": P["kind"][j].astype(np.float64), "since_to": _fs_since(P, "last_to", j, te)}


def fs_columns(name: str, league: str, P: dict, times, facts=None, post=0, lag: float = 0.0, lev=None) -> dict:
    """{column: float64 array over times} for one registered set, from parsed rows P (fs_parse)."""
    fs_spec(name)
    if name == "value_v3":
        return _fs_value_v3(league, P, times, facts, post, lag)
    return _fs_state4(league, P, times, facts, lag, lev)


def fs_rows(name: str, league: str, P: dict, times, facts=None, post=0, lag: float = 0.0, lev=None,
            dtype=np.float64) -> np.ndarray:
    """One row per time in the set's column order (FS_SETS[name].names). Models read rows at float32."""
    cols = fs_columns(name, league, P, times, facts, post, lag, lev)
    return np.column_stack([cols[k] for k in fs_spec(name).names]).astype(dtype)


def fs_features_at(name: str, league: str, summary: dict, now: float, facts=None, post=0, lag: float = 0.0,
                   lev=None, wall=None):
    """One moment as {column: float} (None when the summary has no usable rows)."""
    P = fs_parse(league, summary, wall=wall)
    if P is None:
        return None
    row = fs_rows(name, league, P, [now], facts, post, lag, None if lev is None else [lev])[0]
    return dict(zip(fs_spec(name).names, (float(v) for v in row)))


# ---- drop-in for the notebook reader ------------------------------------------------------------------------------------
def _fs_compat_game_features(league, P, times, facts=None, post=0, lag=0.0):
    return fs_rows("value_v3", league, P, times, facts, post, lag, dtype=np.float32)


def _fs_compat_features_at(league, summary, now, facts=None, post=0, lag=0.0):
    P = fs_parse(league, summary)
    if P is None:
        return None
    return dict(zip(FS_VALUE_V3_NAMES, (float(v) for v in _fs_compat_game_features(league, P, [now], facts, post, lag)[0])))


# `RD = featurestore.FS_STAGE4_COMPAT` replaces `RD = <stage4_reader_v3 module>` in the notebooks: same names, same
# arrays (float32), so RD.parse / RD.game_features / RD.FEATURES calls need no other change. Model loading and
# prediction stay in the reader: this module builds features only.
FS_STAGE4_COMPAT = SimpleNamespace(FEATURES=FS_VALUE_V3_NAMES, MISSING=FS_MISSING, REG=FS_REG, FACT_F=FS_FACT_NAMES,
                                   WP_F=FS_WP_NAMES, parse=fs_parse, facts_arrays=fs_facts,
                                   game_features=_fs_compat_game_features, features_at=_fs_compat_features_at,
                                   kind_of=fs_kind)
