"""Route every game on the past year's public ESPN schedule through the repo's ChannelMap; read only."""
import sys,json,glob,os,collections,datetime as D,zoneinfo,pickle
W=sys.argv[1]; RAW=sys.argv[2]
sys.path.insert(0,W)
from redzone.channels import ChannelMap, NETWORKS
from redzone.sources.espn import parse_scoreboard
from redzone.models import League
ET=zoneinfo.ZoneInfo('America/New_York')
cm=ChannelMap(__import__('pathlib').Path(W)/'data/channels.json'); cm.local_search=True; cm.save=lambda:None
L={'nfl':League.NFL,'cfb':League.CFB,'nba':League.NBA,'cbb':League.CBB,'nhl':League.NHL,'mlb':League.MLB}
HRS={'nfl':3.08,'cfb':3.45,'nba':2.43,'cbb':2.10,'nhl':2.56,'mlb':2.93}
LOCAL={'cbs','fox','nbc','abc','nbcsn'}
MY={'nfl':{'WSH'},'nba':{'WSH'},'nhl':{'WSH'},'mlb':{'WSH'},'cbb':{'UNC'},'cfb':{'UNC'}}
def cls(w):
    t=w.target
    if not t: 
        l=w.label.lower()
        if 'peacock' in l: return 'peacock_only'
        if any(x in l for x in('netflix','apple','max','flosports','b1g','bigten')): return 'other_app'
        return 'no_route'
    a,_,r=t.partition(':')
    if a=='prime': return 'prime'
    if a=='espn': return 'espn_event'
    if a=='youtubetv':
        if r.startswith('st'): return 'sunday_ticket'
        if r=='espnplus' or r.startswith('ev:'): return 'espn_event'
        if r in LOCAL: return 'yttv_local_search'
        return 'yttv_code' if r in cm.codes else 'yttv_no_code'
    return 'no_route'
rows=[];seen=set();nb=collections.Counter()
for f in sorted(glob.glob(RAW+'/*.json')):
    l=os.path.basename(f).split('_')[0]
    d=json.load(open(f))
    bad={e['id'] for e in d.get('events',[]) if e.get('status',{}).get('type',{}).get('name') in('STATUS_CANCELED','STATUS_POSTPONED')}
    allstar={e['id'] for e in d.get('events',[]) if 'all-star' in (e.get('name','')+e.get('shortName','')).lower()}
    for g in parse_scoreboard(L[l],d):
        if g.game_id in seen or g.game_id in bad or g.game_id in allstar or not g.start_time: continue
        seen.add(g.game_id)
        w=cm.where(g)
        c=cls(w)
        for b in g.broadcasts:
            if not NETWORKS.get(b.lower().strip()): nb[(l,b)]+=1
        rows.append(dict(l=l,id=g.game_id,t=g.start_time,day=D.datetime.fromtimestamp(g.start_time-4*3600,ET).date().isoformat() if 0 else D.datetime.fromtimestamp(g.start_time,ET).date().isoformat(),c=c,bc=list(g.broadcasts),m=g.matchup,mine=bool(MY[l]&{g.home.abbr,g.away.abbr}),lab=w.label))
pickle.dump(rows,open(sys.argv[3],'wb'))
print(len(rows))
CL=['yttv_code','sunday_ticket','yttv_local_search','prime','espn_event','yttv_no_code','peacock_only','other_app','no_route']
print('league games  '+' '.join(c[:12].rjust(13) for c in CL))
for l in ['nfl','cfb','nba','cbb','nhl','mlb']:
    r=[x for x in rows if x['l']==l]; n=len(r) or 1; c=collections.Counter(x['c'] for x in r)
    print(l.ljust(5),str(n).rjust(5),' '.join((f"{c[k]} {100*c[k]/n:.0f}%").rjust(13) for k in CL))
print('MY TEAMS')
for l in ['nfl','cfb','nba','cbb','nhl','mlb']:
    r=[x for x in rows if x['l']==l and x['mine']]; n=len(r) or 1; c=collections.Counter(x['c'] for x in r)
    print(l,len(r),{k:v for k,v in c.most_common()})
print('UNMAPPED NETS'); 
for l in ['nfl','cfb','nba','cbb','nhl','mlb']:
    print(l,[(k[1],v) for k,v in nb.most_common() if k[0]==l][:12])
