# Cut a release candidate

Lane 4 cuts an RC; the overseer (or Nick) deploys it with [deploy.md](deploy.md). Cutting is a no-ff merge of the
exact `integration/next` SHA the Integrator handed over into `integration/release`. It never touches
`codex/resume-redzone` or `main`.

## RC history

| RC | integration/release | Cut (UTC) | Notes |
|---|---|---|---|
| RC1 | 9e859b7 | Nov 3 21:48 | |
| RC2 | 5c7727e | Nov 3 23:45 | |
| RC3 | 3a36e51 | Nov 4 02:53 | |
| RC4 | 8a47053 | Nov 4 03:33 | |
| RC5 | 4602c63 | Nov 4 15:11 | failed the Mac deploy |
| RC5.1 | e8bbf8e | Nov 4 16:19 | |
| RC6 | cf480a4 | Nov 4 23:34 | failed the Mac test step (stale city map tag; #193) |
| RC6.1 | 9322c28 | Nov 4 23:39 | live 9:14 PM ET Nov 4; two false health rollbacks (benched feeds counted as failing; #232) |
| RC6.2 | 1bf4150 | Nov 5 03:40 | tree = next 90b6c98; #232 health fix, #227, #229, #231, grader v10 lines; deployed 10:55 PM ET Nov 4, TV off |
| RC6.3 | 39597ea | Nov 5 20:58 | tree = next 88a3b0b, graders v31; RC7 gates not met, so a point release. NHL EXP-303 package, #310 YTTV timed taps, #281 edge reader and fast_feeds in shadow, rc8_on off; Nick yes on the card 3:55 PM ET |
| RC6.3.1 | a8bf14f | Nov 7 17:20 | tree = claude/live-b490-locals 8841ebd0: the live **dev build** b490ab86 (integration/stick-test-20261107b, not an integration/next SHA) + #406 locals by search; deployed 12:11:34 PM ET by the overseer on Nick's go (12:08 PM), live-game gate bypassed while auto-switching was paused; `tuners.yttv_local_search` on via the Mac settings API, false in the tree |
| RC6.3.2 | 932a636 | Nov 7 20:25 | tree = claude/leave-dead-windows 322b71b7 = hotfix/local-search-joinlive be1ed253 (on RC6.3.1: #407 settle, FOX 50 live-row walk + Join live, purchase-guard fixes, edge prior 34.9 s) + the decider.leave double-block fix; deployed 2:49 PM ET by the overseer on Nick's go, HEALTHY. Runtime flags on the Mac only: delay.edge_prior.enabled, decider.leave.dead_windows [cfb, nfl, nhl, nba, cbb], tuners.yttv_local_search, replay, margin 1 |
| RC6.3.3 | cb772c7 | Nov 7 21:20 | tree = claude/live-jump 1a31d40d = RC6.3.2 322b71b7 + jump mode (#415 379c80d4 cherry-picked); live since 3:42 PM ET, healthy; `jump_mode.enabled` true on the Mac. NOT in it: the quiet-break code (#414, on integration/next); only its decisions line came along |
| RC7 | | | after the sync, Prime and mid_break gates pass |

Add a row here **and** a `"<sha7>": "RCx"` line to `docs/releases.json` (the phone's "Last night" card) at every cut.

## Every cut

1. **Window.** No deploy, switch or restart and no Mac-side experiment while games are live and Nick is watching
   (Nick, Nov 4 10:55 PM ET). Cut early enough that the deploy fits in a no-game window; the Mac stays on wall power
   on game nights.
2. **Exact tree.** Take the SHA from the Integrator. Check it out in a checkout **outside `/tmp`**
   (`test_adb_session` asserts `/tmp` is not in the path, so a `/tmp` checkout shows one false failure). Copy `data/`
   from a checkout that has the eval pin and run `python scripts/fetch_eval_data.py --check`.
3. **Gaps check on that tree.**
   - `python -m pytest -q` (all green; the Integrator runs it too, and both must be green).
   - `python scripts/contract_check.py` equals the baseline (477 on Nov 5); `python scripts/citymap.py --check`;
     `python scripts/check_memory.py`; `ruff check .`.
   - `python scripts/grading_check.py`: G1 green, the freeze version recorded (v10 on Nov 5).
   - North Star, same grader on both trees:
     `python scripts/northstar.py --split dev --no-write --json head.json` and the same with
     `--root <checkout of the live RC>`. Mid-play entries, high-leverage leaves, plays missed on entry and dead air
     must not get worse; post the before/after table.
   - **Flags as staged.** Read each flag from the candidate's own config (`Config(user_path=<tmp>).get(key)`), not
     from memory: every new flag ships at the value its PR and decisions.md line say. From RC8 (#331) on:
     `timing.behind_live_s.rc8_on` is false in every league. After the deploy, on the Mac,
     `python scripts/rc8_flip.py --status` must show rc8_on false for every league. Nick flips leagues himself
     (`rc8_flip.py <league> on --seconds <B>`, rollback `... off`, `--dry-run` first); a flip takes no deploy.
4. **Merge.** `git checkout -B rc-cut origin/integration/release && git merge --no-ff <sha> -m "RCx: merge
   integration/next <sha> into integration/release"` with the contents, checks and session trailer in the body.
   `git diff <sha> HEAD` must be empty. Push `rc-cut:integration/release`.
5. **Hand over.** Post the release SHA to the Integrator, the coordinator and this thread. Do not deploy.
6. **Deploy tool.** The Mac runs the deploy.py of the checkout it already has. When the RC changes deploy.py, run the
   new one with `git show <sha>:scripts/deploy.py > scripts/_deploy_rcx.py` (from RC6.2 on, the live one already
   has the benched-feed health rule, #232).

## RC7 gates (Nick, Nov 4 9:45 PM ET; grader freeze v10)

RC7 ships only if all three pass on locked nights plus real hardware. Otherwise the candidate is an RC6.x point
release (that is why the Nov 4 candidate shipped as RC6.2: mid-commercial joins 35 % on locked; sync and Prime pending).

- [ ] `press_to_tv_p95` ≤ 3 s on real presses, **and** the Prime sub-gate: presses tagged `app: prime` have their own
      p95 ≤ 3 s over ≥ 5 presses, and no Prime press that never reaches the TV. Source: lane 3's real-TV Prime press
      run Thursday afternoon, before games, with Nick's OK.
- [ ] `delay_err_p95` ≤ 2 s per stick, worst stick decides, from `data/sync_truth/*.jsonl`: both sticks ≥ 200 rows
      over ≥ 2 nights (manual taps dropped). Lane 2's sync fix is the RC7 top item.
- [ ] `mid_break` = 0: no entry lands in a commercial break with > 10 s of dead time left (`cutgrade.BREAK_TAIL_BAR`).
      Mid-play entries stay a separate hard 0.

**Sync program** (Nick, Nov 5 2:10 AM ET: sync is the #1 priority after Rule #1; lane 2 owns, lane 3 grades, lane 4
integrates). RC7's sync part is lane 2's #281, and it ships only if Nick picks it on the RC7 sync card:

- [ ] `sync.edge.enabled: true` (shadow, read-only): one `cat /proc/uptime; dumpsys media_session` per stick per
      second through `tuners/adb.EDGE_READ`. It never sends a keyevent. Check that K3 holds at the baseline, and that
      the CI RAM/CPU row (tick p99, RSS, CPU) is not worse than RC6.2 with it on.
- [ ] `sync.edge.force_live` and `sync.edge.force_live_visible` ship **false** (they re-tune). Turning them on is a
      separate decision with its own decisions.md line.
- [ ] Rows land in `data/sync_truth/edge/<night>.jsonl` (the edge half only; outside the v10 `*.jsonl` glob), and
      lane 3's syncgrade (#279) reads them. After the first night, check that both sticks logged rows.
- [ ] `tests/conftest.py _no_real_devices` is on the tree, so the Mac deploy's test step can't run the real adb
      next to the live sticks.

RC7 also checks:

- [ ] **Second "Go now" tap** (Nick, Nov 4 10:55 PM ET): one tap waits for a dead ball; a second tap switches at
      once, even mid-play. The forced entry is counted on lane 3's forced go-now line, never as a decider mid-play
      entry and never as a training label. Confirm on the candidate that the mid-play line excludes it and that a
      test covers both the one-tap and the two-tap path.
- [ ] League-scoped defaults: EXP-203 for NFL is in, and NBA/NHL hold the set Nick approved (NHL: rule #1 wins).
- [ ] RC6.2 baseline for the North Star table: dev mid-play 0.48 per 100, dead air 19.3 %, entries at a possession
      start 97.3 %.
- [ ] Cut by Thursday afternoon so the overseer can deploy before TNF (8:15 PM ET) in a no-game window.
