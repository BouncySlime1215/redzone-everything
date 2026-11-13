# Data manifests (F-05)

A manifest pins exactly which files a training run saw: a tag, a window of nights, and the sha256, size, row count, source,
licence line and leak-fence status of every file. A run records the manifest's hash (`manifest_sha256`) as its
`data_manifest_hash` in the model registry; a notebook loads the version with `load_pinned`, which re-hashes every file first
and refuses the whole tag if one byte differs. Code: `redzone/lab/datamanifest.py`, `scripts/data_manifest.py`. Fence windows
come from `redzone/lab/fence.py`; licence lines from `scripts/history/licences.py` and `docs/datasources/terms.json`.

Run from the repo folder with the repo venv python (`.venv/bin/python`). The tool only reads data. It writes one manifest file.

## Pin a version (about 2 minutes, plus hashing time: the whole 385 MB archive takes about 2 seconds)

```sh
# recorded nights (data/archive): a window of nights and the leagues you mean
python scripts/data_manifest.py build archive --tag archive-nba-20261101-20261102-v1 --start 20261101 --end 20261102 --leagues nba
# B-01 history (data/history): per-game files, hoopR/wehoop season files, packed shards
python scripts/data_manifest.py build history --tag history-nba-2026-v1 --leagues nba
# C-02 feed-arrival logs (data/feedlag): one file per night
python scripts/data_manifest.py build feedlag --tag feedlag-20261112-v1
# picture data (data/vision-v1): PRIVATE, see below
python scripts/data_manifest.py build frames --tag frames-20261111-v1
```

`--root DIR` points at another copy of the data (or set `RZ_DATA_ROOT_ARCHIVE`, `_HISTORY`, `_FEEDLAG`, `_FRAMES`). On a
worktree, symlink the Mac's `data/archive` into it; the tool never follows links inside a data folder and never writes there.
The manifest goes to `data/manifests/<tag>.manifest.json` (git-ignored; `--dir` or `RZ_DATA_MANIFESTS` change it).

- **A tag is a pin.** Building the same tag again gives the same bytes and says `already pinned, identical`; different content
  is refused. A new version needs a new tag (`...-v2`).
- **Files still being written are refused** (changed in the last 10 minutes; `--settle-seconds N`, 0 turns it off). The feed-arrival
  log for a night keeps growing until the 06:00 ET rollover and whenever the logger restarts, so pin it the next morning.
- Left out and counted, not pinned: symlinks, hidden files, raw logs (`logger.out`), ledgers, files whose names look like secrets
  (skipped unopened), anything that is not a recognised data file.
- The build runs the repo's licence check (F8) on the manifest first. A source with no licence entry, a red source, or one the
  fetchers marked blocked or unverified (nflverse, cfbfastR today) cannot be pinned.

## Check it before you train (about 1 minute)

```sh
python scripts/data_manifest.py verify archive-nba-20261101-20261102-v1      # exit 0 and "OK ..." or exit 1 and the problem list
python scripts/data_manifest.py list --verify                                # every manifest, re-hashed
```

`verify` re-hashes every file and fails loudly on a missing file, a symlink, a different size, a different sha256, an edited manifest
(its own hash and tree digest), or a fence status that no longer matches `fence.py`. It prints manifest-relative paths, short hashes and
counts. It never prints file contents or absolute paths.

## Load it in a notebook

```python
from redzone.lab.datamanifest import load_pinned
data = load_pinned("archive-nba-20261101-20261102-v1")     # ManifestError: REFUSED ... unless every file matches
data.paths(night="20261101", league="nba")                 # absolute Paths, from data.root
data.manifest_sha256                                        # put this in the registry record as data_manifest_hash
train = load_pinned(tag, for_training=True)                 # fence.FenceError if any night is locked or tuning
```

`for_training=True` also refuses files with no known night (a game no scoreboard lists, a season file) unless you pass
`allow_unknown_nights=True` because the fetcher already kept fenced nights out. On Kaggle or Colab give the bundle's manifest path
and data folder: `load_pinned("<bundle>/<tag>.manifest.json", root="<bundle>/data")`.

## Compare, list, package

```sh
python scripts/data_manifest.py diff TAG_A TAG_B           # added, removed, changed, moved (same bytes, new name); exit 1 if different
python scripts/data_manifest.py list                       # tag, kind, window, files, bytes, fence counts, hashes
python scripts/data_manifest.py package TAG [--out data/bundles/TAG]   # DRY RUN upload layout for Kaggle and Drive
```

`package` uploads nothing and needs no credentials. It prints the bundle layout (`dataset-metadata.json`, the manifest copy,
`data/<path>`), the per-folder file counts, and a Kaggle dataset-metadata skeleton with a placeholder owner. With `--out` it writes
those three small files (never data) into a git-ignored folder. The real upload waits on Nick's decisions: the Kaggle token (N1) and
the Drive sync (N6). The nightly sync of the Mac archive is not built yet.

## Frames and picture data stay private

- A `frames` manifest is marked `private`, is written as `<tag>.private.manifest.json` (ignored by git) and only into a git-ignored
  place: the tool refuses a folder git would commit.
- `package` refuses it unless you pass `--include-private-frames` (and even then it only prints a plan).
- Nothing in a manifest can be an absolute or home path, a URL, an IP or MAC address, an email or a credential: the build,
  `verify`, `load_pinned` and `package` all scan every string and key and refuse. A file named like an address cannot be pinned.

## When verify fails

| Message | Cause | Fix |
|---|---|---|
| `path: sha256 .. != pinned ..` | the file was edited or re-fetched | restore it, or build a new tag from the new data |
| `path: size N != pinned M` | the file grew (a live file) or was cut | same; pin only finished nights |
| `path: missing` | the file was pruned (the disk guard, a new archive) | restore from Drive or Kaggle, or re-pin |
| `path: is a symlink` / `resolves outside the data folder` | a link replaced a file or folder | put the real file back |
| `manifest_sha256 does not match` / `tree_sha256 does not match` | the manifest was edited by hand | rebuild; never edit a manifest |
| `fence status .. is stale` / `fence does not match` | `fence.py` windows moved after the pin | build a new tag so the status is recomputed |
| `REFUSED training set: N locked night(s)` | the version holds fenced nights | pin a window outside the fences (`--start/--end`) |

## What this does not do (yet)

Nightly sync to Kaggle and Drive (N1, N6), per-play first-seen times and K files as their own kinds, picture-truth cuts, and an
odds snapshot all need new data streams or Nick's decisions. The manifest format already carries them: a new kind adds a scanner.
