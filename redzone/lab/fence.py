"""The leak fence in code: fenced nights no model may train on, and a loader guard that refuses a model
whose training cutoff reaches into them or into the nights it is about to be scored on.

Why: WORKPLAN v2 task F-09 and TRAINING-PLAN rule 8 ("tuning and locked nights fenced in code; training
cutoff before the held-out window; train/eval disjointness by hash"): before this the fence was prose, and
scripts/leak_check.py only reported unstamped models as PENDING.

    from redzone.lab import fence
    fence.assert_train_nights(nights)                    # FenceError if any night is fenced
    meta = fence.load_guarded(path, eval_nights)         # FenceError "REFUSED ..." if the model may not be scored there
    fence.disjoint_by_hash(train_files, eval_files)      # [] when no game file appears on both sides (content sha256)

Fences (inclusive, YYYYMMDD, US Eastern nights like redzone/lab/splits.py):
    locked  20251212 .. 20260105   the held-out evaluation nights; touched by the release gate only
    tuning  20261001 .. 20261031   knob-tuning nights; tuned on, never trained on

A model record names its cutoff as `training_cutoff` (ISO date, the field agreed with the F-02 registry);
the older `trained_through` (YYYYMMDD, scripts/leak_check.py) is read too. A model whose cutoff is after a
fence must list `train_nights` so the guard can prove the fenced nights were left out; when it also carries
`train_nights_sha256` that digest must match the list.

Nothing existing calls this module yet, so every current path behaves exactly as before; the F-04
evaluation harness and the model loaders call `guard_model` / `load_guarded` when they land.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Mapping


class FenceError(ValueError):
    """A model or a training set crosses the leak fence."""


@dataclass(frozen=True)
class Window:
    name: str
    start: str          # YYYYMMDD, inclusive
    end: str            # YYYYMMDD, inclusive
    why: str

    def __contains__(self, night: object) -> bool:
        return self.start <= night_key(night) <= self.end   # type: ignore[arg-type]


LOCKED = Window("locked", "20251212", "20260105", "held-out evaluation nights, scored by the release gate only")
TUNING = Window("tuning", "20261001", "20261031", "knob-tuning nights, tuned on but never trained on")
FENCES: tuple[Window, ...] = (LOCKED, TUNING)

_ISO = re.compile(r"^(\d{4})-?(\d{2})-?(\d{2})")


def night_key(x: object) -> str:
    """'YYYYMMDD' from 'YYYYMMDD', 'YYYY-MM-DD', an ISO datetime, a date or a datetime. ValueError otherwise."""
    if isinstance(x, datetime):
        x = x.date()
    if isinstance(x, date):
        return x.strftime("%Y%m%d")
    m = _ISO.match(str(x).strip())
    if not m:
        raise ValueError(f"not a night: {x!r} (want YYYYMMDD or YYYY-MM-DD)")
    y, mo, d = m.groups()
    date(int(y), int(mo), int(d))       # a real calendar day
    return f"{y}{mo}{d}"


def fenced(night: object, fences: Iterable[Window] = FENCES) -> Window | None:
    """The fence this night sits in, or None."""
    k = night_key(night)
    return next((w for w in fences if w.start <= k <= w.end), None)


def train_problems(nights: Iterable[object], fences: Iterable[Window] = FENCES) -> list[str]:
    """Every fenced night in a training set; [] when clean."""
    fences = tuple(fences)
    hits: dict[str, list[str]] = {}
    for n in nights:
        w = fenced(n, fences)
        if w is not None:
            hits.setdefault(w.name, []).append(night_key(n))
    return [f"{len(v)} {k} night(s) in the training set ({', '.join(sorted(v)[:3])}{'...' if len(v) > 3 else ''})"
            for k, v in sorted(hits.items())]


def assert_train_nights(nights: Iterable[object], fences: Iterable[Window] = FENCES) -> None:
    bad = train_problems(nights, fences)
    if bad:
        raise FenceError("REFUSED training set: " + "; ".join(bad))


def trainable(nights: Iterable[object], fences: Iterable[Window] = FENCES) -> list[str]:
    """The nights a model may train on: every fenced night removed (sorted, YYYYMMDD)."""
    fences = tuple(fences)
    return sorted({night_key(n) for n in nights if fenced(n, fences) is None})


def nights_sha256(nights: Iterable[object]) -> str:
    """One digest for a set of nights (order and format do not matter): the `train_nights_sha256` stamp."""
    return hashlib.sha256("\n".join(sorted({night_key(n) for n in nights})).encode()).hexdigest()


def cutoff_of(meta: Mapping) -> str | None:
    """The model's training cutoff as YYYYMMDD: `training_cutoff` (ISO), else legacy `trained_through`."""
    for k in ("training_cutoff", "trained_through"):
        v = meta.get(k)
        if v not in (None, ""):
            return night_key(v)
    return None


def model_problems(meta: Mapping, eval_nights: Iterable[object] = (), fences: Iterable[Window] = FENCES,
                   require_cutoff: bool = True) -> list[str]:
    """Every reason this model may not be scored on eval_nights; [] when it may."""
    fences = tuple(fences)
    out: list[str] = []
    try:
        cut = cutoff_of(meta)
    except ValueError as e:
        return [f"training_cutoff unreadable: {e}"]
    if cut is None:
        return ["no training_cutoff: every model file must say the last night it learned from"] if require_cutoff else []
    train = meta.get("train_nights")
    tn: list[str] | None = None
    if train is not None:
        try:
            tn = sorted({night_key(n) for n in train})
        except ValueError as e:
            return [f"train_nights unreadable: {e}"]
        stamp = meta.get("train_nights_sha256")
        if stamp and stamp != nights_sha256(tn):
            out.append("train_nights_sha256 does not match train_nights (the list was changed after stamping)")
        if tn and tn[-1] > cut:
            out.append(f"train_nights run to {tn[-1]}, after training_cutoff {cut}")
        out += train_problems(tn, fences)
    for w in fences:
        if w.start <= cut <= w.end:
            out.append(f"training_cutoff {cut} is inside the {w.name} window {w.start}..{w.end}: "
                       f"trained inside the {'held-out' if w is LOCKED else w.name} window")
        elif cut > w.end and tn is None:
            out.append(f"training_cutoff {cut} is after the {w.name} window {w.start}..{w.end} and the model "
                       f"lists no train_nights, so nothing proves it left those nights out")
    ev = sorted({night_key(n) for n in eval_nights})
    if ev:
        if cut >= ev[0]:
            out.append(f"training_cutoff {cut} is on or after the first eval night {ev[0]}")
        if tn is not None:
            both = disjoint_by_hash(tn, ev, key=lambda n: n)
            if both:
                out.append(f"{len(both)} eval night(s) are also train nights ({', '.join(sorted(both.values())[:3])})")
    return out


def guard_model(meta: Mapping, eval_nights: Iterable[object] = (), name: str = "model",
                fences: Iterable[Window] = FENCES, require_cutoff: bool = True) -> None:
    """Raise FenceError('REFUSED <name>: ...') unless the model may be scored on eval_nights."""
    bad = model_problems(meta, eval_nights, fences, require_cutoff)
    if bad:
        raise FenceError(f"REFUSED {name}: " + "; ".join(bad))


def read_meta(path: Path) -> dict:
    """A model's metadata: the `meta` array of an .npz (json in uint8, as redzone.models_rt.forest saves
    it), a `<stem>.meta.json` / `<stem>.json` sidecar, or the file itself when it is json."""
    path = Path(path)
    if path.suffix == ".npz":
        import numpy as np
        with np.load(path, allow_pickle=False) as z:
            if "meta" in z.files:
                raw = z["meta"]
                return json.loads(raw.tobytes().decode() if raw.dtype == np.uint8 else str(raw))
        for side in (path.with_suffix(".meta.json"), path.with_suffix(".json")):
            if side.exists():
                return json.loads(side.read_text())
        return {}
    return json.loads(path.read_text())


def load_guarded(path: Path, eval_nights: Iterable[object] = (), fences: Iterable[Window] = FENCES,
                 require_cutoff: bool = True) -> dict:
    """read_meta + guard_model: the metadata, or FenceError naming the file and every reason."""
    meta = read_meta(path)
    guard_model(meta, eval_nights, name=str(path), fences=fences, require_cutoff=require_cutoff)
    return meta


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def disjoint_by_hash(train: Iterable, evals: Iterable, key=None) -> dict[str, str]:
    """{sha256: item} for every eval item whose content hash is also on the train side; {} when disjoint.
    Items are file paths (hashed by bytes) unless `key` maps an item to the string that is hashed.
    Hashing content catches the same game filed under two names, nights or providers."""
    def h(x) -> str:
        return hashlib.sha256(str(key(x)).encode()).hexdigest() if key else file_sha256(Path(x))
    seen = {h(x) for x in train}
    return {d: str(x) for x in evals if (d := h(x)) in seen}


# Parts of a finished ESPN summary that only exist once the game is over (or hold its final state).
_AFTER_THE_FACT = ("boxscore", "leaders", "standings", "seasonseries", "scoringPlays", "videos", "article",
                   "news", "keyEvents", "plays_full")


def cut_feed(league, data: dict, k: int) -> dict:
    """A summary as the feed could have shown it after play k: the plays (and win-probability rows) up to
    k only, the game still in progress, no final score, records, box score or leader board. The causal
    test rebuilds features from this and requires them to equal the stored full-game features at play k."""
    import copy
    from redzone.models import League
    from redzone.sources import espn
    keep = {p.get("id") for p in espn.flatten_plays(league, data)[: k + 1]}
    d = copy.deepcopy(data)
    if league.base == League.NFL:
        dr = d.get("drives") or {}
        prev = []
        for x in dr.get("previous") or []:
            x["plays"] = [p for p in x.get("plays") or [] if p.get("id") in keep]
            if x["plays"]:
                prev.append(x)
        dr["previous"] = prev
        if dr.get("current"):
            dr["current"]["plays"] = [p for p in dr["current"].get("plays") or [] if p.get("id") in keep]
    else:
        d["plays"] = [p for p in d.get("plays") or [] if p.get("id") in keep]
    d["winprobability"] = [w for w in d.get("winprobability") or [] if w.get("playId") in keep]
    for comp in (d.get("header") or {}).get("competitions") or []:
        for c in comp.get("competitors") or []:
            for f in ("score", "record", "winner", "linescores"):
                c.pop(f, None)
        st = comp.setdefault("status", {}).setdefault("type", {})
        st.update(state="in", completed=False)
    for f in _AFTER_THE_FACT:
        d.pop(f, None)
    return d


def game_files(league_dir: Path, nights: Mapping[str, Iterable[str]], which: Iterable[str]) -> list[Path]:
    """The summary files of the games on the given nights (games_by_night from splits.archive_nights)."""
    league_dir = Path(league_dir)
    return [p for n in which for g in nights.get(n, ()) if (p := league_dir / f"summary_{g}.json").exists()]
