"""Trace WHY the queue beats the planner in the night replay, and say whether the queue should own planner-rung ticks.

Why: scripts/queue_replay.py shows NFL ready-in-time 12.3% (planner), 26.8% (queue alone), 12.3% (as wired): as wired,
Engine._queue_standby gives planner-rung ticks to standby.Planner, so the queue's gain never reaches the TV. Before
anyone widens the queue's reach we need to know what that gain is made of (the stick staying on the RedZone channel?
a real game the decider knew about? a game only the queue's own planner rung liked?).

    nice -n 15 python scripts/queue_trace.py --archive data/archive --split fit --leagues nfl
    nice -n 15 python scripts/queue_trace.py --archive data/archive --leagues nfl,cfb,nba,cbb,nhl,mlb --json out.json

A GAIN is a live cut (no catch-up) that the queue-alone arm had ready and the planner arm did not. Each gain gets:
  rung      the queue's rung on the tick that started loading the game (LOCK..PLANNER, or LEFT_BEHIND = no queue load: the game was on screen)
  reaches   the wired arm (what the TV runs) had it ready too: the rung was above the planner rung
  why       what the planner arm did instead: idle, never listed it, listed it behind something else, listed it first
  held      what the planner arm's stick held at the cut: the RedZone channel, a game the show cut to soon after, a
            real game the show did not cut to soon, or nothing
  lead_s    seconds the game sat on the stick before the cut (from its load, or from the cut that left it behind)
  knew      the decider already wanted the game on some tick of the window (a known moment), or not
Same modeled stick times and invented-free physics as queue_replay (no failures here). Reads the archive only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import queue_replay as qr  # noqa: E402

from redzone.noggin import gamequeue as gq  # noqa: E402

REDZONE = "youtubetv:redzone"
MODES = ("none", "ranked", "first")
NEAR_S = 120.0   # a held game the show cuts to within this long after the planner's cut "was one the show wanted"


def is_redzone(target: str | None) -> bool:
    return bool(target) and "redzone" in target.lower()


def _rz_cut(game: str | None, target: str | None) -> bool:
    return game == "redzone" or is_redzone(target)


def _rung_name(hist_row, game: str) -> str:
    rows = hist_row[4] if hist_row else {}
    return rows[game][0] if game in rows else "NONE"


def _load_tick(trace, hist: dict, to: str, cut_t: float):
    """The tick that started loading `to`: the latest one before the cut where the queue wanted it and the stick held
    something else. The queue holds a loaded game while its want moves on, so the want need not run up to the cut.
    None = the stick got it with no queue load (the game was on screen and a cut left it behind)."""
    for tk in reversed([k for k in trace.ticks if k["t"] <= cut_t]):
        h = hist.get(tk["t"])
        if h is not None and h[2] == to and h[0] != to:
            return tk
    return None


def _held_kind(game, target, cuts, cut_t, near_s) -> str:
    if game is None:
        return "nothing"
    if _rz_cut(game, target):
        return "redzone_channel"
    soon = any(c["to"] == game and 0 <= c["t"] - cut_t <= near_s for c in cuts)
    return "game_cut_soon" if soon else "game_not_cut_soon"


RZ_SCORE = 45.0   # decider.redzone_score


def with_redzone(trace, mode: str):
    """The night as the TV would run it: Engine._standby_options adds the RedZone channel (decider.redzone_channel,
    default true) as a candidate and a planner option, and the replay's standby.options call does not. Added on the
    ticks between the first and last time the decider used the channel (it is a Sunday-afternoon thing).
      none    as queue_replay runs it: no channel on the slate
      ranked  the channel is a BEST option worth redzone_score, NEXT when the decider wants it (as options() ranks it)
      first   the channel is the planner's top pick on every such tick (the most the planner could do with it)"""
    if mode == "none":
        return trace
    from types import SimpleNamespace as NS

    from redzone import standby
    used = [t for w in trace.switches for t in (w["t"],) if "redzone" in (w["to"], w["frm"])]
    if not used:
        return trace
    lo, hi = min(used), max(used)
    ticks = []
    for tk in trace.ticks:
        if not lo <= tk["t"] <= hi:
            ticks.append(tk)
            continue
        cands = {**tk["cands"], "redzone": NS(scored=NS(score=RZ_SCORE, is_break=False), target=REDZONE, stale=False)}
        opts = list(tk["opts"])
        if tk["current"] != "redzone":
            cls = standby.NEXT if tk["want"] == "redzone" or mode == "first" else standby.BEST
            opts.append(standby.Option("redzone", REDZONE, cls, 1000.0 if mode == "first" else RZ_SCORE, "NFL RedZone"))
            opts.sort(key=lambda o: (o.cls, -o.value))
        ticks.append(dict(tk, cands=cands, opts=opts))
    return qr.Trace(trace.night, ticks, trace.switches)


def trace_cuts(trace, cfg, near_s: float = NEAR_S, redzone: str = "none") -> dict:
    """The gains of one night. Pure given the trace; no failures."""
    trace = with_redzone(trace, redzone)
    raw = {a: qr.simulate(a, trace, cfg) for a in ("off", "on", "wired")}
    by = {a: {(c["t"], c["to"]): c for c in raw[a]["cuts"]} for a in raw}
    out = {"cuts": len(raw["off"]["cuts"]), "ready": {a: sum(c["ready"] for c in raw[a]["cuts"]) for a in raw},
           "gains": [], "losses": 0}
    for key, c_on in by["on"].items():
        c_off, c_wi = by["off"].get(key), by["wired"].get(key)
        if c_off is None:
            continue
        if c_off["ready"] and not c_on["ready"]:
            out["losses"] += 1
        if not (c_on["ready"] and not c_off["ready"]):
            continue
        t, to = key
        t0 = _load_tick(trace, raw["on"]["hist"], to, t)
        rung = _rung_name(raw["on"]["hist"].get(t0["t"]), to) if t0 else "LEFT_BEHIND"
        leave = max((w["t"] for w in trace.switches if w["frm"] == to and w["t"] < t), default=None)
        start = t0["t"] if t0 else leave if leave is not None else t - 60
        window = [k for k in trace.ticks if start <= k["t"] <= t]
        at = t0 or next((k for k in trace.ticks if k["t"] >= start), None)   # the tick the planner chose on
        opts = at["opts"] if at else []
        rank = next((i for i, o in enumerate([o for o in opts if o.replay_id is None]) if o.game_id == to), None)
        held = c_off["held"]
        held_t = qr._target_of(trace, held) if held else None
        why = ("idle" if held is None else "never_listed" if rank is None else "listed_first" if rank == 0
               else "listed_behind")
        out["gains"].append({
            "t": t, "game": to, "rung": rung, "reaches": bool(c_wi and c_wi["ready"]),
            "lead_s": round(t - start, 1), "cut_kind": c_on["kind"], "why": why,
            "held": _held_kind(held, held_t, raw["off"]["cuts"], t, near_s),
            "knew": any(k["want"] == to for k in window),
            "to_is_redzone": _rz_cut(to, qr._target_of(trace, to)),
            "app": c_on["app"]})
    return out


def _count(rows, key):
    d: dict = {}
    for r in rows:
        d[r[key]] = d.get(r[key], 0) + 1
    return dict(sorted(d.items(), key=lambda kv: -kv[1]))


def summarize(parts: list[dict]) -> dict:
    cuts = sum(p["cuts"] for p in parts)
    gains = [g for p in parts for g in p["gains"]]
    ready = {a: sum(p["ready"][a] for p in parts) for a in ("off", "on", "wired")}
    pct = lambda n: round(100 * n / cuts, 1) if cuts else None   # noqa: E731
    not_tv = [g for g in gains if not g["reaches"]]
    return {
        "cuts": cuts, "ready_in_time_pct": {a: pct(n) for a, n in ready.items()},
        "queue_losses_vs_planner": sum(p["losses"] for p in parts),
        "gains": len(gains), "gain_pts": pct(len(gains)),
        "gains_reaching_the_tv": sum(g["reaches"] for g in gains),
        "gains_lost_by_the_wiring": len(not_tv),
        "by_rung": _count(gains, "rung"), "lost_by_rung": _count(not_tv, "rung"),
        "lost_by_planner_why": _count(not_tv, "why"), "lost_by_what_planner_held": _count(not_tv, "held"),
        "gain_target_is_redzone_channel": sum(g["to_is_redzone"] for g in gains),
        "gains_real_games": sum(not g["to_is_redzone"] for g in gains),
        "real_game_gain_pts": pct(sum(not g["to_is_redzone"] for g in gains) - sum(p["losses"] for p in parts)),
        "gain_planner_held_redzone_channel": sum(g["held"] == "redzone_channel" for g in gains),
        "gain_decider_already_wanted_it": sum(g["knew"] for g in gains),
        "left_behind_gap_s": _count([dict(b=("<=60" if g["lead_s"] <= 60 else "<=180" if g["lead_s"] <= 180 else ">180"))
                                     for g in gains if g["rung"] == "LEFT_BEHIND"], "b"),
        "gain_cut_kind": _count(gains, "cut_kind"),
        "gain_by_app": _count(gains, "app"),
        "median_lead_s": sorted(g["lead_s"] for g in gains if g["lead_s"] is not None)[
            len([g for g in gains if g["lead_s"] is not None]) // 2] if any(g["lead_s"] is not None for g in gains) else None}


def recommend(variants: dict) -> str:
    """Should the queue own planner-rung ticks? Judged on the fair control (the planner given the RedZone channel, as
    the TV gives it): the queue earns the rung only if real-game gains, net of cuts it loses vs the planner, clear 2
    points of ready-in-time. The RedZone channel's share is a harness gap, not a queue skill."""
    s = variants.get("ranked") or next(iter(variants.values()))
    if not s["cuts"]:
        return "no data"
    pts, rz = s["real_game_gain_pts"], s["gain_target_is_redzone_channel"]
    if pts >= 2.0:
        return (f"yes, behind a flag: net {pts} pts from real games ({s['gains_real_games']} won, "
                f"{s['queue_losses_vs_planner']} lost of {s['cuts']} cuts)")
    return (f"no: net {pts} pts from real games ({s['gains_real_games']} won, {s['queue_losses_vs_planner']} lost "
            f"of {s['cuts']}); {rz} of {s['gains']} gains are the RedZone channel")


def load_nights(archive: Path, lg_name: str, split: str, max_nights, step, seed):
    """Yield (night, trace) for the league's archived nights, as queue_replay.run_nights builds them."""
    import yaml  # noqa: F401
    from redzone.channels import ChannelMap
    from redzone.config import Config
    from redzone.lab import evaldata, splits
    from redzone.lab.backtest import Replay
    from redzone.models import League
    from redzone.sources import espn
    pinned = archive.parent / "eval_config.yaml"
    cfg = Config(user_path=pinned if pinned.exists() else archive.parent / "config.yaml")
    evaldata.use_shipped_switch_costs(cfg)
    chans = archive.parent / "_eval_channels.json"
    lg, arch = League(lg_name), archive / lg_name
    if not arch.is_dir() or not list(arch.glob("scoreboard_*.json")):
        return
    evaldata.flatten_league_overrides(cfg, lg_name)
    mine = set(cfg.get(f"teams.{lg_name}") or ["WSH"])
    end = evaldata.window_end(lg_name, None)
    keep = splits.nights_for(arch, split, end)
    sbs = sorted(sb for sb in arch.glob("scoreboard_*.json") if (not end or sb.stem.split("_")[-1] <= end))
    sbs = [sb for sb in sbs if keep is None or sb.stem.split("_")[-1] in keep]
    for sb in sbs[:max_nights] if max_nights else sbs:
        reps, targets = [], {}
        for e in json.loads(sb.read_text()).get("events", []):
            f = arch / f"summary_{e['id']}.json"
            r = Replay.load(lg, f) if f.exists() else None
            if r:
                reps.append(r)
                g = espn._parse_event(lg, e)
                w = ChannelMap(chans).where(g) if g and chans.exists() else None
                targets[r.gid] = f"{(w.target or 'youtubetv:x').split(':')[0] if w else 'youtubetv'}:{r.gid}"
        if len(reps) < 2:
            continue
        tr = qr.record_night(reps, targets, cfg, mine, step, seed)
        tr.night = sb.stem.split("_")[-1]
        if any(s["kind"] not in qr.NO_BUFFER for s in tr.switches):
            yield tr, cfg


def run(archive: Path, leagues, split: str, max_nights, seed: int, near_s: float, modes=MODES, log=print) -> dict:
    rep: dict = {"modeled": "every stick time is MODELED (gamequeue.Params); no failures; same harness as queue_replay",
                 "split": split, "leagues": {}}
    for lg in leagues:
        parts: dict[str, list] = {m: [] for m in modes}
        n = 0
        for tr, cfg in load_nights(archive, lg, split, max_nights, None, seed):
            n += 1
            for m in modes:
                parts[m].append(trace_cuts(tr, cfg, near_s, m))
            log(f"{lg} {tr.night}: {parts[modes[0]][-1]['cuts']} cuts")
        v = {m: summarize(x) if x else {"cuts": 0} for m, x in parts.items()}
        rep["leagues"][lg] = {"nights": n, "variants": v, "recommendation": recommend(v)}
    return rep


def table(rep: dict) -> str:
    rows = ["| league | redzone in planner | cuts | planner % | queue % | wired % | gains | RedZone-channel gains | real-game gains | queue worse |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    more = []
    for lg, d in rep["leagues"].items():
        for m, x in d["variants"].items():
            if not x.get("cuts"):
                continue
            r = x["ready_in_time_pct"]
            rows.append(f"| {lg} | {m} | {x['cuts']} | {r['off']} | {r['on']} | {r['wired']} | {x['gains']} | "
                        f"{x['gain_target_is_redzone_channel']} | {x['gains_real_games']} | {x['queue_losses_vs_planner']} |")
        x = d["variants"].get("none") or {}
        if x.get("cuts"):
            more.append(f"{lg} (none): by rung {x['by_rung']}; planner held {x['lost_by_what_planner_held']}; "
                        f"decider already wanted {x['gain_decider_already_wanted_it']}/{x['gains']}; "
                        f"gap before the cut {x['left_behind_gap_s']}")
        more.append(f"{lg}: {d['recommendation']}")
    return "MODELED stick times\n" + "\n".join(rows + [""] + more)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--archive", type=Path, default=Path("data/archive"))
    ap.add_argument("--split", default="fit", choices=("train", "valid", "fit"), help="never the locked test split")
    ap.add_argument("--leagues", default="nfl")
    ap.add_argument("--max-nights", type=int, default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--near-s", type=float, default=NEAR_S)
    ap.add_argument("--redzone", default=",".join(MODES), help="planner sees the RedZone channel: none,ranked,first")
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args()
    rep = run(a.archive, [x for x in a.leagues.split(",") if x in qr.LEAGUES], a.split, a.max_nights, a.seed,
              a.near_s, tuple(a.redzone.split(",")), log=lambda m: print(m, file=sys.stderr))
    if a.json:
        a.json.write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    print(table(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
