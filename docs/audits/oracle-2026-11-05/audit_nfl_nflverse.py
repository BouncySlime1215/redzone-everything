"""Oracle audit NFL: grader rule big = every scoring play or turnover (redzone/grading.py is_big). Checks each against
nflverse win-probability (wpa) and game state. nflverse pbp 2023-2024 regular season (public, CC-BY 4.0), aggregates only.
Locked NFL nights are excluded. personal-use, not for commercial release."""
import json, sys, numpy as np, pandas as pd
locked = set(json.load(open('/mnt/project-files/attention-g/locked_nights.json'))['nfl'])
p = pd.concat([pd.read_parquet(f, columns=['game_id', 'game_date', 'season_type', 'qtr', 'game_seconds_remaining', 'wp', 'wpa',
                                         'sp', 'touchdown', 'interception', 'fumble_lost', 'score_differential', 'play_type'])
               for f in sys.argv[1:]])
p = p[(p.season_type == 'REG') & p.wpa.notna()]
p = p[~p.game_date.str.replace('-', '').isin(locked)]
p['awpa'] = p.wpa.abs()
p['big'] = ((p.sp == 1) & (p.play_type != 'extra_point')) | (p.interception == 1) | (p.fumble_lost == 1)
p['decided'] = (p.wp - .5).abs() >= 0.45                  # before the play, offense WP <5% or >95%
p['top'] = p.awpa >= p.awpa.quantile(0.99)
b = p[p.big]
gm = p.groupby('game_id').agg(big=('big', 'sum'), exc=('awpa', 'sum'), top=('top', 'sum'),
                              late_close=('score_differential', lambda s: 0))
gm['date'] = p.groupby('game_id').game_date.first()
def pa(df, s, y):
    acc = n = 0
    for _, d in df.groupby('date'):
        if len(d) < 2: continue
        a, c = d[s].to_numpy(), d[y].to_numpy(); i, j = np.triu_indices(len(d), 1); ok = c[i] != c[j]
        acc += ((np.sign(a[i]-a[j]) == np.sign(c[i]-c[j])) + .5*(a[i] == a[j]))[ok].sum(); n += ok.sum()
    return round(100*acc/max(n, 1), 1)
out = dict(games=len(gm), plays=len(p), big_plays=int(len(b)),
           big_awpa_p50=round(float(b.awpa.median()), 4), all_awpa_p50=round(float(p.awpa.median()), 4),
           big_with_awpa_under_2pct=round(100*(b.awpa < .02).mean(), 1),
           big_when_decided_pct=round(100*b.decided.mean(), 1),
           big_in_q4_with_margin_17plus_pct=round(100*((b.qtr >= 4) & (b.score_differential.abs() >= 17)).mean(), 1),
           top1pct_wpa_plays_counted_big_pct=round(100*(p.top & p.big).sum()/p.top.sum(), 1),
           top1pct_wpa_plays_missed_examples=p[p.top & ~p.big].play_type.value_counts().head(5).to_dict(),
           pair_agree_vs_excitement=dict(count_of_big=pa(gm, 'big', 'exc'), top_swings=pa(gm, 'top', 'exc')),
           tag='personal-use, not for commercial release', seasons='2023-2024 REG, locked nights excluded')
print(json.dumps(out, indent=1)); json.dump(out, open('/mnt/project-files/wv-loop/oracle-audit/audit_nfl_nflverse.json', 'w'), indent=1)
