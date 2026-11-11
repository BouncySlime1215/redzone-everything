"""One-off analysis (major/fetch.py) behind docs/agents/planner/2026-11-09-major-considerations.md; not part of the app.

Why: kept so the planner report numbers can be re-run (build plan WP-00).
"""
import json,time,urllib.request,sys,os,gzip,datetime as D
out=sys.argv[1]
P={'nfl':'football/nfl','cfb':'football/college-football','nba':'basketball/nba','cbb':'basketball/mens-college-basketball','nhl':'hockey/nhl','mlb':'baseball/mlb'}
G={'cfb':'&groups=80&limit=400','cbb':'&groups=50&limit=400'}
S={'nfl':[('2025-11-08','2026-02-10'),('2026-08-30','2026-11-07')],'cfb':[('2025-11-08','2026-01-21'),('2026-08-27','2026-11-07')],
   'nba':[('2025-11-08','2026-06-22'),('2026-10-18','2026-11-07')],'cbb':[('2025-11-08','2026-04-07'),('2026-11-02','2026-11-07')],
   'nhl':[('2025-11-08','2026-06-26'),('2026-09-27','2026-11-07')],'mlb':[('2026-03-25','2026-11-05')]}
for l,rs in S.items():
    for a,b in rs:
        d=D.date.fromisoformat(a); e=D.date.fromisoformat(b)
        while d<=e:
            ds=d.strftime('%Y%m%d'); f=f'{out}/{l}_{ds}.json'
            if not os.path.exists(f):
                u=f'https://site.api.espn.com/apis/site/v2/sports/{P[l]}/scoreboard?dates={ds}{G.get(l,"")}'
                try:
                    r=urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':'Mozilla/5.0'}),timeout=30).read()
                    if r[:2]==b'\x1f\x8b': r=gzip.decompress(r)
                    json.loads(r); open(f,'wb').write(r)
                except Exception as x: print('ERR',l,ds,x,flush=True)
                time.sleep(0.7)
            d+=D.timedelta(days=1)
print('done')
