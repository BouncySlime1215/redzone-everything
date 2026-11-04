"""Turn CI results into the PR comment: tests, lint, and the scorecard smoke run, PR vs base.

    python scripts/ci_report.py --head head.json [--base base.json] [--junit pytest.xml] \
        [--lint lint.txt --lint-status 0] [--out comment.md]

Exits 1 when the PR adds mid-play cuts (the one hard scorecard rule: more cuts than the base on the
same slice, or any cut when there is no base to compare with), or when a scorecard crashes on the PR
although its data is there. Every other change is shown with an
arrow and a warning mark, not gated: trade-offs between metrics are a judgment call for review.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

MARKER = "<!-- redzone-ci -->"

# (metric, label, direction): +1 higher is better, -1 lower is better, 0 just shown.
HEADLINE = {
    "nfl": [("mid_play_cuts", "mid-play cuts", -1), ("big_seen_pct", "big plays seen %", 1),
            ("big_live_pct", "big plays live %", 1), ("crunch_live_pct", "crunch live %", 1),
            ("your_team_big_seen_pct", "your-team big seen %", 1),
            ("your_team_crunch_live_pct", "your-team crunch live %", 1),
            ("nfl_drive_live_pct", "drives live %", 1), ("dead_air_pct", "dead air %", -1),
            ("big_missed_on_replay", "big missed on replay", -1), ("switches_per_hour", "switches/hour", 0)],
    "nba": [("mid_play_cuts", "mid-play cuts", -1), ("big_seen_pct", "big plays seen %", 1),
            ("big_live_pct", "big plays live %", 1), ("crunch_live_pct", "crunch live %", 1),
            ("your_team_crunch_live_pct", "your-team crunch live %", 1), ("dead_air_pct", "dead air %", -1),
            ("switches_per_hour", "switches/hour", 0)],
    "nhl": [("mid_play_cuts", "mid-play cuts", -1), ("big_seen_pct", "goals seen %", 1),
            ("big_live_pct", "goals live %", 1), ("crunch_live_pct", "crunch live %", 1),
            ("pp_goals_live_pct", "power-play goals live %", 1),
            ("pp_minutes_on_screen", "power-play minutes on screen", 1),
            ("your_team_crunch_live_pct", "your-team crunch live %", 1), ("dead_air_pct", "dead air %", -1),
            ("switches_per_hour", "switches/hour", 0)],
    "demo": [("mid_play_cuts", "mid-play cuts", -1), ("nfl_big_plays.seen", "NFL big plays seen %", 1),
             ("nfl_big_plays.live", "NFL big plays live %", 1), ("nhl_goals.seen", "NHL goals seen %", 1),
             ("nhl_goals.live", "NHL goals live %", 1), ("crunch_live", "crunch live %", 1),
             ("nfl_drive_live", "NFL drives live %", 1), ("commercial_pct", "commercials on screen %", -1),
             ("behind_live_pct", "behind live %", 0), ("switches_per_hour", "switches/hour", 0)],
}
MISSING = "no data/"   # ci_scorecard.py's error when the pinned data isn't there (not the PR's fault)
WORSE_BY = 0.5   # a directional metric this much worse (points) gets a warning mark
FRACTION = re.compile(r"(-?\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)")


def scalar(v):
    """A number from a scorecard value: numbers as is, "a/b (p%)" strings as the percentage a/b."""
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = FRACTION.search(v)
        if m:
            return round(100 * float(m.group(1)) / max(1.0, float(m.group(2))), 1)
        try:
            return float(v)
        except ValueError:
            return None
    return None


def pick(metrics: dict | None, path: str):
    cur = metrics
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return scalar(cur)


def fmt(x) -> str:
    if x is None:
        return "–"
    return str(int(x)) if float(x).is_integer() else f"{x:.1f}"


def delta_cell(h, b, direction) -> str:
    if h is None or b is None:
        return ""
    d = h - b
    if abs(d) < 1e-9:
        return "="
    arrow = "▲" if d > 0 else "▼"
    worse = direction != 0 and d * direction < 0 and abs(d) >= WORSE_BY
    better = direction != 0 and d * direction > 0
    return f"{arrow} {fmt(abs(d))}" + (" ⚠️" if worse else " ✅" if better else "")


def card_kind(name: str) -> str:
    """The headline set for a card: demo_<day> -> demo, <league>_resync -> <league>."""
    return "demo" if name.startswith("demo") else name.removesuffix("_resync")


def scorecard_section(head: dict, base: dict | None) -> tuple[list[str], list[str]]:
    """Markdown lines, and the hard failures (added mid-play cuts)."""
    lines, hard = [], []
    sl = head.get("slice", {})
    lines.append(("Full scorecard" if sl.get("full") else "Smoke slice") + f": NFL {sl.get('nfl_n')} slates, NBA/NHL last {sl.get('nights')} nights, "
                 f"demo {sl.get('demo_day') or 'off'}. Only comparable with the same slice on the same data.")
    for name, h in head.get("cards", {}).items():
        b = (base or {}).get("cards", {}).get(name) if base else None
        took = f"{h.get('seconds', 0)} s"
        lines.append("")
        if "error" in h:
            lines.append(f"**{name}**: ⚠️ not graded ({h['error']})")
            if not h["error"].startswith(MISSING):
                # The data was there and the scorecard still crashed or timed out on the PR.
                hard.append(f"{name}: scorecard failed on the PR ({h['error']})")
            continue
        lines.append(f"**{name}** ({took})")
        lines.append("")
        if b is not None and "metrics" in b:
            lines += ["| metric | base | PR | change |", "|---|---:|---:|---|"]
        else:
            note = f" (base not graded: {b['error']})" if b and "error" in b else ""
            lines += [f"| metric | PR{note} |", "|---|---:|"]
        for key, label, direction in HEADLINE[card_kind(name)]:
            hv = pick(h["metrics"], key)
            bv = pick(b["metrics"], key) if b and "metrics" in b else None
            if hv is None and bv is None:
                continue
            if b is not None and "metrics" in b:
                lines.append(f"| {label} | {fmt(bv)} | {fmt(hv)} | {delta_cell(hv, bv, direction)} |")
            else:
                lines.append(f"| {label} | {fmt(hv)} |")
        cuts_h = pick(h["metrics"], "mid_play_cuts")
        cuts_b = pick(b["metrics"], "mid_play_cuts") if b and "metrics" in b else None
        if cuts_h is not None and cuts_h > (cuts_b if cuts_b is not None else 0):
            hard.append(f"{name}: {fmt(cuts_h)} mid-play cuts (base {fmt(cuts_b)})")
    return lines, hard


def junit_summary(path: Path) -> tuple[str, bool]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as e:
        return f"⚠️ no test report ({e.__class__.__name__})", False
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    n = sum(int(s.get("tests", 0)) for s in suites)
    fail = sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites)
    skip = sum(int(s.get("skipped", 0)) for s in suites)
    secs = sum(float(s.get("time", 0)) for s in suites)
    mac = sum(1 for c in root.iter("testcase") for s in c.findall("skipped")
              if (s.get("message") or "").startswith("mac_only"))
    ok = fail == 0 and n > 0
    return (f"{'✅' if ok else '❌'} {n - fail - skip} passed, {fail} failed, {skip} skipped "
            f"({mac} of them mac_only) in {secs:.0f} s"), ok


PERF = (("tick_ms_p50", "tick p50", " ms"), ("tick_ms_p99", "p99", " ms"), ("peak_rss_mb", "peak RSS", " MB"),
        ("cpu_s", "CPU", " s"), ("startup_s", "startup", " s"))


def perf_line(head: dict, base: dict | None) -> str:
    """One RAM/CPU line (scripts/perf_probe.py: the decide() tick on a synthetic slate), PR vs base."""
    parts = []
    for k, label, unit in PERF:
        if k not in head:
            continue
        txt = f"{label} {head[k]}{unit}"
        if base and k in base:
            txt += f" (base {base[k]})"
        parts.append(txt)
    return f"**RAM/CPU ({head.get('games', '?')} live games, {head.get('ticks', '?')} decide ticks):** " + ", ".join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--head", type=Path)
    ap.add_argument("--base", type=Path)
    ap.add_argument("--junit", type=Path)
    ap.add_argument("--lint", type=Path)
    ap.add_argument("--lint-status", type=int)
    ap.add_argument("--note", action="append", default=[], help="extra line for the comment")
    ap.add_argument("--section", type=Path, action="append", default=[],
                    help="a markdown file appended as its own section (the North Star delta table)")
    ap.add_argument("--perf", type=Path, help="scripts/perf_probe.py JSON for the PR head")
    ap.add_argument("--perf-base", type=Path, help="...and for the base")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)

    out = [MARKER, "### RedZone CI", ""]
    if a.junit:
        out.append(f"**Tests (Linux):** {junit_summary(a.junit)[0]}")
    if a.lint_status is not None:
        body = a.lint.read_text().strip() if a.lint and a.lint.exists() else ""
        if a.lint_status == 0:
            out.append("**Lint (ruff, real errors only):** ✅ clean")
        else:
            out.append("**Lint (ruff, real errors only):** ❌ errors")
            out += ["", "```", *body.splitlines()[:40], "```"]
    if a.perf and a.perf.exists():
        out.append(perf_line(json.loads(a.perf.read_text()),
                             json.loads(a.perf_base.read_text()) if a.perf_base and a.perf_base.exists() else None))
    out += [f"> {n}" for n in a.note]
    hard: list[str] = []
    if a.head and a.head.exists():
        head = json.loads(a.head.read_text())
        base = json.loads(a.base.read_text()) if a.base and a.base.exists() else None
        lines, hard = scorecard_section(head, base)
        out += ["", "#### Scorecards (PR vs base)" if base else "#### Scorecards", ""] + lines
        if hard:
            out += ["", "❌ **Hard rule broken** (no added mid-play cuts, no crashing scorecard): " + "; ".join(hard)]
    elif a.head:
        out += ["", "#### Scorecard smoke", "", "⚠️ not run (no results file)"]
    for sec in a.section:
        if sec.exists():
            out += ["", sec.read_text().rstrip()]
    text = "\n".join(out) + "\n"
    if a.out:
        a.out.write_text(text)
    else:
        sys.stdout.write(text)
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
