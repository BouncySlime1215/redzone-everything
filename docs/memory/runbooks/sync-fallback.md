# Sync fallback: a join didn't land where we wanted

Nick's Nov 7 sync order, item 2 (fallback ladder) and item 9 (runbooks from real failures). Code:
`redzone/fallback_ladder.py` and `Controller._ladder_steps` / `Controller.ladder_descend` in `redzone/control.py`.
Flag `fallback_ladder.enabled` (OFF). Playbook rows LAD-01..LAD-11 in [playbooks.md](../../playbooks.md); tests
`tests/test_fallback_ladder.py`. Rule #1 is unchanged: every rung that changes the screen waits for a dead ball.

## The ladder (per stick)

| rung | what happens | shown on the phone as |
|---|---|---|
| buffered | the hidden stick plays the target B s behind live, **measured** (YouTube TV overlay label, Prime timecode or the pause on the stick clock) | "buffered, 61 s behind" |
| live | the same stick re-tuned at the live edge (through another channel if it was behind, #391), flipped at a dead ball judged on the live picture | "live edge ok" |
| other_stick | the visible stick tunes the target in place, after the safe wait (`other_stick_apps`, YouTube TV only by default: a Prime tune is ~32 s of spinner) | "other stick ok" |
| hold | keep the current game, press nothing on the visible stick | "kept the current game" |

**Default (Nov 7, Rule #1): `skip_rungs: [live, other_stick]`, so a failed buffered entry goes straight to hold.**
Both skipped rungs enter at the live edge, and the replay with the 25 s dead-ball re-check still adds NFL mid-play
entries (59.3 -> 61.0 per run at 10% scrub failures, 66.0 -> 68.7 at 25%). Set `skip_rungs: []` only after a grader
shows 0 added.

Each climb is one row in `data/ladder.jsonl` (stick named by its HDMI input, never an address). The phone's "Now"
screen shows the last climb that fell back for 15 minutes, and any rung paused on a stick. A rung that failed twice
on one stick in 10 minutes is skipped on that stick for 5 minutes (`skip_after`, `window_s`, `cooldown_s`).
"Buffered" is never shown without a measured position.

## Real failures, Nov 5-7, and what to do

**1. No label read after the scrub (Nov 7, #385).** The post-tune LIVE banner covered the scrubber, so no time label.
- Ladder: buffered fails ("banner: no scrubber" / "no label read") -> live edge on the same stick (LAD-05).
- If it repeats on every tune, the buffered rung pauses on that stick (LAD-10); look for a YouTube TV UI change.
- Stick check (hidden stick only, no game on it): `python scripts/yttv_scrub_try.py <hidden stick> 60 --label`.

**2. A preload overwrote a scrubbed standby (Nick's run, Nov 7).** The planner re-tuned a hidden stick that had just
been scrubbed 60 s behind, so the flip came up live.
- Fixed by `_scrub_protected` (only its flip, a stale label or a phone pick replace it); LAD-08 keeps it fixed with
  the ladder on. If you see it again: `grep scrubbed data/buffer.jsonl` then the next `preload` line in the log.

**3. The standby kept choosing an UNAVAILABLE Prime game (Nick's live test, Nov 7).** The detail page said
UNAVAILABLE; every preload retried it.
- `two_stick.unavailable_backoff_s` keeps that target off the standby. On a switch: live fails, the other stick is
  refused for Prime, so the ladder holds with the screen unchanged (LAD-07).
- Check the account can play it on the visible stick by hand between games. Never during a game.

**4. Manual hold stuck on "you locked this game" (#381, open).** A locked game with no reachable channel retried every
0.5 s (13 failed switches in 6 s, Nov 4) and later sat 210 s on the hold.
- Engine now says "No channel to tune for X" once. #381 adds "why, retry, then let go" (flag off). The ladder's hold
  rung never retries on its own; the decider's next pick does.
- If the phone still shows it: release the lock on the phone, then check `data/channels.json` has the channel.

**5. Prime "playback not confirmed" (Nov 5-6).** The switch ran past `control.max_switch_s` (150 s) and the engine
gave up: usually the Prime detail page started instead of the player (#390, one focus move, flag off).
- Ladder: the hidden stick's tune failed -> other stick refused for Prime -> hold. The standby check re-tunes later.
- Stick check between games: open the game on the hidden stick and read `dumpsys media_session` (state 3 = playing).

**6. Same-channel deep link didn't reset a stick that was behind (Nick's 90 s run, Nov 7, #391).**
- The live rung's tune goes through another channel first (`yttv_seek.reset_channel`); LAD-09.

**7. Plain pause snapped back to live 20-30 s after resume on YouTube TV (Nick, Nov 6 21:45Z).**
- YouTube TV buffers by scrub only. A pause buffer that didn't hold is logged as "buffered failed (pause did not
  hold) -> live" on the ladder; never shown as buffered.

**8. The label said we were off target after entry (join watchdog, item 1).**
- The watchdog re-seeks once, then calls `Controller.ladder_descend(target, "buffered", why)` (LAD-11): live at a
  dead ball, else a fresh standby at the live edge, else keep the picture but stop claiming a buffer (held = 0).

## Turning it on

Only after a hidden-stick night with the flag on, while Nick isn't watching:
`fallback_ladder.enabled: true` in the Mac's config, restart, then read `data/ladder.jsonl` after the night
(`python -c "import json;from redzone.fallback_ladder import summarize;print(summarize([json.loads(l) for l in open('data/ladder.jsonl')]))"`).
