"""python -m redzone.lab.night [--hours H] [--start HH:MM] [--json out.json] ... (see run.py)."""
from redzone.lab.night import vclock

CLOCK = vclock.install(1_793_570_000.0)   # replaced by the night's own start in Night.prepare

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import time as _t  # noqa: E402
from pathlib import Path  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m redzone.lab.night")
    ap.add_argument("--hours", type=float, default=None, help="run only this many hours")
    ap.add_argument("--start", default=None, help="start at HH:MM ET instead of before the first game")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--work", default=None, help="folder for the night's data (default: a temp folder)")
    ap.add_argument("--json", default=None, help="write the report here")
    ap.add_argument("--fault", action="append", default=[], help='extra fault, JSON: {"name":"stick_drop","at":600,"dur":60}')
    ap.add_argument("--faults", action="append", default=[], help="module:function returning more faults")
    ap.add_argument("--no-default-faults", action="store_true")
    ap.add_argument("--no-watchdog", action="store_true")
    ap.add_argument("--tv", choices=("home", "smartthings"), default="home",
                    help="smartthings: flips by the TV's own input change at the latency measured on the user's TV")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    os.environ.setdefault("TZ", "America/New_York")
    _t.tzset()
    work = Path(a.work) if a.work else Path(tempfile.mkdtemp(prefix="rz-night-"))
    from redzone.lab.night.run import Night, hostile_host, setup_logging
    if os.environ.get("RZ_NIGHT_HOSTILE_HOST") == "1":
        hostile_host()      # the CI night: Night.patch() has to pin the host away (run.py pin_host/pin_network)
    night = Night(CLOCK, work, seed=a.seed, end_s=a.hours * 3600 if a.hours else None, start_at=a.start,
                  fault_specs=[json.loads(x) for x in a.fault], plugins=a.faults,
                  default_faults=not a.no_default_faults, use_watchdog=not a.no_watchdog, tv_path=a.tv)
    night.prepare()
    setup_logging(night, a.verbose)
    night.patch()
    started = _t.perf_counter()
    report = vclock.run(night.main(), CLOCK)
    report["real_seconds"] = round(_t.perf_counter() - started, 1)
    report["work"] = str(work)
    text = json.dumps(report, indent=1, default=str)
    if a.json:
        Path(a.json).write_text(text)
    print(text if not a.json else json.dumps({"ok": report["ok"], "checks": {c["name"]: c["ok"] for c in report["checks"]},
                                              "real_seconds": report["real_seconds"], "json": a.json}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
