#!/usr/bin/env python3
"""run_all: run the YTTV sync tests (ui, ads, truth, mechanics, fusion) safely during a live game.

Order (live):
  1. prep, one at a time: truth_01 (truth cache), mechanics_12 + truth_09 (stick clocks).
  2. two lanes at the same time:
       read-only lane  - at most 3 tests in parallel threads (dumpsys / logcat / HTTP / file analysis).
                         All selected ads_NN tests share ONE capture via ads/ads_run_all.py.
       key lane        - key/tune tests ONE at a time, hidden stick only. A watchdog polls
                         /api/state every 2 s and kills the test (SIGINT, then SIGTERM) if the
                         stick it started on becomes visible or the state cannot be read twice running.
                         Key tests are skipped when the on-screen stick is unknown, when the channel
                         has no deep-link code, or (unless --allow-app-active) when the app's
                         auto-switching is not paused.
  3. post: truth_08 / truth_11 on results/obs.jsonl if present, fusion_01..11, then fusion_12_report,
     which prints one table per channel/stick: D, sd, n anchors, families, PASS/MARGINAL/FAIL.

--dry-run runs every selected test against the recordings (default day 2026-11-04), writes
results/<id>.dryrun.jsonl, never touches a stick, runs 3 at a time, and prints the same report.

Examples:
  python3 run_all.py --dry-run
  python3 run_all.py --game 401909930 --channel espn --minutes 20 --only ui_10,ui_02,ads_01,ads_02
  python3 run_all.py --game 401909930 --channel tnt --family ads,truth --minutes 30
  python3 run_all.py --list
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
_STICKS = (__import__("os").environ.get("RZ_STICKS") or "hdmi1:5555,hdmi2:5555").split(",")  # stick addresses stay off the public repo: set RZ_STICKS=host1,host2 on the Mac

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
MANIFEST = os.path.join(HERE, "manifest.json")
STATE_URL = "http://localhost:8778/api/state"
HOSTS = {"HDMI1": _STICKS[0], "HDMI2": _STICKS[1]}
CODES = {"espn", "espn2", "tnt", "tbs", "nbatv", "fs1", "usa", "trutv", "fox", "cbssn", "nflnetwork",
         "espnu", "btn"}
DATE_FLAG = {"ads": "--days", "fusion": "--days", "truth": "--days", "mechanics": "--dates", "ui": "--date"}
PREP = ["truth_01", "mechanics_12", "truth_09"]
POST_TRUTH = ["truth_08", "truth_11"]
REPORT = "fusion_12_report"
PRINT_LOCK = threading.Lock()


def say(msg):
    with PRINT_LOCK:
        print(f"[run_all {time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------------------------- screen state (fail safe)
def screen_state():
    """-> (visible:set, hidden:set, app_paused:bool|None, why). Unknown => both visible, none hidden."""
    try:
        with urllib.request.urlopen(STATE_URL, timeout=3) as r:
            st = json.load(r)
    except Exception as e:
        return set(HOSTS.values()), set(), None, f"state unavailable ({e.__class__.__name__})"
    health = st.get("health") or {}
    on = set()
    agent = health.get("agent") or {}
    if agent.get("on_screen") in HOSTS.values():
        on.add(agent["on_screen"])
    roles = {}
    for s in health.get("sticks") or []:
        if s.get("host") in HOSTS.values():
            roles[s["host"]] = str(s.get("role") or "").lower()
    by_role = {h for h, r in roles.items() if "screen" in r or "visible" in r or "active" in r}
    mode = st.get("mode") or {}
    paused = bool(mode.get("paused")) if "paused" in mode else None
    # both sources must agree on exactly one on-screen stick
    if len(on) == 1 and (not roles or by_role == on) and len(roles) in (0, 2):
        vis = on
    elif not on and len(roles) == 2 and len(by_role) == 1:
        vis = by_role
    else:
        return set(HOSTS.values()), set(), paused, f"ambiguous (agent={sorted(on)} roles={roles})"
    return vis, set(HOSTS.values()) - vis, paused, "api/state"


# ------------------------------------------------------------------------------- manifest / commands
def load_manifest():
    with open(MANIFEST) as f:
        return json.load(f)


def family_of(t):
    return t.get("family") or t["id"].split("_")[0]


def out_path(t, dry):
    base = os.path.basename(t["file"])[:-3]  # the family's own default name, e.g. ads_01_pod_cadence
    return os.path.join(RESULTS, f"{base}{'.dryrun' if dry else ''}.jsonl")


def build_cmd(t, a, dry, extra=None):
    fam = family_of(t)
    cmd = [sys.executable, t["file"]]
    if dry:
        cmd.append("--dry-run")
        day = a.date
        cmd += [DATE_FLAG[fam], day]
    if a.game:
        cmd += ["--game", a.game]
    if a.channel and a.channel in CODES:
        cmd += ["--channel", a.channel]
    elif a.channel and fam in ("truth",):
        cmd += ["--channel", a.channel]
    if t["mode"] == "hidden-stick-keys":
        cmd += ["--stick", "hidden"]
    elif a.stick:
        cmd += ["--stick", a.stick]
    if fam == "fusion":
        cmd += ["--results-dir", RESULTS]
        if not dry and t["id"] != REPORT:
            cmd += ["--minutes", "0"]
    elif not dry:
        cmd += ["--minutes", str(t.get("minutes") or a.minutes)]
    cmd += ["--out", out_path(t, dry)]
    cmd += list(t.get("dry_args" if dry else "live_args") or [])
    cmd += extra or []
    return cmd


def last_verdict(path, since):
    """Last summary-like row written at/after `since` -> short verdict string."""
    best = None
    try:
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if not isinstance(r, dict):
                    continue
                tt = r.get("t")
                if isinstance(tt, (int, float)) and tt < since - 1:
                    continue
                for k in ("verdict", "pass_", "pass", "passed"):
                    if k in r and not (k == "verdict" and isinstance(r[k], dict)):
                        best = (k, r[k], r)
                        break
    except FileNotFoundError:
        return "no output file"
    if not best:
        return "no verdict row"
    k, v, r = best
    if isinstance(v, bool):
        v = "PASS" if v else "FAIL"
    elif v is None:
        v = "N/A"
    return str(v)[:60]


# ------------------------------------------------------------------------------- runners
def run_proc(t, cmd, log_path, watch_host=None, timeout=None):
    t0 = time.time()
    with open(log_path, "w") as log:
        log.write("$ " + " ".join(cmd) + "\n")
        log.flush()
        p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=os.path.dirname(t["file"]),
                             start_new_session=True)
        aborted, unknown = None, 0
        while True:
            try:
                rc = p.wait(timeout=2.0)
                break
            except subprocess.TimeoutExpired:
                pass
            if timeout and time.time() - t0 > timeout:
                aborted = f"timeout {timeout:.0f}s"
            if watch_host and not aborted:
                vis, hid, _, why = screen_state()
                if watch_host in hid:
                    unknown = 0
                elif watch_host in vis and why == "api/state":
                    aborted = f"{watch_host} became VISIBLE"
                else:
                    unknown += 1
                    if unknown >= 2:
                        aborted = f"screen state unknown twice ({why})"
            if aborted:
                say(f"ABORT {t['id']}: {aborted}")
                for sig, wait in ((signal.SIGINT, 5), (signal.SIGTERM, 5), (signal.SIGKILL, 5)):
                    try:
                        os.killpg(p.pid, sig)
                    except ProcessLookupError:
                        break
                    try:
                        p.wait(timeout=wait)
                        break
                    except subprocess.TimeoutExpired:
                        continue
                rc = p.returncode if p.returncode is not None else -9
                break
    return rc, aborted, time.time() - t0


def run_one(t, a, dry, logdir, watch_host=None, extra=None):
    if not dry and t.get("needs_game") and not a.game:
        say(f"SKIP {t['id']}: needs --game (ESPN id) live")
        return dict(id=t["id"], rc=None, verdict="SKIP needs --game")
    cmd = build_cmd(t, a, dry, extra)
    log_path = os.path.join(logdir, f"{t['id']}.log")
    since = time.time()
    timeout = a.timeout if dry else (a.minutes * 60 + 600)
    rc, aborted, dt = run_proc(t, cmd, log_path, watch_host, timeout)
    verdict = "ABORTED: " + aborted if aborted else last_verdict(out_path(t, dry), since)
    row = dict(id=t["id"], rc=rc, secs=round(dt, 1), verdict=verdict, log=log_path)
    say(f"{t['id']:34s} rc={rc:<3} {dt:6.1f}s  {verdict}")
    return row


def run_ads_shared(tests, a, logdir):
    """Live: one shared read-only capture for every selected ads test."""
    nn = ",".join(t["id"][4:6] for t in tests)
    cmd = [sys.executable, os.path.join(HERE, "ads", "ads_run_all.py"), "--only", nn,
           "--minutes", str(a.minutes)]
    if a.game:
        cmd += ["--game", a.game]
    if a.channel in CODES:
        cmd += ["--channel", a.channel]
    if a.stick:
        cmd += ["--stick", a.stick]
    fake = dict(id="ads_run_all", file=cmd[1])
    since = time.time()
    rc, aborted, dt = run_proc(fake, cmd, os.path.join(logdir, "ads_run_all.log"),
                               timeout=a.minutes * 60 + 900)
    rows = []
    for t in tests:
        v = last_verdict(out_path(t, False), since)
        rows.append(dict(id=t["id"], rc=rc, secs=round(dt, 1), verdict=v, log=os.path.join(logdir, "ads_run_all.log")))
        say(f"{t['id']:34s} rc={rc:<3} (shared capture)  {v}")
    return rows


def key_lane(tests, a, logdir):
    rows = []
    for t in tests:
        if t.get("needs_code", True) and a.channel and a.channel not in CODES:
            say(f"SKIP {t['id']}: channel {a.channel!r} has no deep-link code (key tests re-tune)")
            rows.append(dict(id=t["id"], rc=None, verdict="SKIP no deep-link code"))
            continue
        vis, hid, paused, why = screen_state()
        if len(hid) != 1:
            say(f"SKIP {t['id']}: hidden stick unknown ({why}); key tests need exactly one hidden stick")
            rows.append(dict(id=t["id"], rc=None, verdict="SKIP hidden unknown"))
            continue
        if paused is not True and not a.allow_app_active:
            say(f"SKIP {t['id']}: app auto-switching is not paused (pass --allow-app-active to override)")
            rows.append(dict(id=t["id"], rc=None, verdict="SKIP app active"))
            continue
        if t.get("needs_game") and not a.game:
            say(f"SKIP {t['id']}: needs --game (ESPN id)")
            rows.append(dict(id=t["id"], rc=None, verdict="SKIP needs --game"))
            continue
        host = next(iter(hid))
        say(f"KEY {t['id']} on hidden {host}")
        rows.append(run_one(t, a, False, logdir, watch_host=host))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", default=None, help="ESPN game id of the game on --channel")
    ap.add_argument("--channel", default=None, help="YTTV channel key (espn, tnt, espn2, tbs, nbatv, ...)")
    ap.add_argument("--stick", default=None, help="read-only tests: HDMI1 | HDMI2 | both (key tests: always hidden)")
    ap.add_argument("--minutes", type=float, default=15.0, help="live length per test")
    ap.add_argument("--only", default=None, help="comma list of test ids (ui_02, ads_01, fusion_03, ...)")
    ap.add_argument("--family", default=None, help="comma list: ui,ads,truth,mechanics,fusion")
    ap.add_argument("--dry-run", action="store_true", help="recorded files only, no stick touched")
    ap.add_argument("--date", default="2026-11-04", help="recording day for --dry-run")
    ap.add_argument("--no-keys", action="store_true", help="live: run read-only tests only")
    ap.add_argument("--allow-app-active", action="store_true",
                    help="live: run key tests even when the app's auto-switching is not paused")
    ap.add_argument("--workers", type=int, default=3, help="read-only parallelism (max 3)")
    ap.add_argument("--timeout", type=float, default=600, help="dry-run per-test timeout s")
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("--list", action="store_true", help="print the manifest in run order and exit")
    a = ap.parse_args()
    a.workers = max(1, min(3, a.workers))
    dry = a.dry_run

    man = load_manifest()
    for t in man:
        if not os.path.isabs(t["file"]):
            t["file"] = os.path.join(HERE, t["file"])
    sel = man
    if a.only:
        want = [w.strip() for w in a.only.split(",") if w.strip()]
        sel = [t for t in man if any(t["id"] == w or t["id"].startswith(w + "_") for w in want)]
        missing = [w for w in want if not any(t["id"] == w or t["id"].startswith(w + "_") for t in man)]
        if missing:
            sys.exit(f"unknown test ids: {missing}")
    if a.family:
        fams = set(a.family.split(","))
        sel = [t for t in sel if family_of(t) in fams]
    sel.sort(key=lambda t: t.get("rank", 999))
    if a.list:
        for t in sel:
            print(f"{t.get('rank', ''):>3} {t['id']:30s} {t['mode']:18s} {t['title']}")
        return 0

    os.makedirs(RESULTS, exist_ok=True)
    logdir = os.path.join(RESULTS, "logs", f"{'dry' if dry else 'live'}_{time.strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(logdir, exist_ok=True)
    ids = {t["id"] for t in sel}
    by_id = {t["id"]: t for t in man}
    rows = []

    if not dry:
        vis, hid, paused, why = screen_state()
        say(f"LIVE game={a.game} channel={a.channel} minutes={a.minutes}; visible={sorted(vis)} "
            f"hidden={sorted(hid)} app_paused={paused} ({why})")
        if a.channel and a.channel not in CODES:
            say(f"note: {a.channel!r} has no deep-link code; key tests will be skipped")
    else:
        say(f"DRY RUN on {a.date} recordings: {len(sel)} tests, {a.workers} at a time, no stick touched")

    # 1. prep
    for i in PREP:
        if i in ids:
            rows.append(run_one(by_id[i], a, dry, logdir))

    fusion = [t for t in sel if family_of(t) == "fusion" and t["id"] != REPORT]
    keys = [t for t in sel if t["mode"] == "hidden-stick-keys" and t["id"] not in PREP]
    ro = [t for t in sel if t not in keys and t not in fusion and t["id"] not in PREP
          and t["id"] != REPORT and not (not dry and t["id"] in POST_TRUTH)]

    # 2. lanes
    if dry:
        with ThreadPoolExecutor(a.workers) as ex:
            rows += list(ex.map(lambda t: run_one(t, a, True, logdir), ro + keys))
    else:
        ads = [t for t in ro if family_of(t) == "ads"]
        other = [t for t in ro if family_of(t) != "ads"]
        key_rows = []
        kt = None
        if keys and not a.no_keys:
            kt = threading.Thread(target=lambda: key_rows.extend(key_lane(keys, a, logdir)), daemon=True)
            kt.start()
        elif keys:
            say(f"--no-keys: skipping {len(keys)} key tests")
        with ThreadPoolExecutor(a.workers) as ex:
            futs = []
            if ads:
                futs.append(ex.submit(run_ads_shared, ads, a, logdir))
            futs += [ex.submit(lambda t=t: [run_one(t, a, False, logdir)]) for t in other]
            for f in futs:
                rows += f.result()
        if kt:
            kt.join()
        rows += key_rows
        # 3a. post truth tools on an observation file if one exists
        obs = os.path.join(RESULTS, "obs.jsonl")
        for i in POST_TRUTH:
            if i in ids:
                if os.path.exists(obs):
                    rows.append(run_one(by_id[i], a, False, logdir, extra=["--obs-file", obs]))
                else:
                    say(f"SKIP {i}: no {obs} (rows {{game, period, clock, obs_wall, channel}})")

    # 3b. fusion analysis + report
    if fusion:
        with ThreadPoolExecutor(a.workers) as ex:
            rows += list(ex.map(lambda t: run_one(t, a, dry, logdir), fusion))
    if not a.no_report and REPORT in by_id:
        t = by_id[REPORT]
        rows.append(run_one(t, a, dry, logdir))
        md = out_path(t, dry)[:-len(".jsonl")] + ".md"
        if os.path.exists(md):
            print("\n" + open(md).read())

    ok = [r for r in rows if r.get("rc") == 0]
    bad = [r for r in rows if r.get("rc") not in (0, None)]
    summ = dict(t=time.time(), dry_run=dry, args=vars(a), n=len(rows), exit0=len(ok), rows=rows)
    with open(os.path.join(logdir, "summary.json"), "w") as f:
        json.dump(summ, f, indent=1)
    say(f"done: {len(ok)}/{len(rows)} exited 0; logs {logdir}")
    for r in bad:
        say(f"  non-zero: {r['id']} rc={r['rc']} -> {r.get('log')}")
    return 1 if (dry and bad) else 0


if __name__ == "__main__":
    sys.exit(main())
