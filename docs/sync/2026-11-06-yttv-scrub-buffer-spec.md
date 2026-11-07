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
