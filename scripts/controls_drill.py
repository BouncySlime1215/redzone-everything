"""Phone controls drill: replay the end-to-end night with scripted phone presses and grade them.

    python scripts/controls_drill.py --seeds 4 [--tv smartthings] [--hours 3] [--pretune] [--flaky] [--json out.json]

Every press goes through engine.action like a tap (redzone/lab/night/presses.py). The report gives
press-to-TV seconds for "Watch this now" (p50/p95, % within 3 s), locks the TV left before their
release, pauses that still switched, skips that never left, and dropped presses. Exit 1 when any
press was dropped or broken. Fake sticks and TV only; nothing real is touched.
Why: Replays a night with scripted phone presses to grade the phone controls (docstring, controls commits).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def one(seed: int, tv: str, hours: float | None, faults: bool) -> dict:
    """One night in its own process (the night installs a virtual clock for the whole process)."""
    code = f"""
import json, os, sys, tempfile, time as _t
sys.path.insert(0, {str(ROOT)!r})
from redzone.lab.night import vclock
CLOCK = vclock.install(1_793_570_000.0)
os.environ.setdefault("TZ", "America/New_York"); _t.tzset()
from pathlib import Path
from redzone.lab.night.run import Night, setup_logging
from redzone.lab.night import presses
work = Path(tempfile.mkdtemp(prefix="rz-controls-"))
night = Night(CLOCK, work, seed={seed}, end_s={hours * 3600 if hours else None}, plugins=["redzone.lab.night.presses:controls"],
              default_faults={faults}, tv_path={tv!r})
night.prepare(); setup_logging(night, False); night.patch()
rep = vclock.run(night.main(), CLOCK)
g = presses.grade(night)
g["seed"] = {seed}
g["night_ok"] = rep.get("ok")
g["night_failed"] = [{{k: c.get(k) for k in ("name", "detail")}} for c in rep.get("checks", []) if not c.get("ok")]
g["mid_play"] = presses.classify_mid_play(rep, g)
print("@@" + json.dumps(g, default=str))
"""
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=3600, cwd=ROOT)
    line = next((x for x in p.stdout.splitlines() if x.startswith("@@")), None)
    if line is None:
        return {"seed": seed, "error": (p.stderr or p.stdout)[-800:]}
    return json.loads(line[2:])


STARTUP = """
import resource, sys, tempfile, time
t0 = time.perf_counter()
sys.path.insert(0, {root!r})
from pathlib import Path
from redzone.config import Config
from redzone.engine import Engine
from redzone.store import Store
from redzone.web.server import build_app
t_import = time.perf_counter()
d = Path(tempfile.mkdtemp(prefix="rz-start-"))
e = Engine(Config(user_path=d / "config.yaml", history_dir=d / "hist"), Store(d / "rz.db"))
build_app(e)
e.decide()
t_ready = time.perf_counter()
rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024)
print("@@", round(t_import - t0, 2), round(t_ready - t0, 2), round(rss, 1))
"""


def startup() -> dict:
    """Cold start of the app in a fresh process (imports, Engine, web app, first decision tick) and
    its peak RSS. No network, no sticks: the floor of a real restart."""
    p = subprocess.run([sys.executable, "-c", STARTUP.format(root=str(ROOT))], capture_output=True, text=True,
                       timeout=300, cwd=ROOT)
    line = next((x for x in p.stdout.splitlines() if x.startswith("@@")), None)
    if line is None:
        return {"error": (p.stderr or p.stdout)[-400:]}
    imp, ready, rss = (float(x) for x in line.split()[1:])
    return {"import_s": imp, "ready_s": ready, "rss_mb": rss}


def summary(runs: list[dict]) -> dict:
    xs = sorted(x for r in runs for x in r.get("go_now_s", []) + r.get("open_go_s", []))
    q = (lambda p: xs[min(len(xs) - 1, int(p * len(xs)))]) if xs else (lambda p: None)
    s = {"nights": len(runs), "go_now_n": len(xs), "go_now_p50_s": q(0.5), "go_now_p95_s": q(0.95),
         "go_now_max_s": xs[-1] if xs else None,
         "go_now_within_3s_pct": round(100 * sum(x <= 3.0 for x in xs) / len(xs), 1) if xs else None}
    for k in ("warm", "cold"):   # pre-tuned presses (a flip) and cold ones (a tune), reported apart
        v = sorted(x for r in runs for x in r.get(k + "_s", []))
        s[k] = {"n": len(v), "p50_s": v[len(v) // 2] if v else None,
                "p95_s": v[min(len(v) - 1, int(0.95 * len(v)))] if v else None,
                "within_3s_pct": round(100 * sum(x <= 3.0 for x in v) / len(v), 1) if v else None}
    for k in ("go_now_s", "open_go_s", "mid_flip_s", "lock_s", "skip_s"):
        v = sorted(x for r in runs for x in r.get(k, []))
        s[k.replace("_s", "_median_s")] = v[len(v) // 2] if v else None
        s[k.replace("_s", "_max_s")] = v[-1] if v else None
    for k in ("tick_p50_ms", "tick_p99_ms", "tick_max_ms", "rss_peak_mb"):
        v = [r[k] for r in runs if r.get(k) is not None]
        s[k] = max(v) if v else None   # the worst night
    s["taps"] = sum(r.get("taps", 0) for r in runs)
    s["taps_retried"] = sum(r.get("taps_retried", 0) for r in runs)
    for why in ("forced_go_now", "after_release", "unexplained"):
        s["mid_play_" + why] = sum(1 for r in runs for m in r.get("mid_play", []) if m.get("why") == why)
    for k in ("dropped", "lock_broken", "pause_broken", "skip_stuck", "not_once", "lock_waited_out"):
        s[k] = sum(len(r.get(k, [])) for r in runs)
    n: dict = {}
    for r in runs:
        for k, v in (r.get("n") or {}).items():
            n[k] = n.get(k, 0) + v
    s["presses"] = n
    s["errors"] = [r["error"] for r in runs if r.get("error")]
    return s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=4)
    ap.add_argument("--tv", choices=("home", "smartthings"), default="smartthings")
    ap.add_argument("--hours", type=float, default=None)
    ap.add_argument("--with-faults", action="store_true", help="also inject the night's default faults")
    ap.add_argument("--pretune", action="store_true", help="two_stick.pretune_on_open on")
    ap.add_argument("--flaky", action="store_true", help="lose and duplicate taps on the way (RZ_FLAKY=1)")
    ap.add_argument("--profile", default=None, help="a Noggin profile's config overrides (redzone/noggin/profile.py)")
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args(argv)
    if a.pretune:
        os.environ["RZ_PRETUNE"] = "1"
    if a.flaky:
        os.environ["RZ_FLAKY"] = "1"
    if a.profile:
        os.environ["RZ_PROFILE"] = a.profile
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(4, a.seeds)) as ex:
        runs = list(ex.map(lambda s: one(s, a.tv, a.hours, a.with_faults), range(1, a.seeds + 1)))
    out = {"summary": {**summary(runs), "startup": startup()}, "runs": runs}
    text = json.dumps(out, indent=1, default=str)
    if a.json:
        a.json.write_text(text)
    print(json.dumps(out["summary"], indent=1))
    s = out["summary"]
    bad = s["mid_play_after_release"] or s["mid_play_unexplained"] or s["dropped"] or s["lock_broken"] or s["pause_broken"] or s["skip_stuck"] or s["not_once"] or s["errors"]
    return 1 if bad else 0


if __name__ == "__main__":
    os.environ.setdefault("TZ", "America/New_York")
    _ = tempfile
    sys.exit(main())
