"""One-off analysis (product/r3.py) behind docs/agents/planner/2026-11-09-product-per-league.md; not part of the app.

Why: kept so the planner report numbers can be re-run (build plan WP-00).
"""
import sys,collections,datetime as dt
src=open(sys.argv[1]+'/rates.py').read().split("FN={'nfl'")[0]; ns={}; exec(src,ns)
FN={'nfl':ns['football'],'cfb':ns['football'],'nba':ns['hoops'],'cbb':ns['hoops'],'nhl':ns['hockey'],'mlb':ns['baseball']}
for l,fn in FN.items():
    gaps=collections.defaultdict(list); sp={}; J=collections.defaultdict(list)
    for n,gid,d in ns['games'](l):
        E=[]; ta=sorted(fn(l,gid,d,E,J))
        if len(ta)<30 or not (1.2*3600<ta[-1]-ta[0]<7*3600): continue
        sp[gid]=(n,ta[0],ta[-1]); gaps[gid]=[(a,b) for a,b in zip(ta,ta[1:]) if b-a>=90]
    res=collections.defaultdict(lambda:[0,0,0])
    for n in {s[0] for s in sp.values()}:
        gs=[(g,s) for g,s in sp.items() if s[0]==n]; t0=min(s[1] for g,s in gs); t1=max(s[2] for g,s in gs); t=t0
        while t<t1:
            live=[g for g,s in gs if s[1]<=t<s[2]]
            if live:
                onbr=sum(1 for g in live if any(a<=t<b for a,b in gaps[g]))
                k=len(live); bk='1' if k==1 else '2-3' if k<=3 else '4-7' if k<=7 else '8+'
                res[bk][0]+=1; res[bk][1]+= (onbr==k); res[bk][2]+=onbr/k
            t+=15
    print(l,{k:{'slots':v[0],'all_on_break_pct':round(100*v[1]/v[0],1),'mean_frac_on_break':round(v[2]/v[0],2)} for k,v in sorted(res.items())})
    for k,name,lim in (('fo_to_goal','faceoff->goal',(45,60,90)),('dead_to_clutch','deadball->clutchFG',(45,60,90)),('shot_to_clutch','shot->clutchFG',(30,45,60))):
        if J[k]: print('   ',name,'n',len(J[k]),{f'<={x}s':round(100*sum(1 for v in J[k] if v<=x)/len(J[k]),1) for x in lim})
