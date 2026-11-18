"""Follow-ups: (a) what ESPN's NHL play clock means (elapsed vs remaining), (b) NHL TV-timeout hit
rate at the 6/10/14-minute marks with a 3-play lookahead for the 'TV timeout' entry, (c) NHL
whistle hazard at fine elapsed steps (unlabeled TV timeouts), (d) race leads when the league feeds
(nba/nhl) saw a score first, (e) polled feed lag per game (spread across games).
Usage: .venv/bin/python -I nhl_race_check.py <repo_root>
"""
import json
import statistics as st
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT))
from redzone.decider.cuts import DeadTracker  # noqa: E402
from redzone.models import League  # noqa: E402
from redzone.sources import espn  # noqa: E402


def epoch(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except Exception:
        return None


def q(xs, f):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, int(round(f * (len(xs) - 1)))))] if xs else float("nan")


arch = ROOT / "data/archive/nhl"
files = sorted(arch.glob("summary_*.json"))
shown = 0
marks = Counter()
whistle_len = []
for f in files:
    d = json.loads(f.read_text())
    plays = [p for p in d.get("plays") or [] if p.get("wallclock") and epoch(p["wallclock"])]
    plays.sort(key=lambda p: epoch(p["wallclock"]))
    if len(plays) < 50:
        continue
    if shown < 1:
        print("=== NHL clock semantics: first 6 plays (clock displayValue, period, type)")
        for p in plays[:6]:
            print("  ", espn._get(p, "clock", "displayValue"), espn._get(p, "period", "number"), espn._get(p, "type", "text"))
        print("   ...last 3 of period 1:")
        p1 = [p for p in plays if espn._int(espn._get(p, "period", "number")) == 1]
        for p in p1[-3:]:
            print("  ", espn._get(p, "clock", "displayValue"), espn._get(p, "type", "text"), (p.get("text") or "")[:50])
        shown += 1
    # (b) marks with lookahead. Assume clock displayValue is ELAPSED (verified by the print above).
    by_per = defaultdict(list)
    for p in plays:
        per = espn._int(espn._get(p, "period", "number"))
        if per and per <= 3:
            by_per[per].append(p)
    for per, ps in by_per.items():
        for mark in (360, 600, 840):
            for i, p in enumerate(ps):
                c = espn._clock_s(espn._get(p, "clock", "displayValue"))
                if c is None:
                    continue
                t = (espn._get(p, "type", "text") or "").lower()
                stop_types = ("stoppage", "goal", "hooking", "holding", "tripping", "roughing", "interference",
                              "slashing", "high-sticking", "delay of game", "too many men", "penalty")
                if c >= mark and (t in stop_types):
                    txt = " ".join((x.get("text") or "").lower() for x in ps[i:i + 4])
                    hit = "tv timeout" in txt
                    excl = t == "goal" or "icing" in (p.get("text") or "").lower()
                    marks[(mark, "tv" if hit else ("excl" if excl else "no"))] += 1
                    break
    # (c) whistle windows via DeadTracker
    dt = DeadTracker(League.NHL)
    for p in plays:
        w = epoch(p["wallclock"])
        dt.add(str(p.get("id")), w, w, espn._kind(League.NHL, p), p.get("text") or "",
               espn._get(p, "type", "text") or "", espn._clock_s(espn._get(p, "clock", "displayValue")),
               espn._int(espn._get(p, "period", "number")))
    for win in dt.windows():
        if win.end is not None and win.exact and win.kind == "whistle" and 0 < win.end - win.start < 2400:
            whistle_len.append(win.end - win.start)

print("\n=== NHL first stoppage at/after 6/10/14 min ELAPSED -> 'TV timeout' logged within 3 plays?")
for mark in (360, 600, 840):
    tv, no, ex = marks[(mark, "tv")], marks[(mark, "no")], marks[(mark, "excl")]
    tot = tv + no + ex
    if tot:
        print(f"  {mark//60:>2} min: TV timeout {tv}/{tot} ({100*tv/tot:.0f}%); goal/icing excluded {ex}; eligible-but-no {no}"
              f" -> among eligible: {100*tv/max(1,tv+no):.0f}%")
print("\n=== NHL 'whistle' windows: P(remaining > 20 s | elapsed e) and median remaining")
for e in (0, 20, 30, 40, 50, 60, 70, 80, 90, 120):
    rem = [x - e for x in whistle_len if x > e]
    if len(rem) >= 10:
        print(f"  e={e:>3}: n={len(rem):>5}  P(rem>20)={sum(1 for r in rem if r > 20)/len(rem):.2f}  "
              f"median rem={st.median(rem):.0f}  p90 rem={q(rem,.9):.0f}")

# (d) race leads
rec = ROOT / "data/recordings"
lead = defaultdict(list)
alone = Counter()
for day in sorted(rec.glob("*")):
    rf = day / "race.jsonl"
    if not rf.exists():
        continue
    for line in rf.read_text().splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        first = r.get("first")
        others = r.get("others") or {}
        if not others:
            alone[first] += 1
        for src, dt_ in others.items():
            lead[(first, src)].append(dt_)
print("\n=== score-change race: leads (first -> other: median s, n), and 'first with no other source in window'")
for (first, other), xs in sorted(lead.items()):
    print(f"  {first:<15} beat {other:<15} median {st.median(xs):6.1f}s  p90 {q(xs,.9):6.1f}s  n={len(xs)}")
for src, n in alone.most_common():
    print(f"  {src}: first with no other source seeing it in the window: {n}")

# (e) polled feed lag per game
per_game = defaultdict(list)
for day in sorted(rec.glob("*")):
    pf = day / "plays.jsonl"
    if not pf.exists():
        continue
    for line in pf.read_text().splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("lag") is not None and -5 < r["lag"] <= 150:
            per_game[(r.get("league"), r.get("game"))].append(r["lag"])
print("\n=== polled espn_summary lag, per-game medians (s): league: n games, min / median / max of game medians")
by_lg = defaultdict(list)
for (lg, g), xs in per_game.items():
    if len(xs) >= 30:
        by_lg[lg].append(st.median(xs))
for lg, ms in sorted(by_lg.items(), key=lambda kv: str(kv[0])):
    print(f"  {lg}: {len(ms)} games  {min(ms):.0f} / {st.median(ms):.0f} / {max(ms):.0f}")
