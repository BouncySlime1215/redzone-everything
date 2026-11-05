"""Oracle audit, step 1 (WP-swing signal) + step 2 (does the NBA grader favor closest-game?).
Reads Data A derived per-play WP (data-a/wp_swings), regular-season 2021-2024 only, locked nights excluded.
Re-implements the grader's NBA/NFL big + crunch rules on score/clock/period (redzone/grading.py is_big/is_crunch,
crunch.graded rows). Outputs aggregates only. personal-use, not for commercial release."""
import json, numpy as np, pandas as pd, pyarrow.parquet as pq
OUT = '/mnt/project-files/wv-loop/oracle-audit/'
locked = json.load(open('/mnt/project-files/attention-g/locked_nights.json'))
res = {}
def pair_agree(df, score, truth):
    acc, n = 0.0, 0
    for _, d in df.groupby('night'):
        if len(d) < 2: continue
        y, s = d[truth].to_numpy(), d[score].to_numpy(); i, j = np.triu_indices(len(d), 1); ok = y[i] != y[j]
        if not ok.any(): continue
        c = (np.sign(s[i]-s[j])[ok] == np.sign(y[i]-y[j])[ok]) + 0.5*(s[i] == s[j])[ok]
        acc += c.sum(); n += ok.sum()
    return round(100*acc/max(n, 1), 1)
for lg in ('nba', 'nfl'):
    cols = ['game_id', 'night', 'season', 'seq', 'period', 'home_score' if lg == 'nba' else 'game_seconds_remaining',
            'wp', 'surprise', 'playoffs'] + (['clock_s', 'away_score'] if lg == 'nba' else [])
    p = pq.read_table(f'/mnt/project-files/data-a/wp_swings/{lg}/plays.parquet', columns=cols,
                      filters=[('season', '>=', 2021), ('season', '<=', 2024), ('playoffs', '=', False)]).to_pandas()
    p = p[~p.night.astype(str).isin(set(locked.get(lg, [])))].sort_values(['game_id', 'seq'])
    g = p.groupby('game_id')
    if lg == 'nba':
        p['margin_prev'] = (g.home_score.shift() - g.away_score.shift()).abs()
        p['pts'] = (p.home_score + p.away_score) - g.home_score.shift().add(g.away_score.shift())
        late = (p.period == 4) & (p.clock_s <= 120)
        p['big'] = late & (p.pts >= 2) & (p.margin_prev <= 5)      # scoring play, non-FT approx (2+ pts)
        margin = (p.home_score - p.away_score).abs()
        p['crunch'] = (late & (margin <= 5)) | (p.period >= 5)
        p['final_margin'] = g.home_score.transform('last').sub(g.away_score.transform('last')).abs()
    else:
        # NFL file has no score columns: score change proxied by a WP jump at a scoring play is not available;
        # grader-big proxy = surprise >= p95 of all plays (scores + turnovers are the big WP jumps) handled below.
        p['big'] = False; p['crunch'] = (p.period >= 4) & (p.game_seconds_remaining <= 120) & ((p.wp - .5).abs() < .35)
        p['final_margin'] = np.nan
    p['top_swing'] = p.surprise >= p.surprise.quantile(0.99)             # outside signal: top 1% WP swings
    p['garbage'] = ((g.wp.shift() - .5).abs() >= 0.45)                    # outcome ~decided before the play
    gm = p.groupby('game_id').agg(night=('night', 'first'), big=('big', 'sum'), crunch=('crunch', 'sum'),
                                  excitement=('surprise', 'sum'), top=('top_swing', 'sum'), final_margin=('final_margin', 'first'))
    gm['oracle_value'] = 3.0*gm.big + 1.0*gm.crunch                          # OBJECTIVES['combined'] weights
    gm['closeness'] = -gm.final_margin
    r = dict(games=len(gm), plays=len(p),
             games_zero_oracle_value_pct=round(100*(gm.oracle_value == 0).mean(), 1),
             top1pct_swing_plays_counted_big_or_crunch_pct=round(100*(p.top_swing & (p.big | p.crunch)).sum()/max(1, p.top_swing.sum()), 1),
             top1pct_swing_plays_before_last_2min_pct=round(100*(p.top_swing & ~p.crunch).sum()/max(1, p.top_swing.sum()), 1),
             big_plays_below_median_swing_pct=round(100*(p.big & (p.surprise < p.surprise.median())).sum()/max(1, p.big.sum()), 1) if p.big.any() else None,
             pair_agree_vs_excitement=dict(oracle_value=pair_agree(gm, 'oracle_value', 'excitement'),
                                          top_swings=pair_agree(gm, 'top', 'excitement')))
    if lg == 'nba':
        r['pair_agree_vs_excitement']['closeness_final'] = pair_agree(gm, 'closeness', 'excitement')
        r['pair_agree_oracle_value_vs_closeness'] = pair_agree(gm.assign(c=gm.closeness), 'oracle_value', 'c')
        r['spearman_oracle_value_vs_final_margin'] = round(gm[['oracle_value', 'final_margin']].corr('spearman').iloc[0, 1], 3)
        r['oracle_value_share_by_final_margin'] = {k: round(float(v), 3) for k, v in
            (gm.groupby(pd.cut(gm.final_margin, [-1, 5, 10, 20, 80])).oracle_value.sum()/gm.oracle_value.sum()).items().__iter__()} if False else \
            {str(k): round(float(v), 3) for k, v in (gm.groupby(pd.cut(gm.final_margin, [-1, 5, 10, 20, 80]), observed=True).oracle_value.sum()/gm.oracle_value.sum()).items()}
        r['excitement_share_by_final_margin'] = {str(k): round(float(v), 3) for k, v in (gm.groupby(pd.cut(gm.final_margin, [-1, 5, 10, 20, 80]), observed=True).excitement.sum()/gm.excitement.sum()).items()}
        r['games_share_by_final_margin'] = {str(k): round(float(v), 3) for k, v in gm.final_margin.pipe(lambda s: pd.cut(s, [-1, 5, 10, 20, 80])).value_counts(normalize=True).sort_index().items()}
    else:
        r['top1pct_swing_plays_in_garbage_pct'] = round(100*(p.top_swing & p.garbage).sum()/max(1, p.top_swing.sum()), 1)
    res[lg] = r
res['tag'] = 'personal-use, not for commercial release'; res['seasons'] = '2021-2024 regular season, locked nights excluded'
print(json.dumps(res, indent=1)); json.dump(res, open(OUT + 'audit_wp.json', 'w'), indent=1)
