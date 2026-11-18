"""The corpus stats report: events per game by league and era, tokens per game, vocab coverage, shard sizes.

Why: B-02 done-when 4 (WORKPLAN): "events per game by league and era, vocab coverage (what fraction of events map to unknown tokens),
tokens per game, shard sizes". It is computed from the shard headers and the name table alone (no token body is read), so it is cheap
on a 20 GB corpus, and it is deterministic: the verifier recomputes it and compares it with stats.json.

Two scopes: `trainable` (shards in a train, valid or holdout role) and `held` (tuning-window shards, never trained on). Unknown, as the
coverage section counts it, means an event with a wall-clock gap that is unknown on any event but a game's first, a stoppage the rules
table could not classify, or no kind at all (redzone/lab/corpus/tokenise.py). Shared kind slots and clipped margins are reported beside it.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any, Mapping

from redzone.lab.corpus import shardio
from redzone.lab.corpus import tokenise as tk
from redzone.lab.corpus.manifest import CORPUS_NAMES_FILE

CORPUS_STATS_FORMAT = "redzone-corpus-stats/1"
_LEN = shardio.CORPUS_INDEX_FIELDS.index("length")


def _frac(a: int, b: int) -> float:
    return round(a / b, 6) if b else 0.0


class _Acc:
    def __init__(self) -> None:
        self.games = self.events = self.tokens = self.shards = self.bytes = self.wall_games = 0
        self.per_game: list[int] = []
        self.cov: dict[str, int] = {}
        self.providers: set[str] = set()

    def add(self, header: Mapping, size: int) -> None:
        lengths = [r[_LEN] for r in header["index"]]
        self.games += len(lengths)
        self.events += int(header["events"])
        self.tokens += int(header["tokens"])
        self.shards += 1
        self.bytes += size
        self.wall_games += len(lengths) if header["tier"] == "wall" else 0
        self.per_game += [(n - 4) // tk.NT.TOKENS_PER_EVENT for n in lengths]
        tk.add_counters(self.cov, header.get("coverage") or {})
        self.providers |= set(header.get("providers") or [])

    def row(self) -> dict[str, Any]:
        e = self.events
        return {
            "games": self.games, "events": e, "tokens": self.tokens, "shards": self.shards, "bytes": self.bytes, "wall_games": self.wall_games,
            "events_per_game_mean": round(e / self.games, 3) if self.games else 0.0,
            "events_per_game_median": float(statistics.median(self.per_game)) if self.per_game else 0.0,
            "tokens_per_game_mean": round(self.tokens / self.games, 3) if self.games else 0.0,
            "unknown_event_frac": _frac(self.cov.get("unknown_events", 0), e),
            "unk_dt_after_first_frac": _frac(self.cov.get("unk_dt_after_first", 0), e),
            "stop_unknown_frac": _frac(self.cov.get("stop_unknown", 0), e),
            "kind_blank_frac": _frac(self.cov.get("kind_blank", 0), e),
            "clock_missing_frac": _frac(self.cov.get("clock_missing", 0), e),
            "margin_clipped_frac": _frac(self.cov.get("margin_clipped", 0), e),
            "providers": sorted(self.providers),
        }


def compute(doc: Mapping, root: str | Path) -> dict[str, Any]:
    """The stats document for a corpus manifest and the folder that holds its shards."""
    root = Path(root)
    size = {f["path"]: f["bytes"] for f in doc["files"]}
    scopes: dict[str, dict[tuple, _Acc]] = {"trainable": {}, "held": {}}
    roles: dict[str, _Acc] = {}
    tiers: dict[str, _Acc] = {}
    sizes: list[int] = []
    for row in doc["corpus"]["shards"]:
        header, _ = shardio.read_header(root.joinpath(*row["path"].split("/")))
        scope = scopes["trainable" if row["train_ok"] else "held"]
        for key in ((row["league"], row["era"]), (row["league"], "all"), ("all", "all")):
            scope.setdefault(key, _Acc()).add(header, size[row["path"]])
        roles.setdefault(row["role"], _Acc()).add(header, size[row["path"]])
        tiers.setdefault(row["tier"], _Acc()).add(header, size[row["path"]])
        sizes.append(size[row["path"]])
    out: dict[str, Any] = {"format": CORPUS_STATS_FORMAT, "tag": doc["tag"], "vocab_sha256": tk.vocab_fingerprint(), "scopes": {}}
    for name, table in scopes.items():
        by_le: dict[str, dict[str, Any]] = {}
        for (lg, era), acc in sorted(table.items()):
            if lg != "all":
                by_le.setdefault(lg, {})[era] = acc.row()
        out["scopes"][name] = {"by_league_era": by_le, "all": table[("all", "all")].row() if ("all", "all") in table else None}
    out["roles"] = {k: {x: v for x, v in a.row().items() if x in ("games", "events", "tokens", "shards", "bytes")} for k, a in sorted(roles.items())}
    out["tiers"] = {k: {x: v for x, v in a.row().items() if x in ("games", "events", "tokens", "shards", "bytes")} for k, a in sorted(tiers.items())}
    out["shard_sizes"] = ({"count": len(sizes), "bytes_total": sum(sizes), "bytes_min": min(sizes), "bytes_median": float(statistics.median(sizes)),
                           "bytes_max": max(sizes)} if sizes else {"count": 0})
    out["vocab_coverage"] = _coverage(out["scopes"]["trainable"]["all"], root)
    return out


def _coverage(trainable: Mapping | None, root: Path) -> dict[str, Any]:
    """Overall coverage for the trainable events, plus how many events sit in a shared kind slot (from names.json)."""
    out: dict[str, Any] = {"events": trainable["events"] if trainable else 0}
    if trainable:
        for k in ("unknown_event_frac", "unk_dt_after_first_frac", "stop_unknown_frac", "kind_blank_frac", "clock_missing_frac",
                  "margin_clipped_frac"):
            out[k] = trainable[k]
    try:
        names = tk.NameTables.from_json(json.loads((root / CORPUS_NAMES_FILE).read_text()))
        total = sum(sum(n.values()) for slots in names.kinds.values() for n in slots.values())
        out["kind_names"] = len({k for slots in names.kinds.values() for n in slots.values() for k in n})
        out["kind_slots_used"] = sum(len(slots) for slots in names.kinds.values())
        out["shared_kind_slot_events"] = names.shared_kind_events()
        out["shared_kind_slot_frac"] = _frac(names.shared_kind_events(), total)
    except (OSError, ValueError):
        out["names"] = "missing"
    return out


def format_table(stats: Mapping) -> str:
    """The stats as plain text: one line per league and era, then totals, coverage and shard sizes."""
    lines = []
    for scope in ("trainable", "held"):
        by = stats["scopes"][scope]["by_league_era"]
        if not by:
            continue
        lines.append(f"[{scope}]")
        lines.append(f"{'league':<6} {'era':<6} {'games':>7} {'events':>10} {'ev/game':>8} {'median':>7} {'tok/game':>9} {'unk%':>6} {'wall':>6} {'shards':>6} {'MB':>7}")
        for lg, eras in by.items():
            for era, r in eras.items():
                if era == "all":
                    continue
                lines.append(f"{lg:<6} {era:<6} {r['games']:>7,} {r['events']:>10,} {r['events_per_game_mean']:>8.1f} {r['events_per_game_median']:>7.0f} "
                             f"{r['tokens_per_game_mean']:>9.0f} {100 * r['unknown_event_frac']:>6.2f} {r['wall_games']:>6,} {r['shards']:>6} {r['bytes'] / 1e6:>7.2f}")
        a = stats["scopes"][scope]["all"]
        lines.append(f"{'all':<6} {'':<6} {a['games']:>7,} {a['events']:>10,} {a['events_per_game_mean']:>8.1f} {a['events_per_game_median']:>7.0f} "
                     f"{a['tokens_per_game_mean']:>9.0f} {100 * a['unknown_event_frac']:>6.2f} {a['wall_games']:>6,} {a['shards']:>6} {a['bytes'] / 1e6:>7.2f}")
    c = stats["vocab_coverage"]
    lines.append("vocab coverage (trainable events): " + ", ".join(f"{k}={v}" for k, v in c.items()))
    s = stats["shard_sizes"]
    if s.get("count"):
        lines.append(f"shards: {s['count']} files, {s['bytes_total'] / 1e6:.2f} MB total, min {s['bytes_min'] / 1e3:.1f} KB, median {s['bytes_median'] / 1e3:.1f} KB, "
                     f"max {s['bytes_max'] / 1e3:.1f} KB")
    lines.append("roles: " + ", ".join(f"{k}={v['games']} games" for k, v in stats["roles"].items()))
    return "\n".join(lines)
