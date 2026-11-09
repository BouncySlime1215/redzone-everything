import sqlite3,json,collections,statistics as st
c=sqlite3.connect('file:data/redzone.db?mode=ro',uri=True)
rows=c.execute("select ts,target,kind,ok,detail from switches where dry_run=0 order by ts").fetchall()
last={}  # stick-> (app,ts)
buckets=collections.defaultdict(list)
all_kinds=collections.Counter()
for ts,t,k,ok,d in rows:
    try: d=json.loads(d or '{}')
    except: d={}
    tr=d.get('trace') or {}
    stick=tr.get('stick'); app=(t or '').split(':')[0]
    if k and 'replay' in str(k): continue
    if d.get('detail','').startswith('replay'): continue
    sp=tr.get('spans') or {}
    all_kinds[(app,d.get('path'),ok)]+=1
    tune=sp.get('tune')
    if ok and tune and stick:
        p=last.get(stick)
        rel='first' if not p or ts-p[1]>1800 else ('same_app' if p[0]==app else 'other_app')
        buckets[(app,rel)].append(tune)
    if ok and stick: last[stick]=(app,ts)
print(all_kinds.most_common(20))
def q(v,p): v=sorted(v); return v[min(len(v)-1,int(p*len(v)))]
for k,v in sorted(buckets.items()): print(k,len(v),'p50',round(st.median(v),1),'p90',round(q(v,.9),1),'min',round(min(v),1),'max',round(max(v),1))
