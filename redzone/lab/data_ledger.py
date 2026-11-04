"""Data ledger + NFM status (lane 5, Start Planner item 3; cycle 06 "Data ledger" standing section).

Serves: the cycle plans' data ledger table and the phone's "Data + NFM" sheet. Owner: lane 5 (reader);
lane 1 (writer). Frontier code (redzone/lab): read only, never on the decision path.

The ledger lives in ONE small JSON file, committed so the Mac's phone page and every planner read the same
numbers: docs/data-ledger.json (LEDGER). Lane 1 writes it from the factory, e.g. with
`python scripts/data_ledger.py --manifest <factory out>/manifest.json`, which folds a factory manifest
into it. Format (every field optional; missing = PENDING):

    {"updated": "2026-11-04T22:00Z",
     "leagues": {"nfl": {"source": "...", "license": "...", "real_tokens": 0, "synthetic_tokens": 0,
                         "windows": 0, "games": 0, "nights": 0, "seasons": "2002-2025",
                         "crunch_share": 0.0, "ot_share": 0.0, "comeback_share": 0.0, "blowout_share": 0.0}},
     "nfm": {"params": null, "tokens_target": 1e9,
             "milestones": [{"name": "event tokenizer", "status": "done|running|next|blocked",
                             "at": "...", "note": "..."}]}}
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LEDGER = ROOT / "docs" / "data-ledger.json"
LEAGUES = ("nfl", "cfb", "nba", "nhl", "mlb", "cbb", "live")
SHARES = ("crunch_share", "ot_share", "comeback_share", "blowout_share")
STATUSES = ("done", "running", "next", "blocked")
DEFAULT_MILESTONES = [  # the NFM plan (cycle 06 addendum); lane 1 overwrites with real status
    {"name": "Event tokens from the factory (~1B, real + self-play)", "status": "next"},
    {"name": "Pretrain: next event, its time, next stoppage", "status": "next"},
    {"name": "Heads: watch value, crunch enter/exit, breaks, big-play-next, importance", "status": "next"},
    {"name": "Distill to the Reflex (< 5 ms) + Mind tier on the Mac (<= 2 GB)", "status": "next"},
    {"name": "Gates: locked nights, oracle rank agreement, calibration", "status": "next"},
]


def _num(x) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def read(path: Path = LEDGER) -> dict:
    """The ledger file, or {} when missing or unreadable (the phone then shows PENDING everywhere)."""
    try:
        d = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def view(ledger: dict) -> dict:
    """Rows per league (real/synthetic tokens, shares, coverage), totals and the NFM milestones."""
    lgs = ledger.get("leagues") if isinstance(ledger.get("leagues"), dict) else {}
    rows = []
    for lg in list(LEAGUES) + sorted(k for k in lgs if k not in LEAGUES):
        e = lgs.get(lg) if isinstance(lgs.get(lg), dict) else {}
        rows.append({"league": lg, "source": str(e.get("source") or ""), "license": str(e.get("license") or ""),
                     "real_tokens": _num(e.get("real_tokens")), "synthetic_tokens": _num(e.get("synthetic_tokens")),
                     "windows": _num(e.get("windows")), "games": _num(e.get("games")), "nights": _num(e.get("nights")),
                     "seasons": str(e.get("seasons") or ""),
                     **{k: _num(e.get(k)) for k in SHARES},
                     "pending": not any(_num(e.get(k)) is not None for k in ("real_tokens", "synthetic_tokens", "windows"))})
    nfm = ledger.get("nfm") if isinstance(ledger.get("nfm"), dict) else {}
    ms = [m for m in (nfm.get("milestones") or []) if isinstance(m, dict) and m.get("name")] or DEFAULT_MILESTONES
    ms = [{"name": str(m["name"]), "status": m.get("status") if m.get("status") in STATUSES else "next",
           "at": str(m.get("at") or ""), "note": str(m.get("note") or "")} for m in ms]
    real = sum(r["real_tokens"] or 0 for r in rows)
    syn = sum(r["synthetic_tokens"] or 0 for r in rows)
    target = _num(nfm.get("tokens_target")) or 1e9
    return {"updated": str(ledger.get("updated") or ""), "rows": rows,
            "totals": {"real_tokens": real, "synthetic_tokens": syn, "tokens_target": target,
                       "pct_of_target": round(100 * (real + syn) / target, 1)},
            "nfm": {"params": _num(nfm.get("params")), "milestones": ms,
                    "done": sum(1 for m in ms if m["status"] == "done")},
            "pending": all(r["pending"] for r in rows)}


def fold_manifest(ledger: dict, manifest: dict, updated: str) -> dict:
    """Fold a factory manifest.json (scripts/noggin_factory.py: per league source, license, seasons,
    windows and per-split games/nights) into the ledger. Token and share fields are left to lane 1."""
    out = json.loads(json.dumps(ledger or {}))
    lgs = out.setdefault("leagues", {})
    for lg, m in (manifest or {}).items():
        if not isinstance(m, dict):
            continue
        e = lgs.setdefault(lg, {})
        sp = m.get("splits") if isinstance(m.get("splits"), dict) else {}
        e.update({k: m[k] for k in ("source", "license", "seasons", "windows") if k in m})
        if sp:
            e["games"] = sum(int(s.get("games") or 0) for s in sp.values() if isinstance(s, dict))
            e["nights"] = sum(int(s.get("nights") or 0) for s in sp.values() if isinstance(s, dict))
    out["updated"] = updated
    return out


def table(v: dict) -> str:
    """The cycle plan's ledger table (markdown) from a view."""
    def f(x, pct=False):
        if x is None:
            return "PENDING"
        return f"{100 * x:.0f}%" if pct else (f"{x / 1e6:.1f}M" if x >= 1e5 else f"{x:,.0f}")
    lines = ["| league | source (license) | real tokens | synthetic tokens | windows | crunch share | OT / comeback / blowout |",
             "|---|---|---:|---:|---:|---:|---|"]
    for r in v["rows"]:
        src = f"{r['source'] or 'PENDING'}" + (f" ({r['license']})" if r["license"] else "")
        lines.append(f"| {r['league'].upper()} | {src} | {f(r['real_tokens'])} | {f(r['synthetic_tokens'])} | "
                     f"{f(r['windows'])} | {f(r['crunch_share'], True)} | {f(r['ot_share'], True)} / "
                     f"{f(r['comeback_share'], True)} / {f(r['blowout_share'], True)} |")
    return "\n".join(lines)
