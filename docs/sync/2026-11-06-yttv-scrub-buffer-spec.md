# YouTube TV scrub buffer: build spec (Nov 6, 2026)

Evidence: [live-edge tests](2026-11-05-yttv-live-edge-tests.md), Nov 6 update. Nick's order: solve YouTube TV first, then the switching model. Everything ships behind flags, Rule #1 unchanged.

## Measured facts

- Enter the buffer: `DPAD_CENTER`, N x `DPAD_LEFT` (15 s each), `DPAD_CENTER`. N=4 lands about 60 s behind. About 10 s over adb today.
- 7 of 7 repeats held on ESPNU, ACC Network, TNT, on both sticks; one 20-minute hold on TNT.
- Pause does not hold. Rewind and skip keys do nothing. `MEDIA_PLAY` and `BACK` as the commit key are wrong; `MEDIA_PLAY_PAUSE` also commits.
- `media_session` position is wrong after a seek. Never use it for behind-live once seeked.
- Overlay time label is the truth meter. A read needs CENTER, LEFT, RIGHT, CENTER and adds about 8 s of delay.
- Scrub stopped moving back near 2.5 min behind on ACC Network; confirm per channel.
- Previews are blank within about 60-75 s of live. Scores are OCR-readable from previews.
- Key presses are only safe when focus is known: bare LEFT without the overlay does nothing, and pressing on the wrong app hits Prime.

## Build

1. `redzone/tuners/yttv_seek.py`: `enter_buffer(stick, seconds)` with focus check (the #356 guard), label read before and after, returns measured behind_live_s or fails closed and re-links to live.
2. Label reader: on-device OCR from an in-memory or temp screenshot, deleted right away; numbers only.
3. Delay model: `delay = D_edge(channel) + behind_live`, where behind_live comes from the label, not media_session. Do not let `delay_doubt` reset a measured channel.
4. Switch path: seek the hidden stick before the flip, then flip at a dead ball on the delayed picture.
5. Grader: charge the real entry time (about 10 s, plus the label read), not 97-120 s and not zero. Replace the withdrawn 75 s number in #372.
6. Manual tap bug: `hold` on another game sat 210 s on "switch: you locked this game" twice. A user tap must not wait forever; show why it is waiting.
7. ESPN app links stay off (0 of 15 opened).

## Still to prove

- Flip onto a scrubbed stick, and a person looking at the picture.
- Running-clock delay reads to 1-2 s.
- Live game ad breaks measured directly.
- Back limit per channel, return to live, and session timeouts over a full night.

Frames: read, then delete. YouTube TV video stays black; no DRM bypass.

## Update, 10:50 PM: Scores & stats panel and Key plays

- **Open it:** `DPAD_UP`, then `DPAD_CENTER`. Tabs: Scores, Stats, Key plays. Text is crisp and OCR-readable.
- **Scores tab:** score and game clock for many games at once. It refreshes in lumps about every 30 s, 50-89 s after ESPN wall times, so it is too coarse for a 1-2 s delay. Not yet tested whether it follows the playhead.
- **Stats tab:** the current game's score, period and clock.
- **Key plays tab:** a list of plays with game clock, plus a filter by team and player. Selecting a play plays it, then moves on ("11 of 12 key plays"). First use shows "Auto-play key plays?": choose Ask Later, no settings change.
- **Why it matters:** this may give every key moment of a game on demand, on the stick, with no feed timing at all. It fits Nick's "follow every game, get all the key moments". Needs testing: how fast a jump is, how it returns to live, how soon a play appears after it happens, which channels support it.
- **Delay:** preview thumbnails lag their labels (score anchors disagree, 32-62 and 54-78 s), so don't use them for delay. Best honest delay is still about 25 s +-3.
