"""[calib.auto] Self-calibration: values re-measured each night from live telemetry, with no one's input.

Serves: BTN-02 (press_to_tv_p95: the flip deadline the decider prices), STK-06 (hold_cal_s). Owner: lane 3.

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

Why: first added in 8ba60ace (2026-11-04) as "Self-calibration, timing half (calib.auto, off): nightly flip and press-to-TV, 3-MAD guard"; no separate decision is recorded for this module.
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
MIN_SAMPLES = {"tv_flip_p50_s": 5, "tv_flip_p95_s": 10, "tv_flip_max_s": 10, "press_to_tv_p50_s": 3, "press_to_tv_p95_s": 10,
               # lane 2's sync half: a seek fit needs a few batches; a channel's delay a few anchors
               "yttv_seek_step_s": 4, "yttv_seek_wake_presses": 4, "yttv_seek_stall_s": 4}
DELAY_MIN_ANCHORS = 5     # delay_s:<target> needs this many used anchors on the night


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
    config file is untouched. None when off or nothing accepted yet.

    The median moves both ways. The max is a landing deadline's upper bound (rule #1), so calibration
    may only RAISE it: the larger of the shipped prior's max, the newest night's max and every accepted
    night's max in the guard's history (a quiet night's low max never shrinks it)."""
    if not cfg.get("calib.auto", False):
        return None
    got = latest(root)
    med, mx = got.get("tv_flip_p50_s"), got.get("tv_flip_max_s")
    if med is None or mx is None:
        return None
    shipped = float((cfg.get("two_stick.tv_flip_prior_s") or {}).get("max", 2.58))
    upper = max([shipped, mx["value"]] + history(root, "tv_flip_max_s", "9999-12-31"))
    if upper < med["value"]:
        return None
    log.info("calib.auto: TV flip prior median %.2f s (%s), max %.2f s (never below the shipped %.2f s)",
             med["value"], med["night"], upper, shipped)
    return round(med["value"], 2), round(upper, 2)


# --------------------------------------------------------------------- lane 2: the sync half

ANCHOR_KINDS_SKIP = ("tune", "shift", "position")


def seek_fit(rows: list[dict]) -> dict[str, tuple]:
    """YouTube TV's seek from the night's seeks.jsonl ({"sent": presses, "landed_s": how far behind the
    stick's own clock vs position says it went}), as the live lab measured it by hand on Nov 4.

    wake presses: the largest press count that still moved the picture < 1 s (0 if none was seen);
    step: the median of (landed - stall) per counted press, with stall the intercept of a least-squares line
    through (presses that count, landed); stall: that intercept. Rows with one press that did nothing count
    only toward the wake estimate."""
    pts = [(int(r["sent"]), float(r["landed_s"])) for r in rows
           if isinstance(r.get("sent"), int) and r.get("landed_s") is not None and r["sent"] > 0]
    if not pts:
        return {}
    still = [n for n, x in pts if x < 1.0]
    wake = max(still) if still else 0
    moved = [(n - wake, x) for n, x in pts if n > wake and x >= 1.0]
    out: dict[str, tuple] = {"yttv_seek_wake_presses": (float(wake), len(pts), "seeks.jsonl: presses that moved < 1 s")}
    ns = sorted({n for n, _ in moved})
    if len(moved) >= 2 and len(ns) >= 2:
        mx = statistics.mean(n for n, _ in moved)
        my = statistics.mean(x for _, x in moved)
        sxx = sum((n - mx) ** 2 for n, _ in moved)
        step = sum((n - mx) * (x - my) for n, x in moved) / sxx
        stall = my - step * mx
        out["yttv_seek_step_s"] = (step, len(moved), "seeks.jsonl: slope of landed vs counted presses")
        out["yttv_seek_stall_s"] = (stall, len(moved), "seeks.jsonl: intercept (the seek's stall)")
    elif moved:
        # one press count only: the step can't be told from the stall; keep the measured stall's mid prior
        pass
    return out


def delay_fit(rows: list[dict], lo: float | None = None, hi: float | None = None) -> dict[str, tuple]:
    """Per-channel fresh-tune delay from the night's sync.jsonl: every anchor the filter USED, less what
    that tune was deliberately held / rewound behind live (`behind`, logged with each row). The value is
    the median, its spread 1.4826 MAD (floored at 0.5 s), keyed delay_s:<target>."""
    by: dict[str, list[float]] = {}
    for r in rows:
        t = r.get("t")
        if (lo is not None and (t is None or t < lo)) or (hi is not None and (t is None or t >= hi)):
            continue
        if r.get("kind") in ANCHOR_KINDS_SKIP or not r.get("used", False) or r.get("z") is None \
                or "behind" not in r or not r.get("target"):
            continue
        by.setdefault(r["target"], []).append(float(r["z"]) - float(r["behind"] or 0.0))
    out = {}
    for target, xs in by.items():
        if len(xs) < DELAY_MIN_ANCHORS:
            continue
        med = statistics.median(xs)
        sd = max(0.5, 1.4826 * statistics.median(abs(x - med) for x in xs))
        out[f"delay_s:{target}"] = (med, len(xs), "sync.jsonl: used anchors less the held/rewound offset", sd)
    return out


def night_rows(path: Path, lo: float | None = None, hi: float | None = None) -> list[dict]:
    rows = []
    for p in (path.with_suffix(".jsonl.1"), path):
        try:
            for line in p.read_text().splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                t = r.get("t")
                if (lo is None or (t is not None and t >= lo)) and (hi is None or (t is not None and t < hi)):
                    rows.append(r)
        except OSError:
            continue
    return rows


def sync_from_night(root: Path, lo: float, hi: float) -> dict[str, tuple]:
    """Lane 2's sync half for the night [lo, hi): the seek fit and every channel's delay."""
    root = Path(root)
    return {**seek_fit(night_rows(root / "seeks.jsonl", lo, hi)), **delay_fit(night_rows(root / "sync.jsonl", lo, hi))}


def sync_prior(cfg, root: Path) -> dict:
    """At start, with calib.auto on: {"seek": SeekModel | None, "delays": {target: (mu, sd)}} from the
    newest accepted nights. In memory only. Seek values outside sane bounds are ignored."""
    if not cfg.get("calib.auto", False):
        return {"seek": None, "delays": {}}
    got = latest(root, "sync")
    seek = None
    step, wake, stall = (got.get(k) for k in ("yttv_seek_step_s", "yttv_seek_wake_presses", "yttv_seek_stall_s"))
    if step is not None and 5.0 <= step["value"] <= 60.0:
        from redzone.contracts.switching import seek_model
        base = seek_model(cfg)
        st = stall["value"] if stall is not None and 0.0 <= stall["value"] <= 10.0 else base.stall_mid
        half = (base.stall_hi - base.stall_lo) / 2
        seek = type(base)(step=round(step["value"], 2),
                          swallowed=int(round(wake["value"])) if wake is not None and 0 <= wake["value"] <= 3 else base.swallowed,
                          stall_lo=max(0.0, st - half), stall_hi=st + half, late_ok=base.late_ok)
        log.info("calib.auto: seek %.1f s a press, %d wake, stall %.1f-%.1f s (%s)", seek.step, seek.swallowed,
                 seek.stall_lo, seek.stall_hi, step["night"])
    floor = float(cfg.get("calib.sigma_floor_s"))   # a calibrated channel's prior is never surer than this
    delays = {k.split(":", 1)[1]: (v["value"], max(float(v.get("sd", floor)), floor))
              for k, v in got.items() if k.startswith("delay_s:")}
    return {"seek": seek, "delays": delays}
