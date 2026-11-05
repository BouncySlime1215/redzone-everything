"""[SYNC diamond B1 / A4] Log every stick's media session once a second through a game, then report it.

    python tools/rzseek/prime_night.py log HOST [HOST ...] --out data/reports/b1_<night>.jsonl [--every 1]
    python tools/rzseek/prime_night.py report data/reports/b1_<night>.jsonl

Read-only: one `cat /proc/uptime; dumpsys media_session` per stick per tick (the same read the app's probe
makes), never a key press. Sticks are written as stick1, stick2 ... in the order given, never by address.

`report` answers tonight's questions per stick x content (package + title):
  * epoch or counter: is the position above 1e12 (the frame's wall clock in ms, Prime NBA on Nov 4) or a
    running counter (the 24 h one seen on the other stick)?
  * E = wall - pos_now, piecewise flat? Its sd inside steady stretches, and every jump > 0.5 s while
    PLAYING that no state change explains (B1 pass: <= 2 per hour, else re-anchor at ad pods).
  * pauses: the rise in E across each PAUSED stretch next to how long it lasted (should match to 0.1 s).
  * epoch content: abs_behind = E itself (seconds behind real life), median and spread.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from redzone.tuners.adb import parse_playback  # noqa: E402

READ = "cat /proc/uptime; dumpsys media_session"
EPOCH_POSITION = 1e12
JUMP = 0.5


def read(host: str) -> dict:
    t0 = time.time()
    out = subprocess.run(["adb", "-s", host, "shell", READ], capture_output=True, text=True, timeout=8).stdout
    t1 = time.time()
    row = {"t": round((t0 + t1) / 2, 3), "rtt": round(t1 - t0, 3)}
    try:
        row["uptime_ms"] = int(float(out.split()[0]) * 1000)
    except (ValueError, IndexError):
        return dict(row, err="no uptime")
    b = parse_playback(out)
    row["ms"] = {k: b.get(k) for k in ("state", "pkg", "position", "updated", "speed", "title", "subtitle")
                 if b.get(k) is not None}
    return row


def log(hosts: list[str], out: Path, every: float) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    names = {h: f"stick{i + 1}" for i, h in enumerate(hosts)}
    with out.open("a") as f:
        while True:
            t = time.time()
            for h in hosts:
                try:
                    r = read(h)
                except Exception as e:   # a read must never stop the log
                    r = {"t": round(time.time(), 3), "err": type(e).__name__}
                r["stick"] = names[h]
                f.write(json.dumps(r, separators=(",", ":")) + "\n")
            f.flush()
            time.sleep(max(0.0, every - (time.time() - t)))


def pos_now(r: dict) -> float | None:
    m = r.get("ms") or {}
    if m.get("position") is None or m.get("updated") is None or r.get("uptime_ms") is None:
        return None
    sp = float(m.get("speed") or 1.0) if m.get("state") == "playing" else 0.0
    return (m["position"] + (r["uptime_ms"] - m["updated"]) * sp) / 1000.0


def report(rows: list[dict]) -> dict:
    out: dict[str, dict] = {}
    by: dict[tuple, list[dict]] = {}
    for r in rows:
        m = r.get("ms") or {}
        if "err" in r or pos_now(r) is None:
            continue
        by.setdefault((r["stick"], m.get("pkg"), m.get("title", "")), []).append(r)
    for (stick, pkg, title), rs in sorted(by.items(), key=lambda kv: kv[0][:2] + (str(kv[0][2]),)):
        rs.sort(key=lambda r: r["t"])
        e = [r["t"] - pos_now(r) for r in rs]
        epoch = sum(1 for r in rs if r["ms"]["position"] > EPOCH_POSITION) > len(rs) / 2
        jumps, pauses, steady, seg = [], [], [], []
        for i in range(1, len(rs)):
            a, b = rs[i - 1]["ms"], rs[i]["ms"]
            de = e[i] - e[i - 1]
            if a.get("state") == "paused" and b.get("state") == "paused":
                continue
            if a.get("state") == "playing" and b.get("state") == "playing" and a.get("updated") == b.get("updated"):
                seg.append(e[i])
                continue
            if seg:
                steady.append(seg)
                seg = []
            if a.get("state") == "playing" and b.get("state") == "playing" and abs(de) > JUMP:
                jumps.append({"t": rs[i]["t"], "dE": round(de, 2)})
            if b.get("state") == "playing" and a.get("state") == "paused":
                j = i - 1
                while j > 0 and rs[j - 1]["ms"].get("state") == "paused":
                    j -= 1
                pauses.append({"paused_s": round(rs[i]["t"] - rs[j]["t"], 1), "dE": round(e[i] - e[j - 1] if j else 0, 2)})
        if seg:
            steady.append(seg)
        sds = [statistics.pstdev(s) for s in steady if len(s) >= 5]
        hours = max(1e-9, (rs[-1]["t"] - rs[0]["t"]) / 3600)
        d = {"rows": len(rs), "hours": round(hours, 2), "epoch": epoch,
             "E_flat_sd_p50": round(statistics.median(sds), 3) if sds else None,
             "unexplained_jumps_per_h": round(len(jumps) / hours, 2), "jumps": jumps[:20], "pauses": pauses[:20]}
        if epoch:
            d["abs_behind_p50"] = round(statistics.median(e), 2)
            d["abs_behind_p5_p95"] = [round(sorted(e)[int(0.05 * (len(e) - 1))], 2),
                                      round(sorted(e)[int(0.95 * (len(e) - 1))], 2)]
        out[f"{stick} {pkg} {title[:40]}"] = d
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    lg = sub.add_parser("log")
    lg.add_argument("hosts", nargs="+")
    lg.add_argument("--out", type=Path, required=True)
    lg.add_argument("--every", type=float, default=1.0)
    rp = sub.add_parser("report")
    rp.add_argument("file", type=Path)
    a = ap.parse_args()
    if a.cmd == "log":
        log(a.hosts, a.out, a.every)
        return 0
    rows = []
    for line in a.file.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    print(json.dumps(report(rows), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
