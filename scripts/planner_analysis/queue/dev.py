"""One-off analysis (queue/dev.py) behind docs/agents/planner/2026-11-09-queue-games-fast.md; not part of the app.

Why: kept so the planner report numbers can be re-run (build plan WP-00).
"""
import json,glob,os,datetime as dt
A='./data/archive/nfl'; D='analysis_tmp/queue/nfl_dev'
n=k=0
for f in glob.glob(A+'/summary_*'):
    d=json.load(open(f))
    try: ni=(dt.datetime.fromisoformat(d['header']['competitions'][0]['date'].replace('Z','+00:00'))-dt.timedelta(hours=5)).strftime('%Y%m%d')
    except Exception: continue
    if ni<'20251209':
        t=os.path.join(D,os.path.basename(f))
        if not os.path.exists(t): os.symlink(f,t)
        k+=1
    n+=1
print(n,'kept dev',k)
