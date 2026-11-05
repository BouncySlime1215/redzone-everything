"""[WV loop, Nick's oracle-audit item 7] Per-stick delay error at each switch landing, for the mistake ledger.

    python scripts/sync_truth_wv.py --traces data/traces.jsonl --sync data/sync.jsonl \
        --shadow data/buffer_shadow.jsonl --out /mnt/project-files/wv-loop/sync-truth

One row per landed switch (a confirmed picture, not a dry run), joined to the first independent anchor
on the same target that the sync filter logged within WINDOW_S after the picture came up. The rows come
from the same sync.jsonl rows and SOURCES that scripts/sync_truth.py grades, so taps are never used
(no signal from Nick):
    {"night": "YYYYMMDD", "league", "gid", "stick": "hdmi1"|"hdmi2"|None, "t_switch_utc": ISO,
     "delay_true_s": z, "delay_est_s": z - err, "delay_err_s": err (true minus est), "source", "anchor_lag_s"}
It is written to OUT/<league>/<YYYYMMDD>.jsonl. A landing with no anchor in the window still gets a row,
with null delays, so the ledger can tell "no measurement" apart from "no error". The league comes
from the buffer shadow's rows (gid -> league); a gid it never saw is "unknown". Stick hosts (IP:port)
are never written. The stick is the anchor row's HDMI input.
Derived numbers only: nothing here reads frames or the LAN.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sync_truth import SOURCES, night_of  # noqa: E402

WINDOW_S = 120.0   # an anchor this long after the picture still measures the delay the landing used


def _jsonl(lines):
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict):
            yield r


def anchors(lines) -> dict[str, list[dict]]:
    """target -> anchor rows (t, z, err, source, stick) in time order."""
    by: dict[str, list[dict]] = {}
    for r in _jsonl(lines):
        src = SOURCES.get(r.get("kind"))
        if src is None or r.get("z") is None or r.get("err") is None or r.get("t") is None or not r.get("target"):
            continue
        stick = str(r.get("stick") or "").lower().replace(" ", "")
        by.setdefault(r["target"], []).append({"t": float(r["t"]), "z": float(r["z"]), "err": float(r["err"]),
                                               "source": src, "stick": stick if stick in ("hdmi1", "hdmi2") else None})
    for rs in by.values():
        rs.sort(key=lambda a: a["t"])
    return by


def leagues(lines) -> dict[str, str]:
    out = {}
    for r in _jsonl(lines):
        if r.get("gid") and r.get("league"):
            out[str(r["gid"])] = str(r["league"])
    return out


def rows(trace_lines, sync_lines, shadow_lines=(), window_s: float = WINDOW_S) -> list[dict]:
    an, lg = anchors(sync_lines), leagues(shadow_lines)
    out = []
    for t in _jsonl(trace_lines):
        pic = t.get("picture_at")
        if t.get("action") != "switch" or not t.get("ok") or t.get("dry_run") or pic is None or not t.get("to"):
            continue
        pic = float(pic)
        a = next((x for x in an.get(t.get("target") or "", []) if pic <= x["t"] <= pic + window_s), None)
        gid = str(t["to"])
        out.append({
            "night": night_of(pic).replace("-", ""), "league": lg.get(gid, "unknown"), "gid": gid,
            "stick": a["stick"] if a else None,
            "t_switch_utc": datetime.fromtimestamp(pic, timezone.utc).isoformat(timespec="seconds"),
            "delay_true_s": round(a["z"], 3) if a else None,
            "delay_est_s": round(a["z"] - a["err"], 3) if a else None,
            "delay_err_s": round(a["err"], 3) if a else None,
            "source": a["source"] if a else None,
            "anchor_lag_s": round(a["t"] - pic, 1) if a else None,
        })
    return out


def write(rs: list[dict], out_dir: Path) -> dict[str, int]:
    by: dict[tuple[str, str], list[dict]] = {}
    for r in rs:
        by.setdefault((r["league"], r["night"]), []).append(r)
    for (league, night), group in by.items():
        p = out_dir / league / f"{night}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("".join(json.dumps(r) + "\n" for r in sorted(group, key=lambda r: r["t_switch_utc"])))
    return {f"{lg}/{n}": len(g) for (lg, n), g in sorted(by.items())}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--traces", type=Path, nargs="+", required=True)
    ap.add_argument("--sync", type=Path, nargs="+", required=True)
    ap.add_argument("--shadow", type=Path, nargs="*", default=[])
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    read = lambda ps: [line for p in ps for line in p.read_text().splitlines()]  # noqa: E731
    print(json.dumps(write(rows(read(a.traces), read(a.sync), read(a.shadow)), a.out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
