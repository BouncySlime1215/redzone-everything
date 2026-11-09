"""How much of the hold-set choice can any model win? Dev nights only, read only, no training.

    python scripts/holdset.py --archive data/archive --out holdset.json [--leagues nfl,nba,nhl]

Every 180 s on each multi-game slate, for each live game: y = tier-weighted must-see in the NEXT 180 s (past the
look-ahead). Compare picking the top-K games by: random, closest-score hand rule, a shrunken state table fit on the
older dev nights, hindsight. Needs data/archive/<league>/summary_<id>.json plus the scoreboards that date them.

Dev-only: a summary file is opened only if its game's night (from the scoreboards) is on the fit side of the split
(`DevOnly` in scripts/coverage_audit.py). Locked and fresh games are never opened. The 60/40 train/test cut below is
inside the dev nights, by whole night, oldest first.

Why: the planner's hold-set ceiling (the-mind, step 1) lived in a scratch script that read every night and reached into
another script by text surgery; this is the same measurement as a repo tool that cannot read a locked night.
Night here is the scoreboard (US Eastern) date, the split's own, so numbers differ a little from the planner's.
"""
import argparse
import collections
import datetime as dt
import json
import random
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, ROOT.as_posix())

from scripts.coverage_audit import DevOnly, LockedNightError  # noqa: E402

A = None   # archive root, set by main()
def ts(s):
    if not s: return None
    s=s.replace('Z','+00:00')
    try: return dt.datetime.fromisoformat(s).timestamp()
    except: return None
def clk(p):
    v=(p.get('clock') or {}).get('displayValue') or ''
    m=re.match(r'^(\d+):(\d+)',v)
    if m: return int(m[1])*60+int(m[2])
    try: return float(v)
    except: return None
def wpmap(d):
    m={}; prev=None
    for x in d.get('winprobability') or []:
        h=x.get('homeWinPercentage'); pid=str(x.get('playId'))
        if h is None: continue
        if prev is not None: m[pid]=abs(h-prev)
        prev=h
    return m
def ev(out,g,t,tier,kind):
    if t: out.append((t,g,tier,kind))

def football(l,gid,d,E,J):
    wp=wpmap(d); drives=(d.get('drives') or {}).get('previous') or []
    ts_all=[]; 
    for dr in drives:
        rz=False
        for p in dr.get('plays') or []:
            t=ts(p.get('wallclock')); ty=((p.get('type') or {}).get('text') or ''); tx=(p.get('text') or '').lower()
            if t: ts_all.append(t)
            s=p.get('start') or {}; down=s.get('down') or 0; y=s.get('yardsToEndzone')
            nopl='no play' in tx
            istd=('touchdown' in ty.lower()) or (p.get('scoringPlay') and 'touchdown' in tx and not nopl)
            if istd: ev(E,gid,t,1,'td')
            tl=ty.lower()
            if not nopl and ('interception' in tl or 'fumble recovery (opponent)' in tl or 'blocked' in tl or ty=='Safety' or 'muffed' in tl):
                ev(E,gid,t,1,'turnover')
            if not nopl and 'kickoff' not in tl and 'punt' not in tl and 'timeout' not in tl and down and y and y<=20 and not rz:
                rz=True; ev(E,gid,t,2,'rz_trip')
            if not nopl and 'field goal' in tl: ev(E,gid,t,2,'fg')
            if down==4 and not nopl and 'punt' not in tl and 'field goal' not in tl and 'timeout' not in tl and 'penalty' not in tl and 'kickoff' not in tl: ev(E,gid,t,2,'4th_try')
            gain=p.get('statYardage') or 0
            if not nopl and gain>=10 and not istd and any(k in tl for k in('rush','pass')): ev(E,gid,t,3,'gain10')
            if not nopl and down==3 and (s.get('distance') or 0)>=5 and gain>=(s.get('distance') or 99): ev(E,gid,t,3,'big3rd')
            if wp.get(str(p.get('id')),0)>=0.05 and not istd: ev(E,gid,t,3,'wp5')
    return ts_all
SC_DEAD=('Foul','Free Throw','Timeout','TimeOut','Out of Bounds','Substitution','End Period','Violation','Jumpball','Dead Ball','Kicked','Challenge','Traveling','Double Dribble','Technical','Delay')
def hoops(l,gid,d,E,J):
    per2 = l=='cbb'  # halves
    wp=wpmap(d); plays=d.get('plays') or []
    ts_all=[]; lastdead=None; lastshot=None; prevh=prevA=0; lead=0; run=[0,0]; runfired=[False,False]
    for p in plays:
        t=ts(p.get('wallclock')); 
        if not t: continue
        ts_all.append(t); ty=(p.get('type') or {}).get('text') or ''; h=p.get('homeScore') or 0; a=p.get('awayScore') or 0
        per=(p.get('period') or {}).get('number') or 1; c=clk(p)
        lastp = (per>=2 if per2 else per>=4); ot=(per>=3 if per2 else per>=5)
        late = ot or (lastp and c is not None and c<=300)
        margin_before=abs(prevh-prevA)
        sc=p.get('scoringPlay'); isfg=bool(p.get('shootingPlay')) and 'free throw' not in ty.lower() and 'freethrow' not in ty.lower()
        lc=False
        if sc and h+a>prevh+prevA:
            nl=(h>a)-(h<a)
            if nl!=lead: lc=True
            lead=nl
            side=0 if h>prevh else 1
            run[side]+=h+a-prevh-prevA; run[1-side]=0; runfired[1-side]=False
            if run[side]>=8 and not runfired[side]: runfired[side]=True; ev(E,gid,t,2,'run8')
        secondhalf=(per>=2 if per2 else per>=3)
        clutch_ev=False
        if sc and isfg and late and margin_before<=5:
            ev(E,gid,t,1,'clutch_fg'); clutch_ev=True
            if lastdead: J[('dead_to_clutch')].append(t-lastdead)
            if lastshot: J[('shot_to_clutch')].append(t-lastshot)
        elif sc and isfg and lc and secondhalf: ev(E,gid,t,2,'lead_change')
        if sc and isfg and 'dunk' in ty.lower(): ev(E,gid,t,3,'dunk')
        if sc and isfg and (p.get('scoreValue') or 0)==3: ev(E,gid,t,3,'three')
        w=wp.get(str(p.get('id')),0)
        if w>=0.10 and not clutch_ev: ev(E,gid,t,2,'wp10')
        elif w>=0.05 and not clutch_ev: ev(E,gid,t,3,'wp5')
        if late and margin_before<=5 and any(k in ty for k in ('Turnover','Steal')): ev(E,gid,t,3,'clutch_tov')
        if any(k in ty for k in SC_DEAD) or 'Foul' in ty: lastdead=t; J['dead_events'].append(1)
        if bool(p.get('shootingPlay')) or 'Rebound' in ty: lastshot=t
        prevh,prevA=h,a
    return ts_all
PEN_EXC=('Fighting','Misconduct','Instigator','Delay Game','Shootout')
def hockey(l,gid,d,E,J):
    plays=d.get('plays') or []; ts_all=[]; lastfo=None; pens=collections.defaultdict(list); prev=(0,0)
    goals=[]
    for p in plays:
        t=ts(p.get('wallclock'))
        if not t: continue
        ts_all.append(t); ty=(p.get('type') or {}).get('text') or ''; per=(p.get('period') or {}).get('number') or 1; c=clk(p)
        if ty=='Face Off': lastfo=t; J['faceoffs'].append(1)
        if ty=='Stoppage': J['stoppages'].append(1)
        if ty=='Goal':
            if lastfo: J['fo_to_goal'].append(t-lastfo)
            ev(E,gid,t,1,'goal')
            if per>=4: J['ot_goals'].append(1)
        elif ty in('Period Start','Period End','End of Game','Shot','Blocked','Missed','Hit','Giveaway','Takeaway','Stoppage','Face Off'):
            if ty=='Shot': ev(E,gid,t,3,'sog')
        elif ty=='Fighting': ev(E,gid,t,3,'fight')
        elif 'Penalty Shot' in ty or 'on Breakaway' in ty: ev(E,gid,t,1,'penalty_shot')
        elif not any(k in ty for k in PEN_EXC) and p.get('team'):
            pens[(per,c)].append((t,(p.get('team') or {}).get('id')))
    for k,v in pens.items():
        teams={x[1] for x in v}
        if len(v)==1 or len(teams)==1: ev(E,gid,v[0][0],2,'pp_start')
        else:
            J['coincidental'].append(1)
    # late one-goal: goals counted; flag windows via 'late1' on first play in last 2 min of P3 with margin<=1 or any OT start
    fired=set()
    for p in plays:
        t=ts(p.get('wallclock')); per=(p.get('period') or {}).get('number') or 1; c=clk(p)
        if t and per>=3 and c is not None and per not in fired:
            left=1200-c if per==3 else None
            m=abs((p.get('homeScore') or 0)-(p.get('awayScore') or 0))
            if per==3 and left is not None and left<=120 and m<=1: ev(E,gid,t,2,'late_1goal'); fired.add(per)
            if per>=4: ev(E,gid,t,2,'ot_start'); fired.add(per)
    return ts_all
FN = {'nfl': football, 'nba': hoops, 'cbb': hoops, 'nhl': hockey}   # hoops() reads the two college halves when l == 'cbb'

def games(l, only=None):
    """(night, game id, summary) for dev-night games only; locked and fresh files are never opened.
    only: a set of nights to read (still dev nights only: the filter runs first)."""
    dev = DevOnly(A, l)
    for gid, n in sorted(dev.game_ids().items(), key=lambda x: (x[1], x[0])):
        if only is not None and n not in only: continue
        f = Path(A) / l / f"summary_{gid}.json"
        if f.is_file():
            yield n, gid, json.loads(f.read_text())

WEIGHTS = {1: 5.0, 2: 2.0, 3: 0.5}
H = 180.0


def states(l, d):
    """(wall, period, clock_s, margin, fieldpos) for each play in order."""
    out = []
    if l == 'nfl':
        for dr in (d.get('drives') or {}).get('previous') or []:
            for p in dr.get('plays') or []:
                t = ts(p.get('wallclock'))
                if not t: continue
                e = p.get('end') or {}
                out.append((t, (p.get('period') or {}).get('number') or 1, clk(p), (p.get('homeScore') or 0) - (p.get('awayScore') or 0), e.get('yardsToEndzone')))
    else:
        for p in d.get('plays') or []:
            t = ts(p.get('wallclock'))
            if not t: continue
            out.append((t, (p.get('period') or {}).get('number') or 1, clk(p), (p.get('homeScore') or 0) - (p.get('awayScore') or 0), None))
    out.sort(key=lambda x: x[0])
    return out


def key(l, st, t0, t1, t):
    per, c, m, y = st[1], st[2], abs(st[3]), st[4]
    late = min(per, 5 if l != 'nhl' else 4)
    cb = 3 if c is None else (0 if c <= 120 else 1 if c <= 300 else 2)
    mb = 0 if m <= 3 else 1 if m <= 8 else 2 if m <= 16 else 3
    if l == 'nhl': mb = min(m, 3)
    if l == 'nfl':
        fb = 3 if y is None else (0 if y <= 20 else 1 if y <= 40 else 2)
        return (late, cb, mb, fb)
    return (late, cb, mb, 0)


def game_row(l, gid, d):
    """(gid, first play, last play, per-play states, [(wall, tier)] events) for one summary-shaped game, or None if it is too thin."""
    E = []; J = collections.defaultdict(list)
    ta = sorted(FN[l](l, gid, d, E, J))
    if len(ta) < 30 or not (1.2 * 3600 < ta[-1] - ta[0] < 7 * 3600): return None
    return (gid, ta[0], ta[-1], states(l, d), sorted((e[0], e[2]) for e in E if e[0]))


def build(l):
    rows = collections.defaultdict(list)
    for n, gid, d in games(l):
        r = game_row(l, gid, d)
        if r: rows[n].append(r)
    return rows


def decision_times(night):
    """The 180 s decision clock of one night: from 300 s after the first game starts to the look-ahead before the last ends."""
    t0 = min(g[1] for g in night); t1 = max(g[2] for g in night)
    out = []
    t = t0 + 300
    while t < t1 - H:
        out.append(t)
        t += H
    return out


def decisions(l, night, ranks=None):
    """One list per decision with 2+ live games: (gid, state key, |margin|, y, tier-1 in 60 s[, planner score]).
    ranks: {decision time: {gid: score}} from a baseline that sees the live state (the real standby.Planner);
    a game it lists no score for gets -inf (the hidden stick would not hold it). Without ranks the tuples have 5 fields."""
    out = []
    for t in decision_times(night):
        live = []
        for gid, s, e, st, ev in night:
            if not (s + 120 <= t <= e - H): continue
            i = np.searchsorted([x[0] for x in st], t) - 1
            if i < 0: continue
            y = sum(WEIGHTS[tier] for w, tier in ev if t < w <= t + H)
            t1c = sum(1 for w, tier in ev if t < w <= t + 60 and tier == 1)
            row = (gid, key(l, st[i], s, e, t), abs(st[i][3]), y, t1c)
            if ranks is not None: row += (ranks.get(t, {}).get(gid, float('-inf')),)
            live.append(row)
        if len(live) >= 2: out.append(live)
    return out


def fit(train, k_shrink=20.0):
    g = [x for dec in train for x in dec]
    glob = np.mean([x[3] for x in g])
    tot = collections.defaultdict(lambda: [0.0, 0])
    for x in g:
        for lev in range(1, 5):
            ky = (lev, x[1][:lev]); tot[ky][0] += x[3]; tot[ky][1] += 1
    def pred(kx):
        v = glob
        for lev in range(1, 5):
            s, c = tot.get((lev, kx[:lev]), (0.0, 0))
            v = (s + k_shrink * v) / (c + k_shrink)
        return v
    return pred, glob


def capture(tests, score, K, rnd=None):
    """share of the available y captured by holding the top-K by score, among decisions with more than K live."""
    got = tot = best = 0.0
    n = 0
    for dec in tests:
        if len(dec) <= K: continue
        n += 1
        d = list(dec)
        if rnd is not None: rnd.shuffle(d)
        d.sort(key=score, reverse=True)
        got += sum(x[3] for x in d[:K]); tot += sum(x[3] for x in dec)
        best += sum(sorted((x[3] for x in dec), reverse=True)[:K])
    return got, tot, best, n


KS = (2, 4, 8)


def report(l, rows, ranker=None):
    """The measurement for one league: 60/40 by whole night, oldest fit. ranker(l, night, night_rows) -> ranks for decisions(), or None."""
    nights = sorted(rows)
    cut = int(len(nights) * 0.6)
    tr, te = nights[:cut], nights[cut:]
    Dtr = [dd for n in tr for dd in decisions(l, rows[n])]
    ranks = {n: ranker(l, n, rows[n]) for n in te} if ranker else {}
    by_te = {n: decisions(l, rows[n], ranks.get(n)) for n in te}
    Dte = [dd for n in te for dd in by_te[n]]
    have_pl = bool(ranker) and any(len(x) > 5 for d in Dte for x in d)
    res = {'nights_train': len(tr), 'nights_test': len(te), 'games': sum(len(rows[n]) for n in nights), 'decisions_test': len(Dte),
           'rows_train': sum(len(d) for d in Dtr), 'slate_size_median': float(np.median([len(d) for d in Dte])) if Dte else None}
    pred, glob = fit(Dtr)
    ys = np.array([x[3] for d in Dte for x in d]); ps = np.array([pred(x[1]) for d in Dte for x in d])
    res['r2_table'] = round(float(1 - ((ys - ps) ** 2).sum() / ((ys - ys.mean()) ** 2).sum()), 4)
    res['y_mean'] = round(float(ys.mean()), 2); res['y_var'] = round(float(ys.var()), 2); res['pred_var'] = round(float(ps.var()), 3)
    res['y_zero_pct'] = round(float((ys == 0).mean() * 100), 1)
    # learning curve: fit on the newest fraction of train nights, test nights fixed
    lc = {}
    for fr in (0.25, 0.5, 1.0):
        sub = tr[-max(1, int(len(tr) * fr)):]
        D = [dd for n in sub for dd in decisions(l, rows[n])]
        p2, _ = fit(D)
        ps2 = np.array([p2(x[1]) for d in Dte for x in d])
        lc[str(fr)] = {'nights': len(sub), 'r2': round(float(1 - ((ys - ps2) ** 2).sum() / ((ys - ys.mean()) ** 2).sum()), 4)}
    res['learning_curve'] = lc
    for K in KS:
        rnd_shares = []
        for seed in range(30):
            g, t, b, n = capture(Dte, lambda x: 0.0, K, random.Random(seed))
            rnd_shares.append(g / t if t else float('nan'))
        g_h, t_h, b_h, n = capture(Dte, lambda x: -x[2], K, random.Random(0))
        g_t, t_t, b_t, _ = capture(Dte, lambda x: pred(x[1]), K, random.Random(0))
        cell_keys = {'hand': lambda x: -x[2], 'table': lambda x: pred(x[1])}
        if have_pl:
            g_p, t_p, _, _ = capture(Dte, lambda x: x[5], K, random.Random(0))
            cell_keys['planner'] = lambda x: x[5]
        # night-cluster bootstrap: table minus each baseline, in points of available value
        nts = [n2 for n2 in te if any(len(dd) > K for dd in by_te[n2])]
        cell = {n2: {k: capture(by_te[n2], f, K, random.Random(0)) for k, f in cell_keys.items()} for n2 in nts}

        def ci(a_key, b_key):
            diffs, rng = [], random.Random(1)
            for _ in range(2000):
                samp = [rng.choice(nts) for _ in nts]
                a = sum(cell[s][a_key][0] for s in samp); b_ = sum(cell[s][b_key][0] for s in samp); t_ = sum(cell[s][a_key][1] for s in samp)
                if t_: diffs.append((a - b_) / t_ * 100)
            return [round(float(np.percentile(diffs, 2.5)), 1), round(float(np.percentile(diffs, 97.5)), 1)] if diffs else None
        pct = lambda g, t: round(g / t * 100, 1) if t else None
        res[f'K{K}'] = {'decisions': n, 'nights': len(nts), 'random_pct': round(float(np.nanmean(rnd_shares)) * 100, 1) if t_h else None,
                        'closest_score_pct': pct(g_h, t_h), 'state_table_pct': pct(g_t, t_t),
                        'planner_pct': pct(g_p, t_p) if have_pl else None,
                        'hindsight_pct': pct(b_t, t_t),
                        'table_minus_hand_ci95': ci('table', 'hand'),
                        'table_minus_planner_ci95': ci('table', 'planner') if have_pl else None}
    return res


def table(R):
    """Markdown: one table per league, a row per method, a column per K."""
    names = [('state_table_pct', 'state table'), ('closest_score_pct', 'closest score'), ('random_pct', 'random'),
             ('planner_pct', 'real Planner'), ('hindsight_pct', 'hindsight')]
    out = []
    for l, res in R.items():
        ks = [k for k in (f'K{K}' for K in KS) if k in res]
        out.append(f"### {l}: {res['nights_test']} test nights ({res['nights_train']} fit), {res['games']} games\n")
        out.append('| method | ' + ' | '.join(f"{k} ({res[k]['decisions']} decisions, {res[k]['nights']} nights)" for k in ks) + ' |')
        out.append('|---|' + '---|' * len(ks))
        for key_, name in names:
            out.append(f'| {name} | ' + ' | '.join(('n/a' if res[k].get(key_) is None else f"{res[k][key_]:.1f}") for k in ks) + ' |')
        out.append('')
    return '\n'.join(out)


def main(argv=None, ranker_factory=None):
    global A
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--archive', required=True, type=Path)
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--leagues', default='nfl,nba,nhl')
    args = ap.parse_args(argv)
    A = args.archive
    R = {}
    for l in [x for x in args.leagues.split(',') if x in FN]:
        try:
            rows = build(l)
        except LockedNightError as e:
            print(l, 'REFUSED', e)
            continue
        res = report(l, rows, ranker_factory(l) if ranker_factory else None)
        R[l] = res
        print(l, json.dumps(res)); sys.stdout.flush()
    args.out.write_text(json.dumps(R, indent=1))
    return R


if __name__ == '__main__':
    main()
