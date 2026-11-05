"""Shared code for the YTTV "fusion" test family (estimators, validation, join decision).

Model (measured facts, see the family README in each test docstring):
  E(t)  = wall - extrapolated_position          (seconds; constant while playing, +pause while paused)
  D(t)  = seconds the picture is behind the real-world event
  A deep-link tune lands at the live edge, so per (channel, stick) there is one parameter D_edge:
        D(t) = D_edge + (E(t) - E_land)           (E_land = E right after the tune landed)
  Equivalently, within one playback session the "origin" O = E - D is constant, so any anchor
  (a moment where we know D) in a session pins every other moment of that session exactly.

Anchors are rows {family, channel, stick, t, D, sd} coming from
  * other test families' result files (results/*.jsonl, any row with channel + D + sd), and
  * the recorded data (dry run): ad-pod return vs. restart play ("burst_pod", weak, sd ~ 8 s).
Fusion converts every anchor to a D_edge observation using the session's E track, then combines.

All adb use here is READ-ONLY (cat /proc/uptime, dumpsys media_session). Nothing presses keys
or tunes. `require_hidden()` is still provided and used by any code path that would act.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import random
import statistics
import sys
import time
import urllib.request
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
SYNC = os.path.abspath(os.path.join(HERE, "..", ".."))           # scratchpad/sync
RESULTS = os.path.abspath(os.path.join(HERE, "..", "results"))
REPO = "/Users/nick_matta/Documents/Codex/2026-11-02/task-2/redzone"
DATA = os.path.join(REPO, "data")
REC = os.path.join(DATA, "recordings")
for p in (SYNC, REPO):
    if p not in sys.path:
        sys.path.insert(0, p)

PKG = "com.amazon.firetv.youtube.tv"
HOSTS = {"hdmi1": "100.110.165.150:5555", "hdmi2": "100.110.165.181:5555"}
SHORT = {v: k for k, v in HOSTS.items()}
CODES = {"espn": "ferg3lVdMOg", "espn2": "qHoTWZ9M9gw", "tnt": "lVQYDh9weHg", "tbs": "vv_r1X6N3sc",
         "nbatv": "ykTstVbuNpg", "fs1": "_2ONrjDR7S8", "usa": "ZgiS2N0QkbU", "trutv": "x2IdxPvLNC0",
         "fox": "njJRoIW1Lps", "cbssn": "XG1jwCkVgUE", "nflnetwork": "_pYg9qMKKIA", "espnu": "6x-T96Q-5eY",
         "btn": "1D2LKf_AW5w"}
# the app's priors (sync.jsonl 'tune' rows); ESPN 29 +- 8, everything else 42 +- 15
PRIORS = {"espn": (29.07, 8.0), "trutv": (25.42, 8.0), "fs1": (42.0, 8.0)}
DEFAULT_PRIOR = (42.03, 15.22)
LAND_SD = 0.5          # landing repeatability (measured +-0.3 ESPN .181, +-0.7 TNT)
GOAL_SD = 1.0          # 1-2 s goal: sd <= 1.0 pass, <= 2.0 marginal
MARGINAL_SD = 2.0
LANDING_BAND = (46000.0, 47500.0)   # fresh deep-link landings sit at ~46,790-46,803 s


def norm_ch(s: str | None) -> str:
    s = (s or "").lower().replace(" ", "").replace("-", "")
    if s.startswith("youtubetv:"):
        s = s.split(":", 1)[1]
    return {"nfl network": "nflnetwork"}.get(s, s)


def prior_for(ch: str) -> tuple[float, float]:
    return PRIORS.get(ch, DEFAULT_PRIOR)


def stick_name(x: str | None) -> str:
    if not x:
        return "?"
    x = str(x)
    if x.lower() in HOSTS:
        return x.lower()
    if x in SHORT:
        return SHORT[x]
    for h, n in SHORT.items():
        if x in h or h.startswith(x):
            return n
    if x.endswith("150"):
        return "hdmi1"
    if x.endswith("181"):
        return "hdmi2"
    return x


# --------------------------------------------------------------------------------------------- CLI
def make_parser(test_id: str, doc: str) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=test_id, description=(doc or "").split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=doc)
    ap.add_argument("--stick", default="both",
                    help="hdmi1 | hdmi2 | host:port | both | hidden (fusion is read-only; default both)")
    ap.add_argument("--channel", default="all", help="yttv channel (espn, tnt, ...) or all")
    ap.add_argument("--game", default=None, help="ESPN game id to restrict play anchors to")
    ap.add_argument("--minutes", type=float, default=0.0,
                    help="live: minutes of read-only media_session polling before analysis (0 = files only)")
    ap.add_argument("--dry-run", action="store_true", help="recorded files only, never touches adb")
    ap.add_argument("--days", default="", help="comma list of recording days (default: all with sync_probe)")
    ap.add_argument("--results-dir", default=RESULTS, help="where other families' result files live")
    ap.add_argument("--out", default=os.path.join(RESULTS, f"{test_id}.jsonl"))
    ap.add_argument("--interval", type=float, default=2.0, help="live poll interval (>= 0.5 s)")
    ap.add_argument("--seed", type=int, default=7)
    return ap


class Out:
    def __init__(self, path: str, test_id: str, args):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.f = open(path, "a")
        self.test = test_id
        self.path = path
        self.run = f"{test_id}-{int(time.time())}"
        self.dry = bool(args.dry_run)
        self.write("meta", mode="dry-run" if args.dry_run else "live", args=vars(args))

    def write(self, kind: str, **kw):
        row = {"test": self.test, "run": self.run, "kind": kind, "t": round(time.time(), 3), "dry_run": self.dry}
        row.update(kw)
        self.f.write(json.dumps(row, default=_jd) + "\n")
        self.f.flush()
        return row

    def close(self):
        self.f.close()


def _jd(o):
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    return str(o)


def r2(x, n=2):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(x, n)


def grade(sd: float | None, n: int, n_min: int = 3) -> str:
    if sd is None or n < n_min:
        return "insufficient"
    if sd <= GOAL_SD:
        return "pass"
    if sd <= MARGINAL_SD:
        return "marginal"
    return "fail"


# --------------------------------------------------------------------------------------- safety
def visibility(timeout: float = 2.0) -> dict:
    """{host: True if on screen}. Unknown -> both True (fail safe = read-only everywhere)."""
    try:
        with urllib.request.urlopen("http://localhost:8778/api/state", timeout=timeout) as r:
            st = json.load(r)
    except Exception:
        return {h: True for h in HOSTS.values()} | {"_source": "unreachable"}
    vis = {}
    for s in ((st.get("health") or {}).get("sticks") or []):
        role = str(s.get("role") or "").lower()
        h = s.get("host")
        if h:
            vis[h] = role in ("on screen", "onscreen", "visible", "on_screen", "active")
    on = ((st.get("health") or {}).get("agent") or {}).get("on_screen")
    if on:
        for h in HOSTS.values():
            vis[h] = vis.get(h, False) or h == on
    if not any(vis.get(h) for h in HOSTS.values()) or any(h not in vis for h in HOSTS.values()):
        return {h: True for h in HOSTS.values()} | {"_source": "ambiguous"}
    return vis | {"_source": "api/state"}


def require_hidden(host: str):
    """Call before ANY key press / tune. Fusion tests never act, but keep the guard explicit."""
    if visibility().get(host, True):
        raise SystemExit(f"REFUSED: {host} is (or may be) on screen; fusion tests only act on the hidden stick")


def pick_hosts(stick: str) -> list[str]:
    s = (stick or "both").lower()
    if s == "both":
        return list(HOSTS.values())
    if s == "hidden":
        v = visibility()
        hid = [h for h in HOSTS.values() if not v.get(h, True)]
        return hid or list(HOSTS.values())   # read-only, so either stick is allowed
    if s in HOSTS:
        return [HOSTS[s]]
    return [stick]


# ------------------------------------------------------------------------------------- recorded
def days_list(arg: str) -> list[str]:
    if arg:
        return [d.strip() for d in arg.split(",") if d.strip()]
    return sorted(os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(REC, "*", "sync_probe.jsonl")))


def read_jsonl(path: str):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def load_probe(days: list[str]) -> list[dict]:
    rows = []
    for d in days:
        for r in read_jsonl(os.path.join(REC, d, "sync_probe.jsonl")):
            ms = r.get("ms") or {}
            if ms.get("pkg") != PKG or ms.get("position") is None or ms.get("updated") is None:
                continue
            r["_day"] = d
            rows.append(r)
    rows.sort(key=lambda r: r["t"])
    return rows


def boot_epochs(rows: list[dict]) -> dict:
    """Stick boot epoch from a recording: boot ~ t - updated/1000 at a fresh update. The recorded row
    time t is jittery by a few seconds (hourly minima scatter -6..+3 s around the live
    /proc/uptime boot, measured 2026-11-05 03:28), so take the MEDIAN of hourly minima (hours with
    >= 50 rows): it matched the live boot within 0.1 s on both sticks. One boot per host, so E
    differences inside a session are never broken by a boot step."""
    out = {}
    by = {}
    for r in rows:
        v = r["t"] - r["ms"]["updated"] / 1000.0
        by.setdefault(r["host"], {}).setdefault(int(r["t"] // 3600), []).append(v)
    for h, hrs in by.items():
        mins = [min(v) for v in hrs.values() if len(v) >= 50]
        out[h] = statistics.median(mins) if mins else min(min(v) for v in hrs.values())
    return out


@dataclass
class Sample:
    t: float
    host: str
    ch: str
    st: str
    E: float
    upd_wall: float
    pos_upd: float
    title: str = ""


def samples_from_probe(rows: list[dict], boot: dict) -> list[Sample]:
    out = []
    for r in rows:
        ms = r["ms"]
        b = boot.get(r["host"])
        if b is None or ms["position"] < 1_000_000:   # position 0 rows = buffering placeholder
            continue
        upd_wall = b + ms["updated"] / 1000.0
        pos = ms["position"] / 1000.0
        playing = ms.get("state") == "playing"
        sp = (ms.get("speed") or 1.0) if playing else 0.0
        E = r["t"] - (pos + (r["t"] - upd_wall) * sp)
        out.append(Sample(r["t"], r["host"], norm_ch(ms.get("subtitle")), ms.get("state") or "?", E, upd_wall, pos,
                          ms.get("title") or ""))
    return out


def live_samples(hosts: list[str], minutes: float, interval: float, log=print) -> list[Sample]:
    """READ-ONLY polling of media_session through the existing helper t1_retune.read."""
    if minutes <= 0:
        return []
    from t1_retune import read, sh   # existing helper (read-only calls only)
    interval = max(0.5, interval)
    boot = {}
    for h in hosts:
        try:
            t0 = time.time()
            up = float(sh(h, "cat /proc/uptime").split()[0])
            boot[h] = (t0 + time.time()) / 2 - up
        except Exception as e:   # noqa: BLE001
            log(f"[live] {h}: uptime failed {e!r}")
    out, end = [], time.time() + minutes * 60
    while time.time() < end:
        for h in boot:
            try:
                r = read(h)
            except Exception:   # noqa: BLE001
                r = None
            if not r:
                continue
            upd_wall = boot[h] + r["upd"] / 1000.0
            out.append(Sample(time.time(), h, norm_ch(r["sub"]), r["st"], r["E"], upd_wall,
                              upd_wall - r["E"] if r["st"] == "playing" else time.time() - r["E"], r.get("title", "")))
        time.sleep(interval)
    return out


def load_samples(args, log=print) -> tuple[list[Sample], dict]:
    days = days_list(args.days)
    rows = load_probe(days)
    boot = boot_epochs(rows)
    s = samples_from_probe(rows, boot)
    if not args.dry_run and args.minutes > 0:
        s += live_samples(pick_hosts(args.stick), args.minutes, args.interval, log)
    s.sort(key=lambda x: x.t)
    return s, {"days": days, "boot": {SHORT.get(h, h): round(b, 2) for h, b in boot.items()}, "n_rows": len(rows)}


# -------------------------------------------------------------------------------------- sessions
@dataclass
class Session:
    host: str
    ch: str
    samples: list = field(default_factory=list)
    fresh: bool = False          # started by a deep-link landing (position in the landing band)
    E_land: float | None = None
    L: float | None = None       # landing position (s)
    t_land: float | None = None

    @property
    def t0(self):
        return self.samples[0].t

    @property
    def t1(self):
        return self.samples[-1].t

    @property
    def stick(self):
        return stick_name(self.host)

    def E_at(self, t: float) -> float | None:
        """E at wall t (piecewise: playing samples hold E; paused samples grow with wall)."""
        if not self.samples or t < self.t0 - 30 or t > self.t1 + 30:
            return None
        prev = self.samples[0]
        for s in self.samples:
            if s.t > t:
                break
            prev = s
        if prev.st == "playing":
            return prev.E
        return prev.E + max(0.0, t - prev.t)

    def playing_E(self):
        return [s.E for s in self.samples if s.st == "playing"]


def sessions_from(samples: list[Sample], jump_s: float = 2.0) -> list[Session]:
    """Split each stick's YTTV samples into continuous sessions. New session when the channel
    changes, a new update lands in the landing band, or E falls by > jump_s (jump toward live)."""
    out = []
    by = {}
    for s in samples:
        by.setdefault(s.host, []).append(s)
    for h, ss in by.items():
        cur = None
        last_upd = None
        for s in ss:
            new_upd = s.upd_wall != last_upd
            land = new_upd and s.st == "playing" and LANDING_BAND[0] <= s.pos_upd <= LANDING_BAND[1]
            brk = (cur is None or s.ch != cur.ch or s.t - cur.t1 > 120
                   or (land and cur.samples and not (LANDING_BAND[0] <= cur.samples[-1].pos_upd <= LANDING_BAND[1]
                                                     and abs(cur.samples[-1].E - s.E) < jump_s))
                   or (s.st == "playing" and cur.samples and cur.samples[-1].st == "playing"
                       and cur.samples[-1].E - s.E > jump_s))
            if brk:
                cur = Session(h, s.ch)
                out.append(cur)
                if land:
                    cur.fresh, cur.E_land, cur.L, cur.t_land = True, s.E, s.pos_upd, s.upd_wall
            if not cur.samples and not land and s.st == "playing":
                cur.E_land = s.E   # non-fresh session: its first E is the reference
            cur.samples.append(s)
            last_upd = s.upd_wall
    for c in out:
        if c.E_land is None:
            pe = c.playing_E()
            c.E_land = pe[0] if pe else c.samples[0].E
    return [c for c in out if c.ch]


def session_at(sessions: list[Session], host: str, ch: str, t: float) -> Session | None:
    for c in sessions:
        if c.host == host and c.ch == ch and c.t0 - 5 <= t <= c.t1 + 5:
            return c
    return None


# ------------------------------------------------------------------------------- plays & bursts
def load_plays(days: list[str]) -> list[dict]:
    out = []
    for d in days:
        out += [p for p in read_jsonl(os.path.join(REC, d, "plays.jsonl")) if p.get("wall")]
    seen, ded = set(), []
    for p in sorted(out, key=lambda p: (p["wall"], p["t"])):
        k = (p.get("game"), p.get("play"))
        if k in seen:
            continue
        seen.add(k)
        ded.append(p)
    return ded


def load_plans(days: list[str]) -> list[dict]:
    games = []
    for d in days:
        p = os.path.join(DATA, f"plan_{d.replace('-', '')}.json")
        try:
            games += json.load(open(p)).get("games", [])
        except Exception:   # noqa: BLE001
            pass
    return games


def games_on(plans: list[dict], ch: str, t: float, game: str | None = None) -> list[str]:
    if game:
        return [str(game)]
    out = []
    for g in plans:
        tgt = ((g.get("source") or {}).get("target") or "")
        if norm_ch(tgt) == ch and g.get("start") and g["start"] - 1800 <= t <= g["start"] + 4 * 3600:
            out.append(str(g["game_id"]))
    return out


def bursts(samples: list[Sample]) -> list[list[Sample]]:
    """Ad-pod spot bursts: distinct media_session updates 8-17 s apart with position advancing ~gap."""
    out = []
    by = {}
    for s in samples:
        by.setdefault(s.host, {})
        by[s.host].setdefault(s.upd_wall, s)
    for h, d in by.items():
        ev = sorted(d.values(), key=lambda s: s.upd_wall)
        cur = []
        for a, b in zip(ev, ev[1:]):
            gap = b.upd_wall - a.upd_wall
            if a.title == b.title and a.ch == b.ch and 8 <= gap <= 17 and abs((b.pos_upd - a.pos_upd) - gap) < 2:
                if not cur:
                    cur = [a]
                cur.append(b)
            else:
                if len(cur) >= 2:
                    out.append(cur)
                cur = []
        if len(cur) >= 2:
            out.append(cur)
    return out


def _is_stoppage(p):
    ty = (p.get("type") or "").lower()
    return "timeout" in ty or "end period" in ty or "end of" in ty


def _is_live_play(p):
    ty = (p.get("type") or "").lower()
    return not any(k in ty for k in ("timeout", "substitution", "end period", "start period", "instant replay",
                                     "end of", "start of", "review"))


BOOT_SD = 0.7  # recorded rows: boot from the median of hourly minima (matched live boot within 0.1 s)


def pod_events(samples, sessions, plays, plans, game=None):
    """For each ad pod: screen time of return (last spot + spot length) and the real restart play
    after a stoppage in a game on that channel. D_obs = pod_end - restart_wall.
    Game resolution: --game, else the day's plan (source target youtubetv:<ch>), else every game with a
    stoppage in the 7 min before the pod end; more than one plausible candidate -> ambiguous=True."""
    by_game = {}
    for p in plays:
        by_game.setdefault(str(p.get("game")), []).append(p)
    ev = []
    for b in bursts(samples):
        start, last = b[0].upd_wall, b[-1].upd_wall
        spot = (last - start) / (len(b) - 1)
        end = last + spot
        ch = b[0].ch
        cands = games_on(plans, ch, end, game)
        resolved = bool(cands)
        title = (b[0].title or "").lower()
        if not cands and not (" at " in title or " vs" in title):
            continue   # studio show / program break: no game to align with
        if not cands:
            cands = list(by_game)
        found = []
        for g in cands:
            ps = by_game.get(g, [])
            stops = [p for p in ps if _is_stoppage(p) and end - 420 <= p["wall"] <= end - 20]
            if not stops:
                continue
            tw = stops[-1]["wall"]
            nxt = [p for p in ps if p["wall"] > tw + 5 and _is_live_play(p)]
            if not nxt:
                continue
            rp = nxt[0]
            D = end - rp["wall"]
            if 5 <= D <= 120:
                found.append((g, stops[-1], rp, D))
        sess = session_at(sessions, b[0].host, ch, end)
        for g, sp, rp, D in found:
            ev.append(dict(host=b[0].host, stick=stick_name(b[0].host), channel=ch, game=g, t=end, pod_start=start,
                           spots=len(b), stop_wall=sp["wall"], stop_type=sp.get("type"), restart_wall=rp["wall"],
                           restart_type=rp.get("type"), D=D, E=sess.E_at(end) if sess else None,
                           ambiguous=(not resolved and len(found) > 1), resolved=resolved, title=b[0].title))
    return ev


# ------------------------------------------------------------------------------------- anchors
@dataclass
class Anchor:
    family: str
    channel: str
    stick: str
    t: float
    D: float
    sd: float
    src: str = ""
    D_edge: float | None = None
    sd_edge: float | None = None
    night: str = ""


POD_SD = 8.0  # return-from-break vs first logged play: inbound/announcer gap is unknown, ~0-15 s


def recorded_anchors(samples, sessions, plays, plans, game=None) -> list[Anchor]:
    out = []
    for e in pod_events(samples, sessions, plays, plans, game):
        fam = "burst_pod_ambiguous" if e["ambiguous"] else ("burst_pod" if e["resolved"] else "burst_pod_title")
        out.append(Anchor(fam, e["channel"], e["stick"], e["t"], e["D"], math.hypot(POD_SD, BOOT_SD), src="recorded"))
    return out


_D_KEYS = ("D", "D_s", "d", "delay_s", "behind_s", "lag_s", "D_hat", "delay")
_SD_KEYS = ("sd", "D_sd", "sigma", "sd_s", "err_sd")
# Adapters for families whose rows carry a delay without a stated sd (sd = our honest guess):
#   ads_02 'match' rows: D_end = pod return on screen - restart play (same physics as burst_pod)
#   ads_04 'anchor' rows: D_bitrate_end = bitrate step at break end - restart (5 s quantised)
ADAPTERS = {
    "ads_02": lambda r: (r.get("D_end"), 8.0, "ads_pod_end", r.get("restart")) if r.get("rec") == "match" else None,
    "ads_04": lambda r: (r.get("D_bitrate_end"), 3.5, "ads_bitrate_edge", r.get("restart")) if r.get("rec") == "anchor" else None,
}


def _hms_to_epoch(v, times: list | None, days: list[str]):
    """'HH:MM:SS(.ff)' -> epoch on whichever recording day (or the day after) lands closest to a
    recorded sample of that stick (`times`, sorted)."""
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, str) or v.count(":") != 2:
        return None
    hh, mm, ss = v.split(":")
    cands = []
    for d in days:
        base = time.mktime(time.strptime(d, "%Y-%m-%d"))
        for add in (0, 86400):
            cands.append(base + add + int(hh) * 3600 + int(mm) * 60 + float(ss))
    if not cands:
        return None
    if not times:
        return cands[0]
    import bisect

    def gap(c):
        i = bisect.bisect_left(times, c)
        return min(abs(times[j] - c) for j in (i - 1, i) if 0 <= j < len(times))
    return min(cands, key=gap)


def result_anchors(results_dir: str, include_dry: bool = False, exclude_prefix: str = "fusion_",
                   days: list[str] | None = None, coverage: dict | None = None) -> list[Anchor]:
    """Tolerant loader: any JSON row (from another family) carrying channel + D + sd is an anchor,
    plus the ADAPTERS above. Rows may set family, stick/host, t/wall/t_anchor. Synthetic rows are
    always skipped; dry-run rows only when include_dry (fusion's own dry run: they come from the same
    recorded data). coverage = {stick: sorted sample times} resolves HH:MM:SS times to a date."""
    out = []
    days = days or []
    for p in sorted(glob.glob(os.path.join(results_dir, "*.jsonl"))):
        base = os.path.basename(p)
        if base.startswith(exclude_prefix):
            continue
        file_dry = ".dryrun." in base
        if file_dry and not include_dry:
            continue
        fam_default = base.split(".")[0]
        fam_default = "_".join(fam_default.split("_")[:2]) if fam_default.split("_")[0] in ("ads", "ui", "mechanics", "truth") else fam_default
        adapter = next((f for k, f in ADAPTERS.items() if base.startswith(k)), None)
        rows = [r for r in read_jsonl(p) if isinstance(r, dict)]
        runs = [r.get("run") for r in rows if r.get("run")]
        if runs:   # files are appended per run: only the latest run counts (no double counting)
            rows = [r for r in rows if r.get("run") in (None, runs[-1])]
        for r in rows:
            if not isinstance(r, dict) or r.get("synthetic"):
                continue
            if (r.get("dry_run") or r.get("mode") == "dry-run") and not include_dry:
                continue
            ch = norm_ch(r.get("channel") or r.get("ch") or r.get("target"))
            st = stick_name(r.get("stick") or r.get("host") or r.get("hdmi"))
            D = next((r[k] for k in _D_KEYS if isinstance(r.get(k), (int, float))), None)
            sd = next((r[k] for k in _SD_KEYS if isinstance(r.get(k), (int, float))), None)
            fam = str(r.get("family") or fam_default)
            tv = r.get("t_anchor") or r.get("t_screen") or r.get("wall") or r.get("t")
            if adapter:
                ad = adapter(r)
                if not ad:
                    continue
                D, sd, fam, tv = ad[0], ad[1], ad[2], ad[3] or tv
            if not ch or not isinstance(D, (int, float)) or not isinstance(sd, (int, float)) or sd <= 0:
                continue
            t = _hms_to_epoch(tv, (coverage or {}).get(st), days) if not isinstance(tv, (int, float)) else float(tv)
            if t is None:
                continue
            out.append(Anchor(fam, ch, st, t, float(D), float(sd), src=base, D_edge=r.get("D_edge")))
    return out


def dedupe(anchors: list[Anchor], window: float = 90.0) -> list[Anchor]:
    """The same ad pod can arrive twice (another family's result row + our recorded burst). Keep the
    other family's row and drop a recorded burst_pod* anchor on the same (channel, stick) within window."""
    ext = [a for a in anchors if a.src != "recorded"]
    keep = []
    for a in anchors:
        if a.src == "recorded" and any(e.channel == a.channel and e.stick == a.stick and abs(e.t - a.t) <= window
                                       and "pod" in e.family for e in ext):
            continue
        keep.append(a)
    return keep


def to_edge(anchors: list[Anchor], sessions: list[Session]) -> list[Anchor]:
    """D_edge = D - (E(t) - E_land); sd adds landing repeatability when the session was fresh,
    and a larger term when the session has no clean landing."""
    host_of = {v: k for k, v in SHORT.items()}
    for a in anchors:
        if a.D_edge is not None:
            a.sd_edge = a.sd_edge or a.sd
            continue
        h = host_of.get(a.stick, a.stick)
        s = session_at(sessions, h, a.channel, a.t)
        if s is None or s.E_at(a.t) is None:
            a.D_edge, a.sd_edge = a.D, math.hypot(a.sd, 3.0)   # unknown pause history
            continue
        a.D_edge = a.D - (s.E_at(a.t) - s.E_land)
        a.sd_edge = math.hypot(a.sd, LAND_SD if s.fresh else 2.0)
    for a in anchors:
        a.night = night_of(a.t)
    return anchors


def night_of(t: float) -> str:
    # a "night" runs noon->noon local time so a game crossing midnight stays in one fold
    return time.strftime("%Y-%m-%d", time.localtime(t - 12 * 3600))


def synthetic_anchors(seed: int, truth: dict | None = None, n: int = 12) -> tuple[list[Anchor], dict]:
    """Simulated anchors with known truth (for estimator self-checks only; flagged synthetic)."""
    rnd = random.Random(seed)
    truth = truth or {("espn", "hdmi1"): 27.4, ("espn", "hdmi2"): 26.9, ("tnt", "hdmi2"): 31.2}
    fams = [("ui_clock", 0.8), ("burst_pod", 6.0), ("scoreline", 1.5)]
    out = []
    t0 = 1793900000.0
    for (ch, st), d in truth.items():
        for i in range(n):
            fam, sd = fams[i % len(fams)]
            noise = rnd.gauss(0, sd) if rnd.random() > 0.08 else rnd.choice([-1, 1]) * 4 * sd   # 8% outliers
            a = Anchor(fam, ch, st, t0 + 600 * i + rnd.random() * 60, d + noise, sd, src="synthetic")
            a.D_edge, a.sd_edge = a.D, sd
            out.append(a)
    return out, truth


def group(anchors, key=lambda a: (a.channel, a.stick)):
    g = {}
    for a in anchors:
        g.setdefault(key(a), []).append(a)
    return g


def filt(anchors, args):
    ch = norm_ch(args.channel) if args.channel and args.channel != "all" else None
    sticks = None
    if args.stick and args.stick.lower() not in ("both", "hidden"):
        sticks = {stick_name(args.stick)}
    return [a for a in anchors if (ch is None or a.channel == ch) and (sticks is None or a.stick in sticks)]


# ----------------------------------------------------------------------------------- estimators
def iv_mean(xs, sds):
    w = [1 / s ** 2 for s in sds]
    W = sum(w)
    return sum(x * wi for x, wi in zip(xs, w)) / W, math.sqrt(1 / W)


def kalman(anchors: list[Anchor], prior: tuple[float, float], q_per_hour: float = 0.5, gate: float = 2.5):
    """Scalar Kalman over D_edge, random-walk process noise q (s / sqrt(hour)), robust gating:
    an innovation beyond `gate` sigma has R inflated by (|z|/gate)^4 (redescending). The reported sd is
    sqrt(P) * max(1, sqrt(robust NIS)) so it stays calibrated when the stated anchor sds are too small
    (tuned in fusion_11: 1-sigma coverage 0.67, 2-sigma 0.95 with 8% 4-sigma outliers).
    Returns dict(mu, sd, sd_raw, n, nis, nis_raw, z, trace)."""
    mu, P = prior[0], prior[1] ** 2
    last_t = None
    zs, trace = [], []
    for a in sorted(anchors, key=lambda a: a.t):
        if last_t is not None:
            P += (q_per_hour ** 2) * max(0.0, a.t - last_t) / 3600.0
        last_t = a.t
        R = a.sd_edge ** 2
        S = P + R
        z = (a.D_edge - mu) / math.sqrt(S)
        if abs(z) > gate:
            R = R * (abs(z) / gate) ** 4
            S = P + R
        K = P / S
        mu = mu + K * (a.D_edge - mu)
        P = (1 - K) * P
        zs.append(z)
        trace.append(dict(t=round(a.t, 1), fam=a.family, obs=r2(a.D_edge), mu=r2(mu), sd=r2(math.sqrt(P))))
    # NIS: mean squared innovation (1.0 = honest sds). Robust version (MAD of z) ignores gated outliers.
    nis_raw = (sum(z * z for z in zs) / len(zs)) if zs else None
    nis = ((statistics.median([abs(z) for z in zs]) / 0.6745) ** 2) if len(zs) >= 3 else nis_raw
    sd = math.sqrt(P) * max(1.0, math.sqrt(nis)) if nis is not None else math.sqrt(P)
    return dict(mu=mu, sd=sd, sd_raw=math.sqrt(P), n=len(anchors), z=zs, trace=trace, nis=nis, nis_raw=nis_raw)


def huber(xs, sds, k: float = 1.5, iters: int = 30):
    """IRLS Huber M-estimate with known per-point sd. Returns (mu, sd, weights)."""
    if not xs:
        return None, None, []
    mu = statistics.median(xs)
    w = [1.0] * len(xs)
    for _ in range(iters):
        w = []
        for x, s in zip(xs, sds):
            r = abs(x - mu) / s
            w.append(1.0 if r <= k else k / r)
        ww = [wi / s ** 2 for wi, s in zip(w, sds)]
        new = sum(x * wi for x, wi in zip(xs, ww)) / sum(ww)
        if abs(new - mu) < 1e-6:
            mu = new
            break
        mu = new
    ww = [wi / s ** 2 for wi, s in zip(w, sds)]
    return mu, math.sqrt(1 / sum(ww)), w


def fit(anchors: list[Anchor], ch: str, method: str = "kalman"):
    if method == "kalman":
        return kalman(anchors, prior_for(ch))
    mu, sd, _ = huber([a.D_edge for a in anchors], [a.sd_edge for a in anchors])
    return dict(mu=mu, sd=sd, n=len(anchors))


# ------------------------------------------------------------------------------ common pipeline
def build(args, log=print, want_synthetic: bool | None = None):
    """Load everything once: samples, sessions, plays, plans, anchors (results + recorded),
    plus synthetic anchors when real ones are too few (dry run only, flagged)."""
    samples, info = load_samples(args, log)
    sess = sessions_from(samples)
    days = info["days"]
    plays = load_plays(days)
    plans = load_plans(days)
    cov = {}
    for x in samples:
        cov.setdefault(stick_name(x.host), []).append(x.t)
    anchors = (result_anchors(args.results_dir, include_dry=args.dry_run, days=days, coverage=cov)
               + recorded_anchors(samples, sess, plays, plans, args.game))
    anchors = filt(to_edge(dedupe(anchors), sess), args)
    synth, truth = [], {}
    if want_synthetic or (want_synthetic is None and args.dry_run):
        synth, truth = synthetic_anchors(args.seed)
        synth = filt(synth, args)
    info.update(n_samples=len(samples), n_sessions=len(sess), n_plays=len(plays), n_plan_games=len(plans),
                n_anchors=len(anchors), families=sorted({a.family for a in anchors}))
    return dict(samples=samples, sessions=sess, plays=plays, plans=plans, anchors=anchors,
                synth=synth, truth=truth, info=info)


def header(test_id, args, info, log=print):
    log(f"== {test_id}  mode={'DRY-RUN (recorded files)' if args.dry_run else 'LIVE (read-only adb)'}")
    log(f"   days={info.get('days')} rows={info.get('n_rows')} samples={info.get('n_samples')} "
        f"sessions={info.get('n_sessions')} plays={info.get('n_plays')} anchors={info.get('n_anchors')} "
        f"families={info.get('families')} boot={info.get('boot')}")


def usable(anchors):
    """Anchors that may enter the estimate (ambiguous game matches never do)."""
    return [a for a in anchors if not a.family.endswith("_ambiguous")]


def latest_session(sessions, host, ch):
    c = [s for s in sessions if s.host == host and s.ch == ch]
    return max(c, key=lambda s: s.t1) if c else None


def d_now(fitres, sess: Session | None):
    """Propagate D_edge to the latest sample of a session: D = D_edge + (E_last - E_land)."""
    if not sess or fitres.get("mu") is None:
        return None
    e = sess.samples[-1].E if sess.samples[-1].st == "playing" else sess.E_at(sess.t1)
    return fitres["mu"] + (e - sess.E_land)


def fmt_t(t):
    return time.strftime("%m-%d %H:%M:%S", time.localtime(t)) if t else "-"
