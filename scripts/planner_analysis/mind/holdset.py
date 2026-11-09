"""How much of the hold-set choice can any model win? Dev nights only (locked and fresh excluded). Read only, no training.
Every 180 s on each multi-game slate, for each live game: y = tier-weighted must-see in the NEXT 180 s (past the look-ahead).
Compare picking top-K games by: random, closest-score hand rule, a shrunken state table fit on the older dev nights, hindsight."""
import sys, json, collections, random, importlib.util, io, contextlib, os, math
import numpy as np
HERE = os.path.dirname(__file__)
src = open(os.path.join(HERE, '..', 'product', 'rates.py')).read().split("FN={'nfl'")[0]
ns = {}
exec(compile(src, 'rates_head', 'exec'), ns)
games, ts, clk = ns['games'], ns['ts'], ns['clk']
FN = {'nfl': ns['football'], 'nba': ns['hoops'], 'nhl': ns['hockey']}
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


def build(l):
    rows = collections.defaultdict(list)
    for n, gid, d in games(l):
        E = []; J = collections.defaultdict(list)
        ta = sorted(FN[l](l, gid, d, E, J))
        if len(ta) < 30 or not (1.2 * 3600 < ta[-1] - ta[0] < 7 * 3600): continue
        rows[n].append((gid, ta[0], ta[-1], states(l, d), sorted((e[0], e[2]) for e in E if e[0])))
    return rows


def decisions(l, night):
    t0 = min(g[1] for g in night); t1 = max(g[2] for g in night)
    out = []
    t = t0 + 300
    while t < t1 - H:
        live = []
        for gid, s, e, st, ev in night:
            if not (s + 120 <= t <= e - H): continue
            i = np.searchsorted([x[0] for x in st], t) - 1
            if i < 0: continue
            y = sum(WEIGHTS[tier] for w, tier in ev if t < w <= t + H)
            t1c = sum(1 for w, tier in ev if t < w <= t + 60 and tier == 1)
            live.append((gid, key(l, st[i], s, e, t), abs(st[i][3]), y, t1c))
        if len(live) >= 2: out.append(live)
        t += H
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


def main():
    R = {}
    for l in FN:
        rows = build(l)
        nights = sorted(rows)
        cut = int(len(nights) * 0.6)
        tr, te = nights[:cut], nights[cut:]
        Dtr = [dd for n in tr for dd in decisions(l, rows[n])]
        by_te = {n: decisions(l, rows[n]) for n in te}
        Dte = [dd for n in te for dd in by_te[n]]
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
        for K in (2, 4):
            rnd_shares = []
            for seed in range(30):
                g, t, b, n = capture(Dte, lambda x: 0.0, K, random.Random(seed))
                rnd_shares.append(g / t if t else float('nan'))
            g_h, t_h, b_h, n = capture(Dte, lambda x: -x[2], K, random.Random(0))
            g_t, t_t, b_t, _ = capture(Dte, lambda x: pred(x[1]), K, random.Random(0))
            # night-cluster bootstrap for table minus hand and table minus random
            nts = [n2 for n2 in te if by_te[n2]]
            diffs = []
            rng = random.Random(1)
            cell = {n2: (capture(by_te[n2], lambda x: pred(x[1]), K, random.Random(0)), capture(by_te[n2], lambda x: -x[2], K, random.Random(0))) for n2 in nts}
            for _ in range(2000):
                samp = [rng.choice(nts) for _ in nts]
                a = sum(cell[s][0][0] for s in samp); b_ = sum(cell[s][1][0] for s in samp); t_ = sum(cell[s][0][1] for s in samp)
                if t_: diffs.append((a - b_) / t_ * 100)
            res[f'K{K}'] = {'decisions': n, 'random_pct': round(float(np.nanmean(rnd_shares)) * 100, 1) if t_h else None,
                            'closest_score_pct': round(g_h / t_h * 100, 1) if t_h else None, 'state_table_pct': round(g_t / t_t * 100, 1) if t_t else None,
                            'hindsight_pct': round(b_t / t_t * 100, 1) if t_t else None,
                            'table_minus_hand_ci95': [round(float(np.percentile(diffs, 2.5)), 1), round(float(np.percentile(diffs, 97.5)), 1)] if diffs else None}
        R[l] = res
        print(l, json.dumps(res)); sys.stdout.flush()
    json.dump(R, open(sys.argv[1], 'w'), indent=1)


main()
