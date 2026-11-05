# YTTV sync tests: how to run them tonight

First command, any time (about 15 seconds, touches no stick):

```sh
cd /private/tmp/claude-501/-Users-nick-matta-Documents-Codex-2026-11-02-task-2-redzone/db4845a1-e6cf-4448-8874-21dc8f52883f/scratchpad/sync/yttv_tests
python3 run_all.py --dry-run          # 58 tests on the 2026-11-04 recordings; all 58 exit 0
python3 run_all.py --list             # the ranked try order
```

## What this is

58 tests in 5 families. Each one estimates **D**: how many seconds the YTTV picture is behind the real
game, per channel and per stick. Target: within 1 to 2 s, code only.

| family | count | what it uses |
|---|---|---|
| ui | 11 | the YTTV overlay (it is readable even though the video is black): title flips, scrub label, knob, Stats panel, Key plays |
| ads | 12 | ad-pod bursts in media_session, decoder bitrate and logcat around breaks |
| truth | 11 | the real-world clock: NBA pbp `timeActual`, ESPN wallclock, stick clocks |
| mechanics | 12 | how tuning, pausing, seeking and landing behave (is D stable per tune?) |
| fusion | 12 | combines every family's anchors into one D per channel and stick, and writes the report |

`manifest.json` lists every test with its rank, mode, needs, pass rule and dry-run verdict.

## Tonight's games on YTTV (checked against ESPN at 03:40 ET)

The plan assumed ESPN, TNT or ABC NBA games. **Tonight, Thu 11/5, there is only one national NBA game, and it is on NBA TV:**

| time ET | game | channel key | ESPN id | truth source |
|---|---|---|---|---|
| 6:00 PM | Wake Forest @ Vanderbilt (CBB) | `espn2` | 401909726 | ESPN wallclock only (truth_03) |
| 6:30 PM | LIU @ Seton Hall (CBB) | `trutv` | 401919804 | ESPN wallclock only |
| 6:30 PM | UConn @ Michigan (WCBB) | `fs1` | 401911737 | ESPN wallclock only |
| 8:30 PM | San Francisco @ DePaul (CBB) | `trutv` | 401909501 | ESPN wallclock only |
| **10:00 PM** | **Miami @ Golden State (NBA)** | **`nbatv`** | **401909937** | **NBA pbp timeActual (best)** |

TNF (JAX @ BAL, 8:15 PM) is on Prime, which is already solved. ESPN itself has no game tonight.

## Run order for tonight

Each line is one command. `--minutes` is how long each test watches. Read-only tests run up to 3 at a
time. Key tests run one at a time on the hidden stick.

1. **Before games, any time (about 5 minutes):** prep clocks and the truth cache.
   `python3 run_all.py --only truth_01,mechanics_12,truth_09 --minutes 5`
2. **Before games, app auto-switching paused (about 20 minutes):** landing repeatability and the key recipes.
   `python3 run_all.py --only mechanics_01,ui_10 --channel nbatv --minutes 10`
3. **6:00 to 8:00 PM, ESPN2 college game, read-only (about 30 minutes):** checks whether in-game ad pods exist, plus EPG title flips at 6:30 and 7:00.
   `python3 run_all.py --only ads_01,ads_11,ui_05 --channel espn2 --game 401909726 --minutes 30`
   (Matching pods to breaks in ads_02/03/04/06/10 only works for the NBA: `adslib.load_plays` filters `league="nba"`.)
4. **10:00 PM, NBA TV, read-only, the main event (about 45 minutes, spans Q1 timeouts and the end of Q1):**
   `python3 run_all.py --only ads_01,ads_02,ads_04,ads_06,truth_10,truth_05,truth_06,ui_05,ui_11 --channel nbatv --game 401909937 --minutes 45`
5. **During the NBA game, key tests on the hidden stick, at dead balls (about 30 minutes):**
   `python3 run_all.py --only ui_10,ui_07,ui_08,ui_02 --channel nbatv --game 401909937 --minutes 8`
   (This needs either auto-switching paused or `--allow-app-active`; see "Needs a decision".)
6. **After the game (about 1 minute):** truth checks and the report.
   `python3 run_all.py --only truth_02,truth_04,truth_07,fusion --game 401909937`

Every run ends with `fusion_12_report`: one row per channel and stick with D, sd, n anchors, the
families that agree, and PASS (sd ≤ 1 s), MARGINAL (≤ 2 s), FAIL, or INSUFFICIENT (n < 3).
It is saved to `results/fusion_12_report.md`. Each test's log is in `results/logs/<run>/`, plus `summary.json`.

## Ranked: most promising first

| rank | test | why it is near the top |
|---|---|---|
| 1 | ui_10 focus recipes | Run first. It finds the safe key paths that ui_02/03/06/07/08 need. |
| 2 | ui_07 Stats panel | If the panel follows the playhead, each score change gives a direct real-world D to about 1 s. |
| 3 | ads_02 pods vs timeouts | Read-only. Pod start and end against NBA timeout and restart walls gives real D. Its weakness: in-game ad pods were rare in the recordings. |
| 4 | truth_10 pods vs restarts | An independent check of ads_02 against the NBA truth line. Read-only. |
| 5 | ui_08 Key plays seek | Each Key play is a known real moment. Seeking to it pins D per play. |
| 6 | ui_05 title flips | Read-only and very stable (D_flip 18.14 s, MAD 0.10 s in the dry run). It measures delay against YTTV's EPG clock, so it needs one real anchor (2 to 5 above) to convert. After that it is free all night. |
| 7 | ui_11 zero-key monitor | Once C is known, it tracks D from media_session alone (dry run: sd 0.02 s over 5 hours). Run it after ui_05 has a result. |
| 8 | ui_02 scrub label | Reads the overlay MM:SS against the program start. A second, key-based route to C. |
| 9 | ads_06 bitrate lag scan | Read-only. Ad and break edges change the decoder bitrate. It needs about 45 minutes with 4 or more breaks. |
| 10 | mechanics_01 landing repeat | Shows whether one calibration per channel survives re-tunes (landing sd ≤ 0.5 s). It can run before the games. |

The rest follow in `--list` order. mechanics_02 (240 minutes) goes last, and fusion runs after everything.

## Safety rules (built into run_all and every test)

- **Key and tune tests touch only the HIDDEN stick.** Before each test, run_all reads
  `http://localhost:8778/api/state` (`health.agent.on_screen` together with `health.sticks[].role`). It needs exactly one on-screen stick, and both sources must agree.
- **A watchdog** polls the state every 2 s during a key test. If that stick becomes visible, or the
  state can't be read twice in a row, it kills the test (SIGINT, then SIGTERM, then SIGKILL). Every test
  also re-checks before each key press.
- If the on-screen stick is unknown, the key tests are skipped and read-only tests still run.
- By default, key tests are **skipped unless the app's auto-switching is paused**, so the app and a test never drive the standby stick at the same time. `--allow-app-active` overrides this.
- Read-only tests (dumpsys, logcat, screencap of the UI, HTTP) may read either stick.
- Never join mid-play: run key tests at dead balls only. Never Peacock. No secrets. No DRM bypass, no MITM, no root.
- Mac stays light: at most 3 read-only tests at once, polls ≥ 0.5 s, and all ads tests share **one** capture (`ads/ads_run_all.py`).
- `--dry-run` never touches a stick and writes `results/<test>.dryrun.jsonl`, so live results stay clean.

## Needs a decision

1. **Key tests and the app.** During games, auto-switching is normally ON, so key tests are skipped by
   default. Choose one: pause auto-switching for about 30 minutes while they run (TV stays put), or pass
   `--allow-app-active`. That risks the app pre-rolling the standby stick while a test has it paused or rewound.
2. **Tonight's channel.** The only NBA game with exact truth (pbp timeActual) is on NBA TV at 10 PM.
   Earlier CBB games on ESPN2, truTV and FS1 only have ESPN wallclock truth, at about 2 s p90 for college games. That is good enough to learn whether in-game ad pods exist, but not to grade 1 s accuracy.
3. **truth_08 and truth_11** (clock-reading to D) need `results/obs.jsonl` rows `{game, period, clock, obs_wall, channel}`.
   No test writes that file yet, so run_all skips both live. Decide whether ui_07 should write it.
4. **ui_06 captions** toggles CC on the hidden stick (`--enable-cc`). Earlier captures showed no
   caption text in the UI layer, so it ranks low. Keep it or drop it.

## Files

- `run_all.py`: the runner. `--only`, `--family`, `--game`, `--channel`, `--stick`, `--minutes`, `--dry-run`, `--no-keys`, `--allow-app-active`, `--list`.
- `manifest.json`: every test, ranked. Built by `work/make_manifest.py`, which also records the latest dry-run verdicts.
- Family runners still work on their own: `ads/ads_run_all.py`, `mechanics/run_mechanics.py`, `truth/run_truth.py`.
- `results/_smoke_20261105_0335/` holds a 30-second read-only smoke run from 03:35 (no game on). It was archived so it doesn't pollute tonight's report. `results/_builder_dryruns/` holds the builders' earlier dry outputs.
