"""Read-only analysis of data/archive: dead-window lengths per league/kind through the repo's own
DeadTracker (redzone.decider.cuts), conditional remaining-time (the survival view), league-rule
hit rates (NBA mandatory marks, NHL TV-timeout marks), and feed lag from data/recordings.

Usage: .venv/bin/python -I dead_windows.py <repo_root>
"""
import json
import statistics as st
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT))

from redzone.decider.cuts import DeadTracker, _lg  # noqa: E402
from redzone.models import League  # noqa: E402
from redzone.sources import espn  # noqa: E402


def epoch(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except Exception:
        return None


def plays_of(league, d):
    if league.base == League.NFL:
        out = []
        drives = d.get("drives") or {}
        for dr in (drives.get("previous") or []):
            out.extend(dr.get("plays") or [])
        cur = drives.get("current") or {}
        out.extend(cur.get("plays") or [])
        # dedupe by id, keep wallclock order
        seen, uniq = set(), []
        for p in out:
            if p.get("id") in seen:
                continue
            seen.add(p.get("id"))
            uniq.append(p)
        out = uniq
    else:
        out = list(d.get("plays") or [])
    out = [p for p in out if epoch(p.get("wallclock")) is not None]
    out.sort(key=lambda p: epoch(p.get("wallclock")))
    return out


def clock_s(league, p):
    c = espn._clock_s(espn._get(p, "clock", "displayValue"))
    return c


def q(xs, f):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    i = min(len(xs) - 1, max(0, int(round(f * (len(xs) - 1)))))
    return xs[i]


def summarize(name, xs):
    if len(xs) < 5:
        return None
    med = st.median(xs)
    mean = st.mean(xs)
    sd = st.pstdev(xs)
    return dict(kind=name, n=len(xs), p5=round(q(xs, .05)), p25=round(q(xs, .25)), med=round(med),
                p75=round(q(xs, .75)), p95=round(q(xs, .95)), cv=round(sd / mean, 2) if mean else None)


def cond_remaining(xs, elapsed_pts):
    """Median remaining time given the window has already lasted e seconds (survival view)."""
    out = {}
    for e in elapsed_pts:
        rem = [x - e for x in xs if x > e]
        if len(rem) >= 10:
            out[e] = (round(st.median(rem)), round(q(rem, .10)), len(rem))
    return out


def analyze(league):
    arch = ROOT / "data" / "archive" / league.value
    files = sorted(arch.glob("summary_*.json"))
    lengths = defaultdict(list)
    nba_first_two = []            # (clock at timeout #1/#2 of quarter, n)
    nhl_marks = Counter()         # (mark, hit) counts
    games = 0
    for f in files:
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        plays = plays_of(league, d)
        if len(plays) < 50:
            continue
        games += 1
        dt = DeadTracker(league)
        for p in plays:
            w = epoch(p.get("wallclock"))
            per = espn._int(espn._get(p, "period", "number"))
            dt.add(str(p.get("id")), w, w, espn._kind(league, p), p.get("text") or "",
                   espn._get(p, "type", "text") or "", clock_s(league, p), per)
        for win in dt.windows():
            if win.end is None or not win.exact:
                continue
            L = win.end - win.start
            if 0 < L < 2400:
                lengths[win.kind].append(L)
        if league == League.NBA:
            # clock at the first two timeouts of each quarter (mandatory-mark check)
            per_q = defaultdict(list)
            for p in plays:
                t = (espn._get(p, "type", "text") or "").lower()
                if "timeout" in t and "official" not in t or t == "official timeout" or "timeout" in t:
                    per = espn._int(espn._get(p, "period", "number"))
                    c = clock_s(league, p)
                    if per and c is not None and per <= 4:
                        per_q[per].append(c)
            for per, cs in per_q.items():
                for k, c in enumerate(cs[:2]):
                    nba_first_two.append((k + 1, c))
        if league == League.NHL:
            # first stoppage after 6/10/14 min elapsed of regulation periods: was it a TV timeout?
            by_per = defaultdict(list)
            for p in plays:
                per = espn._int(espn._get(p, "period", "number"))
                c = clock_s(league, p)
                if per and per <= 3 and c is not None:
                    by_per[per].append(p)
            for per, ps in by_per.items():
                for mark in (360, 600, 840):
                    for p in ps:
                        c = clock_s(league, p)
                        elapsed = 1200 - c
                        t = (espn._get(p, "type", "text") or "").lower()
                        txt = (p.get("text") or "").lower()
                        if elapsed >= mark and t in ("stoppage", "goal", "penalty", "period end",
                                                     "hooking", "holding", "tripping", "roughing", "interference",
                                                     "slashing", "high-sticking", "delay of game", "too many men"):
                            hit = "tv timeout" in txt
                            excl = t == "goal" or "icing" in txt
                            nhl_marks[(mark, "tv" if hit else ("excl" if excl else "no"))] += 1
                            break
    print(f"\n=== {league.value.upper()}  games={games}")
    rows = [summarize(k, v) for k, v in lengths.items()]
    rows = [r for r in rows if r]
    rows.sort(key=lambda r: -r["n"])
    print("kind                 n     p5   p25   med   p75   p95    CV")
    for r in rows:
        print(f"{r['kind']:<18} {r['n']:>5} {r['p5']:>6} {r['p25']:>5} {r['med']:>5} {r['p75']:>5} {r['p95']:>5} {r['cv']:>6}")
    focus = {League.NFL: ["score", "punt", "tv_timeout", "team_timeout", "two_minute", "review", "huddle", "injury", "turnover", "end_period"],
             League.NBA: ["timeout_long", "timeout_short", "shooting_foul", "foul", "free_throw", "review", "end_period"],
             League.NHL: ["tv_timeout", "whistle", "goal", "penalty", "icing", "review", "timeout"],
             League.MLB: ["inning", "pitching_change", "between_batters", "between_pitches"],
             League.CBB: ["media_timeout", "timeout", "shooting_foul", "foul", "end_period"]}
    print("-- conditional median remaining (p10 remaining, n) given elapsed seconds:")
    for k in focus.get(league, []):
        xs = lengths.get(k) or []
        if len(xs) < 20:
            continue
        pts = [0, 15, 30, 60, 90, 120, 150, 180] if k not in ("between_pitches", "between_batters", "free_throw", "foul", "shooting_foul") else [0, 5, 10, 15, 20, 30]
        cr = cond_remaining(xs, pts)
        print(f"  {k:<16}", "  ".join(f"e={e}:{v[0]}({v[1]},{v[2]})" for e, v in cr.items()))
    if league == League.NBA and nba_first_two:
        for k in (1, 2):
            cs = [c for kk, c in nba_first_two if kk == k]
            if not cs:
                continue
            mark = 419 if k == 1 else 179
            at_mark = sum(1 for c in cs if mark - 60 <= c <= mark)
            before = sum(1 for c in cs if c > mark)
            after = sum(1 for c in cs if c < mark - 60)
            print(f"-- NBA timeout #{k} of quarter: n={len(cs)} clock<=mark within 60s: {at_mark} ({100*at_mark/len(cs):.0f}%), "
                  f"team-called before mark: {before} ({100*before/len(cs):.0f}%), well after: {after}")
    if league == League.NHL and nhl_marks:
        for mark in (360, 600, 840):
            tv = nhl_marks[(mark, "tv")]; no = nhl_marks[(mark, "no")]; ex = nhl_marks[(mark, "excl")]
            tot = tv + no + ex
            if tot:
                print(f"-- NHL first stoppage after {mark//60} min elapsed: TV timeout {tv}/{tot} ({100*tv/tot:.0f}%), "
                      f"goal/icing (excluded) {ex}, other {no}")


def feed_lag():
    rec = ROOT / "data" / "recordings"
    by = defaultdict(list)
    race = Counter()
    lead = defaultdict(list)
    for day in sorted(rec.glob("*")):
        pf = day / "plays.jsonl"
        if pf.exists():
            seen_game = set()
            for line in pf.read_text().splitlines():
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                lag = r.get("lag")
                if lag is None or lag > 150 or lag < -5:
                    continue
                by[(r.get("league"), r.get("source"))].append(lag)
        rf = day / "race.jsonl"
        if rf.exists():
            for line in rf.read_text().splitlines():
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                race[r.get("first")] += 1
                for src, dt in (r.get("others") or {}).items():
                    lead[(r.get("first"), src)].append(dt)
    print("\n=== FEED LAG (play wallclock -> seen by app), s; lag>150 dropped (first-poll backlog)")
    print("league  source           n     p10   med   p90")
    for (lg, src), xs in sorted(by.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        if len(xs) >= 20:
            print(f"{str(lg):<7} {str(src):<15} {len(xs):>5} {q(xs,.1):>6.1f} {st.median(xs):>5.1f} {q(xs,.9):>5.1f}")
    print("-- score-change race: which source saw it first")
    for src, n in race.most_common():
        print(f"  {src}: {n}")
    for (first, other), xs in sorted(lead.items()):
        if len(xs) >= 5:
            print(f"  {first} beat {other} by median {st.median(xs):.1f}s (n={len(xs)}, p90 {q(xs,.9):.1f}s)")


if __name__ == "__main__":
    for lg in (League.NFL, League.NBA, League.NHL, League.MLB, League.CBB):
        try:
            analyze(lg)
        except Exception as e:  # keep going: this is exploratory
            print(f"{lg}: failed: {e!r}")
    feed_lag()
