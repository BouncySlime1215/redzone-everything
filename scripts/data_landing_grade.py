"""Data landing grader (PROPOSAL, test writers, Nov 5 2026; EXP-705). Not frozen until the integrator adds it.

Grades a dataset a Data Division crew landed in the factory, BEFORE any model trains on it. It grades the data,
never a model, so it can run on every shard as it lands (Kaggle CPU or CI, $0).

    python scripts/data_landing_grade.py shard.jsonl [more.jsonl ...] [--label-col y] [--json]

The locked nights come from redzone/lab/splits.py over data/archive/<league>/ (the same split leak_check.py and the
scorecards use, up to the eval pin's window end). They are never typed in. No archive for a league = that league's
locked check is PENDING.

Exit 1 on any FAIL. PENDING (cannot be checked: a field is missing on every row) never fails.

Row schema (one JSON object per line; crews write these field names, extra fields are kept):
    source      catalog id of the source (docs/datasources/scout.json id)          required
    league      nfl / cfb / nba / cbb / nhl / mlb, or the level (milb, gleague, ahl ...)
    game_id     the source's game id (no "_")                                       required
    night       YYYY-MM-DD local night of the game                                   required
    split       train / valid / test (time split by whole night, per league)
    kind        events / context-new / derived / synthetic
    license     the source's license line                                            required
    plan        "A" (catalog green) or "B" (personal use, Nick 11:20 PM Nov 4)
    asof_ts     when the value was known (ISO 8601, UTC)          context rows only
    use_ts      the decision instant the value feeds (ISO 8601)   context rows only
    game_key    canonical game identity across sources, "<league>:<YYYYMMDD>:<away>@<home>" (D8)
    tokens      vocab-v2 token count of the row
    unmapped    vocab-v2 tokens with no id (counted toward the < 1% bar)
    features    {name: number} numeric features (for the single-feature AUC scan)

Checks (ids are proposals; the integrator renames them on freeze):
    D1 license   every row has a license line; every plan-B row carries "personal-use, not for commercial release"
    D2 as-of     no context value known after the instant it feeds (asof_ts <= use_ts); 0 allowed
    D3 dedupe    duplicate share over (source, league, game_id, row key) <= 1% (grading rule 14)
    D4 splits    no game in two splits; split order follows night order per league; no locked night present
                 (locked = splits.split_nights(...).test per league, read from the archive, never hardcoded)
    D5 synth     synthetic tokens <= 3x real tokens (EXP-097 cap)
    D6 vocab     unmapped tokens < 1% per league (EXP-003 bar, extended to vocab v2 and every level)
    D7 leak scan any single numeric feature with AUC > .98 against --label-col is a red flag (grading rule 5)
    D8 identity  (EXP-721) the same real game from two sources (game_key = canonical league:date:away@home) never
                 sits in two splits, and its tokens count as new information once: the cross-source duplicate
                 token share is reported, and FAILs above 1% when the duplicates are marked kind=events
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
ARCHIVE = ROOT / "data" / "archive"
PERSONAL_USE = "personal-use, not for commercial release"
DUP_MAX = 0.01
SYNTH_MAX = 3.0
UNMAPPED_MAX = 0.01
AUC_FLAG = 0.98
REQUIRED = ("source", "game_id", "night", "license")


def load(paths: list[Path]) -> list[dict]:
    rows = []
    for p in paths:
        with p.open() as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _night(s: str) -> str:
    return str(s).replace("-", "")


def locked_nights(archive: Path = ARCHIVE, leagues=None) -> dict[str, set[str]]:
    """league -> locked (test) nights as YYYYMMDD, from redzone/lab/splits.py over the archive (as leak_check.py)."""
    from redzone.lab import splits
    try:
        from redzone.lab import evaldata
        end = evaldata.window_end
    except Exception:  # noqa: BLE001  (no eval pin: whole archive)
        end = lambda lg, d: d  # noqa: E731
    out = {}
    for lg in leagues or sorted(p.name for p in Path(archive).glob("*") if p.is_dir()):
        d = Path(archive) / lg
        if d.is_dir():
            out[lg] = set(splits.split_nights(splits.archive_nights(d, end(lg, None))).test)
    return out


def d0_schema(rows: list[dict]) -> list[tuple[str, str]]:
    if not rows:
        return [("PENDING", "D0 no rows")]
    bad = Counter(f for r in rows for f in REQUIRED if not r.get(f))
    if bad:
        return [("FAIL", f"D0 rows missing required fields: {dict(bad)}")]
    return [("PASS", f"D0 {len(rows)} rows carry {', '.join(REQUIRED)}")]


def d1_license(rows: list[dict]) -> list[tuple[str, str]]:
    no_line = sum(1 for r in rows if not str(r.get("license", "")).strip())
    b_untagged = Counter(r.get("source") for r in rows
                         if str(r.get("plan", "")).upper() == "B" and PERSONAL_USE not in str(r.get("license", "")))
    out = []
    if no_line:
        out.append(("FAIL", f"D1 {no_line} rows have no license line"))
    if b_untagged:
        out.append(("FAIL", f"D1 plan-B rows without '{PERSONAL_USE}': {dict(b_untagged)}"))
    return out or [("PASS", "D1 every row has a license line; plan-B rows are tagged personal-use")]


def d2_asof(rows: list[dict]) -> list[tuple[str, str]]:
    ctx = [r for r in rows if r.get("asof_ts") and r.get("use_ts")]
    if not ctx:
        return [("PENDING", "D2 no rows with asof_ts + use_ts")]
    late = [r for r in ctx if _ts(r["asof_ts"]) > _ts(r["use_ts"])]
    if late:
        by = Counter(r.get("source") for r in late)
        return [("FAIL", f"D2 {len(late)}/{len(ctx)} context values known after the instant they feed: {dict(by)}")]
    return [("PASS", f"D2 {len(ctx)} context values all known at or before use")]


def _key(r: dict) -> str:
    skip = {"split", "license", "plan", "tokens", "unmapped"}
    return json.dumps({k: v for k, v in r.items() if k not in skip}, sort_keys=True, default=str)


def d3_dedupe(rows: list[dict]) -> list[tuple[str, str]]:
    if not rows:
        return [("PENDING", "D3 no rows")]
    c = Counter(_key(r) for r in rows)
    dup = sum(n - 1 for n in c.values() if n > 1)
    share = dup / len(rows)
    tag = "FAIL" if share > DUP_MAX else "PASS"
    return [(tag, f"D3 duplicate share {share:.2%} (bar <= {DUP_MAX:.0%})")]


def d4_splits(rows: list[dict], locked: dict[str, set[str]]) -> list[tuple[str, str]]:
    out = []
    leagues = sorted({str(r.get("league")) for r in rows})
    hit = sorted({(r.get("league"), r["night"]) for r in rows
                  if _night(r.get("night", "")) in locked.get(r.get("league"), ())})
    if hit:
        out.append(("FAIL", f"D4 {len(hit)} locked league-nights present (first {hit[:3]}); locked is never landed"))
    no_arch = [lg for lg in leagues if lg not in locked]
    if no_arch:
        out.append(("PENDING", f"D4 no archive split for {', '.join(no_arch)}: locked-night check not run"))
    with_split = [r for r in rows if r.get("split")]
    if not with_split:
        return out or [("PENDING", "D4 no split column")]
    games = defaultdict(set)
    for r in with_split:
        games[(r.get("league"), r["game_id"])].add(r["split"])
    multi = [g for g, s in games.items() if len(s) > 1]
    if multi:
        out.append(("FAIL", f"D4 {len(multi)} games in two splits (first {multi[:3]})"))
    order = {"train": 0, "valid": 1, "test": 2}
    span = defaultdict(lambda: defaultdict(lambda: [None, None]))
    for r in with_split:
        sp = span[r.get("league")][r["split"]]
        sp[0] = r["night"] if sp[0] is None or r["night"] < sp[0] else sp[0]
        sp[1] = r["night"] if sp[1] is None or r["night"] > sp[1] else sp[1]
    for lg, sps in span.items():
        named = sorted((s for s in sps if s in order), key=order.get)
        for a, b in zip(named, named[1:]):
            if sps[a][1] >= sps[b][0]:
                out.append(("FAIL", f"D4 {lg}: {a} ends {sps[a][1]} on/after {b} starts {sps[b][0]} (time split)"))
    if not [o for o in out if o[0] == "FAIL"]:
        out.append(("PASS", f"D4 {len(games)} games, splits disjoint and time-ordered per league"))
    return out


def d5_synth(rows: list[dict]) -> list[tuple[str, str]]:
    tok = Counter()
    for r in rows:
        tok["synthetic" if r.get("kind") == "synthetic" else "real"] += int(r.get("tokens") or 0)
    if not tok["synthetic"]:
        return [("PASS", "D5 no synthetic tokens")]
    if not tok["real"]:
        return [("FAIL", "D5 synthetic tokens with no real tokens in the same landing")]
    ratio = tok["synthetic"] / tok["real"]
    return [("FAIL" if ratio > SYNTH_MAX else "PASS", f"D5 synthetic:real = {ratio:.2f}x (cap {SYNTH_MAX:.0f}x)")]


def d6_vocab(rows: list[dict]) -> list[tuple[str, str]]:
    tok, unm = Counter(), Counter()
    for r in rows:
        if "unmapped" in r and r.get("tokens"):
            tok[r.get("league")] += int(r["tokens"])
            unm[r.get("league")] += int(r["unmapped"])
    if not tok:
        return [("PENDING", "D6 no tokens/unmapped counts")]
    out = []
    for lg in sorted(tok, key=str):
        share = unm[lg] / tok[lg]
        out.append(("FAIL" if share >= UNMAPPED_MAX else "PASS", f"D6 {lg}: unmapped {share:.2%} (bar < 1%)"))
    return out


def auc(scores: list[float], labels: list[int]) -> float | None:
    """Mann-Whitney AUC with tie handling; None when one class is missing."""
    pos = sum(labels)
    neg = len(labels) - pos
    if not pos or not neg:
        return None
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    rpos = sum(r for r, y in zip(ranks, labels) if y)
    return (rpos - pos * (pos + 1) / 2) / (pos * neg)


def d7_leakscan(rows: list[dict], label_col: str | None) -> list[tuple[str, str]]:
    if not label_col:
        return [("PENDING", "D7 no --label-col")]
    lab = [r for r in rows if r.get(label_col) in (0, 1, True, False) and isinstance(r.get("features"), dict)]
    if not lab:
        return [("PENDING", f"D7 no rows with features and a 0/1 {label_col}")]
    names = sorted({k for r in lab for k, v in r["features"].items() if isinstance(v, (int, float))})
    flags = []
    for n in names:
        sub = [r for r in lab if isinstance(r["features"].get(n), (int, float))]
        a = auc([float(r["features"][n]) for r in sub], [int(bool(r[label_col])) for r in sub])
        if a is not None and max(a, 1 - a) > AUC_FLAG:
            flags.append(f"{n}={max(a, 1 - a):.3f}")
    if flags:
        return [("FAIL", f"D7 single-feature AUC > {AUC_FLAG}: {', '.join(flags)} (red flag: likely leak)")]
    return [("PASS", f"D7 {len(names)} features, none above AUC {AUC_FLAG}")]


def d8_identity(rows: list[dict]) -> list[tuple[str, str]]:
    keyed = [r for r in rows if r.get("game_key")]
    if not keyed:
        return [("PENDING", "D8 no game_key column")]
    sources = defaultdict(set)
    splits_of = defaultdict(set)
    for r in keyed:
        sources[r["game_key"]].add(r.get("source"))
        if r.get("split"):
            splits_of[r["game_key"]].add(r["split"])
    out = []
    multi_split = sorted(k for k, s in splits_of.items() if len(s) > 1)
    if multi_split:
        out.append(("FAIL", f"D8 {len(multi_split)} games in two splits across sources (first {multi_split[:3]})"))
    multi = {k for k, s in sources.items() if len(s) > 1}
    tok = Counter()
    first_src = {k: min(map(str, sources[k])) for k in multi}
    for r in keyed:
        n = int(r.get("tokens") or 0)
        tok["all"] += n
        if r["game_key"] in multi and str(r.get("source")) != first_src[r["game_key"]] and r.get("kind") == "events":
            tok["dup_events"] += n
    share = tok["dup_events"] / tok["all"] if tok["all"] else 0.0
    if share > DUP_MAX:
        out.append(("FAIL", f"D8 {share:.2%} of tokens are a second source's copy of a game marked kind=events "
                            f"({len(multi)} games); mark them derived or drop them (bar <= {DUP_MAX:.0%})"))
    if not out:
        out.append(("PASS", f"D8 {len(sources)} games, {len(multi)} from more than one source, "
                            f"cross-source event duplicates {share:.2%}"))
    return out


def grade(rows: list[dict], locked: dict[str, set[str]] | None = None,
          label_col: str | None = None) -> list[tuple[str, str]]:
    """locked: league -> YYYYMMDD nights; None reads them from the archive via redzone/lab/splits.py."""
    if locked is None:
        locked = locked_nights(ARCHIVE, sorted({str(r.get("league")) for r in rows}))
    res = d0_schema(rows)
    if res[0][0] == "FAIL":
        return res
    return (res + d1_license(rows) + d2_asof(rows) + d3_dedupe(rows) + d4_splits(rows, locked)
            + d5_synth(rows) + d6_vocab(rows) + d7_leakscan(rows, label_col) + d8_identity(rows))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--archive", type=Path, default=ARCHIVE, help="data/archive root (locked nights via splits.py)")
    ap.add_argument("--label-col")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rows = load(a.paths)
    res = grade(rows, locked_nights(a.archive, sorted({str(r.get("league")) for r in rows})), a.label_col)
    if a.json:
        print(json.dumps([{"status": s, "check": m} for s, m in res], indent=1))
    else:
        for s, m in res:
            print(f"{s:8} {m}")
    return 1 if any(s == "FAIL" for s, _ in res) else 0


if __name__ == "__main__":
    sys.exit(main())
