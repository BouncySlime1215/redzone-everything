"""truthlib: the real-world truth line for the YTTV "truth" test family.

wall(period, game_clock) -> real-world epoch seconds, built from the NBA liveData play-by-play
(timeActual, ms resolution) on the public S3 mirror the app already uses, with ESPN summary
(wallclock, 1 s resolution) as a cross-check. Everything fetched is cached under ./cache so the
tests can --dry-run offline against the recorded nights (2026-11-04 / 2026-11-05).

No stick is ever tuned or pressed from this module. The only adb it runs is read-only
(cat /proc/uptime, date, dumpsys media_session).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
_STICKS = (__import__("os").environ.get("RZ_STICKS") or "hdmi1:5555,hdmi2:5555").split(",")  # stick addresses stay off the public repo: set RZ_STICKS=host1,host2 on the Mac

HERE = os.path.dirname(os.path.abspath(__file__))
SYNC = os.path.abspath(os.path.join(HERE, "..", ".."))
CACHE = os.path.join(HERE, "cache")
RESULTS = os.path.join(SYNC, "yttv_tests", "results")
REPO = "/Users/nick_matta/Documents/Codex/2026-11-02/task-2/redzone"
REC = os.path.join(REPO, "data", "recordings")
DAYS = ["2026-11-04", "2026-11-05"]
STICKS = {"HDMI1": _STICKS[0], "HDMI2": _STICKS[1]}
PKG = "com.amazon.firetv.youtube.tv"
for p in (SYNC, REPO):
    if p not in sys.path:
        sys.path.insert(0, p)

NBA_SCOREBOARD = ("https://nba-prod-us-east-1-mediaops-stats.s3.amazonaws.com/NBA/liveData/"
                  "scoreboard/todaysScoreboard_00.json")
NBA_PBP = ("https://nba-prod-us-east-1-mediaops-stats.s3.amazonaws.com/NBA/liveData/"
           "playbyplay/playbyplay_{id}.json")
ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/{path}/summary?event={id}"
ESPN_PATH = {"nba": "basketball/nba", "wcbb": "basketball/womens-college-basketball",
             "mcbb": "basketball/mens-college-basketball", "ncaab": "basketball/mens-college-basketball",
             "nhl": "hockey/nhl", "nfl": "football/nfl", "cfb": "football/college-football"}
NBA_ABBR = {"WSH": "WAS", "GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA"}
PRIOR_D = {"espn": (29.0, 8.0)}            # the app's prior for YTTV ESPN (mu, sd)

# action types after which the NBA game clock is (normally) stopped
STOP_TYPES = {"timeout", "foul", "violation", "substitution", "instantreplay", "period", "jumpball",
              "ejection", "stoppage"}


# --- CLI / output -----------------------------------------------------------------------------

def parser(test_id: str, doc: str) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=test_id, description=(doc or "").strip().splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=doc)
    ap.add_argument("--stick", default="hidden", help="HDMI1 | HDMI2 | host:port | hidden (default)")
    ap.add_argument("--channel", default="espn", help="YTTV channel key (espn, tnt, abc, ...)")
    ap.add_argument("--game", default=None, help="ESPN event id (default: all games in the data)")
    ap.add_argument("--minutes", type=float, default=10.0, help="live run length")
    ap.add_argument("--dry-run", action="store_true", help="offline, against recorded files + cache")
    ap.add_argument("--days", default=",".join(DAYS), help="recorded days for --dry-run")
    ap.add_argument("--out", default=os.path.join(RESULTS, f"{test_id}.jsonl"))
    return ap


class Out:
    def __init__(self, path: str, test_id: str, args):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.f = open(path, "a")
        self.id = test_id
        self.run = f"{test_id}-{int(time.time())}"
        self.write("start", args={k: v for k, v in vars(args).items()})

    def write(self, kind: str, **kw):
        rec = {"t": round(time.time(), 3), "test": self.id, "run": self.run, "kind": kind, **kw}
        self.f.write(json.dumps(rec, default=str) + "\n")
        self.f.flush()
        return rec

    def verdict(self, passed, rule: str, **metrics):
        rec = self.write("verdict", passed=passed, rule=rule, **metrics)
        tag = {True: "PASS", False: "FAIL", None: "N/A"}[passed]
        print(f"[{self.id}] {tag}  {rule}")
        for k, v in metrics.items():
            print(f"    {k}: {json.dumps(v, default=str)[:300]}")
        self.f.close()
        return rec


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 3)


def summ(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "p10": pct(xs, .1), "p50": pct(xs, .5), "p90": pct(xs, .9),
            "mean": round(statistics.fmean(xs), 3), "max": round(max(xs), 3)}


def mad(xs):
    m = statistics.median(xs)
    return statistics.median(abs(x - m) for x in xs)


def fmt(t):
    return datetime.fromtimestamp(t).strftime("%H:%M:%S.%f")[:-4] if t else "-"


# --- fetching / cache -------------------------------------------------------------------------

def http_json(url: str, name: str, offline: bool, refresh: bool = False):
    path = os.path.join(CACHE, name)
    if os.path.exists(path) and not refresh:
        with open(path) as f:
            return json.load(f)
    if offline:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 redzone-truth"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
    except Exception as e:  # network / 403 / json
        print(f"  fetch failed {url}: {e}", file=sys.stderr)
        return None
    os.makedirs(CACHE, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)
    return data


def espn_summary(league: str, eid: str, offline: bool, refresh=False):
    return http_json(ESPN_SUMMARY.format(path=ESPN_PATH.get(league, league), id=eid),
                     f"espn_{league}_{eid}.json", offline, refresh)


def nba_pbp(nba_id: str, offline: bool, refresh=False):
    return http_json(NBA_PBP.format(id=nba_id), f"nba_pbp_{nba_id}.json", offline, refresh)


def nba_scoreboard(offline: bool, refresh=False):
    sb = http_json(NBA_SCOREBOARD, "nba_scoreboard_live.json", offline, refresh)
    if sb and not offline:   # keep a dated copy so a past night stays mappable
        d = (sb.get("scoreboard") or {}).get("gameDate")
        if d:
            with open(os.path.join(CACHE, f"nba_scoreboard_{d}.json"), "w") as f:
                json.dump(sb, f)
    return sb


def _idmap():
    p = os.path.join(CACHE, "idmap.json")
    return json.load(open(p)) if os.path.exists(p) else {}


def espn_to_nba(eid: str, offline: bool) -> str | None:
    m = _idmap()
    if eid in m:
        return m[eid]
    s = espn_summary("nba", eid, offline)
    if not s:
        return None
    comp = s["header"]["competitions"][0]
    ab = {c["homeAway"]: NBA_ABBR.get(c["team"]["abbreviation"], c["team"]["abbreviation"])
          for c in comp["competitors"]}
    code = f"{ab.get('away')}{ab.get('home')}"
    boards = [os.path.join(CACHE, f) for f in os.listdir(CACHE) if f.startswith("nba_scoreboard_")] \
        if os.path.isdir(CACHE) else []
    if not offline:
        nba_scoreboard(False, refresh=True)
        boards = [os.path.join(CACHE, f) for f in os.listdir(CACHE) if f.startswith("nba_scoreboard_")]
    for b in boards:
        for g in (json.load(open(b)).get("scoreboard") or {}).get("games", []):
            if g.get("gameCode", "").endswith("/" + code):
                m[eid] = g["gameId"]
                with open(os.path.join(CACHE, "idmap.json"), "w") as f:
                    json.dump(m, f, indent=1)
                return g["gameId"]
    return None


# --- recorded files ---------------------------------------------------------------------------

def load_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def load_plays(days):
    rows = []
    for d in days:
        rows += load_jsonl(os.path.join(REC, d, "plays.jsonl"))
    return rows


def load_probe(days):
    rows = []
    for d in days:
        rows += load_jsonl(os.path.join(REC, d, "sync_probe.jsonl"))
    return rows


def recorded_games(days, league=None):
    g = {}
    for r in load_plays(days):
        if league is None or r["league"] == league:
            g.setdefault(r["game"], r["league"])
    return g


# --- parsing ---------------------------------------------------------------------------------

def iso_clock(s):
    m = re.fullmatch(r"PT(\d+)M([\d.]+)S", s or "")
    return int(m.group(1)) * 60 + float(m.group(2)) if m else None


def iso_ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp() if s else None
    except ValueError:
        return None


def clock_text(text: str):
    """'5:23' -> 323, '45.6' -> 45.6 (same rules as redzone.screen.scorebug.clock_seconds)."""
    try:
        from redzone.screen.scorebug import clock_seconds
        return clock_seconds(text)
    except Exception:
        t = (text or "").strip()
        if ":" in t:
            a, b = t.split(":")
            return int(a) * 60 + float(b)
        return float(t)


def nba_actions(pbp) -> list[dict]:
    acts = ((pbp or {}).get("game") or {}).get("actions") or []
    out = []
    for a in acts:
        w, c = iso_ts(a.get("timeActual")), iso_clock(a.get("clock"))
        if w is None or c is None or a.get("period") is None:
            continue
        out.append(dict(n=a.get("actionNumber"), order=a.get("orderNumber") or 0, period=int(a["period"]),
                        clock=c, wall=w, edited=iso_ts(a.get("edited")), type=a.get("actionType") or "",
                        sub=(a.get("subType") or "").lower(), desc=a.get("description") or "",
                        team=a.get("teamTricode"), score=(a.get("scoreAway"), a.get("scoreHome"))))
    out.sort(key=lambda x: (x["period"], -x["clock"], x["wall"], x["order"]))
    return out


def espn_actions(summary) -> list[dict]:
    out = []
    for p in (summary or {}).get("plays", []):
        w = iso_ts(p.get("wallclock"))
        c = clock_text((p.get("clock") or {}).get("displayValue", ""))
        per = (p.get("period") or {}).get("number")
        if w is None or c is None or per is None:
            continue
        out.append(dict(id=p.get("id"), seq=int(p.get("sequenceNumber") or 0), period=int(per), clock=c, wall=w,
                        type=(p.get("type") or {}).get("text", ""), desc=p.get("text", ""),
                        score=(p.get("awayScore"), p.get("homeScore"))))
    # NHL (and soccer) clocks count UP on ESPN: store them negated so "clock decreases" holds everywhere
    by = {}
    for x in out:
        by.setdefault(x["period"], []).append(x)
    for per, xs in by.items():
        xs.sort(key=lambda x: x["seq"])
        up = sum(b["clock"] > a["clock"] for a, b in zip(xs, xs[1:]))
        down = sum(b["clock"] < a["clock"] for a, b in zip(xs, xs[1:]))
        if up > down:
            for x in xs:
                x["clock"], x["count_up"] = -x["clock"], True
    out.sort(key=lambda x: (x["period"], -x["clock"], x["seq"]))
    return out


def is_stop(a) -> bool:
    """True when the game clock stays frozen at this action's clock until a later restart.
    Calibrated on the 2026-11-04 night (truth_02): fouls, subs, turnovers, violations, timeouts and ALL
    free throws (after the final one the clock restarts on the inbound / rebound touch), team rebounds,
    period ends, and made field goals in the last 2:00 of the 4th / OT."""
    t = a["type"]
    if t in STOP_TYPES - {"period", "jumpball"}:
        return True
    if t in ("freethrow", "turnover"):
        return True
    if t == "period":
        return a["sub"] == "end"
    if t == "rebound" and ("team" in a["desc"].lower()):
        return True
    if t in ("2pt", "3pt") and not a["desc"].upper().startswith("MISS") and a["period"] >= 4 and a["clock"] <= 120:
        return True
    return False


ESPN_STOP_WORDS = ("foul", "timeout", "turnover", "substitution", "free throw", "violation", "end ",
                   "jumpball", "jump ball", "offensive charge", "out of bounds", "deadball", "dead ball")


def espn_is_stop(a) -> bool:
    t = (a["type"] + " " + a["desc"]).lower()
    return any(w in t for w in ESPN_STOP_WORDS)


def pairs(acts, tol=1.0):
    """Consecutive (A, B) in the same period with B's clock <= A's; ran=True when wall gap ~ clock gap."""
    out = []
    for a, b in zip(acts, acts[1:]):
        if a["period"] != b["period"]:
            continue
        dc, dw = a["clock"] - b["clock"], b["wall"] - a["wall"]
        if dc < 0:
            continue
        out.append(dict(a=a, b=b, dc=dc, dw=dw, res=dw - dc, ran=dc > 0 and abs(dw - dc) <= tol))
    return out


# --- the truth line ---------------------------------------------------------------------------

class TruthLine:
    """wall(period, clock) from feed actions (NBA liveData by default).

    Between two consecutive actions A (clock cA) and B (cB < cA): if the wall gap equals the clock
    gap the clock ran the whole time and wall(c) = wB - (c - cB) exactly. If the wall gap is larger,
    the clock was stopped for a while; if A is a clock-stopping action the stop was at cA, so
    back-projection from B is still exact for cB <= c < cA, and a frozen reading c == cA only gives an
    interval [wA, restart]. Error bars: feed timing sigma (measured by truth_02) plus display
    quantisation (whole seconds above 1:00)."""

    def __init__(self, acts, feed_sigma=None, tol=1.0):
        self.acts = [a for a in acts]
        if feed_sigma is None:
            cp = os.path.join(CACHE, "calib.json")
            feed_sigma = (json.load(open(cp)).get("feed_sigma") if os.path.exists(cp) else None) or 0.35
        self.sig = feed_sigma
        self.tol = tol
        self.by_period = {}
        for a in self.acts:
            self.by_period.setdefault(a["period"], []).append(a)

    def restart_after(self, a, b):
        """Real wall time the clock restarted after a stop at A, assuming it then ran to B."""
        return b["wall"] - (a["clock"] - b["clock"])

    def wall(self, period: int, c: float, display="ceil"):
        """-> dict(wall, err, kind) or None. `c` is the clock as shown; for whole-second displays
        (>= 60 s) display='ceil' means the true clock is in (c-1, c]; centre c-0.5."""
        acts = self.by_period.get(period) or []
        q = 0.0
        # a reading equal to a stop's clock may be the frozen clock (during the stoppage): only an interval,
        # unless the caller saw it tick (display='exact' / --edge)
        if display != "exact":
            for a, b in zip(acts, acts[1:]):
                if a["clock"] == c and b["clock"] < c and any(is_stop(x) for x in acts if x["clock"] == c and "sub" in x):
                    lo = min(x["wall"] for x in acts if x["clock"] == c)
                    r = self.restart_after(a, b)
                    if r > lo:
                        return dict(wall=(lo + r) / 2, err=(r - lo) / 2, kind="frozen_interval", lo=lo, hi=r)
        if display != "exact" and c >= 60 and float(c).is_integer():
            q = 0.29                                 # sd of a uniform 1 s bin
            c = c - 0.5 if display == "ceil" else c + 0.5
        for a, b in zip(acts, acts[1:]):
            if not (b["clock"] <= c <= a["clock"]) or a["clock"] == b["clock"]:
                continue
            dc, dw = a["clock"] - b["clock"], b["wall"] - a["wall"]
            res = dw - dc
            if abs(res) <= self.tol:                # clock ran A -> B: average both projections
                w = 0.5 * ((b["wall"] - (c - b["clock"])) + (a["wall"] + (a["clock"] - c)))
                return dict(wall=w, err=math.hypot(self.sig, q, abs(res) / 2), kind="running",
                            a=a["n"] if "n" in a else a.get("id"), b=b.get("n", b.get("id")))
            if res < -self.tol:                     # clock ran faster than real time: feed error
                return dict(wall=b["wall"] - (c - b["clock"]), err=math.hypot(self.sig, q, -res), kind="feed_err")
            same = [x for x in acts if x["clock"] == a["clock"]]
            stop_known = any(is_stop(x) for x in same if "sub" in x)
            w = b["wall"] - (c - b["clock"])
            if c >= a["clock"] - 0.05:              # frozen at the stop: only an interval
                r = self.restart_after(a, b)
                lo = min(x["wall"] for x in same)
                return dict(wall=(lo + r) / 2, err=(r - lo) / 2, kind="frozen_interval", lo=lo, hi=r)
            # unlogged stop somewhere between: back-projection is right only below the stop
            return dict(wall=w, err=math.hypot(self.sig, q, 0 if stop_known else min(res, 30) / 2),
                        kind="after_stop" if stop_known else "unlogged_stop", gap=round(res, 2))
        return None

    def stoppages(self):
        """Timeouts, period ends/starts: dict(n, period, clock, type, sub, wall=call, restart, dur, next_n,
        run_s = clock run from the restart to the anchor action B (short = fewer chances of an unlogged
        stop), b_ran = the pair after B ran)."""
        out = []
        for per, acts in self.by_period.items():
            for i, a in enumerate(acts):
                if a["type"] not in ("timeout", "period") or (a["type"] == "period" and a["sub"] == "start"):
                    continue
                stop_clock, pool = a["clock"], acts[i + 1:]
                if a["type"] == "period":            # period end -> restart in the next period
                    pool = self.by_period.get(per + 1, [])
                    if not pool:
                        continue
                    stop_clock = max(x["clock"] for x in pool)
                nxt = next((b for b in pool if b["clock"] < stop_clock), None)
                restart, b_ran = None, False
                if nxt is not None:
                    restart = nxt["wall"] - (stop_clock - nxt["clock"])
                    j = pool.index(nxt)
                    if j + 1 < len(pool):
                        c = pool[j + 1]
                        dc = nxt["clock"] - c["clock"]
                        b_ran = dc > 0 and abs((c["wall"] - nxt["wall"]) - dc) <= self.tol
                out.append(dict(n=a["n"], period=per, clock=a["clock"], type=a["type"], sub=a["sub"],
                                team=a.get("team"), wall=a["wall"], restart=restart,
                                dur=(restart - a["wall"]) if restart else None,
                                next_n=nxt["n"] if nxt else None,
                                run_s=(stop_clock - nxt["clock"]) if nxt else None, b_ran=b_ran))
        return sorted(out, key=lambda s: s["wall"])


def truth_for(eid: str, offline: bool):
    """(nba_id, actions, TruthLine) for an ESPN NBA event id, or (None, [], None)."""
    nid = espn_to_nba(eid, offline)
    if not nid:
        return None, [], None
    acts = nba_actions(nba_pbp(nid, offline))
    return nid, acts, (TruthLine(acts) if acts else None)


def estimate_d(tl: TruthLine, period: int, clock_shown: float, obs_wall: float, obs_sigma=0.25, display="ceil"):
    w = tl.wall(period, clock_shown, display)
    if not w:
        return None
    return dict(D=round(obs_wall - w["wall"], 3), err=round(math.hypot(w["err"], obs_sigma), 3),
                truth_kind=w["kind"], real_wall=round(w["wall"], 3))


# --- sticks (read-only) -----------------------------------------------------------------------

def visible_sticks():
    """Hosts currently on screen per the app's /api/state. Unknown -> both (fail safe)."""
    try:
        with urllib.request.urlopen("http://localhost:8778/api/state", timeout=3) as r:
            st = json.loads(r.read().decode())
    except Exception:
        return set(STICKS.values()), "api unreachable: treat both as visible"
    vis = set()
    on = ((st.get("health") or {}).get("agent") or {}).get("on_screen")
    if on:
        vis.add(on)
    for s in (st.get("health") or {}).get("sticks") or []:
        if "screen" in str(s.get("role", "")).lower() and "off" not in str(s.get("role", "")).lower():
            vis.add(s.get("host"))
    if not vis:
        return set(STICKS.values()), "no visible stick field: treat both as visible"
    return vis, "api"


def resolve_stick(name: str):
    if name in STICKS:
        return STICKS[name]
    if name == "hidden":
        vis, why = visible_sticks()
        hid = [h for h in STICKS.values() if h not in vis]
        return hid[0] if hid else None
    return name


def sh(host, cmd, timeout=10):
    """Read-only adb shell. Refuses anything that could press keys or tune."""
    bad = ("input ", "am start", "am force", "keyevent", "pm ", "settings put", "reboot", "svc ")
    if any(b in cmd for b in bad):
        raise RuntimeError(f"truth tests are read-only; refused: {cmd}")
    return subprocess.run(["adb", "-s", host, "shell", cmd], capture_output=True, text=True,
                          timeout=timeout).stdout


def stick_boot_epoch(host, n=5):
    """Boot epoch = wall - uptime, bracketed by the adb round trip; returns (boot, rtt)."""
    best = None
    for _ in range(n):
        t0 = time.time()
        out = sh(host, "cat /proc/uptime")
        t1 = time.time()
        try:
            up = float(out.split()[0])
        except (IndexError, ValueError):
            continue
        cand = ((t0 + t1) / 2 - up, t1 - t0)
        if best is None or cand[1] < best[1]:
            best = cand
        time.sleep(0.5)
    return best


def read_session(host):
    """Read-only media_session snapshot: dict(st, sub, title, pos, upd, speed, E) or None."""
    from redzone.tuners.adb import parse_playback
    t0 = time.time()
    out = sh(host, "cat /proc/uptime; dumpsys media_session")
    t1 = time.time()
    try:
        up = float(out.split()[0]) * 1000
    except (IndexError, ValueError):
        return None
    b = parse_playback(out, PKG)
    if not b or b.get("position") is None:
        return None
    sp = b.get("speed", 1.0) if b.get("state") == "playing" else 0.0
    return dict(t=(t0 + t1) / 2, st=b.get("state"), sub=b.get("subtitle", ""), title=b.get("title", ""),
                pos=b["position"], upd=b["updated"], speed=sp, boot=(t0 + t1) / 2 - up / 1000,
                E=(t0 + t1) / 2 - (b["position"] + (up - b["updated"]) * sp) / 1000)


# --- recorded YTTV probe helpers --------------------------------------------------------------

def boot_envelope(probe_rows, host):
    """Boot epoch from a recording: min over rows of (t - updated/1000) for fresh updates.
    t - updated/1000 = boot + (poll latency after the update) >= boot; the minimum is boot + ~0."""
    vals, seen = [], set()
    for r in probe_rows:
        ms = r.get("ms") or {}
        if r.get("host") != host or not ms.get("updated"):
            continue
        k = ms["updated"]
        if k in seen:
            continue
        seen.add(k)
        vals.append(r["t"] - ms["updated"] / 1000)
    return vals


def probe_events(probe_rows, boots):
    """Distinct YTTV media_session updates as dict(w=wall of update, host, pos, E, title, sub, st)."""
    ev, seen = [], set()
    for r in probe_rows:
        ms = r.get("ms") or {}
        h = r.get("host")
        if ms.get("pkg") != PKG or not ms.get("updated") or h not in boots:
            continue
        k = (h, ms["updated"])
        if k in seen:
            continue
        seen.add(k)
        w = boots[h] + ms["updated"] / 1000
        ev.append(dict(w=w, host=h, pos=ms["position"] / 1000, E=w - ms["position"] / 1000,
                       title=ms.get("title") or "", sub=ms.get("subtitle") or "", st=ms.get("state"),
                       role=r.get("role")))
    ev.sort(key=lambda e: e["w"])
    return ev


def bursts(events):
    """DAI ad-pod bursts: consecutive updates on the same stick/title, 8-17 s apart, position advancing
    by the gap. -> list of dict(host, sub, title, start, last, spot, end, n)."""
    out = []
    for h in sorted({e["host"] for e in events}):
        es = [e for e in events if e["host"] == h]
        cur = []
        for a, b in zip(es, es[1:]):
            gap, dpos = b["w"] - a["w"], b["pos"] - a["pos"]
            if a["title"] == b["title"] and 8 <= gap <= 17 and abs(dpos - gap) < 2:
                cur = cur or [a]
                cur.append(b)
            else:
                if len(cur) >= 2:
                    out.append(cur)
                cur = []
        if len(cur) >= 2:
            out.append(cur)
    res = []
    for b in out:
        spot = (b[-1]["w"] - b[0]["w"]) / (len(b) - 1)
        res.append(dict(host=b[0]["host"], sub=b[0]["sub"], title=b[0]["title"], start=b[0]["w"],
                        last=b[-1]["w"], spot=spot, end=b[-1]["w"] + spot, n=len(b), E=b[-1]["E"]))
    return res


def title_teams(title: str):
    """'San Jose Sharks at Los Angeles Kings' -> ('san jose sharks', 'los angeles kings')."""
    m = re.match(r"(.+?)\s+(?:at|vs\.?|@)\s+(.+)", title or "", re.I)
    return (m.group(1).strip().lower(), m.group(2).strip().lower()) if m else None


def summary_teams(summary):
    comp = (summary or {}).get("header", {}).get("competitions", [{}])[0]
    return {c["homeAway"]: c["team"].get("displayName", "").lower() for c in comp.get("competitors", [])}


def match_title_to_game(title, games_teams):
    tt = title_teams(title)
    if not tt:
        return None
    for eid, t in games_teams.items():
        names = " ".join(t.values())
        if any(w and w.split()[-1] in names for w in tt) and all(w.split()[-1] in names for w in tt):
            return eid
    return None


def boot_windows(probe_rows, host, win_s=1800.0):
    """[(t_start, t_end, boot_est, n)] per window: the lower envelope of t - updated/1000."""
    rows = sorted((r for r in probe_rows if r.get("host") == host), key=lambda r: r["t"])
    out, seen, cur = [], set(), None
    for r in rows:
        ms = r.get("ms") or {}
        if not ms.get("updated") or ms["updated"] in seen:
            continue
        seen.add(ms["updated"])
        v = r["t"] - ms["updated"] / 1000
        if cur is None or r["t"] - cur[0] > win_s:
            cur = [r["t"], r["t"], v, 0]
            out.append(cur)
        cur[1] = r["t"]
        cur[2] = min(cur[2], v)
        cur[3] += 1
    return [tuple(w) for w in out]


def boot_estimate(probe_rows, host):
    """Recording-only boot epoch: p10 of (row t - updated/1000) over fresh updates. The minimum is NOT
    safe (row t is stamped before dumpsys returns, so an update landing mid-poll goes below boot);
    p10 was within 0.5 s of a live read-only /proc/uptime bracket on all 4 stick-days (truth_09)."""
    v = [x for x in boot_envelope(sorted(probe_rows, key=lambda r: r["t"]), host)]
    if len(v) < 5:
        return None
    lo = pct(v, 0.10)
    return lo


def boots_for(probe_rows, prefer_live=True):
    """{host: boot epoch}. Uses cache/boots_live.json (read-only live bracket) when the recording's own
    estimate agrees with it within 5 s (= no reboot in between), else the recording estimate."""
    live = {}
    lp = os.path.join(CACHE, "boots_live.json")
    if prefer_live and os.path.exists(lp):
        live = json.load(open(lp))
    res = {}
    for h in sorted({r.get("host") for r in probe_rows if r.get("host")}):
        est = boot_estimate(probe_rows, h)
        lv = (live.get(h) or {}).get("boot")
        if est is None and lv is None:
            continue
        res[h] = lv if (lv is not None and (est is None or abs(est - lv) <= 5)) else est
    return res


def tune_segments(events, jump_s=2.0, edge_band=25.0):
    """Split a stick's YTTV events into landings at the live edge. YTTV reports a near-constant position at
    the edge (~46,790-46,805 s), so a new landing = channel change, or E jumping (either way) by > jump_s
    while the position is back within edge_band of that stick+channel's edge (lowest sane position).
    A forward E jump with the position NOT at the edge is a pause/stall: the stick legitimately fell
    behind, so it stays in the same tune. -> list of dict(host, sub, E_land, t_land, edge, events)."""
    segs = []
    edge = {}
    for e in events:
        if e["st"] == "playing" and e["pos"] > 1000:
            k = (e["host"], e["sub"])
            edge[k] = min(edge.get(k, 1e12), e["pos"])
    for h in sorted({e["host"] for e in events}):
        cur = None
        for e in (x for x in events if x["host"] == h):
            if e["st"] != "playing" or e["pos"] <= 1000:
                continue
            ed = edge.get((h, e["sub"]), 0)
            jump = cur is not None and abs(e["E"] - cur["E_last"]) > jump_s
            reland = jump and e["pos"] <= ed + edge_band
            # forward jump whose position could have been reached from a fresh landing since the last event
            # (a re-land the 7 s poll missed) - as likely as a pause: new tune, flagged ambiguous
            amb = jump and not reland and e["E"] > cur["E_last"] and e["pos"] - ed < e["w"] - cur["w_last"]
            if cur is None or e["sub"] != cur["sub"] or reland or amb:
                cur = dict(host=h, sub=e["sub"], E_land=e["E"], t_land=e["w"] - (e["pos"] - ed if amb else 0),
                           E_last=e["E"], edge=ed, ambiguous=bool(amb), events=[])
                segs.append(cur)
            cur["w_last"] = e["w"]
            cur["events"].append(e)
            cur["E_last"] = e["E"]
    return segs


def game_teams_cache(league="nba"):
    out = {}
    if not os.path.isdir(CACHE):
        return out
    for f in os.listdir(CACHE):
        m = re.fullmatch(rf"espn_{league}_(\d+)\.json", f)
        if m:
            out[m.group(1)] = summary_teams(json.load(open(os.path.join(CACHE, f))))
    return out


def adpod_samples(days, offline=True, lead_prior=(10.0, 6.0)):
    """YTTV ad-pod bursts on recorded nights matched to the real stoppage they cover.
    D_raw_end = pod end on the stick - real restart (D minus the broadcast's return-to-restart lead);
    D_raw_start = pod start on the stick - real timeout call (D plus the call-to-break lag).
    D_est = D_raw_end + lead_prior (mu, sd) -> err = sd."""
    probe = sorted(load_probe(days), key=lambda r: r["t"])
    boots = boots_for(probe)
    ev = probe_events(probe, boots)
    teams = game_teams_cache("nba")
    lines = {}
    out = []
    for b in bursts(ev):
        eid = match_title_to_game(b["title"], teams)
        if not eid:
            out.append(dict(b, game=None, why="title not an NBA game in cache"))
            continue
        if eid not in lines:
            lines[eid] = truth_for(eid, offline)[2]
        tl = lines[eid]
        if tl is None:
            out.append(dict(b, game=eid, why="no truth line"))
            continue
        cands = [s for s in tl.stoppages() if s["restart"] and s["wall"] <= b["start"] and
                 b["end"] - 150 <= s["restart"] <= b["end"] + 30]
        if not cands:
            out.append(dict(b, game=eid, why="no stoppage brackets the pod"))
            continue
        s = max(cands, key=lambda x: x["wall"])
        d_end = b["end"] - s["restart"]
        out.append(dict(b, game=eid, stop=s, D_raw_end=round(d_end, 2), D_raw_start=round(b["start"] - s["wall"], 2),
                        D_est=round(d_end + lead_prior[0], 2), err=lead_prior[1]))
    return out, boots, ev
