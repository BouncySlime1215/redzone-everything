#!/usr/bin/env python3
"""Collect a live test's logs into one folder, with a summary the North Star harness reads (cycle 01
plan, task 4: "the sync-probe log and data/tv_input.jsonl upload automatically after the test").

    python scripts/collect_live_test.py --data <mac repo>/data --since 2026-11-04T22:00 \
        --out /mnt/project-files/live-test/2026-11-04

Copies only rows at or after --since (default: the start in /tmp/rz_test_start, else 18 h ago):
  sync_probe.jsonl      recordings/<day>/sync_probe.jsonl of every day in range (the read-only probe, #71)
  tv_input.jsonl        every TV input command + its verify latency (redzone/tv.py)  -> switch p95
  supervisor_cuts.jsonl where each switch landed (enter/leave play/dead)            -> mid-play entries
  traces.jsonl          per-switch timing marks (cut -> picture, flip); kind=user rows -> press_to_tv_p95
and writes summary.json + summary.md: rows per file, switch p95 (scripts/northstar.switch_samples rule:
verified rows' total_s), mid-play entries, picture-within-3s share.

Private LAN addresses and MACs are masked in the copies, so the folder can be shared (the repo is
public: never commit these files). Read-only on the source: it never edits or deletes the Mac's data.
Feed it to the harness with: python scripts/northstar.py --switch-log <out>/tv_input.jsonl
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_IP = re.compile(r"\b(?:10|127|172\.(?:1[6-9]|2\d|3[01])|192\.168)(?:\.\d{1,3}){2,3}\b")
_MAC = re.compile(r"\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b")
TS_KEYS = ("ts", "decided_at", "cut_at", "t", "at", "time")


def redact(line: str) -> str:
    return _MAC.sub("xx:xx:xx:xx:xx:xx", _IP.sub(lambda m: "lan." + m.group(0).rsplit(".", 1)[-1], line))


def _ts(row: dict) -> float | None:
    for k in TS_KEYS:
        v = row.get(k)
        if isinstance(v, (int, float)) and v > 1e9:
            return float(v)
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
    return None


def copy_rows(srcs: list[Path], dst: Path, since: float, until: float | None = None) -> list[dict]:
    rows, out = [], []
    for p in srcs:
        if not p.exists():
            continue
        with p.open(errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = _ts(r) if isinstance(r, dict) else None
                if t is None or (t >= since and (until is None or t < until)):
                    rows.append(r)
                    out.append(redact(line.rstrip("\n")))
    if out:
        dst.write_text("\n".join(out) + "\n")
    return rows


def _clock_et(ts) -> str | None:
    """Epoch seconds -> 'HH:MM' Eastern (the night's clock), None when not a time."""
    if not isinstance(ts, (int, float)):
        return None
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(float(ts), ZoneInfo("America/New_York")).strftime("%H:%M")


def p95(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(0.95 * len(s) + 0.999999) - 1)], 2)


_SPAN_KEYS = {"queued_s": "queued", "safe_wait_s": "safe_wait", "tune_s": "tune", "flip_s": "flip",
              "resume_s": "resume", "dead_air_s": "dead_air", "to_picture_s": "to_picture"}


def switches(rows: list[dict]) -> list[dict]:
    """data/traces.jsonl holds one TraceEvent per line (switch_id, stage, at, component, detail: redzone/
    trace.py). Fold each switch's events into one flat record: kind, decided_at, cut_at, picture_at,
    spans. Rows already flat (decided_at at top level) pass through."""
    out, by = [], {}
    for r in rows:
        if "switch_id" not in r or "stage" not in r:
            out.append(r)
            continue
        sw = by.get(r["switch_id"])
        if sw is None:
            sw = by[r["switch_id"]] = {"id": r["switch_id"], "spans": {}}
            out.append(sw)
        d, at = r.get("detail") or {}, r.get("at")
        if r["stage"] == "decided":
            sw.update(kind=d.get("kind"), action=d.get("action"), target=d.get("target"), decided_at=at)
        elif r["stage"] == "picture":
            sw["picture_at"] = at
            if isinstance(d.get("dead_air_s"), (int, float)) and isinstance(at, (int, float)):
                sw["cut_at"] = at - float(d["dead_air_s"])
        elif r["stage"] in ("aborted", "failed"):
            sw["failed"] = r["stage"]
        for k, name in _SPAN_KEYS.items():
            if isinstance(d.get(k), (int, float)):
                sw["spans"][name] = float(d[k])
    return out


def summarize(got: dict[str, list[dict]]) -> dict:
    tv = [float(r["total_s"]) for r in got["tv_input.jsonl"]
          if r.get("verified") and isinstance(r.get("total_s"), (int, float))]
    cuts = got["supervisor_cuts.jsonl"]
    tr = switches(got["traces.jsonl"])
    pics = [(r.get("picture_at") or 0) - (r.get("cut_at") or 0) for r in tr if r.get("picture_at") and r.get("cut_at")]
    # Lane 3's press_to_tv_p95: a phone press (decided kind=user) -> the picture on the TV.
    # (pause / lock / skip are user decisions too, but not switches: only a switch can reach a picture.)
    user = [r for r in tr if r.get("kind") == "user" and r.get("action") in (None, "switch")]
    press = [float(r["picture_at"]) - float(r["decided_at"]) for r in user
             if isinstance(r.get("picture_at"), (int, float)) and isinstance(r.get("decided_at"), (int, float))]
    # [live nights] Where each landing came up in its dead ball (supervisor grade_cut offsets) and how
    # far each sync anchor was from the delay we predicted, per stick (decider/sync.py err, stick).
    after = sorted(float(r["enter_after_dead_s"]) for r in cuts if isinstance(r.get("enter_after_dead_s"), (int, float)))
    before = sorted(float(r["enter_before_play_s"]) for r in cuts if isinstance(r.get("enter_before_play_s"), (int, float)))
    anchors: dict[str, list[float]] = {}
    for r in got.get("sync.jsonl", []):
        if r.get("kind") in ("flip", "score", "clock", "tap") and isinstance(r.get("err"), (int, float)):
            anchors.setdefault(str(r.get("stick") or "unknown"), []).append(abs(float(r["err"])))
    # Where a press's time goes, app side vs the TV (trace.build spans): decide, queued (the lock),
    # tune, safe_wait (a dead ball), flip (SmartThings), resume. p50 / p95 per span, user presses only.
    spans: dict[str, list[float]] = {}
    for r in user:
        for k, v in (r.get("spans") or {}).items():
            if isinstance(v, (int, float)) and k not in ("total",):
                spans.setdefault(k, []).append(float(v))
    # [STK-06] the hidden stick's time-shift buffer: holds that went bad (lost / drift) and were rebuilt.
    buf = got.get("buffer.jsonl", [])
    bad = [r for r in buf if r.get("kind") in ("lost", "drift")]
    errs = [abs(float(r["err_s"])) for r in bad if isinstance(r.get("err_s"), (int, float))]
    # [STK-07] the left-game hold (buffer.hold_left): held, played on from the dead ball, or reloaded live.
    left = {k: sum(1 for r in buf if r.get("kind") == f"left_{k}") for k in ("held", "resumed", "expired", "lost")}
    # [provenance] which module contributed what, per switch (switches.detail.trace.provenance, from the DB)
    sw = got.get("switches.jsonl", [])
    prov = [((r.get("detail") or {}).get("trace") or {}).get("provenance") for r in sw]
    fired: dict[str, int] = {}
    for p in prov:
        for pb in (p or {}).get("playbook") or []:
            fired[pb] = fired.get(pb, 0) + 1
    # [night health] Every cut that left or entered a play: when (ET), which game, why (the grade's
    # reason); and what the supervisor fixed on its own (supervisor.jsonl kind=fix: rule, ok).
    misses = []
    for r in cuts:
        for end, game in (("enter", r.get("to_label") or r.get("to")), ("leave", r.get("from_label") or r.get("from"))):
            if r.get(end) == "play":
                misses.append({"at": _clock_et(r.get("ts")), "game": str(game or "?"), "end": end,
                               "why": str(r.get(f"{end}_why") or "")})
    faults: dict[str, dict] = {}
    for r in got.get("supervisor.jsonl", []):
        if r.get("kind") == "fix" and r.get("rule"):
            f = faults.setdefault(str(r["rule"]), {"n": 0, "failed": 0})
            f["n"] += 1
            f["failed"] += 0 if r.get("ok") else 1
    # [night health] big plays on screen when they aired, per league (supervisor track_moments; dry runs out)
    bigs: dict[str, dict] = {}
    for r in got.get("supervisor_moments.jsonl", []):
        if r.get("dry_run"):
            continue
        b = bigs.setdefault(str(r.get("league") or "?"), {"n": 0, "seen": 0})
        b["n"] += 1
        b["seen"] += 1 if r.get("seen") else 0
    return {
        "left_hold": left,
        "provenance": {"switches": len(sw), "with_provenance": sum(1 for p in prov if p),
                       "modules": sorted({m for p in prov for m in (p or {})}), "playbooks_fired": fired},
        "buffer": {"detected": len(bad), "lost": sum(1 for r in bad if r["kind"] == "lost"),
                   "recovered": sum(1 for r in buf if r.get("kind") == "recovered"),
                   "rebuild_failed": sum(1 for r in buf if r.get("kind") == "rebuild_failed"),
                   "err_p95_s": p95(errs)},
        "big_plays": dict(sorted(bigs.items())),
        "misses": misses[:50], "faults_fixed": dict(sorted(faults.items())),
        "landings_dead": sum(1 for r in cuts if r.get("enter") == "dead"),
        "landings_graded": sum(1 for r in cuts if r.get("enter") in ("dead", "play")),
        "press_spans": {k: {"n": len(v), "p50_s": round(sorted(v)[len(v) // 2], 2), "p95_s": p95(v)}
                        for k, v in sorted(spans.items())},
        "landings": {"n": len(after), "after_dead_p50_s": after[len(after) // 2] if after else None,
                     "before_play_min_s": before[0] if before else None,
                     "before_play_p50_s": before[len(before) // 2] if before else None},
        "sync_anchor_err": {k: {"n": len(v), "p50_s": sorted(v)[len(v) // 2], "p95_s": p95(v)}
                            for k, v in sorted(anchors.items())},
        "rows": {k: len(v) for k, v in got.items()},
        "switch_samples": len(tv), "switch_p50_s": round(sorted(tv)[len(tv) // 2], 2) if tv else None,
        "switch_p95_s": p95(tv), "switch_max_s": round(max(tv), 2) if tv else None,
        "tv_unverified": sum(1 for r in got["tv_input.jsonl"] if not r.get("verified")),
        "cuts_graded": len(cuts), "entries_mid_play": sum(1 for r in cuts if r.get("enter") == "play"),
        "leaves_mid_play": sum(1 for r in cuts if r.get("leave") == "play"),
        "picture_within_3s": f"{sum(1 for x in pics if x <= 3)}/{len(pics)}" if pics else None,
        "picture_p95_s": p95(pics),
        "press_samples": len(press), "press_to_tv_p95_s": p95(press),
        "press_to_tv_p50_s": round(sorted(press)[len(press) // 2], 2) if press else None,
        "press_no_picture": len(user) - len(press),
    }


def copy_switches(db: Path, dst: Path, since: float, until: float | None = None) -> list[dict]:
    """Every switch row of the window from the app's database (read-only), detail parsed: its trace,
    provenance included, is the night's record of who decided what. [] without a database."""
    if not db.exists():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        cur = con.execute("SELECT ts, frm, to_game, target, kind, reason, ok, dry_run, detail FROM switches "
                          "WHERE ts >= ? AND ts < ? ORDER BY ts", (since, until if until else 4e9))
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    except sqlite3.Error:
        return []
    finally:
        con.close()
    for r in rows:
        try:
            r["detail"] = json.loads(r["detail"] or "{}")
        except ValueError:
            r["detail"] = {}
    dst.write_text("".join(json.dumps(r, default=str) + "\n" for r in rows))
    return rows


def collect(data: Path, out: Path, since: float, until: float | None = None) -> dict:
    """Collect [since, until) into `out` and write summary.json / summary.md; returns the summary."""
    out.mkdir(parents=True, exist_ok=True)
    day0 = datetime.fromtimestamp(since).date()
    end = datetime.fromtimestamp(until).date() if until else datetime.now().date()
    days = [(day0 + timedelta(days=i)).isoformat() for i in range((end - day0).days + 1)]
    srcs = {"sync_probe.jsonl": [data / "recordings" / d / "sync_probe.jsonl" for d in days],
            "tv_input.jsonl": [data / "tv_input.jsonl"], "supervisor_cuts.jsonl": [data / "supervisor_cuts.jsonl"],
            "traces.jsonl": [data / "traces.jsonl"], "sync.jsonl": [data / "sync.jsonl"],
            "supervisor.jsonl": [data / "supervisor.jsonl"],
            "supervisor_moments.jsonl": [data / "supervisor_moments.jsonl"],
            "buffer.jsonl": [data / "buffer.jsonl"]}
    got = {k: copy_rows(v, out / k, since, until) for k, v in srcs.items()}
    got["switches.jsonl"] = copy_switches(data / "redzone.db", out / "switches.jsonl", since, until)
    s = summarize(got)
    s["since"] = datetime.fromtimestamp(since).isoformat(timespec="seconds")
    if until:
        s["until"] = datetime.fromtimestamp(until).isoformat(timespec="seconds")
    (out / "summary.json").write_text(json.dumps(s, indent=1))
    return s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, required=True, help="the service's data/ folder")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--since", help="ISO time or epoch seconds (default /tmp/rz_test_start, else 18 h ago)")
    a = ap.parse_args(argv)
    if a.since:
        since = float(a.since) if a.since.replace(".", "").isdigit() else datetime.fromisoformat(a.since).timestamp()
    else:
        try:
            since = float(Path("/tmp/rz_test_start").read_text().strip())
        except (OSError, ValueError):
            since = time.time() - 18 * 3600
    a.out.mkdir(parents=True, exist_ok=True)
    day0 = datetime.fromtimestamp(since).date()
    days = [(day0 + timedelta(days=i)).isoformat() for i in range((datetime.now().date() - day0).days + 1)]
    got = {
        "sync_probe.jsonl": copy_rows([a.data / "recordings" / d / "sync_probe.jsonl" for d in days],
                                      a.out / "sync_probe.jsonl", since),
        "tv_input.jsonl": copy_rows([a.data / "tv_input.jsonl"], a.out / "tv_input.jsonl", since),
        "supervisor_cuts.jsonl": copy_rows([a.data / "supervisor_cuts.jsonl"], a.out / "supervisor_cuts.jsonl", since),
        "traces.jsonl": copy_rows([a.data / "traces.jsonl"], a.out / "traces.jsonl", since),
        "sync.jsonl": copy_rows([a.data / "sync.jsonl"], a.out / "sync.jsonl", since),
        "buffer.jsonl": copy_rows([a.data / "buffer.jsonl"], a.out / "buffer.jsonl", since),
        "switches.jsonl": copy_switches(a.data / "redzone.db", a.out / "switches.jsonl", since),
        "supervisor.jsonl": copy_rows([a.data / "supervisor.jsonl"], a.out / "supervisor.jsonl", since),
        "supervisor_moments.jsonl": copy_rows([a.data / "supervisor_moments.jsonl"], a.out / "supervisor_moments.jsonl", since),
    }
    s = summarize(got)
    s["since"] = datetime.fromtimestamp(since).isoformat(timespec="seconds")
    (a.out / "summary.json").write_text(json.dumps(s, indent=1))
    md = [f"# Live test logs since {s['since']}", "",
          f"- Switches (TV-confirmed): {s['switch_samples']}, p95 {s['switch_p95_s']} s, max {s['switch_max_s']} s, "
          f"unverified {s['tv_unverified']}",
          f"- Cuts graded: {s['cuts_graded']}; entries mid-play {s['entries_mid_play']}, leaves mid-play {s['leaves_mid_play']}",
          f"- Picture within 3 s: {s['picture_within_3s']}; cut -> picture p95 {s['picture_p95_s']} s",
          f"- Phone presses (decided kind=user -> picture): {s['press_samples']}, press-to-TV p95 "
          f"{s['press_to_tv_p95_s']} s, {s['press_no_picture']} never reached a picture",
          "- Press spans (p50 / p95 s): " + (", ".join(f"{k} {v['p50_s']}/{v['p95_s']}"
                                                      for k, v in s["press_spans"].items()) or "none"),
          f"- Landings (dead ball): {s['landings']['n']}, came up {s['landings']['after_dead_p50_s']} s into it "
          f"(p50), {s['landings']['before_play_p50_s']} s before play resumed (p50, min {s['landings']['before_play_min_s']})",
          "- Sync anchor error by stick: " + (", ".join(f"{k} n={v['n']} p50 {v['p50_s']} s p95 {v['p95_s']} s"
                                                       for k, v in s["sync_anchor_err"].items()) or "none"),
          f"- Buffer (STK-06): {s['buffer']['detected']} went bad, {s['buffer']['recovered']} rebuilt, "
          f"error p95 {s['buffer']['err_p95_s']} s",
          f"- Left-game hold (STK-07): {s['left_hold']}",
          f"- Provenance: {s['provenance']['with_provenance']}/{s['provenance']['switches']} switches, playbooks fired "
          f"{s['provenance']['playbooks_fired'] or 'none'}",
          f"- Rows: {', '.join(f'{k} {v}' for k, v in s['rows'].items())}", ""]
    (a.out / "summary.md").write_text("\n".join(md))
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    sys.exit(main())
