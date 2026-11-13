"""The corpus manifest: the data-manifest envelope (tag, licences, one sha256 per file, tree digest, manifest hash) plus a `corpus` section that
describes every shard, the walk-forward splits, the dedup and the fence, and a verifier that proves the shards on disk are what it says.

Why: B-02 done-when 2 (WORKPLAN): "the manifest verifies by hash and a leakage test passes". The envelope is redzone/lab/datamanifest.py
(F-05): same canonical JSON, same manifest_sha256 rule, same file entries, same renderer, same licence block, same scrub of paths, addresses
and credentials, same refusal to re-pin a tag with different content. It cannot use datamanifest's own verifier because a shard spans many
nights, so the fence is checked here, game by game, from the shard headers.

What verify_corpus proves (every line is a problem; [] means all of it held):
  hashes     every listed file exists and re-hashes to the manifest; the manifest hash matches its content
  shards     header equals manifest row; index equals body; ids inside vocab v1; coverage counters equal a recount from the tokens
  fence      every game in a shard that may train sits on a clear night; every game in a tuning shard sits on a tuning night; no shard of
             either kind holds a locked, fresh or undated night
  splits     each game's night maps to the shard's role under the recorded cut nights; no night, game id or game content is in two roles
  provenance vocab layout equals this checkout's vocab v1; licence lines present and not red, blocked or unverified; names and stats
             sidecars match
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from redzone.lab import datamanifest as dm
from redzone.lab import evaldata
from redzone.lab.corpus import shardio, walkforward
from redzone.lab.corpus import tokenise as tk
from redzone.lab.corpus.records import CORPUS_ROLES, CORPUS_TIERS, CORPUS_TRAIN_ROLES, era_of, night_class
from scripts.history.licences import PERSONAL_TAG, SOURCES

CORPUS_MANIFEST_FORMAT = "redzone-corpus-manifest/1"
CORPUS_KIND = "corpus"
CORPUS_NAMES_FILE = "names.json"
CORPUS_DEDUP_FILE = "dedup.jsonl"
CORPUS_STATS_FILE = "stats.json"
CORPUS_SIDECARS = (CORPUS_NAMES_FILE, CORPUS_DEDUP_FILE, CORPUS_STATS_FILE)
CORPUS_SHOWN = 20


def licence_block(providers: list[str]) -> dict[str, dict[str, str]]:
    """The licence entry per provider from scripts/history/licences.py, refusing a source that is blocked, unverified or has red terms."""
    out: dict[str, dict[str, str]] = {}
    for p in providers:
        s = SOURCES.get(p)
        if s is None:
            raise dm.ManifestError(f"provider {p!r} has no licence entry in scripts/history/licences.py")
        entry = {"licence": s.licence, "status": s.status, "tag": PERSONAL_TAG if s.terms_class != "open" else "", "terms_class": s.terms_class}
        bad = dm._licence_problems(p, entry)
        if bad:
            raise dm.ManifestError("; ".join(bad))
        out[p] = entry
    return out


def file_license(sources: list[str], block: Mapping[str, Mapping]) -> str:
    return "; ".join(sorted({block[s]["tag"] or block[s]["licence"] for s in sources}))


def file_entry(path: str, size: int, sha: str, *, league: str | None, rows: int | None, sources: list[str], fence_status: str,
               block: Mapping[str, Mapping]) -> dict[str, Any]:
    return {"bytes": size, "fence": fence_status, "league": league, "license": file_license(sources, block), "night": None, "path": path,
            "rows": rows, "sha256": sha, "sources": sorted(sources)}


def derive(files: list[dict], shards: list[dict]) -> dict[str, Any]:
    """Everything in the envelope that follows from the file list and the shard rows."""
    rows = [f["rows"] for f in files if f["rows"] is not None]
    firsts = [s["nights"][0] for s in shards if isinstance(s.get("nights"), list) and len(s["nights"]) == 2]
    lasts = [s["nights"][1] for s in shards if isinstance(s.get("nights"), list) and len(s["nights"]) == 2]
    counts = {"clear": sum(1 for s in shards if s.get("fence") == "clear"), "tuning": sum(1 for s in shards if s.get("fence") == "tuning")}
    return {
        "leagues": sorted({f["league"] for f in files if f["league"]}),
        "sources": sorted({s for f in files for s in f["sources"]}),
        "totals": {"bytes": sum(f["bytes"] for f in files), "files": len(files), "rows": sum(rows) if rows else None},
        "tree_sha256": evaldata.tree_digest({f["path"]: f["sha256"] for f in files}),
        "window": {"start": min(firsts), "end": max(lasts)} if firsts else None,
        "fence_shards": counts,
    }


def assemble(tag: str, files: list[dict], corpus: dict[str, Any], *, selection: dict[str, Any], notes: list[str]) -> dict[str, Any]:
    dm.check_tag(tag)
    files = sorted(files, key=lambda f: f["path"])
    sources = sorted({s for f in files for s in f["sources"]})
    block = licence_block(sources)
    doc: dict[str, Any] = {
        "format": CORPUS_MANIFEST_FORMAT, "tag": tag, "kind": CORPUS_KIND, "private": False,
        "license": "; ".join(sorted({block[s]["tag"] or block[s]["licence"] for s in block})), "licences": block,
        "selection": selection, "notes": sorted(notes)[:CORPUS_SHOWN], **derive(files, corpus["shards"]), "corpus": corpus, "files": files}
    doc["manifest_sha256"] = dm.manifest_hash(doc)
    bad = dm.scrub_problems(doc)
    if bad:
        raise dm.ManifestError("the manifest would hold something that must never be committed: " + "; ".join(bad[:5]))
    return doc


# --- checks that need only the document -------------------------------------------------------------------------------------

def check_corpus_manifest(doc: Any) -> list[str]:
    """Problems in the manifest itself (no disk): format, hashes, file entries, derived sections, shard rows, role and fence consistency."""
    if not isinstance(doc, dict) or doc.get("format") != CORPUS_MANIFEST_FORMAT:
        return [f"not a {CORPUS_MANIFEST_FORMAT} document"]
    out: list[str] = []
    for k in ("tag", "kind", "files", "licences", "tree_sha256", "manifest_sha256", "sources", "totals", "corpus"):
        if k not in doc:
            out.append(f"missing field {k!r}")
    if out:
        return out
    try:
        dm.check_tag(doc["tag"])
    except dm.ManifestError as e:
        out.append(str(e))
    if doc["kind"] != CORPUS_KIND or doc.get("private") is not False:
        out.append("kind must be corpus and not private")
    files, corpus = doc["files"], doc["corpus"]
    if not isinstance(files, list) or not files or not isinstance(corpus, dict) or not isinstance(corpus.get("shards"), list):
        return out + ["no files or shards listed"]
    seen: set[str] = set()
    for i, f in enumerate(files):
        if not isinstance(f, dict) or sorted(f) != sorted(dm.FILE_KEYS):
            out.append(f"files[{i}]: wrong fields (want {', '.join(dm.FILE_KEYS)})")
            continue
        if not dm._safe_rel(f["path"]):
            out.append(f"files[{i}]: unsafe path {str(f['path'])[:60]!r}")
            continue
        if f["path"] in seen:
            out.append(f"{f['path']}: listed twice")
        seen.add(f["path"])
        if not (isinstance(f["sha256"], str) and dm._SHA.match(f["sha256"])) or not isinstance(f["bytes"], int) or f["bytes"] < 0:
            out.append(f"{f['path']}: bad sha256 or size")
        if f["night"] is not None or f["fence"] not in ("none", "tuning"):
            out.append(f"{f['path']}: night must be null and fence none or tuning (a shard spans nights; the corpus section carries them)")
        if not f["sources"] or any(s not in doc["licences"] for s in f["sources"]) or not f["license"]:
            out.append(f"{f['path']}: no licence line for its source")
    if [f["path"] for f in files if isinstance(f, dict)] != sorted(f["path"] for f in files if isinstance(f, dict)):
        out.append("files are not sorted by path")
    if not out:
        for k, want in derive(files, corpus["shards"]).items():
            if doc.get(k) != want:
                out.append(f"{k} does not match the file list")
    for s, e in doc["licences"].items():
        out.extend(dm._licence_problems(s, e))
    out.extend(_shard_row_problems(doc, seen))
    if doc["manifest_sha256"] != dm.manifest_hash(doc):
        out.append("manifest_sha256 does not match the content (the manifest was edited)")
    out.extend(dm.scrub_problems(doc, limit=CORPUS_SHOWN))
    return out


def _shard_row_problems(doc: Mapping, listed: set[str]) -> list[str]:
    out: list[str] = []
    corpus = doc["corpus"]
    v = corpus.get("vocab") or {}
    if v.get("sha256") != tk.vocab_fingerprint():
        out.append("vocab fingerprint differs from this checkout's vocab v1 (token ids may mean something else); do not train on this corpus here")
    paths = set()
    for s in corpus["shards"]:
        p = s.get("path")
        if p in paths:
            out.append(f"{p}: shard row listed twice")
        paths.add(p)
        if p not in listed:
            out.append(f"{p}: shard row has no file entry")
            continue
        role, fnc = s.get("role"), s.get("fence")
        if role not in CORPUS_ROLES or s.get("tier") not in CORPUS_TIERS or s.get("league") not in tk.NT.LEAGUES:
            out.append(f"{p}: role, tier or league is not one of the known values")
            continue
        if (role == "tuning") != (fnc == "tuning") or s.get("train_ok") is not (role in CORPUS_TRAIN_ROLES) or fnc not in ("clear", "tuning"):
            out.append(f"{p}: role {role}, fence {fnc} and train_ok {s.get('train_ok')} disagree (only clear shards in a train, valid or holdout role may train)")
        if s.get("era") != era_of(int(s.get("season") or 0)):
            out.append(f"{p}: era does not follow the season")
        n = s.get("nights")
        if not (isinstance(n, list) and len(n) == 2 and all(dm._is_night(x) for x in n) and n[0] <= n[1]):
            out.append(f"{p}: nights range is malformed")
    for p in listed - paths - {f"{x}" for x in CORPUS_SIDECARS}:
        out.append(f"{p}: file entry has no shard row")
    out.extend(_cut_problems(corpus))
    return out


def _cut_problems(corpus: Mapping) -> list[str]:
    """Time order of the roles per league, from shard night ranges and the recorded cuts."""
    out: list[str] = []
    by_league: dict[str, dict[str, list[list[str]]]] = {}
    for s in corpus["shards"]:
        if s.get("role") in CORPUS_TRAIN_ROLES and dm._is_night((s.get("nights") or [None])[0]):
            by_league.setdefault(s["league"], {}).setdefault(s["role"], []).append(s["nights"])
    for lg, roles in sorted(by_league.items()):
        cut = (corpus.get("splits") or {}).get(lg)
        if not isinstance(cut, dict):
            out.append(f"{lg}: no split recorded for its train-ok shards")
            continue
        ls = walkforward.LeagueSplit(lg, cut.get("mode", ""), cut.get("valid_from"), cut.get("holdout_from"), int(cut.get("gap_days") or 0),
                                     int(cut.get("nights") or 0))
        for role, ranges in roles.items():
            for first, last in ranges:
                if ls.role_of(first) != role or ls.role_of(last) != role:
                    out.append(f"{lg}: a {role} shard spans nights {first}..{last} outside that role's cut ({ls.valid_from}, {ls.holdout_from})")
    return out


# --- checks that read the files ---------------------------------------------------------------------------------------------

def verify_corpus(doc: Any, root: str | Path) -> list[str]:
    """check_corpus_manifest + hashes + every shard cross-checked + the leakage and split proofs. [] only when all of it holds."""
    out = check_corpus_manifest(doc)
    if not isinstance(doc, dict) or not isinstance(doc.get("files"), list):
        return out
    out += dm.verify_files(doc, root)
    if out:
        return out                      # a missing or changed file: nothing below can be trusted
    root = Path(root)
    corpus = doc["corpus"]
    cuts = corpus.get("splits") or {}
    seen_gid: dict[tuple[str, str], str] = {}
    seen_sha: dict[str, str] = {}
    nights_by_role: dict[str, dict[str, set[str]]] = {}
    games_by_night: dict[str, dict[str, list[str]]] = {}
    for row in corpus["shards"]:
        p = row["path"]
        try:
            sh = shardio.read_shard(root.joinpath(*p.split("/")))
        except (shardio.ShardError, OSError, ValueError) as e:
            out.append(f"{p}: unreadable ({e})")
            continue
        out += [f"{p}: {x}" for x in _header_vs_row(sh.header, row)]
        out += [f"{p}: {x}" for x in shardio.shard_problems(sh)]
        lg, role = row["league"], row["role"]
        c = cuts.get(lg) or {}
        ls = walkforward.LeagueSplit(lg, c.get("mode", ""), c.get("valid_from"), c.get("holdout_from"), int(c.get("gap_days") or 0), 0)
        for r in (dict(zip(shardio.CORPUS_INDEX_FIELDS, x)) for x in sh.header["index"]):
            night = r["night"]
            klass = night_class(lg, night, True) if dm._is_night(night) else "undated"
            want = "tuning" if role == "tuning" else "clear"
            if klass != want:
                out.append(f"{p}: game {r['gid']} on night {night} is {klass}, but this is a {role} shard (fence: needs {want})")
            elif role != "tuning" and ls.role_of(night) != role:
                out.append(f"{p}: game {r['gid']} on night {night} belongs to role {ls.role_of(night)}, not {role}")
            gk = (lg, r["gid"])
            if gk in seen_gid:
                out.append(f"{p}: game {r['gid']} is also in {seen_gid[gk]} (one game, one shard)")
            seen_gid[gk] = p
            if r["sha16"] in seen_sha and seen_sha[r["sha16"]] != p:
                out.append(f"{p}: game {r['gid']} has the same tokens as a game in {seen_sha[r['sha16']]}")
            seen_sha[r["sha16"]] = p
            if role in CORPUS_TRAIN_ROLES:
                nights_by_role.setdefault(lg, {}).setdefault(role, set()).add(night)
                games_by_night.setdefault(lg, {}).setdefault(night, []).append(r["gid"])
    for lg, roles in sorted(nights_by_role.items()):
        out += [f"{lg}: {x}" for x in walkforward.split_problems(roles, games_by_night.get(lg))]
    out += _sidecar_problems(doc, root)
    return out


def _header_vs_row(h: Mapping, row: Mapping) -> list[str]:
    out = []
    for k, rk in (("league", "league"), ("season", "season"), ("era", "era"), ("tier", "tier"), ("role", "role"), ("fence", "fence"),
                  ("train_ok", "train_ok"), ("games", "games"), ("events", "events"), ("tokens", "ids"), ("providers", "providers")):
        if h.get(k) != row.get(rk):
            out.append(f"header {k} {h.get(k)!r} differs from the manifest row {row.get(rk)!r}")
    nights = sorted({r[3] for r in h.get("index", [])})
    if nights and [nights[0], nights[-1]] != row.get("nights"):
        out.append(f"header nights {nights[0]}..{nights[-1]} differ from the manifest row {row.get('nights')}")
    return out


def _sidecar_problems(doc: Mapping, root: Path) -> list[str]:
    from redzone.lab.corpus import stats
    out: list[str] = []
    try:
        names = json.loads((root / CORPUS_NAMES_FILE).read_text())
        if names.get("vocab_sha256") != tk.vocab_fingerprint() or names.get("format") != "redzone-corpus-names/1":
            out.append(f"{CORPUS_NAMES_FILE}: not the name table of this vocab")
    except (OSError, ValueError, AttributeError):
        out.append(f"{CORPUS_NAMES_FILE}: unreadable")
    try:
        saved = json.loads((root / CORPUS_STATS_FILE).read_text())
        fresh = stats.compute(doc, root)
        if saved != fresh:
            out.append(f"{CORPUS_STATS_FILE}: differs from a recount of the shard headers (rerun `corpus_build.py stats --write`)")
    except (OSError, ValueError, shardio.ShardError):
        out.append(f"{CORPUS_STATS_FILE}: unreadable")
    return out
