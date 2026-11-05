"""GI v3 NBA (WV loop cycle 1, prereg /mnt/project-files/wv-loop/prereg-gi-v3-nba.md). Arm G3b (G3a + market price, init z(C1)) vs C0 and C1.
Valid = 2025-26 rolling origin by month; pair-weighted pair agreement; night bootstrap CI. Locked nights not in table.
personal-use, not for commercial release."""
import sys, json, numpy as np, pandas as pd, lightgbm as lgb
SEED = 1720
t = pd.read_parquet('/mnt/project-files/attention-g/gi/nba_volume_games.parquet')
t['night'] = t.night.astype(str); t['season'] = t.season.astype(int)
t = t[t.season <= 2025].sort_values(['night', 'game_id']).reset_index(drop=True)
m = pd.concat([pd.read_csv(f'/mnt/project-files/attention-g/mkt/nba-{x}-market_games.csv') for x in ('2024-25', '2025-26', '2026-27')])
pk = m.drop_duplicates('game_key').set_index('game_key').p_home_pre
t['p_home_pre'] = ('nba:' + t.night + ':' + t.away + '@' + t.home).map(pk)
t['mkt_close'] = -(t.p_home_pre - 0.5).abs()
locked = set(json.load(open('/mnt/project-files/attention-g/locked_nights.json'))['nba'])
assert not set(t.night) & locked
# 1) Elo carried forward per team when missing
for side in 'ha':
    tm = t['home' if side == 'h' else 'away']
    long = pd.concat([pd.DataFrame({'night': t.night, 'tm': t.home, 'elo': t.h_elo}),
                      pd.DataFrame({'night': t.night, 'tm': t.away, 'elo': t.a_elo})]).sort_values('night')
    last = long.dropna().groupby('tm').elo.apply(lambda s: s)  # placeholder
for c in ('h_elo', 'a_elo'):
    tmcol = 'home' if c == 'h_elo' else 'away'
    t[c] = t.groupby(tmcol)[c].transform(lambda s: s.ffill())
t['g_elo_diff_abs'] = t.g_elo_diff_abs.fillna((t.h_elo - t.a_elo).abs())
# 2) early-season shrinkage of standings toward prior-season final value, w = gp/(gp+10), gp ~ 82*month_frac
ST = ['win_pct', 'games_back', 'playoff_odds', 'playin_odds', 'top10_odds', 'div_rank']
gp = 82 * t.month_frac.fillna(0); w = gp / (gp + 10)
for f in ST:
    for side, tmcol in (('h', 'home'), ('a', 'away')):
        c = f'{side}_{f}'
        if c not in t: continue
        long = pd.concat([pd.DataFrame({'season': t.season, 'tm': t.home, 'v': t.get(f'h_{f}')}),
                          pd.DataFrame({'season': t.season, 'tm': t.away, 'v': t.get(f'a_{f}')})])
        fin = long.dropna().groupby(['season', 'tm']).v.last()
        prior = pd.Series([fin.get((s - 1, tm), np.nan) for s, tm in zip(t.season, t[tmcol])], index=t.index)
        prior = prior.fillna(long.v.median())
        t[c] = w * t[c].fillna(prior) + (1 - w) * prior
    if f'h_{f}' in t:
        t[f'max_{f}'] = t[[f'h_{f}', f'a_{f}']].max(axis=1); t[f'sum_{f}'] = t[f'h_{f}'] + t[f'a_{f}']
# 3) team draw = prior-season mean within-night volume rank
long = pd.concat([pd.DataFrame({'season': t.season, 'tm': t.home, 'y': t.y}), pd.DataFrame({'season': t.season, 'tm': t.away, 'y': t.y})])
draw = long.groupby(['season', 'tm']).y.mean()
for side, tmcol in (('h', 'home'), ('a', 'away')):
    t[f'{side}_draw'] = [draw.get((s - 1, tm), np.nan) for s, tm in zip(t.season, t[tmcol])]
t['sum_draw'] = t.h_draw + t.a_draw; t['max_draw'] = t[['h_draw', 'a_draw']].max(axis=1)
c0 = -t.g_elo_diff_abs.fillna(999.0)
t['c0'] = c0
c1 = t.mkt_close.where(t.mkt_close.notna(), np.nan)
c1z = (c1 - c1.mean()) / c1.std();  t['c0z'] = (c0 - c0[c0 > -999].mean()) / c0[c0 > -999].std(); t.loc[c0 <= -999, 'c0z'] = -3
t['c1'] = c1.fillna(-1.0); t['c1z'] = c1z.fillna(t.c0z)
bad = {"game_id", "join", "night", "y", "truth", "volume_usd", "season", "home", "away", "start", "game_type", "nba_game_id", "c0", "c0z", "c1", "c1z", "rel", "month"}
cols = [c for c in t.columns if c not in bad and pd.api.types.is_numeric_dtype(t[c])]
t['rel'] = t.groupby('night').truth.rank(method='first').astype(int) - 1
t['month'] = t.night.str[:6]
P = dict(objective='lambdarank', learning_rate=0.05, num_leaves=15, min_data_in_leaf=40, feature_fraction=0.8,
         bagging_fraction=0.8, bagging_freq=1, seed=SEED, verbose=-1, lambdarank_truncation_level=30,
         label_gain=list(range(64)))
def ds(d, wt=None):
    d = d.sort_values('night'); return lgb.Dataset(d[cols], d.rel, group=d.groupby('night', sort=True).size().to_numpy(),
                                                    init_score=d.c1z, weight=None)
pred = pd.Series(np.nan, index=t.index)
for mo in sorted(t[t.season == 2025].month.unique()):
    tr = t[(t.night < mo + '01')]
    te = t[t.month == mo]
    ns = sorted(tr.night.unique()); cut = ns[int(0.7 * len(ns))]
    a, b = tr[tr.night < cut], tr[tr.night >= cut]
    m = lgb.train(P, ds(a), 200, valid_sets=[ds(b)], callbacks=[lgb.early_stopping(30, verbose=False)])
    pred[te.index] = te.c1z + m.predict(te[cols], num_iteration=m.best_iteration)
va = t[t.season == 2025].assign(g3=pred)
def per_night(d, s):
    out = {}
    for n, g in d.assign(s=s).groupby('night'):
        y, ss = g.truth.to_numpy(), g.s.to_numpy(); i, j = np.triu_indices(len(g), 1); ok = y[i] != y[j]
        if not ok.any(): continue
        c = (np.sign(ss[i] - ss[j])[ok] == np.sign(y[i] - y[j])[ok]) + 0.5 * (ss[i] == ss[j])[ok]
        out[n] = (c.sum(), ok.sum())
    return out
A, B, C = per_night(va, va.g3.to_numpy()), per_night(va, va.c0.to_numpy()), per_night(va, va.c1.to_numpy())
ns = sorted(A); pw = lambda D, k: 100 * sum(D[x][0] for x in k) / sum(D[x][1] for x in k)
rng = np.random.default_rng(SEED); diffs, d1s, d10 = [], [], []
for _ in range(5000):
    k = rng.choice(ns, len(ns)); diffs.append(pw(A, k) - pw(B, k)); d1s.append(pw(A, k) - pw(C, k)); d10.append(pw(C, k) - pw(B, k))
lo, hi = np.percentile(diffs, [2.5, 97.5]); d = pw(A, ns) - pw(B, ns)
ci = lambda x: [round(v, 2) for v in np.percentile(x, [2.5, 97.5])]
res = dict(arm='G3b', nights=len(ns), pairs=int(sum(A[x][1] for x in ns)), g3b=round(pw(A, ns), 2), c0=round(pw(B, ns), 2), c1=round(pw(C, ns), 2),
           g3b_minus_c1=round(pw(A, ns) - pw(C, ns), 2), g3b_minus_c1_ci95=ci(d1s), c1_minus_c0=round(pw(C, ns) - pw(B, ns), 2), c1_minus_c0_ci95=ci(d10),
           diff=round(d, 2), ci95=[round(lo, 2), round(hi, 2)], verdict='PASS' if (lo > 0 and d >= 2) else 'FAIL',
           mean_over_nights=dict(g3b=round(100*np.mean([A[x][0]/A[x][1] for x in ns]), 2), c0=round(100*np.mean([B[x][0]/B[x][1] for x in ns]), 2)),
           by_month={mo: dict(g3b=round(pw(A, [n for n in ns if n.startswith(mo)]), 1), c0=round(pw(B, [n for n in ns if n.startswith(mo)]), 1), c1=round(pw(C, [n for n in ns if n.startswith(mo)]), 1))
                     for mo in sorted({n[:6] for n in ns})}, features=len(cols), seed=SEED,
           tag='personal-use, not for commercial release')
print(json.dumps(res, indent=1))
json.dump(res, open('/mnt/project-files/wv-loop/gi_v3_nba_G3b.json', 'w'), indent=1)
