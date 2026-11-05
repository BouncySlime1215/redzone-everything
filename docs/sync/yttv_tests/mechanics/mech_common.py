"""Shared plumbing for the YTTV "mechanics" test family.

Safety model (every live action goes through Guard):
  * Screen state comes from GET http://localhost:8778/api/state (health.sticks[].role == "on screen" /
    "standby", health.agent.on_screen). If that fails or is ambiguous, BOTH sticks count as visible and the
    test runs read-only (Guard refuses every key press / tune).
  * Guard re-reads screen state before EVERY action (the app may flip sticks mid-test).
  * One per-host lock file so two tests never drive the same stick at once.
  * Polls sleep >= 0.5 s. No Peacock, no secrets, no training.

Recorded-data helpers (dry runs): sync_probe rows carry ms.position/updated (stick uptime ms) but no uptime at
sample time, so each stick's boot epoch is estimated as min(t - updated/1000) per boot segment (tight to the
poll latency, ~0.1-0.5 s). E_rec = t - pos_now/1000 with pos_now extrapolated from that boot estimate.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import re
import statistics as st
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
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


SYNC = Path(_sync_dir())
REPO = Path(__import__("os").environ.get("RZ_REPO") or "/Users/nick_matta/Documents/Codex/2026-11-02/task-2/redzone")
RESULTS = Path(__file__).resolve().parent.parent / "results"
REC = REPO / "data" / "recordings"
for p in (str(SYNC), str(REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)

import t1_retune as T  # noqa: E402  read(host), sh(host, cmd), tune(host, ch), CODES, PKG

ALL_CODES = {"espn": "ferg3lVdMOg", "espn2": "qHoTWZ9M9gw", "tnt": "lVQYDh9weHg", "tbs": "vv_r1X6N3sc",
             "nbatv": "ykTstVbuNpg", "fs1": "_2ONrjDR7S8", "usa": "ZgiS2N0QkbU", "trutv": "x2IdxPvLNC0",
             "fox": "njJRoIW1Lps", "cbssn": "XG1jwCkVgUE", "nflnetwork": "_pYg9qMKKIA", "espnu": "6x-T96Q-5eY",
             "btn": "1D2LKf_AW5w"}
T.CODES.update(ALL_CODES)
PKG = T.PKG
HOSTS = {"hdmi1": _STICKS[0], "hdmi2": _STICKS[1]}
STATE_URL = "http://localhost:8778/api/state"
# PlaybackState action bits (android.media.session.PlaybackState)
ACTION_BITS = {1: "STOP", 2: "PAUSE", 4: "PLAY", 8: "REWIND", 16: "SKIP_PREV", 32: "SKIP_NEXT", 64: "FAST_FORWARD",
               128: "SET_RATING", 256: "SEEK_TO", 512: "PLAY_PAUSE", 1024: "PLAY_FROM_MEDIA_ID",
               2048: "PLAY_FROM_SEARCH", 4096: "SKIP_TO_QUEUE_ITEM", 8192: "PLAY_FROM_URI", 16384: "PREPARE",
               262144: "SET_PLAYBACK_SPEED"}
DEFAULT_DATES = ("2026-11-04", "2026-11-05")
# boot epochs measured read-only with stick_clock() on 2026-11-05 ~03:30 ET (valid until a stick reboots;
# recorded-data estimates were 5.0 s / 5.9 s low because sync_probe's t precedes its read)
KNOWN_BOOT = {_STICKS[0]: 1793752328.94, _STICKS[1]: 1793650371.46}


# ----------------------------------------------------------------------------------------------- CLI / output
def parser(test_id: str, doc: str, minutes: float = 10.0, channel: str = "espn") -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=test_id, description=(doc or "").split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=doc)
    ap.add_argument("--stick", default="hidden",
                    help="hidden (default) | visible | hdmi1 | hdmi2 | both | host:port. Actions only ever "
                         "run on a stick the app reports as hidden.")
    ap.add_argument("--channel", default=channel, help=f"YTTV channel key ({', '.join(ALL_CODES)})")
    ap.add_argument("--game", default=None, help="ESPN game id (used to pull plays for context)")
    ap.add_argument("--minutes", type=float, default=minutes, help="live time budget")
    ap.add_argument("--dry-run", action="store_true", help="analyse recorded files only; touch no stick")
    ap.add_argument("--out", default=str(RESULTS / f"{test_id}.jsonl"))
    ap.add_argument("--dates", default=",".join(DEFAULT_DATES), help="recording dates for --dry-run")
    ap.add_argument("--state-url", default=STATE_URL)
    return ap


class Out:
    def __init__(self, path: str, test_id: str, dry: bool):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.f = open(path, "a")
        self.id, self.dry = test_id, dry
        self.rows = 0

    def __call__(self, kind: str, **kw) -> dict:
        row = dict(test=self.id, kind=kind, dry_run=self.dry, t=round(time.time(), 3))
        for k, v in kw.items():  # never clobber the envelope (e.g. a recorded row's own 't')
            row[k + "_ev" if k in row else k] = v
        self.f.write(json.dumps(row, default=str) + "\n")
        self.f.flush()
        self.rows += 1
        print(json.dumps(row, default=str)[:400], flush=True)
        return row

    def verdict(self, passed: bool | None, rule: str, **kw) -> dict:
        return self("verdict", passed=passed, rule=rule, **kw)


def summary(xs) -> dict:
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    if not xs:
        return dict(n=0)
    xs = sorted(xs)
    q = lambda p: xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]  # noqa: E731
    return dict(n=len(xs), mean=round(st.fmean(xs), 3), sd=round(st.pstdev(xs), 3) if len(xs) > 1 else 0.0,
                min=round(xs[0], 3), p50=round(q(.5), 3), p90=round(q(.9), 3), max=round(xs[-1], 3))


def slope(ts, ys) -> float | None:
    if len(ts) < 2:
        return None
    mt, my = st.fmean(ts), st.fmean(ys)
    den = sum((t - mt) ** 2 for t in ts)
    return None if den == 0 else sum((t - mt) * (y - my) for t, y in zip(ts, ys)) / den


def chan_key(subtitle: str | None) -> str:
    s = (subtitle or "").lower().replace(" ", "")
    return {"nflnetwork": "nflnetwork", "nfl": "nflnetwork"}.get(s, s)


def decode_actions(mask: int | None) -> list[str]:
    return [] if mask is None else [n for b, n in ACTION_BITS.items() if mask & b]


# ----------------------------------------------------------------------------------------------- screen safety
def screen_state(url: str = STATE_URL) -> dict:
    """{'visible': set(hosts), 'hidden': set(hosts), 'ok': bool, 'why': str}. Fails safe (both visible)."""
    both = set(HOSTS.values())
    try:
        with urllib.request.urlopen(url, timeout=2.5) as r:
            d = json.load(r)
    except Exception as e:  # app down -> unknown -> read-only
        return dict(visible=both, hidden=set(), ok=False, why=f"state fetch failed: {e!r}"[:160])
    vis, hid = set(), set()
    for s in ((d.get("health") or {}).get("sticks") or []):
        role = str(s.get("role") or "").lower().replace("_", " ")
        h = s.get("host")
        if not h:
            continue
        if role in ("on screen", "onscreen", "visible", "active", "live"):
            vis.add(h)
        elif role in ("standby", "hidden", "parked", "off screen", "offscreen"):
            hid.add(h)
    on = ((d.get("health") or {}).get("agent") or {}).get("on_screen")
    if on:
        vis.add(on)
        hid.discard(on)
    if (d.get("health") or {}).get("switch_times", {}).get("input", {}).get("unknown"):
        return dict(visible=both, hidden=set(), ok=False, why="TV input unknown")
    if len(vis) != 1 or vis & hid or not (vis | hid) >= both:
        return dict(visible=both, hidden=set(), ok=False, why=f"ambiguous roles vis={vis} hid={hid}")
    return dict(visible=vis, hidden=both - vis, ok=True, why="api/state health.sticks")


def recorded_screen_state() -> dict:
    """Dry-run stand-in: no network, pretend unknown (fail safe) so dry runs exercise the refusal path."""
    return dict(visible=set(HOSTS.values()), hidden=set(), ok=False, why="dry-run: screen state not queried")


def resolve(stick: str, ss: dict) -> list[str]:
    s = stick.lower()
    if s == "hidden":
        return sorted(ss["hidden"])
    if s == "visible":
        return sorted(ss["visible"] if ss["ok"] else [])
    if s == "both":
        return sorted(HOSTS.values())
    if s in HOSTS:
        return [HOSTS[s]]
    if re.match(r"^\d+\.\d+\.\d+\.\d+(:\d+)?$", s):
        return [s if ":" in s else s + ":5555"]
    raise SystemExit(f"unknown --stick {stick!r}")


class Refused(RuntimeError):
    pass


class Guard:
    """Every key press / tune goes through here. Re-checks screen state each time."""

    def __init__(self, host: str, out: Out, url: str = STATE_URL):
        self.host, self.out, self.url = host, out, url
        self.lock = None

    def __enter__(self):
        RESULTS.mkdir(parents=True, exist_ok=True)
        self.lock = open(RESULTS / f".lock_{self.host.replace(':', '_')}", "w")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Refused(f"another test holds the lock for {self.host}")
        return self

    def __exit__(self, *a):
        if self.lock:
            fcntl.flock(self.lock, fcntl.LOCK_UN)
            self.lock.close()

    def check(self):
        ss = screen_state(self.url)
        if not ss["ok"] or self.host not in ss["hidden"]:
            raise Refused(f"{self.host} not confirmed hidden ({ss['why']})")

    def key(self, code: str | int):
        self.check()
        self.out("action", host=self.host, key=str(code))
        return T.sh(self.host, f"input keyevent {code}")

    def dispatch(self, name: str):
        """media_session dispatch: play, pause, play-pause, stop, next, previous, rewind, fast-forword (sic)."""
        self.check()
        self.out("action", host=self.host, dispatch=name)
        return T.sh(self.host, f"cmd media_session dispatch {name}")

    def tune(self, ch: str) -> dict:
        if ch not in T.CODES:
            raise Refused(f"no deep-link code for {ch!r}")
        if ch == "peacock":
            raise Refused("never Peacock")
        self.check()
        self.out("action", host=self.host, tune=ch)
        return T.tune(self.host, ch)


def read(host: str) -> dict | None:
    """t1_retune.read plus raw position/actions (one extra parse of the same dumpsys text)."""
    t0 = time.time()
    out = T.sh(host, "cat /proc/uptime; dumpsys media_session")
    t1 = time.time()
    try:
        up = float(out.split()[0]) * 1000
    except (ValueError, IndexError):
        return None
    from redzone.tuners.adb import parse_playback
    b = parse_playback(out, PKG)
    if b.get("position") is None or b.get("updated") is None:
        return None
    sp = b.get("speed", 1.0) if b["state"] == "playing" else 0.0
    pos_now = b["position"] + (up - b["updated"]) * sp
    m = re.search(r"package=" + re.escape(PKG) + r".*?actions=(\d+)", out, re.S)
    return dict(st=b["state"], sub=b.get("subtitle", ""), title=b.get("title", ""), upd=b["updated"],
                pos=b["position"] / 1000, pos_now=pos_now / 1000, up=up / 1000, rtt=round(t1 - t0, 3),
                E=(t0 + t1) / 2 - pos_now / 1000, t=(t0 + t1) / 2, actions=int(m.group(1)) if m else None)


def wait_settle(host: str, hold_s: float = 2.0, timeout: float = 12.0, poll: float = 0.5) -> tuple[dict | None, float]:
    """Poll until `updated` stops changing for hold_s. Returns (reading, seconds to settle)."""
    t0 = time.time()
    last, last_change = None, t0
    r = None
    while time.time() - t0 < timeout:
        r = read(host)
        key = r and (r["upd"], r["st"])
        if key != last:
            last, last_change = key, time.time()
        elif time.time() - last_change >= hold_s:
            return r, round(last_change - t0, 2)
        time.sleep(max(0.5, poll))
    return r, None


def wifi(host: str) -> dict:
    out = T.sh(host, "dumpsys wifi | grep -m1 mWifiInfo")
    g = lambda pat: (re.search(pat, out) or [None, None])[1]  # noqa: E731
    return dict(rssi=int(g(r"RSSI: (-?\d+)")) if g(r"RSSI: (-?\d+)") else None,
                link=g(r"Link speed: (\d+)"), freq=g(r"Frequency: (\d+)"))


def stick_clock(host: str) -> dict:
    t0 = time.time()
    out = T.sh(host, "date +%s.%N; cat /proc/uptime")
    t1 = time.time()
    parts = out.split()
    try:
        dev = float(parts[0])
        up = float(parts[1])
    except (ValueError, IndexError):
        return dict(err=out[:80])
    mid = (t0 + t1) / 2
    return dict(offset_s=round(dev - mid, 3), boot_epoch=round(mid - up, 3), rtt=round(t1 - t0, 3))


def preflight(args, out: Out, need_action: bool) -> tuple[list[str], dict]:
    """Resolve target sticks; for action tests drop any stick not confirmed hidden."""
    ss = screen_state(args.state_url)
    hosts = resolve(args.stick, ss)
    out("preflight", screen=dict(visible=sorted(ss["visible"]), hidden=sorted(ss["hidden"]), ok=ss["ok"],
                                 why=ss["why"]), targets=hosts, need_action=need_action)
    if need_action:
        bad = [h for h in hosts if h not in ss["hidden"]]
        if bad:
            out("refused", hosts=bad, why="action test on a stick not confirmed hidden -> read-only/skip")
        hosts = [h for h in hosts if h in ss["hidden"]]
    return hosts, ss


# ----------------------------------------------------------------------------------------------- recordings
def load_jsonl(path: Path):
    if not path.exists():
        return []
    rows = []
    with open(path) as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def load_probe(dates: str) -> list[dict]:
    rows = []
    for d in dates.split(","):
        rows += load_jsonl(REC / d.strip() / "sync_probe.jsonl")
    rows.sort(key=lambda r: r["t"])
    return rows


def load_plays(dates: str, game: str | None = None) -> list[dict]:
    rows = []
    for d in dates.split(","):
        rows += [r for r in load_jsonl(REC / d.strip() / "plays.jsonl") if game is None or r.get("game") == game]
    rows.sort(key=lambda r: r.get("wall") or 0)
    return rows


def yt_rows(rows: list[dict], host: str | None = None) -> list[dict]:
    return [r for r in rows if (r.get("ms") or {}).get("pkg") == PKG and r["ms"].get("position") is not None
            and r["ms"]["position"] >= 1000  # -1 / 0 = no media loaded (E would just track wall time)
            and r["ms"].get("updated") is not None and (host is None or r["host"] == host)]


def boot_segments(rows: list[dict]) -> dict[str, list[tuple[float, float]]]:
    """{host: [(t_from, boot_epoch_est), ...]}. Only rows whose `updated` just changed ("fresh") vote, since a
    fresh update happened within one poll before t: boot ~= t - updated/1000 (upper bound; min is kept).
    A fresh vote > 60 s above the current estimate starts a new segment (reboot, or a stale session that the
    next fresh votes pull back down via the min)."""
    segs: dict[str, list] = {}
    cur: dict[str, list] = {}
    last_upd: dict[str, int] = {}
    for r in rows:
        ms = r.get("ms") or {}
        u = ms.get("updated")
        if u is None:
            continue
        h = r["host"]
        fresh = last_upd.get(h) is not None and u != last_upd[h]
        first = h not in cur
        last_upd[h] = u
        if not (fresh or first):
            continue
        b = r["t"] - u / 1000
        c = cur.get(h)
        if c is None or b > c[1] + 60:
            c = [r["t"], b]
            segs.setdefault(h, []).append(c)
            cur[h] = c
        else:
            c[1] = min(c[1], b)
    out = {}
    for h, v in segs.items():
        # boot epochs only move forward across reboots: a segment above some LATER segment is a stale vote
        v = [(a, b) for i, (a, b) in enumerate(v) if not any(b2 < b - 60 for _, b2 in v[i + 1:])]
        m = []  # merge segments that converged to the same boot
        for a, b in v:
            if m and abs(m[-1][1] - b) < 60:
                m[-1][1] = min(m[-1][1], b)
            else:
                m.append([a, b])
        # The recorder stamps `t` up to ~6 s BEFORE its dumpsys read, so min(t - updated) sits 5-6 s below the
        # true boot. When a directly measured boot (stick_clock) matches within 30 s, use it instead.
        out[h] = [(a, KNOWN_BOOT[h] if h in KNOWN_BOOT and abs(KNOWN_BOOT[h] - b) < 30 else b) for a, b in m]
    return out


def boot_at(segs: dict, host: str, t: float) -> float | None:
    best = None
    for t_from, b in segs.get(host, []):
        if t_from <= t + 1:
            best = b
    return best


def rec_E(r: dict, segs: dict) -> float | None:
    ms = r["ms"]
    boot = boot_at(segs, r["host"], r["t"])
    if boot is None:
        return None
    up_ms = (r["t"] - boot) * 1000
    sp = ms.get("speed", 1.0) if ms.get("state") == "playing" else 0.0
    return r["t"] - (ms["position"] + (up_ms - ms["updated"]) * sp) / 1000


def tune_events(rows: list[dict], segs: dict, jump_s: float = 2.0) -> list[dict]:
    """Recorded landings: a new YTTV playing segment (channel change or E jump > jump_s after non-play).
    L = landing position (s) at the first PLAYING update after the change; E_land from that row."""
    ev, prev = [], {}
    for r in yt_rows(rows):
        ms, h = r["ms"], r["host"]
        if ms.get("state") != "playing":
            prev[h] = dict(r=r, E=None, playing=False, sub=chan_key(ms.get("subtitle")))
            continue
        E = rec_E(r, segs)
        p = prev.get(h)
        sub = chan_key(ms.get("subtitle"))
        new = p is None or sub != p["sub"] or (p["E"] is not None and E is not None and abs(E - p["E"]) > jump_s)
        if new and p is not None and sub and ms["position"] > 1000:
            ev.append(dict(host=h, ch=sub, t=r["t"], L=round(ms["position"] / 1000, 3),
                           upd_age_s=None if boot_at(segs, h, r["t"]) is None else
                           round(r["t"] - boot_at(segs, h, r["t"]) - ms["updated"] / 1000, 2),
                           E_land=None if E is None else round(E, 2), from_ch=p["sub"],
                           from_playing=p["playing"], role=r.get("role"), title=(ms.get("title") or "")[:30]))
        prev[h] = dict(r=r, E=E, playing=True, sub=sub)
    return ev


def playing_runs(rows: list[dict], segs: dict, host: str, jump_s: float = 1.0) -> list[list[tuple]]:
    """Runs of (t, E, row) while PLAYING on one channel with E continuous within jump_s."""
    runs, cur, last = [], [], None
    for r in yt_rows(rows, host):
        ms = r["ms"]
        E = rec_E(r, segs) if ms.get("state") == "playing" else None
        key = chan_key(ms.get("subtitle"))
        if E is None or (last and (key != last[0] or abs(E - last[1]) > jump_s)):
            if len(cur) > 1:
                runs.append(cur)
            cur = []
        if E is not None:
            cur.append((r["t"], E, r))
            last = (key, E)
        else:
            last = None
    if len(cur) > 1:
        runs.append(cur)
    return runs


def note_overnight_running() -> str | None:
    try:
        out = subprocess.run(["pgrep", "-fl", "overnight.py"], capture_output=True, text=True, timeout=3).stdout
        return out.strip() or None
    except Exception:
        return None


# ----------------------------------------------------------------------------------------------- live helpers 2
def tune_measure(g: Guard, ch: str, timeout: float = 25.0) -> dict:
    """Deep-link tune with precise timing. play_s_poll = Mac poll at first PLAYING on the new channel;
    play_s_upd = (boot + updated/1000) - t_cmd, i.e. the stick's own timestamp of the PLAYING update.
    L = position (s) at that first PLAYING update."""
    if ch not in T.CODES:
        raise Refused(f"no deep-link code for {ch!r}")
    g.check()
    before = read(g.host)
    clk = stick_clock(g.host)
    t_cmd = time.time()
    g.out("action", host=g.host, tune=ch)
    T.sh(g.host, f"am start -a android.intent.action.VIEW -d https://tv.youtube.com/watch/{T.CODES[ch]} {PKG}")
    while time.time() - t_cmd < timeout:
        r = read(g.host)
        if r and r["st"] == "playing" and chan_key(r["sub"]) in (ch, ch.replace("network", "")) \
                and (before is None or r["upd"] != before["upd"]):
            boot = clk.get("boot_epoch")
            return dict(ch=ch, ok=True, t_cmd=round(t_cmd, 3), play_s_poll=round(r["t"] - t_cmd, 2),
                        play_s_upd=None if boot is None else round(boot + r["upd"] / 1000 - t_cmd, 3),
                        L=round(r["pos"], 3), E_land=round(r["E"], 3), title=r["title"][:30],
                        boot_epoch=boot, rtt=r["rtt"])
        time.sleep(0.5)
    r = read(g.host)
    return dict(ch=ch, ok=False, t_cmd=round(t_cmd, 3), sub=r and r["sub"], st=r and r["st"])


def codec_lines_since(host: str, epoch: float, n: int = 400) -> list[tuple[float, str]]:
    """Read-only: MediaCodecLogger logcat lines with device epoch stamps after `epoch`."""
    out = T.sh(host, f"logcat -d -v epoch -t {n} -s MediaCodecLogger:I", timeout=10)
    res = []
    for line in out.splitlines():
        m = re.match(r"\s*(\d{9,}\.\d+)\s+(.*)$", line)
        if m and float(m.group(1)) >= epoch:
            res.append((float(m.group(1)), m.group(2)))
    return res


def run(test_id: str, doc: str, dry, live, minutes: float = 10.0, channel: str = "espn", extra=None) -> None:
    ap = parser(test_id, doc, minutes, channel)
    if extra:
        extra(ap)
    args = ap.parse_args()
    out = Out(args.out, test_id, args.dry_run)
    out("start", args=vars(args))
    if args.dry_run:
        try:
            dry(args, out)
        except Exception as e:  # dry runs must exit 0; record the failure instead
            import traceback
            out("dry_error", err=repr(e)[:300], tb=traceback.format_exc()[-800:])
            out("end", rows=out.rows)
            raise
        out("end", rows=out.rows)
        sys.exit(0)
    bg = note_overnight_running()
    if bg:
        out("warning", why="overnight.py is running and drives the hidden stick; results may collide", ps=bg)
    try:
        live(args, out)
    except Refused as e:
        out("refused", why=str(e))
        out("end", rows=out.rows)
        sys.exit(2)
    except KeyboardInterrupt:
        out("interrupted")
    out("end", rows=out.rows)
