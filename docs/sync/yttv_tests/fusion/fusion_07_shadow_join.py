#!/usr/bin/env python3
"""fusion_07_shadow_join: would the fused D have joined YTTV at a dead ball? (shadow scorer, no switching)

Hypothesis: with D_hat from the best available anchors, (a) the on-screen time of a restart is
predictable as real_restart_wall + D_hat(t) within 2 s, and (b) a join policy that enters only inside
[stop + D_hat + 2 sd, restart + D_hat - 2 sd - 3 s] never lands mid-play (Rule #1: 0 mid-play entries).

Procedure:
  A. Restart prediction. Observed screen events = recorded/live ad-pod returns (pod end on screen vs
     the restart play's real wall) plus any other family's result row carrying t_screen + wall_real
     (+ channel, stick). For each event, D_hat = Kalman on the OTHER anchors of that (channel, stick)
     (leave-one-out: anchors within 60 s of the event are excluded), propagated by the session E track.
     err = predicted screen time - observed screen time.
  B. Join decision. Dead-ball windows = timeout/foul -> next live play, from plays.jsonl for games on
     that channel (day plan or --game). For each, the policy decides join/skip from D_hat, sd_hat; a
     join is mid-play if its real-world moment J - D_true falls outside the dead window. D_true is the
     event-observed D when available (live) or the flagged synthetic truth (dry run; real stoppage
     timings, simulated delay), so the decision logic is exercised on real NBA dead-ball lengths.
     The same windows are also scored with the app's prior only (ESPN 29 +- 8) to show what fusion buys.
     Real D_true needs >= 3 precise (sd <= 2 s) observed screen events, else 'WOULD simulate'.
Read-only: it only predicts; nothing is tuned or pressed.

Pass rule: (A) |err| <= 2 s for >= 90% of events (n >= 3); (B) 0 mid-play joins, join rate reported.
"""
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fusion_lib as L  # noqa: E402

ID = "fusion_07_shadow_join"
DWELL = 3.0


def screen_events(B, args):
    ev = []
    for e in L.pod_events(B["samples"], B["sessions"], B["plays"], B["plans"], args.game):
        if not e["ambiguous"]:
            ev.append(dict(channel=e["channel"], stick=e["stick"], t_screen=e["t"], wall_real=e["restart_wall"],
                           ev_kind="pod_return", obs_sd=L.POD_SD))
    for p in L.glob.glob(os.path.join(args.results_dir, "*.jsonl")):
        if os.path.basename(p).startswith("fusion_"):
            continue
        for r in L.read_jsonl(p):
            ts = r.get("t_screen") or r.get("screen_t")
            wr = r.get("wall_real") or r.get("real_wall")
            if isinstance(ts, (int, float)) and isinstance(wr, (int, float)) and not r.get("dry_run"):
                ev.append(dict(channel=L.norm_ch(r.get("channel") or r.get("ch")), stick=L.stick_name(r.get("stick") or r.get("host")),
                               t_screen=ts, wall_real=wr, ev_kind=os.path.basename(p), obs_sd=r.get("sd") or 1.0))
    return [e for e in ev if args.channel == "all" or e["channel"] == L.norm_ch(args.channel)]


def predict(anchors, sessions, ch, st, t, exclude_t=None):
    g = [a for a in anchors if a.channel == ch and a.stick == st and (exclude_t is None or abs(a.t - exclude_t) > 60)]
    if not g:
        return None, None
    k = L.kalman(g, L.prior_for(ch))
    hosts = {v: k_ for k_, v in L.SHORT.items()}
    s = L.session_at(sessions, hosts.get(st, st), ch, t)
    shift = (s.E_at(t) - s.E_land) if s and s.E_at(t) is not None else 0.0
    return k["mu"] + shift, k["sd"]


def dead_windows(B, ch, game=None):
    games = set()
    for g in B["plans"]:
        if L.norm_ch((g.get("source") or {}).get("target")) == ch:
            games.add(str(g["game_id"]))
    if game:
        games = {str(game)}
    by = {}
    for p in B["plays"]:
        if str(p.get("game")) in games:
            by.setdefault(str(p["game"]), []).append(p)
    out = []
    for g, ps in by.items():
        ps.sort(key=lambda p: p["wall"])
        for i, p in enumerate(ps):
            ty = (p.get("type") or "").lower()
            if "timeout" in ty or "foul" in ty:
                nxt = next((q for q in ps[i + 1:] if q["wall"] > p["wall"] + 1 and L._is_live_play(q)
                            and "free throw" not in (q.get("type") or "").lower() and "freethrow" not in (q.get("type") or "").lower()), None)
                if nxt:
                    out.append((g, p["wall"], nxt["wall"], p.get("type")))
    return out


def join_policy(windows, D_hat, sd_hat, D_true):
    joins = mid = skips = 0
    for g, s, r, ty in windows:
        lo, hi = s + D_hat + 2 * sd_hat, r + D_hat - 2 * sd_hat - DWELL
        if hi <= lo:
            skips += 1
            continue
        joins += 1
        J = lo
        real = J - D_true
        if not (s <= real <= r):
            mid += 1
    return dict(windows=len(windows), joins=joins, skips=skips, mid_play=mid,
                join_rate=L.r2(joins / len(windows)) if windows else None)


def main(argv=None):
    args = L.make_parser(ID, __doc__).parse_args(argv)
    out = L.Out(args.out, ID, args)
    B = L.build(args)
    L.header(ID, args, B["info"])
    anchors = L.usable(B["anchors"])
    # A. restart prediction
    errs = []
    for e in screen_events(B, args):
        D, sd = predict(anchors, B["sessions"], e["channel"], e["stick"], e["t_screen"], exclude_t=e["t_screen"])
        if D is None:
            out.write("event", **e, predicted=None, note="no other anchors for LOO prediction")
            print(f"   event {e['ev_kind']} {e['channel']} {e['stick']} {L.fmt_t(e['t_screen'])}: no other anchors (WOULD predict)")
            continue
        err = (e["wall_real"] + D) - e["t_screen"]
        errs.append(err)
        out.write("event", **e, D_hat=L.r2(D), sd_hat=L.r2(sd), err=L.r2(err))
        print(f"   event {e['ev_kind']} {e['channel']} {e['stick']} {L.fmt_t(e['t_screen'])}: D_hat={D:.1f}+-{sd:.1f} err={err:+.1f}s")
    a_ok = len(errs) >= 3 and sum(abs(x) <= 2 for x in errs) / len(errs) >= 0.9
    # B. join decisions
    jrows = []
    sources = [("real", anchors, None)]
    if B["synth"]:
        sources.append(("synthetic", B["synth"], B["truth"]))
    for label, an, truth in sources:
        for (ch, st), g in sorted(L.group(an).items()):
            k = L.kalman(g, L.prior_for(ch))
            wins = dead_windows(B, ch, args.game)
            if truth:
                D_true = truth[(ch, st)]
            else:
                # real truth needs >= 3 PRECISE observed screen events (obs sd <= 2 s); ad-pod returns
                # (sd ~8 s) are far too loose to certify "no mid-play join"
                obs = [e for e in screen_events(B, args) if e["channel"] == ch and e["stick"] == st and e["obs_sd"] <= 2.0]
                D_true = statistics.median([e["t_screen"] - e["wall_real"] for e in obs]) if len(obs) >= 3 else None
            if D_true is None or not wins:
                print(f"   [{label}] {ch} {st}: windows={len(wins)} D_true={'known' if D_true else 'unknown'} -> "
                      f"WOULD simulate joins once both exist (D_hat={k['mu']:.1f}+-{k['sd']:.1f})")
                continue
            res = join_policy(wins, k["mu"], k["sd"], D_true)
            pri = join_policy(wins, L.prior_for(ch)[0], L.prior_for(ch)[1], D_true)
            res["prior_only"] = dict(joins=pri["joins"], mid_play=pri["mid_play"], join_rate=pri["join_rate"])
            row = dict(channel=ch, stick=st, D_hat=L.r2(k["mu"]), sd_hat=L.r2(k["sd"]), D_true=L.r2(D_true), source=label,
                       synthetic=label == "synthetic", **res, pass_=res["mid_play"] == 0)
            out.write("join", **row)
            jrows.append(row)
            print(f"   [{label}] {ch:7s} {st:6s} D_hat={row['D_hat']}+-{row['sd_hat']} D_true={row['D_true']} "
                  f"windows={res['windows']} joins={res['joins']} skips={res['skips']} MID_PLAY={res['mid_play']} "
                  f"join_rate={res['join_rate']}  | app-prior-only policy: joins={pri['joins']} mid_play={pri['mid_play']}")
    real_j = [r for r in jrows if not r["synthetic"]]
    out.write("summary", pass_=a_ok and bool(real_j) and all(r["pass_"] for r in real_j), n_events=len(errs),
              within_2s=L.r2(sum(abs(x) <= 2 for x in errs) / len(errs)) if errs else None,
              mae=L.r2(statistics.mean(abs(x) for x in errs)) if errs else None,
              mid_play_real=sum(r["mid_play"] for r in real_j), mid_play_synth=sum(r["mid_play"] for r in jrows if r["synthetic"]))
    print(f"SUMMARY {ID}: restart events scored={len(errs)} within2s_ok={a_ok}; "
          f"mid-play joins real={sum(r['mid_play'] for r in real_j)} synthetic={sum(r['mid_play'] for r in jrows if r['synthetic'])} -> {args.out}")
    out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
