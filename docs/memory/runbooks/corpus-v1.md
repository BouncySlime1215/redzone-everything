# Corpus v1 (B-02): tokenise the play-by-play into hashed shards

Do this when: Track B needs training data (B-03 trainer, B-04 first runs) or you want to prove a corpus is clean.
Code: `redzone/lab/corpus/`, `scripts/corpus_build.py`. Input: `data/history/` (B-01) and, optionally, `data/archive/`. Output:
`data/corpus/<tag>/` (git-ignored, never committed). Why this exists: V2 plan, WORKPLAN row B-02 and Track B corpus rules 1 to 7
(`redzone-agents/v2/tracks/B-foundation-model.md`). The sibling runbooks are [history-fetch.md](history-fetch.md) (where the games
come from) and [data-manifests.md](data-manifests.md) (the manifest format this one reuses).

Run from the repo folder with the repo venv python (`.venv/bin/python`). The tool only reads the input folders and writes one output folder.
It needs numpy; hoopR and wehoop files also need pandas and pyarrow (CI and Kaggle have them). No network, no secret, no hardware.

## 1. Build a corpus (small one: 2 minutes for about 700,000 events)

```sh
python scripts/corpus_build.py build --tag corpus-smoke-v1 --history-root data/history
python scripts/corpus_build.py verify data/corpus/corpus-smoke-v1/corpus-smoke-v1.manifest.json    # exit 0 = every proof held
python scripts/corpus_build.py stats  data/corpus/corpus-smoke-v1/corpus-smoke-v1.manifest.json    # the report below
python scripts/corpus_build.py show   data/corpus/corpus-smoke-v1/corpus-smoke-v1.manifest.json --league nba --role holdout --game 0
```

Options: `--leagues nba,cbb`, `--seasons 2025-2016`, `--archive-root data/archive --archive-nights 20261021,20261022` (the recorded
archive is opt-in: nearly all of it is fenced), `--fenced hold` (section 3), `--valid-frac 0.1 --holdout-frac 0.1 --gap-days 0`,
`--out DIR` (default `data/corpus/<tag>`).

- **A tag is a pin.** Building the same tag from the same input prints the same hash and changes nothing; different content under the
  same tag is refused and leaves the folder as it was. A new corpus needs a new tag.
- The build writes into `<out>.building` and moves it into place only after the finished corpus verifies. A refused or failed build
  leaves nothing behind.
- Deterministic: the same games give the same shard bytes whatever order the input holds them in (`tests/test_corpus_build.py`).
- `data/history` may hold B-01's packed shards (`fetch_history.py --pack`) or only the per-game files; packed shards win when both exist,
  so repack after a fetch. hoopR and wehoop season files (`datasets/hoopr_nba`, `hoopr_mbb`, `wehoop_wbb`) are read where present.
  nflverse and cfbfastR are blocked or unverified in `scripts/history/licences.py` and are not read.

## 2. What a corpus is

```
data/corpus/<tag>/
  <tag>.manifest.json                  the pin: tag, licences, sha256 per file, tree digest, manifest hash, and the `corpus` section
  shards/<league>/<season>-<tier>-<role>.rzs     one shard per league, season, tier and role
  names.json                           kind and team names per hashed slot, with counts (how a slot is read back as a name)
  dedup.jsonl                          every copy removed: league, how it matched (id or signature), kept, dropped
  stats.json                           the report (recomputed and compared by verify)
```

- **Tokens.** Vocab v1 exactly as `scripts/noggin_tokens.py` writes it (10,511 ids, six tokens per event, `BOS home away [events] EOS`,
  uint16). `redzone/lab/corpus/tokenise.py` wraps it and fingerprints the layout; the manifest stores the fingerprint and `verify` refuses a
  corpus built under a layout that is not this checkout's. Changing any id offset means a new vocab version and a rebuild of everything.
- **Tags on every shard.** League, season, era (decade of the season label), tier (`wall`: 90% or more of plays have a wall-clock stamp;
  `nowall`: the game-clock fallback, corpus rule 2), role, provider(s) and the personal-use licence line (rule 6).
- **Dedup (rule 3).** hoopR and wehoop use ESPN's event id, so their copy of an ESPN game matches by id; a copy under another id space matches
  by score trajectory (at least four score changes, nights within a day, one copy per provider). The kept copy: wall-clock tier first, then
  more plays, then ESPN before hoopR before wehoop. Ambiguous clusters (two ESPN games with one trajectory) are left alone and counted.
- **Walk-forward roles (rule 5).** Per league: with three or more seasons, the newest season(s) are `holdout`, the one(s) before are `valid`,
  the rest `train` (cuts at season starts, 10% each by default); with fewer seasons the cuts are whole nights. A night is never split.
  `--gap-days N` embargoes the nights just before each cut. The manifest records the cuts and five forward-chaining folds
  (`corpus.splits.<league>`), and `verify` checks that no night, game id or game content is in two roles.
- **Wall-clock tiers.** Only the `wall` tier should feed anything that needs timing (dead-ball length, lag). In the `nowall` tier the gap
  token is the difference of the game clock, not wall seconds (that is what vocab v1 does when a play has no stamp), so its gap buckets mean
  something else: use that tier for event-order learning only, never pooled with the `wall` tier for a timing head.

## 3. The leak fence (F-09), and what reaches a training shard

A game is judged on its night (`redzone/lab/corpus/records.py`); only `clear` games reach a train, valid or holdout shard.

| Verdict | Night | `exclude` (default) | `hold` |
|---|---|---|---|
| clear | everything else | train, valid, holdout shards | same |
| locked | 2025-12-12 to 2026-01-05 (`fence.LOCKED`) | dropped, tallied only | dropped |
| tuning | October 2026 (`fence.TUNING`) | dropped, tallied | `tuning` shard, `train_ok=false` |
| fresh | on or after the league's first evaluation night (`scripts/history/leagues.EVAL_FIRST_NIGHT`) | dropped, tallied | dropped |
| undated | no night at all | dropped, tallied | dropped |

- Locked, fresh and undated games are never tokenised and never stored. A summary in the recorded archive is judged from the scoreboard
  that lists it, so a fenced night's summary file is never opened. A game whose night came from a date column only is judged on the day
  before, the day and the day after.
- The recorded archive is nearly all fenced (NBA, NHL and college from 2026-10-19 are tuning or fresh; NFL 2025 is fresh or locked), so a
  corpus built from it alone is empty. `--fenced hold` keeps the tuning-window nights in `tuning` shards for evaluating on tuning nights.
- **Both sides check.** The writer never puts a fenced night in a training role. `verify` re-judges every game night of every shard from
  the shard header (a hash match is not enough: a hand-edited shard with a locked night and a re-stamped manifest still fails), and the
  reader (`redzone/lab/corpus/reader.py`) refuses a `tuning` shard when asked for training and re-asserts `fence.assert_train_nights`.

## 4. Use a corpus (the contract for B-03 and B-04)

```python
from redzone.lab.corpus.reader import load_corpus
c = load_corpus("data/corpus/corpus-v1/corpus-v1.manifest.json")      # re-hashes every file and re-runs the proofs; raises if anything is off
for row, tokens in c.games(roles=("train",), leagues=("nba",), tiers=("wall",)):    # tokens: uint16 BOS home away ... EOS
    ...
stamp = c.train_stamp(("train",))     # {"training_cutoff", "train_nights", "train_nights_sha256", "data_manifest_hash"} for the model record
```

`Shard.tokens` is a read-only memory map, so a trainer can slice games without loading a shard. `names.json` plus
`tokenise.detokenise_game(tokens, c.names())` reads a game back as events (kind slot and names, gap bucket, period, clock bucket, margin,
points and side, stoppage). The stamp goes straight into the model registry (F-02) and the fence guard (`fence.guard_model`).

## 5. Run the whole thing on Kaggle or Colab (the Mac stays free)

The full tokenisation is a single-core CPU job. Do it after B-01's big fetch has produced `history/` with packed shards.

Kaggle (CPU notebook, Internet off is fine; no secret is involved; until the Kaggle token exists the code goes up through Chrome like the
other jobs, see [kaggle-run.md](kaggle-run.md)):

1. Upload the repo at the commit you want (a private dataset, for example named `redzone-code`; `git archive HEAD` makes the tarball). It
   must hold `redzone/` and `scripts/`, because the build imports the vocab, the fence and the ESPN parser from them.
2. Attach the B-01 output (the `history/` folder with `shards/`, `datasets/`) as a second input.
3. One cell:

```sh
cd /kaggle/working
python /kaggle/input/redzone-code/scripts/corpus_build.py build --tag corpus-v1 \
    --history-root /kaggle/input/redzone-history/history --out /kaggle/working/corpus/corpus-v1
python /kaggle/input/redzone-code/scripts/corpus_build.py verify /kaggle/working/corpus/corpus-v1/corpus-v1.manifest.json
python /kaggle/input/redzone-code/scripts/corpus_build.py stats  /kaggle/working/corpus/corpus-v1/corpus-v1.manifest.json
```

Save `/kaggle/working/corpus/corpus-v1` as the notebook output (about 1 GB), then attach that output as an input to the training notebook and
call `load_corpus` on it. The script puts the repo folder on `sys.path` itself; nothing needs installing (numpy, pandas and pyarrow are on the
Kaggle image).

Colab: same three commands. Mount Drive, point `--history-root` at the folder you synced (`/content/drive/MyDrive/redzone/history`) and `--out` at
`/content/drive/MyDrive/redzone/corpus/corpus-v1`, and clone or copy the repo to `/content/redzone-code` for the script path.

Verify the pin once more where you train: `verify` needs only the folder, and `load_corpus` runs the same proofs on every load.

### Expected volume (ESTIMATE; corrected for the Nov 13 B-01 probes)

College games average about 320 plays in ESPN summaries, not 513, and women's college is about 328 (`tracks/B-foundation-model.md`, added Nov 13).
Ten seasons, all seven leagues, with the games per league from the B0 arithmetic:

| League | Games | Events per game | Events | Tokens (6 per event + 4 per game) | Shard MB (2 bytes per token + index) |
|---|---:|---:|---:|---:|---:|
| NBA | 13,200 | 481 (measured, NBA 2024) | 6.4M | 38M | 77 |
| Men's college basketball | 58,000 | 320 | 18.6M | 112M | 224 |
| Women's college basketball | 49,500 (90% coverage) | 328 | 16.2M | 97M | 195 |
| MLB | 24,300 | 602 | 14.6M | 88M | 176 |
| NHL | 14,000 | 315 | 4.4M | 26M | 53 |
| College football | 8,000 | 180 | 1.4M | 9M | 17 |
| NFL | 2,850 | 187 (measured, NFL 2023) | 0.5M | 3M | 6 |
| **Total** | **169,850** | | **62M** | **373M** | **about 750** |

At full depth (every season with plays, the 85M-event ESPN plan in the B-01 notes) expect about 85M events, 0.51B tokens and about 1.0 GB.
Compared with the Track B plan (84M events from ten seasons) the college share is about 38% lower, which moves the tokens-versus-size gate:
62M events is about 373M unique tokens, so the U/4 rule allows about 90M parameters (rungs 5M, 15M and 50M; the 100M rung still needs the full-depth run).

Time (ESTIMATE from the smoke run: 7,000 to 13,000 events per second per core for both passes together on the Mac while other jobs ran, hoopR
season files at the low end): 62M events is about 2 to 3 hours, 85M about 3 to 4 hours, on one Kaggle core, inside the 12-hour session. Memory:
one league-season of tokens (under 100 MB), the game list of pass 1 (about 50 MB) and one hoopR season table (under 2 GB). The build is not
resumable mid-run; if the session dies, rerun it (nothing is half-written: the output appears only when the whole corpus verifies).

## 6. If it goes wrong

| What you see | Cause | Do |
|---|---|---|
| `REFUSED: no game survived the fence, dedup and length rules` | every input game was fenced, undated, a duplicate or under 10 events | read the counts printed above it; check `--seasons` and `--leagues`; the archive alone is empty by design |
| `REFUSED: <name> already holds different content` | the tag is already built from other input or options | use a new tag (`-v2`) |
| `corpus verify`: `sha256 ... != pinned` | a shard changed after the build | rebuild under a new tag; never edit a shard |
| `FAIL ... is locked, but this is a train shard` | a shard holds a fenced night | do not train; rebuild; tell the coordinator, this is a bug |
| `FAIL vocab fingerprint differs` | `scripts/noggin_tokens.py` changed after the build | rebuild with the new layout, or check out the code the corpus was built with |
| `FAIL stats.json ... differs from a recount` | stats.json was edited or is stale | `python scripts/corpus_build.py stats MANIFEST --json` shows the true numbers |
| `REFUSED ... for training: N shard(s) are tuning-window data` | the reader was asked for `tuning` with `for_training=True` | ask for `train`, `valid` or `holdout`; evaluate on tuning with `for_training=False` |
| `refusing to write ...: not git-ignored` | `--out` is inside a repo that would track it | use the default `data/corpus/` or a folder outside the repo |

## 7. What v1 does not do

- No team or player id leakage report beyond game identity (one game, one shard; content hash unique). Teams recur across seasons by nature.
- nflverse and cfbfastR rows are not read (blocked and unverified in `scripts/history/licences.py`); NFL and college football come from ESPN.
- No resume inside a build; no parallel workers (one core). Both are cheap to add if the full run proves too slow.
- Nothing here trains, scores or promotes a model.
