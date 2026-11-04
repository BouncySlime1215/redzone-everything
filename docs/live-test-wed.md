# Wednesday evening live test (about 30 minutes of your time)

The goal is to see the two big new pieces on real games and record where every switch really
landed:

1. **SmartThings input flips.** These are already ON on the Mac (RC4).
2. **The time-shift buffer.** Each stream is held B seconds behind live: NFL 60, NBA/NHL 90.
   Its North Star metric is entries mid-play: 0 in the sim, and NBA big plays seen 67.8 → 87.4 %.

3. **Sync data, collected by itself.** You do nothing for this. While the app runs, it logs what
   each stick exposes: playback state, buffering, Wi-Fi signal and link speed, and on the hidden
   stick any on-screen ad, countdown, clock or caption text. It presses nothing and pauses itself
   during switches. The log is `data/recordings/<date>/sync_probe.jsonl`, and Claude reads it from the
   Mac afterwards. The flips also now wait until the hidden stick has been playing steadily for
   about 1 s (PR #70), and every switch records whether its picture was playing within 3 s.

**It passes when** `enter == "play"` is **0** across every graded cut, the buffer log lines show
streams held near B, and no flip took longer than about 4 s.

Before you start, make sure the Mac runs a release that includes the buffer (PR #61; NHL feed
PR #58). Run `.venv/bin/python scripts/deploy.py status`. If the release doesn't have it, run
only part A and skip B.

All commands run from the repo folder on the Mac:

```sh
cd /Users/nick_matta/Documents/Codex/2026-11-02/task-2/redzone
```

The app answers on port **8778** (the installed service). If yours differs, change it below.

## Setup (5 min, before the games)

- [ ] **Usual checks:** sticks on wall power, Tailscale quit, and
      `.venv/bin/python -m redzone.network` says **home**.
- [ ] **Note the start time.** Everything below is counted from then:
      ```sh
      date +%s > /tmp/rz_test_start
      ```
- [ ] **Turn the buffer on** (no restart needed). Leave it off for part A:
      ```sh
      curl -s -X POST localhost:8778/api/settings -H 'content-type: application/json' \
        -d '{"changes": {"timing": {"behind_live_s": {"enabled": true}}}}'
      ```
      It should answer `{"ok": true, ...}`. Defaults are NFL/CFB 60 s and NBA/NHL/college 90 s,
      capped at 90 s, for YouTube TV and Prime only.
- [ ] *(Optional)* **NHL's faster feed:**
      `-d '{"changes": {"sources": {"nhl_primary": true}}}'`

## A. Flips without the buffer (10 min, 2+ live games)

Just watch. Let the app switch at least **4 times**.

- [ ] Each flip should change the picture within about **2-4 s** of the switch starting.
- [ ] Note anything odd in one line with the time, e.g.
      "9:12 flip took ~10 s" or "9:20 landed on a live play".

## B. Flips with the buffer (10 min)

Turn the buffer on (setup step above), then watch at least **4 more switches**.

- [ ] After each flip, the picture should sit about **B s behind live**. Check YouTube TV's LIVE
      badge (it shows how far behind you are) or the Prime clock against the score app.
- [ ] Note any flip that **landed mid-play**, showed a **black screen or spinner**, or sat
      **at live** (the buffer didn't hold).

## C. Optional: phone taps and the TV's local input keys (5 min)

Skip this part if time is short. Run it between games or during a long break, since it switches
the TV.

- [ ] **Phone taps.** On the phone, open a game that isn't on the TV, then tap **Watch this** and
      **Go now**. Count the seconds until that game is playing on the TV. Do it 3 times. The sim
      drill says about 8.5 s when the game wasn't preloaded and about 3 s when it was. Each tap is
      in `data/traces.jsonl` as a `decided` row with `"kind": "user"`.
- [ ] **Local keys vs SmartThings** (about 1 minute, it flips the TV 6 times):

  ```sh
  .venv/bin/python scripts/tv_local_probe.py --rounds 3
  ```

  It times each flip, local key vs SmartThings, until the TV reports the new input, and writes
  `data/tv_local_probe.json`. It changes no setting. If the TV shows **Allow**, press it. If the
  local keys switched every time, the fast path can be turned on later with `scripts/tv_setup.py`.

## Record the results (5 min, after the games or the next morning)

Grading needs about 90 s after each cut, so run these once the last switch has settled.

- [ ] **Where each switch landed** (the most important number: mid-play entries must be 0):
      ```sh
      .venv/bin/python - <<'EOF'
      import json, time
      t0 = float(open("/tmp/rz_test_start").read())
      rows = [json.loads(l) for l in open("data/supervisor_cuts.jsonl") if l.strip()]
      rows = [r for r in rows if r.get("ts", r.get("cut_at", 0)) >= t0]
      from collections import Counter
      print("cuts graded:", len(rows))
      print("enter:", dict(Counter(r.get("enter") for r in rows)))
      print("leave:", dict(Counter(r.get("leave") for r in rows)))
      for r in rows:
          if r.get("enter") == "play" or r.get("leave") == "play":
              print("MID-PLAY:", r.get("from"), "->", r.get("to"), r.get("leave_why"), r.get("enter_why"))
      EOF
      ```
- [ ] **How long the switches took** (cut to picture, and the flip itself):
      ```sh
      .venv/bin/python - <<'EOF'
      import json
      t0 = float(open("/tmp/rz_test_start").read())
      rows = [json.loads(l) for l in open("data/traces.jsonl") if l.strip()]
      rows = [r for r in rows if (r.get("cut_at") or 0) >= t0]
      for r in rows:
          pic = (r.get("picture_at") or 0) - (r.get("cut_at") or 0)
          flip = (r.get("flip_done") or 0) - (r.get("flip_start") or 0)
          print(f"picture {pic:5.1f}s  flip {flip:4.1f}s  rewind {r.get('rewind_s')}  "
                f"settled {r.get('settled')}  stall {r.get('stall_s')}  {r.get('to')}")
      EOF
      ```
- [ ] **Did the buffer hold?**
      ```sh
      grep -E "buffer: .* (held|didn't)" data/service.log | tail -30
      ```
- [ ] **Turn the buffer back off** if anything looked wrong:
      `-d '{"changes": {"timing": {"behind_live_s": {"enabled": false}}}}'`
- [ ] **Paste the three outputs and your notes** into the project chat. They become the real
      landing numbers for the release gate.

## If something goes wrong

- **Stop the app touching the TV:** tap **Pause** on the phone page.
- **Buffer misbehaving:** turn it off with the command above. No restart is needed.
- **Roll back the release:** `.venv/bin/python scripts/deploy.py rollback`
