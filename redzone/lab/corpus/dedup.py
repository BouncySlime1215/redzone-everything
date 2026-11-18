"""Keep one copy of a game that two providers (or two folders) both hold: ESPN direct against hoopR or wehoop, or a packed shard against
the recorded archive.

Why: B-02 corpus rule 3 (tracks/B-foundation-model.md): "the same game from two providers is deduplicated" or the model would see it
twice and a held-out game could sit in training under another provider's name (hazard analogous to benchmark leakage, PRE Q8).

Two stages, both on `Light` records (a game without its plays):
  1. by id         same league and same provider game id. hoopR and wehoop use ESPN's event id, so their copies of an ESPN game match here.
                   Copies whose nights disagree by more than a day are not merged (an id clash between unrelated games); they are counted.
  2. by signature  same league and same score trajectory (the digest of every home/away score change), at least four changes, nights
                   within a day of each other. This catches providers with a different id space. A cluster with two copies from the same
                   provider is ambiguous (two real games with one trajectory) and is left alone, counted as such.
Which copy survives: a game in the wall-clock tier first, then more plays, then ESPN before hoopR before wehoop, then the folder it was
read from (packed shard, per-game files, archive, dataset), then the id and the file. The choice depends on the games only, never on read order.
A game read twice from the same provider and folder (the same id in two shard files) is two copies like any other.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from redzone.lab.corpus.records import CORPUS_WALL_MIN, Light

CORPUS_PROVIDER_RANK = {"espn": 0, "hoopr": 1, "wehoop": 2}
CORPUS_ORIGIN_RANK = {"history-shard": 0, "history-games": 1, "archive": 2, "dataset": 3}
CORPUS_MIN_CHANGES = 4
CORPUS_NIGHT_SLACK_DAYS = 1


def quality_key(g: Light) -> tuple:
    return (0 if g.wall_frac >= CORPUS_WALL_MIN else 1, -g.n_plays, CORPUS_PROVIDER_RANK.get(g.provider, 9),
            CORPUS_ORIGIN_RANK.get(g.origin, 9), g.gid, g.ref)


def _days_apart(a: str | None, b: str | None) -> int | None:
    if not a or not b:
        return None
    return abs((dt.datetime.strptime(a, "%Y%m%d") - dt.datetime.strptime(b, "%Y%m%d")).days)


def _compatible(a: Light, b: Light) -> bool:
    d = _days_apart(a.night, b.night)
    return d is not None and d <= CORPUS_NIGHT_SLACK_DAYS


def _label(g: Light) -> str:
    return f"{g.provider}:{g.origin}:{g.gid}"


def _pair(by: str, kept: Light, dropped: Light) -> dict[str, str]:
    return {"league": kept.league, "by": by, "kept": _label(kept), "dropped": _label(dropped), "kept_ref": kept.ref, "dropped_ref": dropped.ref}


@dataclass
class DedupResult:
    keep: dict[tuple, Light]
    pairs: list[dict] = field(default_factory=list)       # one row per removed copy: league, by, kept, dropped
    ambiguous: int = 0                                     # signature clusters left alone (two copies from one provider)
    id_conflicts: int = 0                                  # same id, nights more than a day apart: both kept

    def summary(self, games_in: int) -> dict[str, int]:
        return {"games_in": games_in, "games_kept": len(self.keep), "removed": len(self.pairs),
                "by_id": sum(1 for p in self.pairs if p["by"] == "id"), "by_signature": sum(1 for p in self.pairs if p["by"] == "signature"),
                "ambiguous_clusters": self.ambiguous, "id_conflicts": self.id_conflicts}


def _clusters(group: list[Light]) -> list[list[Light]]:
    ordered = sorted(group, key=lambda g: (g.night or "", g.key))
    out: list[list[Light]] = []
    for g in ordered:
        if out and _compatible(out[-1][-1], g):
            out[-1].append(g)
        else:
            out.append([g])
    return out


def resolve(lights: Iterable[Light]) -> DedupResult:
    games = sorted(lights, key=lambda g: g.key)
    res = DedupResult(keep={})
    by_id: dict[tuple[str, str], list[Light]] = defaultdict(list)
    for g in games:
        by_id[(g.league, g.gid)].append(g)
    survivors: list[Light] = []
    for (_lg, _gid), group in sorted(by_id.items()):
        group.sort(key=quality_key)
        best = group[0]
        survivors.append(best)
        for other in group[1:]:
            if _compatible(best, other) or (best.night is None and other.night is None):
                res.pairs.append(_pair("id", best, other))
            else:
                survivors.append(other)
                res.id_conflicts += 1
    by_sig: dict[tuple[str, str], list[Light]] = defaultdict(list)
    for g in survivors:
        if g.n_changes >= CORPUS_MIN_CHANGES:
            by_sig[(g.league, g.signature)].append(g)
    dropped: set[tuple] = set()
    for _k, group in sorted(by_sig.items()):
        if len(group) < 2:
            continue
        for cluster in _clusters(group):
            if len(cluster) < 2:
                continue
            providers = [c.provider for c in cluster]
            if len(set(providers)) != len(providers):
                res.ambiguous += 1
                continue
            cluster.sort(key=quality_key)
            for other in cluster[1:]:
                dropped.add(other.key)
                res.pairs.append(_pair("signature", cluster[0], other))
    res.keep = {g.key: g for g in survivors if g.key not in dropped}
    res.pairs.sort(key=lambda p: (p["league"], p["kept"], p["dropped"], p["kept_ref"], p["dropped_ref"]))
    return res
