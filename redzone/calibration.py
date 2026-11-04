"""[calib.auto] Self-calibration: values re-measured each night from live telemetry, with no one's input.

One dated file per night, data/calib/YYYY-MM-DD.json, shared by every lane that calibrates, one section
per half (Start Planner, Nov 4): "timing" (lane 3: flip time, press-to-TV) and "sync" (lane 2: seek step,
wake presses, per-channel delay):

    {"night": "2026-11-04", "written_at": 1793...,
     "timing": {"tv_flip_p50_s": {"value": 1.89, "n": 64, "source": "tv_input.jsonl verified total_s",
                                  "accepted": true, "reason": "ok", "ref": 1.92, "mad": 0.03}, ...},
     "sync": {"yttv_seek_step_s": {...}, "delay_s:youtubetv:espn": {...}}}

A key is a name plus its unit (`_s` seconds); per-target values use `<name>:<target>`. `merge` adds keys
to one section of an existing night's file, so several writers share it (last write per key wins).

Outlier guard: a value further than GUARD_MADS median absolute deviations from the accepted values of
the last HISTORY nights is written with accepted=false and never read back. With fewer than MIN_HISTORY
nights there is nothing to judge against and the value is accepted. `latest` returns the newest accepted
value per key: what the planner reads at the next start (when calib.auto is on).
"""
from __future__ import annotations

import json
import logging
import statistics
import time
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

HISTORY = 5           # nights judged against
MIN_HISTORY = 3       # fewer: accept (nothing to judge against)
GUARD_MADS = 3.0      # refuse a value further than this many MADs from the history median
MAD_FLOOR = 0.05      # ...where the MAD is at least 5% of that median (identical nights give MAD 0)
MIN_SAMPLES = {"tv_flip_p50_s": 5, "tv_flip_p95_s": 10, "tv_flip_max_s": 10, "press_to_tv_p50_s": 3, "press_to_tv_p95_s": 10}


def folder(root: Path) -> Path:
    return Path(root) / "calib"


def _nights(root: Path) -> list[tuple[str, dict]]:
    out = []
    for p in sorted(folder(root).glob("????-??-??.json")):
        try:
            out.append((p.stem, json.loads(p.read_text())))
        except (OSError, ValueError):
            log.warning("unreadable calibration file %s", p)
    return out


def history(root: Path, key: str, before: str, section: str = "timing") -> list[float]:
    """Accepted values of `key` on the last HISTORY nights before `before` (ISO date), oldest first."""
    xs = [n[section][key]["value"] for d, n in _nights(root) if d < before
          and (n.get(section) or {}).get(key, {}).get("accepted")]
    return [float(x) for x in xs[-HISTORY:]]


def judge(value: float, past: list[float]) -> tuple[bool, str, float | None, float | None]:
    """(accepted, reason, history median, mad)."""
    if len(past) < MIN_HISTORY:
        return True, f"accepted: {len(past)} earlier night(s), guard needs {MIN_HISTORY}", None, None
    ref = statistics.median(past)
    mad = max(statistics.median(abs(x - ref) for x in past), MAD_FLOOR * abs(ref), 1e-6)
    off = abs(value - ref) / mad
    if off > GUARD_MADS:
        return False, f"refused: {off:.1f} MADs from the last {len(past)} nights (limit {GUARD_MADS:g})", ref, mad
    return True, "ok", ref, mad


def merge(root: Path, night: date | str, measured: dict[str, tuple], now: float | None = None,
          section: str = "timing") -> dict:
    """Judge and write `measured` {key: (value, n, source[, sd])} into one section of the night's file.
    Returns that section."""
    night = night if isinstance(night, str) else night.isoformat()
    path = folder(root) / f"{night}.json"
    try:
        doc = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        doc = {}
    vals = doc.get(section) or {}
    for key, m in measured.items():
        value, n, source = m[:3]
        sd = m[3] if len(m) > 3 else None      # optional spread (lane 2's delay_s for the sync prior)
        if value is None:
            continue
        if n < MIN_SAMPLES.get(key, 1):
            vals[key] = {"value": round(float(value), 3), "n": n, "source": source, "accepted": False,
                         "reason": f"refused: {n} samples, needs {MIN_SAMPLES.get(key, 1)}"}
            continue
        ok, why, ref, mad = judge(float(value), history(root, key, night, section))
        vals[key] = {"value": round(float(value), 3), "n": n, "source": source, "accepted": ok, "reason": why,
                     "ref": None if ref is None else round(ref, 3), "mad": None if mad is None else round(mad, 3)}
        if sd is not None:
            vals[key]["sd"] = round(float(sd), 3)
        if not ok:
            log.warning("calibration %s=%.3f on %s %s", key, value, night, why)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.update({"night": night, "written_at": round(now if now is not None else time.time(), 1), section: vals})
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True))
    tmp.replace(path)
    return vals


def latest(root: Path, section: str = "timing") -> dict[str, dict]:
    """The newest accepted value per key of one section across every night: {key: {"value", "night", "n"}}."""
    out: dict[str, dict] = {}
    for night, doc in _nights(root):
        for key, v in (doc.get(section) or {}).items():
            if v.get("accepted"):
                out[key] = {"value": float(v["value"]), "night": night, "n": v.get("n"),
                            **({"sd": float(v["sd"])} if v.get("sd") is not None else {})}
    return out


def timing_from_summary(s: dict) -> dict[str, tuple[float, int, str]]:
    """Lane 3's timing half, from a live night's summary.json (scripts/collect_live_test.py)."""
    n_sw, n_pr = int(s.get("switch_samples") or 0), int(s.get("press_samples") or 0)
    src_sw, src_pr = "tv_input.jsonl verified total_s", "traces.jsonl user switch decided -> picture"
    return {"tv_flip_p50_s": (s.get("switch_p50_s"), n_sw, src_sw),
            "tv_flip_p95_s": (s.get("switch_p95_s"), n_sw, src_sw),
            "tv_flip_max_s": (s.get("switch_max_s"), n_sw, src_sw),
            "press_to_tv_p50_s": (s.get("press_to_tv_p50_s"), n_pr, src_pr),
            "press_to_tv_p95_s": (s.get("press_to_tv_p95_s"), n_pr, src_pr)}


def flip_prior(cfg, root: Path) -> tuple[float, float] | None:
    """At start, with calib.auto on: the (median, max) TV flip the planner uses before 5 flips of its
    own (in place of two_stick.tv_flip_prior_s), from the newest accepted nights. In memory only: the
    config file is untouched. None when off or nothing accepted yet."""
    if not cfg.get("calib.auto", False):
        return None
    got = latest(root)
    med, mx = got.get("tv_flip_p50_s"), got.get("tv_flip_max_s")
    if med is None or mx is None or mx["value"] < med["value"]:
        return None
    log.info("calib.auto: TV flip prior median %.2f s (%s), max %.2f s (%s)",
             med["value"], med["night"], mx["value"], mx["night"])
    return round(med["value"], 2), round(mx["value"], 2)
