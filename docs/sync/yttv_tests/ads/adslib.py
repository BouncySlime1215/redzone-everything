"""Shared helpers for the YTTV "ads" test family (ads_NN_*.py).

Everything here is READ-ONLY toward the sticks: dumpsys media_session, /proc/uptime, logcat -d.
Nothing in this module presses keys or tunes. The one optional tune (ads_05 --tune-hidden) goes
through `guard_hidden()` and the existing helper `t1_retune.tune`.

Time bases
  stick uptime ms (media_session 'updated', logcat is printed with -v epoch so already wall)
  boot epoch = wall - uptime  (stick clock ~= Mac clock within 0.1 s)
  D (seconds picture is behind real world) = wall time an event is SEEN on the stick
                                             - wall time the event HAPPENED (plays.jsonl 'wall')
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics as st
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime
_STICKS = (__import__("os").environ.get("RZ_STICKS") or "hdmi1:5555,hdmi2:5555").split(",")  # stick addresses stay off the public repo: set RZ_STICKS=host1,host2 on the Mac

def _sync_dir():
    """RZ_SYNC, else Nick's Nov 4 scratchpad if it still exists (reference PNGs, codec logs), else this bank's parent."""
    import os as _os
    env = _os.environ.get("RZ_SYNC")
    if env:
        return env
    old = "/private/tmp/claude-501/-Users-nick-matta-Documents-Codex-2026-11-02-task-2-redzone/db4845a1-e6cf-4448-8874-21dc8f52883f/scratchpad/sync"
    here = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))
    return old if _os.path.isdir(old) else here


SYNC = _sync_dir()
REPO = __import__("os").environ.get("RZ_REPO") or "/Users/nick_matta/Documents/Codex/2026-11-02/task-2/redzone"
RESULTS = __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))), "results")
REC = f"{REPO}/data/recordings"
sys.path.insert(0, SYNC)
sys.path.insert(0, REPO)

from redzone.tuners.adb import parse_playback  # noqa: E402

try:  # live helpers (import only; never rewritten)
    import t1_retune  # noqa: E402
    PKG = t1_retune.PKG
except Exception:  # pragma: no cover
    t1_retune = None
    PKG = "com.amazon.firetv.youtube.tv"

HOSTS = {"hdmi1": _STICKS[0], "hdmi2": _STICKS[1]}
ALIASES = {"150": "hdmi1", "181": "hdmi2", "1": "hdmi1", "2": "hdmi2"}
CODES = {"espn": "ferg3lVdMOg", "espn2": "qHoTWZ9M9gw", "tnt": "lVQYDh9weHg", "tbs": "vv_r1X6N3sc",
         "nbatv": "ykTstVbuNpg", "fs1": "_2ONrjDR7S8", "usa": "ZgiS2N0QkbU", "trutv": "x2IdxPvLNC0",
         "fox": "njJRoIW1Lps", "cbssn": "XG1jwCkVgUE", "nflnetwork": "_pYg9qMKKIA", "espnu": "6x-T96Q-5eY",
         "btn": "1D2LKf_AW5w"}
PRIOR_ESPN = (29.0, 8.0)
LANDING_MAX = 46900.0  # every measured deep-link tune lands at position 46,780-46,810 s  # app prior for YTTV ESPN delay (mu, sd)

# NBA nickname -> tricode, used to map a YTTV title ("Philadelphia 76ers at Detroit Pistons") to a game
NBA = {"hawks": "ATL", "celtics": "BOS", "nets": "BKN", "hornets": "CHA", "bulls": "CHI", "cavaliers": "CLE",
       "mavericks": "DAL", "nuggets": "DEN", "pistons": "DET", "warriors": "GSW", "rockets": "HOU", "pacers": "IND",
       "clippers": "LAC", "lakers": "LAL", "grizzlies": "MEM", "heat": "MIA", "bucks": "MIL", "timberwolves": "MIN",
       "pelicans": "NOP", "knicks": "NYK", "thunder": "OKC", "magic": "ORL", "76ers": "PHI", "suns": "PHX",
       "trail blazers": "POR", "blazers": "POR", "kings": "SAC", "spurs": "SAS", "raptors": "TOR", "jazz": "UTA",
       "wizards": "WAS"}


# ---------------------------------------------------------------- CLI / output
def make_parser(test_id: str, doc: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=test_id, description=(doc or "").strip().splitlines()[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=doc)
    p.add_argument("--stick", default="hidden",
                   help="hdmi1|hdmi2|150|181|hidden|both (default hidden = whichever is not on screen)")
    p.add_argument("--channel", default=None, help="YTTV channel key (espn, tnt, ...); default = whatever is playing")
    p.add_argument("--game", default=None, help="ESPN game id; default = auto-match from the YTTV title")
    p.add_argument("--minutes", type=float, default=20.0, help="live collection length")
    p.add_argument("--dry-run", action="store_true", help="analyse recorded files only, touch no stick")
    p.add_argument("--days", default="2026-11-04,2026-11-05", help="recording days for --dry-run")
    p.add_argument("--out", default=None, help=f"JSONL output (default {RESULTS}/<id>.jsonl)")
    p.add_argument("--poll", type=float, default=1.0, help="live poll period s (>=0.5)")
    return p


class Out:
    def __init__(self, test_id: str, args):
        self.id = test_id
        default = f"{RESULTS}/{test_id}{'.dryrun' if args.dry_run else ''}.jsonl"
        self.path = args.out or default
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.f = open(self.path, "a")
        self.mode = "dry-run" if args.dry_run else "live"
        self.run = f"{test_id}-{int(time.time())}"

    def w(self, rec: str, **row):
        """One JSONL record; `rec` is the record type (summary, pod, match, ...)."""
        row = dict(test=self.id, run=self.run, mode=self.mode, rec=rec, t=round(time.time(), 3), **row)
        self.f.write(json.dumps(row, default=str) + "\n")
        self.f.flush()
        return row

    def summary(self, passed, **row):
        r = self.w("summary", passed=passed, **row)
        print(json.dumps(r, default=str))
        self.f.close()
        return r


def hhmmss(t):
    return datetime.fromtimestamp(t).strftime("%H:%M:%S.%f")[:-4] if t else None


def robust(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return dict(n=0)
    med = st.median(xs)
    mad = st.median([abs(x - med) for x in xs])
    return dict(n=len(xs), median=round(med, 2), mean=round(st.fmean(xs), 2),
                sd=round(st.pstdev(xs), 2) if len(xs) > 1 else None, mad=round(mad, 2),
                min=round(min(xs), 2), max=round(max(xs), 2))


# ---------------------------------------------------------------- safety
def visible_hosts() -> tuple[set, str]:
    """Hosts currently on screen per the app (/api/state health.sticks[].role). Fail safe: both."""
    try:
        with urllib.request.urlopen("http://localhost:8778/api/state", timeout=3) as r:
            d = json.load(r)
        sticks = (d.get("health") or {}).get("sticks") or []
        vis, known = set(), 0
        for s in sticks:
            role = str(s.get("role") or "").lower()
            if s.get("host") in HOSTS.values() and role:
                known += 1
                if "screen" in role or "visible" in role or "active" in role:
                    vis.add(s["host"])
        if known == 2 and len(vis) == 1:
            return vis, "api/state health.sticks.role"
        return set(HOSTS.values()), f"ambiguous roles ({known} known, {len(vis)} visible): treat both visible"
    except Exception as e:
        return set(HOSTS.values()), f"state unavailable ({e.__class__.__name__}): treat both visible"


def resolve_sticks(arg: str) -> list[str]:
    a = (arg or "hidden").lower()
    a = ALIASES.get(a, a)
    if a in HOSTS:
        return [HOSTS[a]]
    if a == "both":
        return list(HOSTS.values())
    vis, _ = visible_hosts()
    hidden = [h for h in HOSTS.values() if h not in vis]
    # read-only tests may read either stick; prefer the hidden one, else both
    return hidden or list(HOSTS.values())


def guard_hidden(host: str) -> tuple[bool, str]:
    """True only when the app says `host` is the standby stick. Required before any key/tune."""
    vis, why = visible_hosts()
    if host in vis:
        return False, f"refused: {host} is (or may be) on screen [{why}]"
    return True, f"ok: {host} hidden [{why}]"


# ---------------------------------------------------------------- live reads (read-only)
def sh(host, cmd, timeout=10):
    return subprocess.run(["adb", "-s", host, "shell", cmd], capture_output=True, text=True,
                          timeout=timeout).stdout


def boot_epoch(host) -> float:
    t0 = time.time()
    up = float(sh(host, "cat /proc/uptime").split()[0])
    t1 = time.time()
    return (t0 + t1) / 2 - up


def read_ms(host):
    """One media_session sample: dict(state, position, updated, speed, title, subtitle, wall, up_ms)."""
    t0 = time.time()
    out = sh(host, "cat /proc/uptime; dumpsys media_session")
    t1 = time.time()
    try:
        up = float(out.split()[0]) * 1000
    except Exception:
        return None
    b = parse_playback(out, PKG)
    if b.get("position") is None:
        return None
    b["wall"] = (t0 + t1) / 2
    b["up_ms"] = up
    return b


def logcat_dump(host, tags="MediaCodecLogger:I VPUD:I whad-MediaSession:I MPB_DRM:W AmazonHdmiService:I "
                           "MtkHdmiService:I hwcomposer:I *:S", lines=4000):
    """Read-only logcat ring-buffer dump with epoch stamps."""
    spec = " ".join(f"'{t}'" for t in tags.split())  # quote so '*:S' never globs on the device shell
    # -v epoch right-aligns the stamp: strip so every parser can anchor on ^epoch
    return [ln.strip() for ln in sh(host, f"logcat -d -v epoch -t {lines} {spec}", timeout=20).splitlines()
            if ln.strip() and not ln.startswith("---------")]


DEFAULT_TAGS = ("MediaCodecLogger:I VPUD:I whad-MediaSession:I MPB_DRM:W AmazonHdmiService:I "
                "MtkHdmiService:I hwcomposer:I *:S")


class LogStream:
    """Read-only streaming `adb logcat -v epoch -T 1 <filters>` per host (the ring buffer is too small
    for periodic -d dumps: MediaCodecLogger lines are evicted within seconds). Lines are stripped."""

    def __init__(self, hosts, tags=DEFAULT_TAGS):
        import threading
        self.lines = defaultdict(list)
        self.procs = []
        for h in hosts:
            cmd = ["adb", "-s", h, "logcat", "-v", "epoch", "-T", "1"] + tags.split()
            pr = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            self.procs.append(pr)
            threading.Thread(target=self._pump, args=(h, pr), daemon=True).start()

    def _pump(self, h, pr):
        for ln in pr.stdout:
            ln = ln.strip()
            if ln and not ln.startswith("---------"):
                self.lines[h].append(ln)

    def stop(self):
        for pr in self.procs:
            try:
                pr.terminate()
                pr.wait(timeout=3)
            except Exception:
                pass
        return self.lines


def collect_live(hosts, minutes, poll=1.0, logcat=True, on_sample=None, tags=DEFAULT_TAGS):
    """Poll media_session (dedupe by updated) on `hosts` for `minutes` while streaming logcat.
    Returns (events, loglines_by_host, boot_by_host). CPU-light: sleeps >= 0.5 s between polls."""
    poll = max(0.5, poll)
    boot = {h: boot_epoch(h) for h in hosts}
    events, seen = [], set()
    ls = LogStream(hosts, tags) if logcat else None
    t_end = time.time() + minutes * 60
    try:
        while time.time() < t_end:
            for h in hosts:
                try:
                    b = read_ms(h)
                except Exception:
                    b = None
                if b and b.get("pkg") == PKG and b.get("updated") is not None:
                    k = (h, b["updated"])
                    if k not in seen:
                        seen.add(k)
                        e = mk_event(h, b, boot[h])
                        events.append(e)
                        if on_sample:
                            on_sample(e)
            time.sleep(poll)
    finally:
        logs = ls.stop() if ls else defaultdict(list)
    events.sort(key=lambda e: e["w"])
    return events, logs, boot


def mk_event(host, ms, boot):
    return dict(host=host, h=host.split(":")[0][-3:], w=boot + ms["updated"] / 1000.0,
                pos=ms["position"] / 1000.0, st=ms.get("state"), speed=ms.get("speed", 1.0),
                title=ms.get("title") or "", sub=(ms.get("subtitle") or ""))


# ---------------------------------------------------------------- recorded data
def days_of(args):
    return [d.strip() for d in args.days.split(",") if d.strip()]


_WHAD = re.compile(r"^(\d{9,}\.\d+)\s.*whad-MediaSession.*updated=(\d+)")


def load_probe(days):
    """sync_probe rows -> (events, logs_by_host, boot_segments).
    events: deduped YTTV media_session updates with wall = boot + updated/1000.
    Boot epoch per host per uptime segment: exact from whad-MediaSession log lines (epoch - updated)
    when present, else min(t - updated/1000) over the segment (upper bound, tight within a poll)."""
    rows = []
    for d in days:
        p = f"{REC}/{d}/sync_probe.jsonl"
        if not os.path.exists(p):
            continue
        for ln in open(p):
            try:
                rows.append(json.loads(ln))
            except Exception:
                pass
    rows.sort(key=lambda r: r.get("t", 0))
    logs = defaultdict(list)
    seen_log = set()
    seg_of = {}
    segs = defaultdict(list)  # host -> list of dict(start_t, cand, exact)
    last_upd = {}
    for r in rows:
        h = r.get("host")
        ms = r.get("ms") or {}
        for ln in r.get("log") or []:
            if (h, ln) not in seen_log:
                seen_log.add((h, ln))
                logs[h].append(ln)
        u = ms.get("updated")
        if u is None:
            continue
        if h not in segs or (last_upd.get(h) is not None and u < last_upd[h] - 600_000):
            segs[h].append(dict(start_t=r["t"], cand=[], exact=[]))
        last_upd[h] = u
        seg = segs[h][-1]
        seg["cand"].append(r["t"] - u / 1000.0)
        for ln in r.get("log") or []:
            m = _WHAD.match(ln)
            if m:
                seg["exact"].append(float(m.group(1)) - int(m.group(2)) / 1000.0)
        seg_of[id(r)] = seg
    for h, ss in segs.items():
        allx = [x for s in ss for x in s["exact"]]
        hx = st.median(allx) if allx else None
        for s in ss:
            if s["exact"]:
                s["boot"], s["boot_src"] = st.median(s["exact"]), "whad_log"
            elif hx is not None and -15 < min(s["cand"]) - hx < 15:
                s["boot"], s["boot_src"] = hx, "whad_log(host)"
            else:  # row 't' is stamped at cycle start, so this can sit a few s early
                s["boot"], s["boot_src"] = min(s["cand"]), "min(t-updated)"
    events, seen = [], set()
    for r in rows:
        ms = r.get("ms") or {}
        if ms.get("pkg") != PKG or not ms.get("updated") or ms.get("position") is None:
            continue
        k = (r["host"], ms["updated"])
        if k in seen:
            continue
        seen.add(k)
        seg = seg_of.get(id(r))
        if not seg:
            continue
        e = mk_event(r["host"], ms, seg["boot"])
        e["role"] = r.get("role")
        e["t_seen"] = r["t"]
        events.append(e)
    events.sort(key=lambda e: e["w"])
    for h in logs:
        logs[h].sort()
    return events, logs, segs


def load_plays(days, league="nba"):
    out = []
    for d in days:
        p = f"{REC}/{d}/plays.jsonl"
        if not os.path.exists(p):
            continue
        for ln in open(p):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if league and r.get("league") != league:
                continue
            out.append(r)
    out.sort(key=lambda r: r.get("wall") or 0)
    return out


def live_plays():
    return load_plays([datetime.now().strftime("%Y-%m-%d")])


# ---------------------------------------------------------------- anchors (real world)
def _ty(p):
    return (p.get("type") or "").lower()


def is_timeout(p):
    t = _ty(p)
    return "timeout" in t and "no timeout" not in t


def is_period_end(p):
    t, x = _ty(p), (p.get("text") or "").lower()
    return t in ("period", "end period") and ("end" in x) or t == "end period"


def is_dead(p):
    t = _ty(p)
    return (is_timeout(p) or "substitution" in t or t in ("period", "end period", "end game")
            or "challenge" in t or "replay" in t or "instant" in t)


def anchors(plays, game=None):
    """Real-world break anchors per game: dict(game, kind, wall, restart, len, text).
    kind: timeout | period_end | halftime. restart = first live (non-dead) play after the break,
    deduped across sources (nba_cdn/espn_summary/fastcast report the same timeout)."""
    by = defaultdict(list)
    for p in plays:
        if game and p.get("game") != str(game):
            continue
        by[p["game"]].append(p)
    out = []
    for g, ps in by.items():
        ps.sort(key=lambda p: p["wall"])
        live = [p for p in ps if not is_dead(p)]
        cands = []
        for p in ps:
            if is_timeout(p):
                cands.append(("timeout", p))
            elif is_period_end(p):
                cands.append(("period_end", p))
        last = None
        for kind, p in cands:
            if last and abs(p["wall"] - last["wall"]) < 45 and (last["base"] == kind or kind == "timeout"):
                # duplicate of the same break from another source: keep the ms-precise nba_cdn wall
                if p.get("source") == "nba_cdn" and last["source"] != "nba_cdn":
                    last.update(wall=p["wall"], source="nba_cdn")
                    if last["restart"]:
                        last["len"] = last["restart"] - last["wall"]
                continue
            nxt = next((q for q in live if q["wall"] > p["wall"] + 5), None)
            prv = next((q for q in reversed(live) if q["wall"] <= p["wall"]), None)
            a = dict(game=g, kind=kind, base=kind, wall=p["wall"], text=(p.get("text") or "")[:40], source=p.get("source"),
                     restart=nxt["wall"] if nxt else None, restart_type=(nxt or {}).get("type"),
                     last_live=prv["wall"] if prv else None)
            a["len"] = (a["restart"] - a["wall"]) if a["restart"] else None
            if kind == "period_end" and a["len"] and a["len"] > 600:
                a["kind"] = "halftime"
            out.append(a)
            last = a
    out.sort(key=lambda a: a["wall"])
    return out


def game_for_title(title, plays):
    """Map 'X at Y' YTTV title to an ESPN game id by team names in that game's timeout texts."""
    tl = (title or "").lower()
    codes = {c for nick, c in NBA.items() if nick in tl}
    nicks = {nick for nick in NBA if nick in tl}
    if len(codes) < 2:
        return None
    score = defaultdict(int)
    for p in plays:
        x = (p.get("text") or "").lower()
        if is_timeout(p) and (any(c.lower() in x.split() for c in codes) or any(n in x for n in nicks)):
            score[p["game"]] += 1
    return max(score, key=score.get) if score else None


# ---------------------------------------------------------------- pods (stick side)
def segments(events, jump=3.0):
    """Split events per host into tune segments (same subtitle, no position jump > `jump` s)."""
    by = defaultdict(list)
    for e in events:
        by[e["host"]].append(e)
    segs = []
    for h, es in by.items():
        cur = []
        for e in es:
            if cur:
                a = cur[-1]
                gap = e["w"] - a["w"]
                exp = a["pos"] + gap * (a["speed"] if a["st"] == "playing" else 0)
                landed = abs(e["pos"] - exp) > 3 and e["pos"] < LANDING_MAX  # deep-link retune lands ~46.8k s
                if e["sub"] != a["sub"] or abs(e["pos"] - exp) > jump or gap > 1800 or landed:
                    segs.append(cur)
                    cur = []
            cur.append(e)
        if cur:
            segs.append(cur)
    return segs


def find_pods(events, lo=8.0, hi=17.0, min_bounds=2, tol=2.0):
    """DAI ad pods: runs of consecutive media_session updates on one stick, same title, gaps lo..hi s,
    position advancing ~= the gap (playing). Returns list of dicts with spot boundary walls."""
    pods = []
    by = defaultdict(list)
    for e in events:
        by[e["host"]].append(e)
    for h, es in by.items():
        cur = []
        for a, b in zip(es, es[1:]):
            gap = b["w"] - a["w"]
            dpos = b["pos"] - a["pos"]
            ok = (a["title"] == b["title"] and a["sub"] == b["sub"] and lo <= gap <= hi
                  and abs(dpos - gap) < tol and b["st"] == "playing")
            if ok:
                if not cur:
                    cur = [a]
                cur.append(b)
            else:
                if len(cur) >= min_bounds:
                    pods.append(_pod(cur))
                cur = []
        if len(cur) >= min_bounds:
            pods.append(_pod(cur))
    pods.sort(key=lambda p: p["start"])
    return pods


def _pod(cur):
    walls = [e["w"] for e in cur]
    gaps = [b - a for a, b in zip(walls, walls[1:])]
    spot = st.median(gaps)
    return dict(host=cur[0]["host"], h=cur[0]["h"], sub=cur[0]["sub"], title=cur[0]["title"],
                start=walls[0], last=walls[-1], end_est=walls[-1] + spot, spots=len(cur), spot_med=round(spot, 2),
                gaps=[round(g, 2) for g in gaps], bounds=walls, pos0=cur[0]["pos"], E=walls[0] - cur[0]["pos"])


def chan_key(sub):
    return (sub or "").lower().replace(" ", "")


def filter_events(events, args, hosts=None):
    out = events
    if args.channel:
        out = [e for e in out if chan_key(e["sub"]) == chan_key(args.channel)]
    if hosts:
        out = [e for e in out if e["host"] in hosts]
    return out


def dry_hosts(args):
    a = ALIASES.get((args.stick or "").lower(), (args.stick or "").lower())
    return [HOSTS[a]] if a in HOSTS else list(HOSTS.values())


def match_pod_anchor(pod, anc, plays, game=None, dmin=5.0, dmax=90.0):
    """Best real-world break for a stick pod: D_start = pod.start - anchor.wall and
    D_end = pod.end_est - anchor.restart, both must lie in [dmin, dmax]. Game restricted to `game`
    or the title's game when resolvable. Returns (anchor, D_start, D_end) or None."""
    g = game or game_cached(pod["title"], plays)
    if not g:
        return None  # pod title is not an NBA game we have plays for (studio show, NHL, ...)
    best = None
    for a in anc:
        if g and a["game"] != g:
            continue
        ds = pod["start"] - a["wall"]
        de = (pod["end_est"] - a["restart"]) if a["restart"] else None
        if dmin <= ds <= dmax:
            score = abs(ds - PRIOR_ESPN[0]) + (abs(de - ds) if de is not None else 30)
        elif de is not None and dmin <= de <= dmax and a["wall"] + dmin <= pod["start"] <= a["restart"] + dmax:
            # partial pod: the stick joined (or the poll caught) the break mid-way; only the end anchors
            ds, score = None, abs(de - PRIOR_ESPN[0]) + 20
        else:
            continue
        if best is None or score < best[0]:
            best = (score, a, ds, de)
    if not best:
        return None
    r = lambda x: round(x, 2) if x is not None else None  # noqa: E731
    return best[1], r(best[2]), r(best[3]), g


# ---------------------------------------------------------------- logcat parsing
_BR = re.compile(r"^(\d{9,}\.\d+)\s.*MediaCodecLogger:.*video\.([\w]+)\.bitrateInKbps\s*=\s*(\d+)")
_VPUD = re.compile(r"^(\d{9,}\.\d+)\s.*VPUD\s*:\s*Resolution Changed!!BitStream WxH = \((\d+)x(\d+)\)")


def bitrate_series(lines):
    out = []
    for ln in lines:
        m = _BR.search(ln)
        if m:
            out.append((float(m.group(1)), m.group(2), int(m.group(3))))
    return sorted(out)


def codec_file_lines(host):
    """Lines of SYNC/codec_<ip>.log (codec_logger.sh output) with the leading Mac stamp/ip stripped."""
    p = f"{SYNC}/codec_{host.split(':')[0]}.log"
    if not os.path.exists(p):
        return []
    out = []
    for ln in open(p):
        parts = ln.split(None, 2)
        if len(parts) == 3:
            out.append(parts[2].strip())
    return out


def res_changes(lines):
    out = []
    for ln in lines:
        m = _VPUD.search(ln)
        if m:
            out.append((float(m.group(1)), int(m.group(2)), int(m.group(3))))
    return sorted(out)


def log_epoch(ln):
    m = re.match(r"^\s*(\d{9,}\.\d+)\s", ln)
    return float(m.group(1)) if m else None


def log_tag(ln):
    m = re.match(r"^\s*\d{9,}\.\d+\s+\d+\s+\d+\s+[VDIWEF]\s+(.+?)\s*:", ln)
    return m.group(1).strip()[:28] if m else None


def run_or_dry(args, out, dry_fn, live_fn, would):
    """Common entry: dry-run prints what live would measure then analyses recordings; exit 0."""
    if args.dry_run:
        print(f"[{out.id}] DRY-RUN. Live run WOULD: {would}")
        out.w("would", text=would)
        dry_fn()
        return 0
    return live_fn() or 0


def main(test_id, doc, analyse, would, hosts_mode="either", log_tags=None, extra_args=None):
    """Generic runner. analyse(events, logs, plays, args, out, ctx) -> None (writes rows + summary).
    hosts_mode: 'either' (read-only, default hidden stick), 'both' (needs both sticks)."""
    p = make_parser(test_id, doc)
    if extra_args:
        extra_args(p)
    args = p.parse_args()
    out = Out(test_id, args)
    if args.dry_run:
        days = days_of(args)
        print(f"[{test_id}] DRY-RUN on {days}. Live run WOULD: {would}")
        out.w("would", text=would, days=days)
        ev, logs, segs = load_probe(days)
        plays = load_plays(days)
        hosts = dry_hosts(args) if hosts_mode != "both" else list(HOSTS.values())
        ev = filter_events(ev, args, hosts)
        ctx = dict(hosts=hosts, boot={h: [round(s["boot"], 3) for s in segs.get(h, [])] for h in hosts},
                   boot_src={h: sorted({s["boot_src"] for s in segs.get(h, [])}) for h in hosts})
        out.w("dataset", n_events=len(ev), n_plays=len(plays), log_lines={h: len(logs.get(h, [])) for h in hosts},
              **ctx)
        analyse(ev, logs, plays, args, out, ctx)
        return 0
    vis, why = visible_hosts()
    hosts = list(HOSTS.values()) if hosts_mode == "both" else resolve_sticks(args.stick)
    out.w("safety", visible=sorted(vis), why=why, hosts=hosts, actions="read-only (dumpsys/logcat)")
    print(f"[{test_id}] LIVE read-only on {hosts} for {args.minutes} min ({why})")
    ev, logs, boot = collect_live(hosts, args.minutes, args.poll, logcat=True,
                                  tags=DEFAULT_TAGS if log_tags is None else (log_tags or ""))
    plays = live_plays()
    ev = filter_events(ev, args, hosts)
    analyse(ev, logs, plays, args, out, dict(hosts=hosts, boot=boot, visible=sorted(vis)))
    return 0



_GT = {}


def game_cached(title, plays):
    k = (title, len(plays))
    if k not in _GT:
        _GT[k] = game_for_title(title, plays)
    return _GT[k]


def showing(ev, host, t):
    """Last media_session event on `host` at or before wall t (what the stick was playing)."""
    last = None
    for e in ev:
        if e["host"] != host:
            continue
        if e["w"] > t:
            break
        last = e
    return last


def stick_on_game(ev, host, t, game, plays):
    e = showing(ev, host, t)
    return bool(e and e["st"] == "playing" and game_cached(e["title"], plays) == game)


def seg_index(ev, jump=3.0):
    """(host, wall) -> segment number, plus the segments list (tune boundaries)."""
    segs = segments(ev, jump)
    idx = {}
    for i, sg in enumerate(segs):
        for e in sg:
            idx[(e["host"], e["w"])] = i
    return idx, segs
