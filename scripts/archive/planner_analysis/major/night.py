"""Per-night reachability from cover.py rows. A night runs 5 AM to 5 AM ET.

Why: kept so the planner report numbers can be re-run (build plan WP-00).
"""
import pickle,sys,collections,datetime as D,zoneinfo,statistics as S
ET=zoneinfo.ZoneInfo('America/New_York')
rows=pickle.load(open(sys.argv[1],'rb'))
HRS={'nfl':3.08,'cfb':3.45,'nba':2.43,'cbb':2.10,'nhl':2.56,'mlb':2.93}
FAST={'yttv_code','sunday_ticket'}; SLOW={'yttv_local_search','prime'}; FIX={'espn_event','yttv_no_code'}; NONE={'peacock_only','other_app','no_route'}
for g in rows:
    g['night']=D.datetime.fromtimestamp(g['t']-5*3600,ET).date()
    a,_,h=g['m'].partition(' @ ')
    g['bo']=(g['l']=='nhl' and g['c']=='espn_event' and 'CAR' in (a,h)) or (g['l']=='nba' and g['c']=='prime' and 'CHA' in (a,h))
def ok(g,s):
    if g['bo']: return False
    if g['c'] in FAST|SLOW: return True
    if s>=1 and g['c']=='espn_event': return True
    if s>=2 and g['l']=='mlb' and g['c']=='no_route': return True
    return False
def fast(g,s):
    if g['bo']: return False
    return g['c'] in FAST or (s>=1 and g['c']=='espn_event')
def peak(gs):
    ev=[]
    for g in gs: ev+=[(g['t'],1),(g['t']+HRS[g['l']]*3600,-1)]
    ev.sort(); c=m=0; two=0; last=None
    for t,d in ev:
        if last is not None and c>=2: two+=t-last
        c+=d; m=max(m,c); last=t
    return m,two/3600
start=D.date(2025,11,8); end=D.date(2026,11,7)
days=[start+D.timedelta(n) for n in range((end-start).days+1)]
by=collections.defaultdict(list)
for g in rows:
    if start<=g['night']<=end: by[g['night']].append(g)
print('nights',len(days),'games',sum(len(v) for v in by.values()))
for s,name in [(0,'today'),(1,'+ESPN events via YTTV'),(2,'+ESPN+MLB.TV')]:
    c=collections.Counter(); fc=collections.Counter()
    for d in days:
        p,_=peak([g for g in by[d] if ok(g,s)]); c['0' if p==0 else '1' if p==1 else '2-3' if p<4 else '4-7' if p<8 else '8+']+=1
        p,_=peak([g for g in by[d] if fast(g,s)]); fc['0' if p==0 else '1' if p==1 else '2-3' if p<4 else '4-7' if p<8 else '8+']+=1
    print(name,'peak reachable',dict(sorted(c.items())),'| peak FAST',dict(sorted(fc.items())))
print('month nights none_all games/night reach_today peak reachtoday<=1 peakFAST<=1 ; after ESPN; after MLBTV')
M=collections.defaultdict(list)
for d in days: M[d.strftime('%Y-%m')].append(d)
for m in sorted(M):
    ds=M[m]; tot=[len(by[d]) for d in ds]
    p0=[peak([g for g in by[d] if ok(g,0)])[0] for d in ds]
    f0=[peak([g for g in by[d] if fast(g,0)])[0] for d in ds]
    p1=[peak([g for g in by[d] if ok(g,1)])[0] for d in ds]
    p2=[peak([g for g in by[d] if ok(g,2)])[0] for d in ds]
    print(m,len(ds),'zero',sum(t==0 for t in tot),'games med',S.median(tot),'| peak med',S.median(p0),'<=1:',sum(x<=1 for x in p0),'| fast<=1:',sum(x<=1 for x in f0),'| espn<=1:',sum(x<=1 for x in p1),'| mlb<=1:',sum(x<=1 for x in p2))
# summer detail
sm=[d for d in days if D.date(2026,6,27)<=d<=D.date(2026,8,26)]
print('summer Jun27-Aug26',len(sm),'games',sum(len(by[d]) for d in sm),Counter:=collections.Counter(g['c'] for d in sm for g in by[d]))
# nights by league presence share reachable
print('blackout certain', collections.Counter(g['l'] for g in rows if g['bo']))
# league reach today
for l in ['nfl','cfb','nba','cbb','nhl','mlb']:
    r=[g for g in rows if g['l']==l and start<=g['night']<=end]
    if r: print(l,len(r),'fast %.0f%%'%(100*sum(fast(g,0) for g in r)/len(r)),'reach today %.0f%%'%(100*sum(ok(g,0) for g in r)/len(r)),'with ESPN %.0f%%'%(100*sum(ok(g,1) for g in r)/len(r)),'with MLBTV %.0f%%'%(100*sum(ok(g,2) for g in r)/len(r)))
# my teams
for l in ['nfl','cfb','nba','cbb','nhl','mlb']:
    r=[g for g in rows if g['l']==l and g['mine'] and start<=g['night']<=end]
    print('mine',l,len(r),dict(collections.Counter(g['c'] for g in r)))
