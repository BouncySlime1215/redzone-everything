"""Noise ceiling for next-3-min must-see value, dev nights only. Compound-Poisson: Var(y | true rate) has mean E[sum w^2]."""
import sys, json, runpy, collections, random
import numpy as np
sys.argv=['x','/dev/null']
src=open('holdset.py').read().replace('\nmain()\n','\n')
ns={'__file__':'holdset.py'}
exec(compile(src,'h','exec'),ns)
FN,W,H=ns['FN'],ns['WEIGHTS'],ns['H']
R={}
for l in FN:
    rows=ns['build'](l); nights=sorted(rows); cut=int(len(nights)*.6); tr,te=nights[:cut],nights[cut:]
    Dtr=[d for n in tr for d in ns['decisions'](l,rows[n])]; Dte=[d for n in te for d in ns['decisions'](l,rows[n])]
    pred,glob=ns['fit'](Dtr)
    # E[sum w^2] over the same (game, t) grid
    s2=[];ys=[]
    for n in te:
        night=rows[n]; t0=min(g[1] for g in night); t1=max(g[2] for g in night); t=t0+300
        while t<t1-H:
            lv=[g for g in night if g[1]+120<=t<=g[2]-H]
            if len(lv)>=2:
                for g in lv:
                    e=[W[tier] for w,tier in g[4] if t<w<=t+H]; ys.append(sum(e)); s2.append(sum(x*x for x in e))
            t+=H
    ys=np.array(ys);vy=float(ys.var());pois=float(np.mean(s2))
    between=max(vy-pois,0.0); r2max=between/vy
    ps=np.array([pred(x[1]) for d in Dte for x in d]); pv=float(ps.var())
    f=(between/pv)**.5 if pv>0 else 0
    # simulate: a model that knows the true expected value exactly, Poisson outcomes
    rng=np.random.default_rng(5); out={}
    mix={'nfl':(.117,.231,.687),'nba':(.051,.234,1.29),'nhl':(.126,.132,.981)}[l]; base=5*mix[0]+2*mix[1]+.5*mix[2]
    for K in (2,4):
        got=tot=0.0; dd=[d for d in Dte if len(d)>K]
        for _ in range(100):
            for d in dd:
                mu=np.clip(glob+f*(np.array([pred(x[1]) for x in d])-glob),.03,None); sc=mu/base
                y=5*rng.poisson(mix[0]*sc)+2*rng.poisson(mix[1]*sc)+.5*rng.poisson(mix[2]*sc)
                o=np.argsort(-mu)[:K]; got+=y[o].sum(); tot+=y.sum()
        out[K]=round(got/tot*100,1)
    R[l]={'var_y':round(vy,2),'poisson_var':round(pois,2),'r2_ceiling':round(r2max,3),'between_var':round(between,2),'table_pred_var':round(pv,3),'scale':round(f,2),'sim_capture_true_rate':out,'n':len(ys)}
    print(l,json.dumps(R[l]));sys.stdout.flush()
