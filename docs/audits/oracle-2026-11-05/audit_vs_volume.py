"""Non-circular check: per-game oracle value (v1 grader rules vs v2 WP-swing reward) against human attention =
Polymarket per-game volume (crew D), within-night pair agreement. NBA 2024-25 + 2025-26 (non-locked nights).
personal-use, not for commercial release."""
import json, numpy as np, pandas as pd, pyarrow.parquet as pq
locked = set(json.load(open('/mnt/project-files/attention-g/locked_nights.json'))['nba'])
v = pd.read_parquet('/mnt/project-files/attention-g/gi/nba_volume_games.parquet')[['nba_game_id', 'night', 'truth']]
v = v[~v.night.astype(str).isin(locked)].dropna(subset=['nba_game_id'])
ids = set(v.nba_game_id.astype(str).str.zfill(10))
p = pq.read_table('/mnt/project-files/data-a/wp_swings/nba/plays.parquet',
                  columns=['game_id', 'seq', 'period', 'clock_s', 'home_score', 'away_score', 'wp', 'surprise', 'suspense'],
                  filters=[('season', '>=', 2024)]).to_pandas()
p['gid'] = p.game_id.astype(str).str.zfill(10); p = p[p.gid.isin(ids)].sort_values(['gid', 'seq'])
g = p.groupby('gid')
pts = (p.home_score + p.away_score) - (g.home_score.shift() + g.away_score.shift())
mprev = (g.home_score.shift() - g.away_score.shift()).abs(); m = (p.home_score - p.away_score).abs()
late = (p.period == 4) & (p.clock_s <= 120)
p['v1'] = 3.0*(late & (pts >= 2) & (mprev <= 5)) + 1.0*((late & (m <= 5)) | (p.period >= 5))
decided = (g.wp.shift() - .5).abs() >= 0.45
p['v2'] = np.where(decided, 0.0, p.surprise)                     # WP swing, garbage time zero
p['v2s'] = p.v2 * (1 + p.suspense.fillna(0))                     # + suspense weight
gm = p.groupby('gid')[['v1', 'v2', 'v2s']].sum()
fm = g.apply(lambda d: abs(d.home_score.iloc[-1] - d.away_score.iloc[-1]))
gm['closeness'] = -fm
v['gid'] = v.nba_game_id.astype(str).str.zfill(10); t = v.merge(gm, left_on='gid', right_index=True)
def pa(df, s):
    acc = n = 0
    for _, d in df.groupby('night'):
        if len(d) < 2: continue
        a, c = d[s].to_numpy(), d.truth.to_numpy(); i, j = np.triu_indices(len(d), 1); ok = c[i] != c[j]
        acc += ((np.sign(a[i]-a[j]) == np.sign(c[i]-c[j])) + .5*(a[i] == a[j]))[ok].sum(); n += ok.sum()
    return round(100*acc/max(n, 1), 1), int(n)
out = dict(games=len(t), nights=int(t.night.nunique()), vs_volume={k: pa(t, k) for k in ('v1', 'v2', 'v2s', 'closeness')},
           note='pair-weighted within-night agreement with log volume; (score, pairs)', tag='personal-use, not for commercial release')
rng = np.random.default_rng(0); ns = t.night.unique(); d = []
for _ in range(1000):
    s = t[t.night.isin(rng.choice(ns, len(ns)))]  # approx (unique nights)
    d.append(pa(s, 'v2')[0] - pa(s, 'v1')[0])
out['v2_minus_v1_ci95_approx'] = [round(x, 1) for x in np.percentile(d, [2.5, 97.5])]
print(json.dumps(out, indent=1)); json.dump(out, open('/mnt/project-files/wv-loop/oracle-audit/audit_vs_volume.json', 'w'), indent=1)
