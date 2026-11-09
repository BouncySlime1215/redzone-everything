"""One-off analysis (product/r2.py) behind docs/agents/planner/2026-11-09-product-per-league.md; not part of the app.

Why: kept so the planner report numbers can be re-run (build plan WP-00).
"""
import json,sys,collections,statistics as st,datetime as dt,glob,importlib.util,types,io,contextlib
src=open(sys.argv[1]+'/rates.py').read().split("FN={'nfl'")[0]
ns={}; exec(src,ns); g=ns
# patch hoops: wp>=10 as T2, wp>=5 as T3
hs=src
src2=src.replace("if wp.get(str(p.get('id')),0)>=0.05 and not clutch_ev: ev(E,gid,t,2,'wp5')","w=wp.get(str(p.get('id')),0)\n        if w>=0.10 and not clutch_ev: ev(E,gid,t,2,'wp10')\n        elif w>=0.05 and not clutch_ev: ev(E,gid,t,3,'wp5')")
ns={}; exec(src2,ns)
FN={'nfl':ns['football'],'cfb':ns['football'],'nba':ns['hoops'],'cbb':ns['hoops'],'nhl':ns['hockey'],'mlb':ns['baseball']}
clk=ns['clk'];ts=ns['ts']
def q(xs,p):
    xs=sorted(xs)
    return xs[min(len(xs)-1,int(p*len(xs)))] if xs else None
def plays(l,d):
    if l in('nfl','cfb'):
        for dr in (d.get('drives') or {}).get('previous') or []:
            yield from dr.get('plays') or []
    else: yield from d.get('plays') or []
def crunch(l,p):
    per=(p.get('period') or {}).get('number') or 1; c=clk(p); m=abs((p.get('homeScore') or 0)-(p.get('awayScore') or 0))
    if l in('nfl','cfb'): return per>=5 or (per==4 and c is not None and c<=300 and m<=8)
    if l=='nba': return per>=5 or (per==4 and c is not None and c<=300 and m<=5)
    if l=='cbb': return per>=3 or (per==2 and c is not None and c<=240 and m<=6)
    if l=='nhl': return per>=4 or (per==3 and c is not None and (1200-c)<=300 and m<=1)
    if l=='mlb': return per>=10 or (per>=8 and m<=2)
CLIP={'nfl':27,'cfb':27,'nba':35,'cbb':35,'nhl':60,'mlb':30}
out={}
for l,fn in FN.items():
    N=collections.defaultdict(list); gh=0; kinds=collections.Counter(); sp={}; intervals=collections.defaultdict(list); nc=0; dur=[]; G=0; reached=0
    J=collections.defaultdict(list)
    for n,gid,d in ns['games'](l):
        E=[]; ta=sorted(fn(l,gid,d,E,J))
        if len(ta)<30 or not (1.2*3600<ta[-1]-ta[0]<7*3600): continue
        s,e=ta[0],ta[-1]; gh+=(e-s)/3600; G+=1
        sp[gid]=(n,s,e)
        for t,g_,tier,k in E:
            if s<=t<=e: N[n].append((t,g_,tier,k)); kinds[k]+=1
        tl=[]
        for p in plays(l,d):
            t=ts(p.get('wallclock'))
            if t: tl.append((t,crunch(l,p)))
        tl.sort(); ct=0; iv=[]; cur=None
        for (t,c),nxt in zip(tl,tl[1:]+[(tl[-1][0],False)]):
            if c:
                if cur is None: cur=t
                ct+=max(0,min(nxt[0]-t,300))
            elif cur is not None: iv.append((cur,t)); cur=None
        if cur is not None: iv.append((cur,tl[-1][0]))
        if ct>0: reached+=1; dur.append(ct/60); intervals[n].extend(iv)
    # one-TV estimate
    cov={}
    for slack in (30,300):
        for tiers,name in (((1,),'T1'),((1,2),'T1+T2')):
            sh=tt=0
            for n,evs in N.items():
                if len({x[1] for x in evs})<4: continue
                f=0
                for t,g_,tier,k in sorted(x for x in evs if x[2] in tiers):
                    tt+=1; start=max(f,t)
                    if start<=t+slack: sh+=1; f=start+CLIP[l]+2.5
            cov[f'{name}_slack{slack}']=(sh,tt,round(100*sh/max(1,tt),1))
    # concurrent crunch
    both=tot=0
    for n,iv in intervals.items():
        if not iv: continue
        a=min(x[0] for x in iv); b=max(x[1] for x in iv); t=a
        while t<b:
            k=sum(1 for x in iv if x[0]<=t<x[1])
            if k>=1:
                tot+=1
                if k>=2: both+=1
            t+=30
    per10={k:round(v/gh/6,3) for k,v in kinds.most_common()}
    out[l]={'games':G,'gh':round(gh,1),'avg_game_h':round(gh/G,2),'per_game_per10':per10,'crunch_games_pct':round(100*reached/G,1),'crunch_wall_min_p50':round(q(dur,.5),1) if dur else None,'crunch_wall_min_p90':round(q(dur,.9),1) if dur else None,'crunch_time_with_2plus_pct':round(100*both/max(1,tot),1),'one_tv':cov}
    print(l,json.dumps(out[l])); sys.stdout.flush()
    if l in('nba','cbb'):
        ws=collections.defaultdict(list)
        for n,evs in N.items():
            gs=[(g_,s_) for g_,s_ in sp.items() if s_[0]==n]; t0=min(s_[1] for g_,s_ in gs); t1=max(s_[2] for g_,s_ in gs); evs.sort(); tw=t0
            while tw<t1:
                live=[g_ for g_,s_ in gs if min(s_[2],tw+600)-max(s_[1],tw)>=300]
                if live:
                    inw=[x for x in evs if tw<=x[0]<tw+600 and x[1] in live]
                    seq=[x[1] for x in inw if x[2]<=2]
                    ws['1' if len(live)==1 else '2-3' if len(live)<=3 else '4-7' if len(live)<=7 else '8+'].append((sum(1 for x in inw if x[2]==1),sum(1 for x in inw if x[2]<=2),sum(1 for a,b in zip(seq,seq[1:]) if a!=b),sum(1 for x in inw if x[2]<=3)))
                tw+=600
        for k,v in ws.items():
            tot_=[a[1] for a in v]; print('   bucket',k,'n',len(v),'T1',round(st.mean(a[0] for a in v),2),'T12',round(st.mean(tot_),2),'p50',q(tot_,.5),'p90',q(tot_,.9),'cuts p50',q([a[2] for a in v],.5),'p90',q([a[2] for a in v],.9),'all',round(st.mean(a[3] for a in v),2),'zero%',round(100*sum(1 for x in tot_ if x==0)/len(tot_),1))
json.dump(out,open(sys.argv[2],'w'),indent=1)
