import json,glob,collections,datetime as dt
A='./data/archive'
D={'nfl':3.08,'cfb':3.45,'nba':2.43,'cbb':2.1,'nhl':2.56,'mlb':2.93}
S=collections.defaultdict(lambda:collections.defaultdict(list))
for l in D:
    seen=set()
    for f in glob.glob(f'{A}/{l}/summary_*')+glob.glob(f'{A}/{l}/scoreboard_*'):
        d=json.load(open(f))
        if 'summary' in f:
            evs=[(d['header']['id'],d['header']['competitions'][0]['date'])]
        else: evs=[(e['id'],e['date']) for e in d.get('events',[])]
        for i,date in evs:
            if i in seen or not date: continue
            seen.add(i); t=dt.datetime.fromisoformat(date.replace('Z','+00:00')) if len(date)>17 else dt.datetime.strptime(date,'%Y-%m-%dT%H:%MZ').replace(tzinfo=dt.timezone.utc)
            n=(t-dt.timedelta(hours=5)).strftime('%Y%m%d')
            S[n][l].append(t)
for n in sorted(S):
    if not ('20261019'<=n<='20261108'): continue
    c=S[n]; tot=sum(len(v) for v in c.values())
    # peak concurrency in 15 min slots
    all_=[(t,t+dt.timedelta(hours=D[l]),l) for l,v in c.items() for t in v]
    a=min(x[0] for x in all_); b=max(x[1] for x in all_); best=0;t=a
    while t<b:
        k=sum(1 for x in all_ if x[0]<=t<x[1]); best=max(best,k); t+=dt.timedelta(minutes=15)
    lgs=' '.join(f'{l}{len(c[l])}' for l in D if c.get(l))
    print(n,dt.datetime.strptime(n,'%Y%m%d').strftime('%a'),'total',tot,'peak_live',best,'|',lgs)
