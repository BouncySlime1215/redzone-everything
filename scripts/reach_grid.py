"""How much of each Tier 1 is reachable on facts alone: the join point must be known before the stick has to start.
Read only, dev nights only.

    python scripts/reach_grid.py --archive data/archive

Needs data/archive/<league>/summary_<id>.json (nfl, nhl, nba) and the scoreboards that date them. A summary is opened
only if its game's night is on the fit side of the split (`DevOnly` in scripts/coverage_audit.py); locked and fresh
games are never opened. A league with no known split is refused and skipped.

Why: the planner's reach grid (queue, games fast) lived in a scratch script with a hard-coded first-excluded-night table;
this is the same grid with the split read from the repo's own lock, so it cannot drift or touch a locked night.
"""
import argparse
import collections
import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, ROOT.as_posix())

from scripts.coverage_audit import DevOnly, LockedNightError  # noqa: E402

A = None   # archive root, set by main()


def ts(s):
    if not s: return None
    try: return dt.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
    except ValueError: return None


def games(l):
    """Dev-night summaries only; locked and fresh files are never opened. A refused league yields nothing."""
    try:
        ids = DevOnly(A, l).game_ids()
    except LockedNightError as e:
        print(l, 'REFUSED', e)
        return
    for gid in sorted(ids):
        f = Path(A) / l / f"summary_{gid}.json"
        if f.is_file():
            yield json.loads(f.read_text())


def clk(p):
    m=re.match(r'^(\d+):(\d+)',(p.get('clock') or {}).get('displayValue') or '')
    return int(m[1])*60+int(m[2]) if m else None


def main(argv=None):
    global A
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--archive', required=True, type=Path)
    A = ap.parse_args(argv).archive
    run()


def run():
    gap=collections.defaultdict(list)   # seconds from the join point to the Tier 1 event
    warn=[]; td=0; nowarn=0
    for d in games('nfl'):
        for dr in (d.get('drives') or {}).get('previous') or []:
            first_rz=None; prev=None
            for p in dr.get('plays') or []:
                t=ts(p.get('wallclock')); ty=((p.get('type') or {}).get('text') or '').lower(); tx=(p.get('text') or '').lower()
                s=p.get('start') or {}; y=s.get('yardsToEndzone'); down=s.get('down') or 0
                nopl='no play' in tx
                istd=('touchdown' in ty or (p.get('scoringPlay') and 'touchdown' in tx)) and not nopl
                if istd and t:
                    td+=1
                    gap['nfl'].append(0.0)      # ESPN NFL wallclock is the snap: the join point is just before it
                    if first_rz is not None: warn.append(t-first_rz)
                    else: nowarn+=1
                if t and not nopl and down and y and y<=20 and 'kickoff' not in ty and 'punt' not in ty and 'timeout' not in ty and first_rz is None and not istd:
                    first_rz=t
    for d in games('nhl'):
        lf=None
        for p in d.get('plays') or []:
            t=ts(p.get('wallclock')); ty=(p.get('type') or {}).get('text') or ''
            if not t: continue
            if ty=='Face Off': lf=t
            if ty=='Goal' and lf: gap['nhl'].append(t-lf)
    SC=('Foul','Free Throw','Timeout','TimeOut','Out of Bounds','Substitution','End Period','Violation','Jumpball','Dead Ball','Kicked','Challenge','Traveling','Double')
    for d in games('nba'):
        prevh=preva=0; ld=ls=None
        for p in d.get('plays') or []:
            t=ts(p.get('wallclock'))
            if not t: continue
            ty=(p.get('type') or {}).get('text') or ''; h=p.get('homeScore') or 0; a=p.get('awayScore') or 0
            per=(p.get('period') or {}).get('number') or 1; c=clk(p)
            late=per>=5 or (per>=4 and c is not None and c<=300)
            isfg=bool(p.get('shootingPlay')) and 'free throw' not in ty.lower()
            if p.get('scoringPlay') and isfg and late and abs(prevh-preva)<=5:
                cands=[x for x in (ld,ls) if x]
                if cands: gap['nba_shot_or_dead'].append(t-max(cands))
                if ld: gap['nba_dead_only'].append(t-ld)
            if any(k in ty for k in SC): ld=t
            if p.get('shootingPlay') or 'Rebound' in ty: ls=t
            prevh,preva=h,a
    LAG={'nfl':(44.0,62.5),'nba_shot_or_dead':(45.4,69.7),'nba_dead_only':(45.4,69.7),'nhl':(50.2,71.3)}
    MU={'B60':34.9+60,'B75':34.9+75,'B90':34.9+90}
    PREP=[(str(x),float(x)) for x in (5,8,10,15,20,25,30,35,40,45,50,60)]
    for k,xs in gap.items():
        print(k,'n',len(xs))
        for mn,mu in MU.items():
            for tag,lag in (('p50',LAG[k][0]),('p90',LAG[k][1])):
                L=mu-lag
                row=[f"{100*sum(1 for x in xs if x<=L-p-2)/len(xs):.0f}" for _,p in PREP]
                print(f"  {mn} lag{tag} L={L:.0f}", dict(zip([n for n,_ in PREP],row)))
    warn.sort()
    print('NFL TDs',td,'with a prior RZ snap',len(warn),f'{100*len(warn)/td:.0f}%','no warning',nowarn)
    for th in (15,30,60,120,180): print(' warn>=',th,f"{100*sum(1 for w in warn if w>=th)/td:.0f}% of all TDs")
    print(' median',warn[len(warn)//2])


if __name__ == '__main__':
    main()
