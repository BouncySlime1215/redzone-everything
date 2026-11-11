"""Per-league must-see rates from data/archive (read only). Locked/fresh nights excluded per release_gate.HELDOUT.

Why: kept so the planner report numbers can be re-run (build plan WP-00).
"""
import json,glob,os,sys,collections,statistics as st,datetime as dt,re,math
A='./data/archive'
EXCL={'nfl':'20251209','nba':'20261029','nhl':'20261029'}   # first excluded night (locked + fresh)
def ts(s):
    if not s: return None
    s=s.replace('Z','+00:00')
    try: return dt.datetime.fromisoformat(s).timestamp()
    except: return None
def night(d):
    t=dt.datetime.fromisoformat(d['header']['competitions'][0]['date'].replace('Z','+00:00'))-dt.timedelta(hours=5)
    return t.strftime('%Y%m%d')
def clk(p):
    v=(p.get('clock') or {}).get('displayValue') or ''
    m=re.match(r'^(\d+):(\d+)',v)
    if m: return int(m[1])*60+int(m[2])
    try: return float(v)
    except: return None
def games(l):
    for f in sorted(glob.glob(f'{A}/{l}/summary_*')):
        d=json.load(open(f)); n=night(d)
        if l in EXCL and n>=EXCL[l]: continue
        yield n,os.path.basename(f)[8:-5],d
def wpmap(d):
    m={}; prev=None
    for x in d.get('winprobability') or []:
        h=x.get('homeWinPercentage'); pid=str(x.get('playId'))
        if h is None: continue
        if prev is not None: m[pid]=abs(h-prev)
        prev=h
    return m
def ev(out,g,t,tier,kind):
    if t: out.append((t,g,tier,kind))

def football(l,gid,d,E,J):
    wp=wpmap(d); drives=(d.get('drives') or {}).get('previous') or []
    ts_all=[]; 
    for dr in drives:
        rz=False
        for p in dr.get('plays') or []:
            t=ts(p.get('wallclock')); ty=((p.get('type') or {}).get('text') or ''); tx=(p.get('text') or '').lower()
            if t: ts_all.append(t)
            s=p.get('start') or {}; down=s.get('down') or 0; y=s.get('yardsToEndzone')
            nopl='no play' in tx
            istd=('touchdown' in ty.lower()) or (p.get('scoringPlay') and 'touchdown' in tx and not nopl)
            if istd: ev(E,gid,t,1,'td')
            tl=ty.lower()
            if not nopl and ('interception' in tl or 'fumble recovery (opponent)' in tl or 'blocked' in tl or ty=='Safety' or 'muffed' in tl):
                ev(E,gid,t,1,'turnover')
            if not nopl and 'kickoff' not in tl and 'punt' not in tl and 'timeout' not in tl and down and y and y<=20 and not rz:
                rz=True; ev(E,gid,t,2,'rz_trip')
            if not nopl and 'field goal' in tl: ev(E,gid,t,2,'fg')
            if down==4 and not nopl and 'punt' not in tl and 'field goal' not in tl and 'timeout' not in tl and 'penalty' not in tl and 'kickoff' not in tl: ev(E,gid,t,2,'4th_try')
            gain=p.get('statYardage') or 0
            if not nopl and gain>=10 and not istd and any(k in tl for k in('rush','pass')): ev(E,gid,t,3,'gain10')
            if not nopl and down==3 and (s.get('distance') or 0)>=5 and gain>=(s.get('distance') or 99): ev(E,gid,t,3,'big3rd')
            if wp.get(str(p.get('id')),0)>=0.05 and not istd: ev(E,gid,t,3,'wp5')
    return ts_all
SC_DEAD=('Foul','Free Throw','Timeout','TimeOut','Out of Bounds','Substitution','End Period','Violation','Jumpball','Dead Ball','Kicked','Challenge','Traveling','Double Dribble','Technical','Delay')
def hoops(l,gid,d,E,J):
    per2 = l=='cbb'  # halves
    wp=wpmap(d); plays=d.get('plays') or []
    ts_all=[]; lastdead=None; lastshot=None; prevh=prevA=0; lead=0; run=[0,0]; runfired=[False,False]
    for p in plays:
        t=ts(p.get('wallclock')); 
        if not t: continue
        ts_all.append(t); ty=(p.get('type') or {}).get('text') or ''; h=p.get('homeScore') or 0; a=p.get('awayScore') or 0
        per=(p.get('period') or {}).get('number') or 1; c=clk(p)
        lastp = (per>=2 if per2 else per>=4); ot=(per>=3 if per2 else per>=5)
        late = ot or (lastp and c is not None and c<=300)
        margin_before=abs(prevh-prevA)
        sc=p.get('scoringPlay'); isfg=bool(p.get('shootingPlay')) and 'free throw' not in ty.lower() and 'freethrow' not in ty.lower()
        lc=False
        if sc and h+a>prevh+prevA:
            nl=(h>a)-(h<a)
            if nl!=lead: lc=True
            lead=nl
            side=0 if h>prevh else 1
            run[side]+=h+a-prevh-prevA; run[1-side]=0; runfired[1-side]=False
            if run[side]>=8 and not runfired[side]: runfired[side]=True; ev(E,gid,t,2,'run8')
        secondhalf=(per>=2 if per2 else per>=3)
        clutch_ev=False
        if sc and isfg and late and margin_before<=5:
            ev(E,gid,t,1,'clutch_fg'); clutch_ev=True
            if lastdead: J[('dead_to_clutch')].append(t-lastdead)
            if lastshot: J[('shot_to_clutch')].append(t-lastshot)
        elif sc and isfg and lc and secondhalf: ev(E,gid,t,2,'lead_change')
        if sc and isfg and 'dunk' in ty.lower(): ev(E,gid,t,3,'dunk')
        if sc and isfg and (p.get('scoreValue') or 0)==3: ev(E,gid,t,3,'three')
        w=wp.get(str(p.get('id')),0)
        if w>=0.10 and not clutch_ev: ev(E,gid,t,2,'wp10')
        elif w>=0.05 and not clutch_ev: ev(E,gid,t,3,'wp5')
        if late and margin_before<=5 and any(k in ty for k in ('Turnover','Steal')): ev(E,gid,t,3,'clutch_tov')
        if any(k in ty for k in SC_DEAD) or 'Foul' in ty: lastdead=t; J['dead_events'].append(1)
        if bool(p.get('shootingPlay')) or 'Rebound' in ty: lastshot=t
        prevh,prevA=h,a
    return ts_all
PEN_EXC=('Fighting','Misconduct','Instigator','Delay Game','Shootout')
def hockey(l,gid,d,E,J):
    plays=d.get('plays') or []; ts_all=[]; lastfo=None; pens=collections.defaultdict(list); prev=(0,0)
    goals=[]
    for p in plays:
        t=ts(p.get('wallclock'))
        if not t: continue
        ts_all.append(t); ty=(p.get('type') or {}).get('text') or ''; per=(p.get('period') or {}).get('number') or 1; c=clk(p)
        if ty=='Face Off': lastfo=t; J['faceoffs'].append(1)
        if ty=='Stoppage': J['stoppages'].append(1)
        if ty=='Goal':
            if lastfo: J['fo_to_goal'].append(t-lastfo)
            ev(E,gid,t,1,'goal')
            if per>=4: J['ot_goals'].append(1)
        elif ty in('Period Start','Period End','End of Game','Shot','Blocked','Missed','Hit','Giveaway','Takeaway','Stoppage','Face Off'):
            if ty=='Shot': ev(E,gid,t,3,'sog')
        elif ty=='Fighting': ev(E,gid,t,3,'fight')
        elif 'Penalty Shot' in ty or 'on Breakaway' in ty: ev(E,gid,t,1,'penalty_shot')
        elif not any(k in ty for k in PEN_EXC) and p.get('team'):
            pens[(per,c)].append((t,(p.get('team') or {}).get('id')))
    for k,v in pens.items():
        teams={x[1] for x in v}
        if len(v)==1 or len(teams)==1: ev(E,gid,v[0][0],2,'pp_start')
        else:
            J['coincidental'].append(1)
    # late one-goal: goals counted; flag windows via 'late1' on first play in last 2 min of P3 with margin<=1 or any OT start
    fired=set()
    for p in plays:
        t=ts(p.get('wallclock')); per=(p.get('period') or {}).get('number') or 1; c=clk(p)
        if t and per>=3 and c is not None and per not in fired:
            left=1200-c if per==3 else None
            m=abs((p.get('homeScore') or 0)-(p.get('awayScore') or 0))
            if per==3 and left is not None and left<=120 and m<=1: ev(E,gid,t,2,'late_1goal'); fired.add(per)
            if per>=4: ev(E,gid,t,2,'ot_start'); fired.add(per)
    return ts_all
def baseball(l,gid,d,E,J):
    plays=d.get('plays') or []; ab=collections.OrderedDict(); ts_all=[]; last=None
    wp=wpmap(d)
    for p in plays:
        t=ts(p.get('wallclock'))
        if t: ts_all.append(t)
        aid=p.get('atBatId')
        if aid: ab.setdefault(aid,[]).append(p)
    gaps=[]
    prevend=(0,0)
    for aid,ps in ab.items():
        t0=ts(ps[0].get('wallclock')); t1=ts(ps[-1].get('wallclock'))
        parts={x.get('type') for x in ps[0].get('participants') or []}
        risp=('onSecond' in parts) or ('onThird' in parts); loaded={'onFirst','onSecond','onThird'}<=parts
        inn=(ps[0].get('period') or {}).get('number') or 1
        h0,a0=ps[0].get('homeScore') or 0,ps[0].get('awayScore') or 0; h1,a1=ps[-1].get('homeScore') or 0,ps[-1].get('awayScore') or 0
        h0,a0=prevend; prevend=(max(h0,h1),max(a0,a1))
        m=abs(h0-a0); runs=max(0,h1+a1-h0-a0)
        hr=any('home run' in ((x.get('type') or {}).get('text') or '').lower() or 'homered' in (x.get('text') or '').lower() for x in ps)
        late=inn>=7 and m<=3
        if t1 and t0 and t1>t0: J['pa_len'].append(t1-t0)
        J['pa_pitches'].append(len([x for x in ps if x.get('pitchCount') or x.get('atBatPitchNumber')]) )
        if hr: ev(E,gid,t1,1,'hr')
        elif runs: ev(E,gid,t1,1,'run_scored')
        if not hr and not runs and (loaded or (risp and late)): ev(E,gid,t0,2,'big_pa')
        swing=max([wp.get(str(x.get('id')),0) for x in ps]+[0])
        if swing>=0.05 and not hr and not runs: ev(E,gid,t1,2,'wp5')
        if not hr and not runs and risp: ev(E,gid,t0,3,'risp_pa')
    return ts_all
FN={'nfl':football,'cfb':football,'nba':hoops,'cbb':hoops,'nhl':hockey,'mlb':baseball}
def q(xs,p):
    xs=sorted(xs); 
    return xs[min(len(xs)-1,int(p*len(xs)))] if xs else None
R={}
for l in FN:
    N=collections.defaultdict(list); spans={}; J=collections.defaultdict(list); gh=0; kinds=collections.Counter(); skip=0; pergame=[]
    for n,gid,d in games(l):
        E=[]; ta=FN[l](l,gid,d,E,J)
        ta=sorted(ta)
        if len(ta)<30: skip+=1; continue
        s,e=ta[0],ta[-1]
        if not (1.2*3600<e-s<7*3600): skip+=1; continue
        spans[gid]=(n,s,e); gh+=(e-s)/3600; 
        for t,g,tier,k in E:
            if s<=t<=e: N[n].append((t,g,tier,k)); kinds[k]+=1
        gs=[b-a for a,b in zip(ta,ta[1:])]
        J['gap'].extend(gs); J['_sum_gap_90'].append(sum(g for g in gs if 90<=g<600)); J['_sum_all'].append(sum(g for g in gs if g<600)); J['_n90'].append(sum(1 for g in gs if 90<=g<600)); J['_recs'].append(len(ta))
    bk=lambda k:'1' if k==1 else '2-3' if k<=3 else '4-7' if k<=7 else '8+'
    W=collections.defaultdict(list); gaps12=collections.defaultdict(list); collide=[0,0]
    for n,evs in N.items():
        gs=[(g,sp) for g,sp in spans.items() if sp[0]==n]
        t0=min(sp[1] for g,sp in gs); t1=max(sp[2] for g,sp in gs); evs.sort()
        tw=t0
        while tw<t1:
            live=[g for g,sp in gs if min(sp[2],tw+600)-max(sp[1],tw)>=300]
            if live:
                inw=[x for x in evs if tw<=x[0]<tw+600 and x[1] in live]
                c1=sum(1 for x in inw if x[2]==1); c2=sum(1 for x in inw if x[2]==2); c3=sum(1 for x in inw if x[2]==3)
                seq=[x[1] for x in inw if x[2]<=2]; sw=sum(1 for a,b in zip(seq,seq[1:]) if a!=b)
                W[bk(len(live))].append((c1,c2,c3,sw))
            tw+=600
        ev12=[x for x in evs if x[2]<=2]
        for a,b in zip(ev12,ev12[1:]):
            k=sum(1 for g,sp in gs if sp[1]<=a[0]<=sp[2])
            gaps12[bk(max(k,1))].append(b[0]-a[0])
        for i,a in enumerate(ev12):
            if a[2]==1:
                collide[1]+=1
                if any(x[1]!=a[1] and abs(x[0]-a[0])<=45 for x in ev12): collide[0]+=1
    out={'games':len(spans),'nights':len(N),'skipped':skip,'game_hours':round(gh,1),'kinds_per_game':{k:round(v/len(spans),2) for k,v in kinds.most_common()}}
    out['per_game_per10']={k:round(v/gh/6,3) for k,v in kinds.most_common()}
    for k in ['1','2-3','4-7','8+']:
        w=W.get(k) or []
        if not w: continue
        tot=[a+b for a,b,c,d in w]; sw=[d for a,b,c,d in w]; all3=[a+b+c for a,b,c,d in w]
        out['win_'+k]={'n':len(w),'T1':round(st.mean(a for a,b,c,d in w),2),'T1+T2':round(st.mean(tot),2),'all':round(st.mean(all3),2),
          'T12_p50':q(tot,.5),'T12_p90':q(tot,.9),'cuts_p50':q(sw,.5),'cuts_p90':q(sw,.9),'cuts_max':max(sw),'zero_T12_pct':round(100*sum(1 for x in tot if x==0)/len(tot),1),
          'gap12_p50_s':round(q(gaps12[k],.5)) if gaps12[k] else None,'gap12_p90_s':round(q(gaps12[k],.9)) if gaps12[k] else None}
    out['T1_collide_45s_pct']=round(100*collide[0]/max(1,collide[1]),1)
    gp=sorted(J['gap'])
    out['recgap_p50']=q(gp,.5);out['recgap_p90']=q(gp,.9)
    out['break90_per_game_hr']=round(sum(J['_n90'])/gh,2); out['break90_time_pct']=round(100*sum(J['_sum_gap_90'])/max(1,sum(J['_sum_all'])),1)
    for k in ('dead_to_clutch','shot_to_clutch','fo_to_goal','pa_len'):
        if J[k]: out[k]={'n':len(J[k]),'p25':round(q(J[k],.25)),'p50':round(q(J[k],.5)),'p90':round(q(J[k],.9))}
    if l in('nba','cbb'): out['deadball_per10game']=round(len(J['dead_events'])/gh/6,2)
    if l=='nhl': out['faceoff_per10game']=round(len(J['faceoffs'])/gh/6,2); out['stoppage_per10game']=round(len(J['stoppages'])/gh/6,2)
    R[l]=out
    print(l,json.dumps(out)); sys.stdout.flush()
json.dump(R,open(sys.argv[1],'w'),indent=1)
