"""One-off analysis (queue/grid.py) behind docs/agents/planner/2026-11-09-queue-games-fast.md; not part of the app.

Why: kept so the planner report numbers can be re-run (build plan WP-00).
"""
import subprocess,json,re,sys
base=['nice','-n','15','python','scripts/buffered_jump_sim.py','--maxrate','--archive','analysis_tmp/queue/nfl_dev','--min-games','7']
def run(st,g,prep,seek,fail):
    o=subprocess.run(base+['--mr-sticks',str(st),'--mr-games',str(g),'--mr-prep',str(prep),'--mr-seek',str(seek),'--mr-fail',str(fail)],capture_output=True,text=True).stdout
    return json.loads(o[o.index('{'):])
d=run(2,4,45,8,.1); print(sorted(d.keys())); print({k:d[k] for k in d if not isinstance(d[k],(dict,list))})
print('sticks games prep seek fail | jumps/10 p90 midjoins midleaves')
for r in [(2,4,60,40,.74),(2,4,60,15,.25),(2,4,45,15,.25),(2,4,45,8,.1),(2,4,30,8,.1),(2,4,20,8,.1),(2,4,15,8,.1),(2,2,45,8,.1),(2,3,45,8,.1),(2,4,110,30,.25),(2,4,110,8,.1),(2,2,110,8,.1),(3,4,45,8,.1),(3,4,20,8,.1),(3,4,110,8,.1),(4,4,20,8,.1)]:
    d=run(*r); print(r,[ (k,d[k]) for k in d if 'jump' in k and 'late' not in k][:4], [ (k,d[k]) for k in d if 'mid' in k or k in('games','minutes','slates')])
