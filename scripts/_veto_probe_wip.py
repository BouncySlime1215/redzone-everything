"""Probe (scratch, not shipped): why the arbiter refused, joined to value still lost after 180 s catch-up.
usage: veto_audit_probe.py LEAGUE BRAIN OUT.json [KEY=VALUE ...]   (run with cwd = worktree)"""
import bisect, json, sys
from collections import Counter
sys.path.insert(0, ".")
from scripts import veto_audit as va
from redzone.noggin import arbiter, legal
from redzone.contracts import brain as contract

league, brain_name, out_path, *sets = sys.argv[1:]
BASE = {"legal.leave_at_flip": True, "legal.leave_low_leverage": ["nfl", "nba", "nhl", "mlb"], "arbiter.catchup_route": True, "arbiter.last_game_to_live": True}
BASE.update(va.parse_sets(sets))
DET = {}       # (pick, now) -> detail, per night (cleared in charge)
AGG = {"floor": Counter(), "l1_class": Counter(), "late_narrow": Counter(), "margin": Counter(), "n": Counter()}
real_judge, real_charge = va.judge, va.charge_night


def judge(host, pick, cands, now):
    named, arb, vetoes, d = real_judge(host, pick, cands, now)
    a = next((n for n in named if n.startswith("arbiter:")), None)
    if a and not any(n.startswith("legal:") for n in named):
        facts = host.legal_facts(cands, now)
        opts = legal.legal_options(facts)
        p = host._arbiter_params()
        show, musts = host._arbiter_show(cands, now, p), host._arbiter_musts(cands, now, p)
        pool = list(opts)
        l1 = arbiter.lowest_l1_class(pool, show.current_class)
        floor = arbiter.must_show_floor(l1, musts, show.current, now, p)
        full_floor = arbiter.must_show_floor(pool, musts, show.current, now, p)
        me = next((o for o in opts if o.id == f"cut:{pick}"), None)
        ch = arb.option.id if arb.option is not None else None
        sc = host.pick_scores
        DET[(pick, now)] = dict(
            rule=a, why=arb.why, chosen="stay" if ch == "stay" else "other" if ch else "none",
            pick_in_floor=f"cut:{pick}" in floor, pick_in_full_floor=f"cut:{pick}" in full_floor,
            pick_rank=full_floor.get(f"cut:{pick}", (99, None))[0],
            ch_rank=floor.get(ch, (99, None))[0] if ch else 99,
            pick_cls=me.l1_class if me else -1, cur_cls=show.current_class,
            best_cls=min([o.l1_class for o in opts if o.verb != "stay"] + [show.current_class]),
            s_pick=sc.get(f"cut:{pick}", 0.0), s_stay=sc.get("stay", 0.0), s_ch=sc.get(ch, 0.0) if ch else 0.0,
            late=show.late, in_close=pick in show.close, vetoes=len(vetoes),
            lands=host.truth.lands_mid_play(pick, now), mid=host.truth.mid_play_on_screen(host.current, now),
            high=host.truth.high_leverage_on_screen(host.current, now))
    return named, arb, vetoes, d


def bucket(x):
    return 0 if x <= 0 else 3 if x < 3 else 10 if x < 10 else 20 if x < 20 else 99


def charge_night(t, truth, row):
    real_charge(t, truth, row)
    unseen = [m for ms in truth.moments.values() for m in ms if not va.seen(m, t.screen)]
    shown = set(va.route_shown(unseen, t, truth, 180)[0])
    for gid, ms in truth.moments.items():
        log = t.log.get(gid, [])
        times = [e[0] for e in log]
        for m in ms:
            if va.seen(m, t.screen):
                continue
            i = bisect.bisect_right(times, m.at - truth.tune_s - va.PLAY_LEN_S) - 1
            if i < 0 or times[i] < m.at - va.RUN_UP_S:
                continue
            rule = log[i][1]
            if not rule.startswith("arbiter:"):
                continue
            d = DET.get((gid, times[i]))
            after = m not in shown
            name = rule.split(":")[1]
            AGG["n"][f"{name}|lost"] += m.value
            AGG["n"][f"{name}|after180"] += m.value if after else 0
            if d is None or name not in AGG or not after:
                continue
            harm = "landsmid" if d["lands"] else "clean"
            lev = "screen_high" if d["high"] else "screen_mid" if d["mid"] else "screen_dead"
            if name == "floor":
                key = f"chosen={d['chosen']} ch_rank={d['ch_rank']} pick_rank={d['pick_rank']} pick_in_l1floor={d['pick_in_floor']} lostkind={m.kind} gap={bucket(d['s_pick'] - d['s_ch'])}"
            elif name == "l1_class":
                key = f"pick_cls={d['pick_cls']} cur_cls={d['cur_cls']} best={d['best_cls']} why={d['why'].split(':')[0]} chosen={d['chosen']} lostkind={m.kind} gap={bucket(d['s_pick'] - d['s_ch'])}"
            elif name == "late_narrow":
                key = f"cur_cls={d['cur_cls']} pick_cls={d['pick_cls']} chosen={d['chosen']} lostkind={m.kind} gap={bucket(d['s_pick'] - d['s_stay'])}"
            else:
                key = f"gapstay={round(d['s_pick'] - d['s_stay'], 1)} lostkind={m.kind} s_stay={bucket(d['s_stay'])}"
            AGG[name][key] += m.value
            AGG["n"][f"{name}|{harm}"] += m.value
            AGG["n"][f"{name}|{lev}"] += m.value
    DET.clear()


va.judge, va.charge_night = judge, charge_night
row = va.run_one((league, brain_name, BASE, None))
res = {"total_after_180": sum(row["lost_after_catchup"]["180"].values()), "by_rule_180": row["lost_after_catchup"]["180"], "card": row["card"],
       "agg": {k: dict(v.most_common(40)) for k, v in AGG.items()}}
json.dump(res, open(out_path, "w"), indent=1)
print(json.dumps(res, indent=1))
