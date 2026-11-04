"""Scorecard for NBA / NHL (and NFL/MLB) using real archived nights.

    .venv/bin/python scripts/eval_sports.py nba          # every archived NBA night
    .venv/bin/python scripts/eval_sports.py nhl --nights 6 --slates

Real-world messiness (all off by default; same knobs as scripts/eval.py, see backtest.run):
    --true-delay 41      the stream really runs 41 s behind (the app still believes 35)
    --sigma 5            the uncertainty the app states for its delay estimate
    --lag-jitter         each play reaches the feed 3-25 s after it happened (seeded)
    --drop [RATE]        the source goes silent 60-120 s, RATE times per game-minute (0.1)
    --late-plays [FRAC]  this fraction of plays arrive a poll late / out of order (0.15)
    --no-wallclock [FRAC]  this fraction of plays come without a wallclock (0.5)
    --seed N             which random draw of the above
"""
import argparse
import json
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.channels import ChannelMap  # noqa: E402
from redzone.config import Config  # noqa: E402
from redzone.lab import evaldata, splits, sportsgrade  # noqa: E402
from redzone.lab.backtest import Replay, feed_lag_knobs, run  # noqa: E402
from redzone.models import League  # noqa: E402
from redzone.sources import espn  # noqa: E402


# The graded window ends here. The service archives each new night at 5 AM (and refreshes the last
# scoreboards), so an open window slides under any before/after comparison. Move it on purpose:
# the pinned eval dataset (eval-data.lock.json) wins; this is the fallback without one.
EVAL_END = "20261101"


def nights(league: League, n: int, end: str | None = None, split: str = "all"):
    """The last n archived nights, up to `end` (YYYYMMDD) when given: the service archives every
    new night at 5 AM, and an unpinned window would slide under a before/after comparison."""
    arch = ROOT / "data/archive" / league.value
    sbs = sorted(sb for sb in arch.glob("scoreboard_*.json") if not end or sb.stem.split("_")[1] <= str(end))
    keep = splits.nights_for(arch, split, end)
    # [no overfitting] --split grades exactly one time split (every night in it); --nights is ignored then.
    sbs = sbs[-n:] if keep is None else [sb for sb in sbs if sb.stem.split("_")[-1] in keep]
    for sb in sbs:
        evs = json.loads(sb.read_text()).get("events", [])
        reps, targets = [], {}
        for e in evs:
            f = arch / f"summary_{e['id']}.json"
            r = Replay.load(league, f) if f.exists() else None
            if not r:
                continue
            reps.append(r)
            g = espn._parse_event(league, e)
            w = ChannelMap(ROOT / "data/_eval_channels.json").where(g) if g else None
            app = (w.target or "youtubetv:x").split(":")[0] if w else "youtubetv"
            targets[r.gid] = f"{app}:{r.gid}"
        if len(reps) >= 2:
            yield sb.stem.split("_")[-1], reps, targets


def stress_args(ap: argparse.ArgumentParser) -> None:
    """[xleague] The stress knobs, spelled exactly like scripts/eval.py's."""
    ap.add_argument("--true-delay", type=float, default=None)
    ap.add_argument("--sigma", type=float, default=3.0)
    ap.add_argument("--lag-jitter", action="store_true")
    ap.add_argument("--drop", type=float, nargs="?", const=0.1, default=0.0)
    ap.add_argument("--late-plays", type=float, nargs="?", const=0.15, default=0.0)
    ap.add_argument("--no-wallclock", type=float, nargs="?", const=0.5, default=0.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--channel-spread", type=float, default=0.0)   # [per-channel delay] see scripts/eval.py
    ap.add_argument("--history", type=float, default=0.0)
    ap.add_argument("--delay-model", choices=("channel", "legacy"), default=None)
    ap.add_argument("--resync", action="store_true",
                    help="model live Kalman re-sync on every tune (backtest.run resync)")
    ap.add_argument("--break-anchors", action="store_true",
                    help="[auto-anchors] the live filter fed only by on-screen ad breaks (backtest.run anchors; "
                         "needs --set sync.break_anchor.enabled=true)")
    ap.add_argument("--tune-jitter", type=float, default=0.0, metavar="S",
                    help="[auto-anchors] each tune lands U(0, S) s behind the live edge, unknown to the app")
    ap.add_argument("--real-lag", action="store_true",
                    help="[switch timing] plays reach the app after the MEASURED feed lag per league "
                         "(redzone/lab/feed_lag_profile.json), not a fixed 8 s")
    ap.add_argument("--feed-lag", type=float, default=None, metavar="S",
                    help="[switch timing] every play reaches the app S s after it happened (default 8). The "
                         "screen stays 43 s behind real life, so only the warning before it airs changes")
    ap.add_argument("--behind-live", type=float, default=None, metavar="S",
                    help="[time-shift buffer] hold every stream S s behind its live edge: the screen runs "
                         "26 s (a YouTube TV channel) + S behind real life, so the feed arrives S s earlier "
                         "relative to the picture")


def knobs(a: argparse.Namespace) -> dict:
    return dict(true_delay=a.true_delay, sigma=a.sigma, lag_jitter=(3.0, 25.0) if a.lag_jitter else None,
                drop=a.drop, late_plays=a.late_plays, no_wallclock=a.no_wallclock, seed=a.seed,
                channel_spread=getattr(a, "channel_spread", 0.0), history=getattr(a, "history", 0.0),
                delay_model=getattr(a, "delay_model", None), resync=getattr(a, "resync", False),
                anchors=getattr(a, "break_anchors", False), tune_jitter_s=getattr(a, "tune_jitter", 0.0),
                feed_lag_model="measured" if getattr(a, "real_lag", False) else None,
                **feed_lag_knobs(getattr(a, "feed_lag", None), getattr(a, "behind_live", None)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("league")
    ap.add_argument("--nights", type=int, default=14)
    ap.add_argument("--slates", action="store_true")
    ap.add_argument("--step", type=float, default=None)
    ap.add_argument("--end", default=None, help="last night to grade (YYYYMMDD); default eval.<league>_end")
    ap.add_argument("--split", choices=splits.SPLITS, default="all",
                    help="grade one time split of the pinned window (redzone/lab/splits.py); test = LOCKED")
    ap.add_argument("--oracle", action="store_true",
                    help="also solve the hindsight oracle per night (redzone/lab/oracle.py): each line's ceiling")
    ap.add_argument("--pick", type=Path, default=None,
                    help="also roll out a Noggin PICK model (e.g. redzone/noggin/models/pick_nba.v1.txt.gz) on each night")
    ap.add_argument("--pick-margin", type=float, default=0.1)
    ap.add_argument("--oracle-out", type=Path, default=None,
                    help="with --oracle: write each night's oracle schedules + Pick labels to DIR/<league>_<night>.json")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override a config value for this run, e.g. replay.redzone_way=true")
    stress_args(ap)
    a = ap.parse_args()
    lg = League(a.league)
    # The live service rewrites data/config.yaml (measured switch costs, taps): scorecards read a
    # pinned copy when one exists, so a before/after comparison isn't moved by the live night.
    pinned = ROOT / "data/eval_config.yaml"
    cfg = Config(user_path=pinned if pinned.exists() else ROOT / "data/config.yaml")
    if pinned.exists() and (cfg.rejected or not cfg.overrides):
        # Config falls back to defaults when a file doesn't validate (an unknown key, say): a
        # scorecard on silent defaults compares the wrong thing.
        sys.exit(f"{pinned} has settings the app rejects ({cfg.rejected}): refusing to grade on a partial config")
    evaldata.use_shipped_switch_costs(cfg)   # one source of truth for switch costs (contracts/switching.py)
    for kv in a.set:
        key, _, val = kv.partition("=")
        node = cfg.data
        *path, last = key.split(".")
        for part in path:
            node = node.setdefault(part, {})
        node[last] = yaml.safe_load(val)
    mine = set(cfg.get(f"teams.{lg.value}") or ["WSH"])
    total = sportsgrade.Card(lg.value)
    t0 = time.time()
    end = a.end or evaldata.window_end(lg.value, EVAL_END)
    per_night = []   # the unit of evidence for the release gate's night bootstrap (scripts/release_gate.py)
    for day, reps, targets in nights(lg, a.nights, end, a.split):
        rep = run(reps, cfg, mine, targets=targets, step=a.step, **knobs(a))
        # [xleague] Grade against what the screen REALLY showed (true delay), not the app's belief.
        card = sportsgrade.grade(reps, rep.switches, mine, rep.lag_true, 5.0, rep.t0, rep.t1, rep.replayed)
        total.add(card)
        cs = card.cut.summary()
        per_night.append({"night": day, "entries": cs["entries"], "entries_mid_play": cs["entries_mid_play"],
                          "cuts": len(card.cuts), "big": card.big, "big_seen": card.big_seen,
                          "big_seen_10m": card.big_seen_10m, "my_big": card.my_big,
                          "my_big_seen_10m": card.my_big_seen_10m, "crunch_on_replay": card.crunch_on_replay,
                          "big_seen_3m": card.big_seen_3m, "big_seen_2m": card.big_seen_2m,
                          "my_big_seen_3m": card.my_big_seen_3m,
                          # [north star] per-night counts so scripts/northstar.py can bootstrap every line
                          "big_live": card.big_live, "landings": card.cut.landings()[1],
                          "landings_dead": card.cut.landings()[0],
                          "possession_starts": card.cut.possession_starts,
                          "dead_s": round(card.dead, 1), "watched_s": round(card.watched, 1),
                          "crunch": card.crunch, "crunch_live": card.crunch_live,
                          "my_crunch": card.my_crunch, "my_crunch_live": card.my_crunch_live,
                          "sync_honesty": rep.sync_honesty})
        if a.oracle:
            from redzone.lab import oracle as O
            n = O.Night(reps, mine, rep.lag_true, 5.0, rep.t0, rep.t1)
            import numpy as np
            tab = O.tables(n, np.arange(n.t0, n.t1, O.STEP_S))
            per_night[-1]["oracle"] = {}
            scheds = {}
            for obj in O.OBJECTIVES:
                osw = O.solve(n, obj, tab)
                scheds[obj] = osw
                oc = O.grade(n, osw)
                ocs = oc.cut.summary()
                per_night[-1]["oracle"][obj] = {
                    "big": oc.big, "big_live": oc.big_live, "crunch": oc.crunch, "crunch_live": oc.crunch_live,
                    "my_crunch": oc.my_crunch, "my_crunch_live": oc.my_crunch_live, "mid_play_cuts": len(oc.cuts),
                    "entries": ocs["entries"], "entries_mid_play": ocs["entries_mid_play"],
                    "dead_s": round(oc.dead, 1), "watched_s": round(oc.watched, 1), "switches": oc.switches}
            if a.oracle_out:
                # Pick labels: the combined oracle's game at every 5 s step (the one picked, also while black)
                steps = np.arange(n.t0, n.t1, O.STEP_S)
                gi, on, cur = {r.gid: r for r in reps}, [], None
                sw = sorted(scheds["combined"], key=lambda x: x.t)
                j = 0
                for t in steps:
                    while j < len(sw) and sw[j].t <= t:
                        cur, j = sw[j].to, j + 1
                    on.append(cur if cur in gi else None)
                a.oracle_out.mkdir(parents=True, exist_ok=True)
                (a.oracle_out / f"{lg.value}_{day}.json").write_text(json.dumps({
                    "night": day, "league": lg.value, "t0": n.t0, "step_s": O.STEP_S, "tune_s": n.tune_s,
                    "schedules": {k: [dict(t=x.t, frm=x.frm, to=x.to) for x in v] for k, v in scheds.items()},
                    "pick_labels": on}))
        if a.pick:
            import numpy as np

            from redzone.lab import oracle as O
            from redzone.noggin import pick as PK
            booster = PK.load_booster(a.pick)
            n = O.Night(reps, mine, rep.lag_true, 5.0, rep.t0, rep.t1)
            steps = np.arange(n.t0, n.t1, O.STEP_S)
            tab = O.tables(n, steps)
            X = PK.features_for_night(reps, tab, steps, rep.lag_true, mine)
            G, K, F = X.shape
            sc = booster.predict(X.reshape(-1, F)).reshape(G, K)
            psw = PK.rollout(sc, X, reps, steps, 5.0, a.pick_margin)
            per_night[-1]["pick"] = O.counts(O.grade(n, psw))
            per_night[-1]["app_sg"] = O.counts(card)
        if a.slates:
            print(" ", day, json.dumps(card.summary()))
            for t, what in card.cuts[:3]:
                print("     CUT", round(t), what)
            for t, kind, what in card.cut.mid_entries[:3]:
                print("     MID-PLAY ENTRY", round(t), kind, what)
    out = total.summary()
    out["seconds"] = round(time.time() - t0)
    out["eval_data"] = evaldata.stamp()
    out["eval_end"] = end
    out["per_night"] = per_night
    if a.pick:
        from redzone.lab import oracle as O
        out["pick"] = O.aggregate([x["pick"] for x in per_night if "pick" in x])
        out["app_sg"] = O.aggregate([x["app_sg"] for x in per_night if "app_sg" in x])
    if a.oracle:
        out["oracle"] = {}
        for obj in {k for x in per_night for k in x.get("oracle", {})}:
            tot = {}
            for x in per_night:
                for k, v in x.get("oracle", {}).get(obj, {}).items():
                    tot[k] = tot.get(k, 0) + v
            tot["big_live_pct"] = round(100 * tot["big_live"] / max(1, tot["big"]), 1)
            tot["crunch_live_pct"] = round(100 * tot["crunch_live"] / max(1, tot["crunch"]), 1)
            tot["dead_air_pct"] = round(100 * tot["dead_s"] / max(1, tot["watched_s"]), 1)
            out["oracle"][obj] = tot
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
