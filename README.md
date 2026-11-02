# RedZone Everything

Your own RedZone for every sport. A program on your Mac watches every live NFL, NBA, NHL and MLB
game through free data feeds, scores how exciting each one is right now (0–100, with a plain-English
reason), and switches your TV to the best moment — **never in the middle of a play** — prioritizing
your Washington teams, your bets and your fantasy players. Commercials get filled with catch-up
replays of the big plays you missed. Your phone is the remote and the dashboard.

- Dry-run by default: it shows what it **would** do until you turn TV control on.
- $0: free data sources and free tools only.
- Runs on your Mac; portable to a Raspberry Pi later (no Mac-only code in the core).

---

## 1. Setup (Phase 0 — about 30 minutes, once)

1. **Mac tools** (already done on this Mac): Python 3.12, Homebrew, `adb`.
   ```bash
   brew install --cask android-platform-tools
   ```
2. **Turn on ADB on each Fire TV stick** (with the TV remote):
   1. Settings → My Fire TV → About → click the device name **7 times** ("You are now a developer").
   2. Back → Developer Options → **ADB Debugging: ON**.
   3. Keep the TV on — the first connection shows "Allow USB debugging?" → choose **Always allow** → OK.
3. **Same Wi-Fi:** the Mac, both sticks and the TV must be on your home Wi-Fi. If a VPN is on, turn it off
   while using RedZone (Tailscale is usually fine).
4. **Run the setup checker** — it finds your sticks and TV, reads each stick's model and OS (Vega OS sticks
   can't be controlled, so you'd get single-stick mode), finds the app package names, and which HDMI port
   each stick is on:
   ```bash
   .venv/bin/python scripts/doctor.py
   ```
   If a stick isn't found, add it by IP (Settings → My Fire TV → About → Network on the stick):
   ```bash
   .venv/bin/python scripts/doctor.py --ip 192.168.1.42
   ```
5. **Link your YouTube TV channels** (≈10 minutes, once): on your computer open tv.youtube.com → Live,
   hover a channel → right-click → Copy link → paste it in the phone page under Settings → Channels
   (or `scripts/rz.py link espn "<link>"`). Do ESPN, ESPN2, ABC, CBS, FOX, NBC, NFL Network, NFL RedZone,
   TNT, truTV, TBS, NBA TV, NHL Network, FS1. Sunday Ticket games and Prime/ESPN+ events are found
   automatically each day.
6. **Optional — ESPN fantasy:** put your league ID in Settings → Fantasy. Private league: copy the
   `espn_s2` and `SWID` cookies from fantasy.espn.com into `.env` (see `.env.example`).
7. **Optional — two-stick mode:** needs both sticks on Fire OS and the TV's HDMI-CEC (Anynet+) on.
   If input switching over CEC doesn't work on your TV, add a SmartThings token to `.env`.

## 2. Running it

```bash
.venv/bin/python -m redzone
```
It prints your phone address (`http://<your-mac>.local:8777`) and a QR code. Open it on your iPhone.

Start automatically at login (and restart if it ever crashes):
```bash
scripts/install_service.sh          # install
scripts/install_service.sh remove   # uninstall
```
Logs: `data/redzone.log`.

**Keep the MacBook lid open** (or plugged into a display): closing the lid puts the Mac to sleep and
the TV stops switching. While games are live the app runs `caffeinate`, so the Mac won't idle-sleep; when
nothing is live it lets the Mac sleep normally.

## 3. Using the phone page

- **Now** — what's on and why, the Focus dropdown, Go back · Skip · Pause, and More (Lock, Mute, Boost,
  👍/👎, "I just saw it").
- **Focus** — *Autopilot* (best game anywhere), *Focus on a game* (stay on it during action; visit other
  games/replays only during its breaks, back before the next snap), *Focus + must-see* (also leave for
  something huge elsewhere).
- **"I just saw it"** — tap the instant a play ends on your TV. That measures your stream delay, which is
  how the app knows what your screen is showing. Do it 3–4 times on each app the first night.
- **👍/👎** — every vote is training data for the tuner.
- **Bets** — add a bet in ~20 seconds; games where your bet is sweating get boosted.
- **Lab** — backtest report cards, the auto-tuner, and the Screen Lab buttons.

## 4. How it decides (plain English)

1. **Score every game every second.** Score = how much the game matters (records, spread, rivalry,
   your teams, bets, fantasy) × how intense this moment is (field position, down & distance, time, score,
   power plays, leverage, runs, comebacks). Neutral games top out at 90; 90–100 is for games you care
   about. Blowouts and stoppages score low.
2. **Know what your screen shows.** Feeds report each play ~5–10 s after it happens; your stream shows it
   ~35–60 s after it happens. The app tracks that delay per channel, so it knows when a play is airing on
   *your* screen.
3. **Hard rules:** never cut while a play is on screen; never leave a locked moment (red-zone drive, power
   play, final two minutes of a close game) except for something much bigger; stay at least ~45 s; switch
   only for a clear margin; at most a few switches per 5 minutes; freeze if it starts flip-flopping.
4. **Commercials:** when your game goes to break, visit the best live moment elsewhere or play catch-up
   replays of big plays you missed, then come back just before your game resumes.
5. **Catch it live:** when a big play happens in another game, the app has a ~30 s head start before it
   airs — if it's safe to leave, it goes there so you see it live.

## 5. Tuning the weights

Everything lives in `redzone/default_config.yaml`; your changes are saved to `data/config.yaml`
(validated, versioned, one-tap undo). Edit from the phone (Settings → Advanced) or the file.

Most useful knobs:

| Setting | What it does |
|---|---|
| `score.personal.my_team` | Washington boost (default 25) |
| `score.personal.bet_max` | how much a sweating bet adds (20) |
| `decider.switch_margin` | how much better another game must be to switch (10) |
| `decider.min_dwell_s` | minimum time on a game (45 s) |
| `decider.max_switches_per_5min` | busy-ness cap (4) |
| `decider.break_destination_min` | what's worth visiting during a commercial |
| `replay.*` | catch-up replays: window, clip length, rewind step |
| `delay.default.*` | starting stream delay per app before you calibrate |

**Test a change before it touches your TV** — replay real past games through the brain:
```bash
.venv/bin/python scripts/eval.py --slates          # NFL: 16 real 2025 Sundays
.venv/bin/python scripts/eval_sports.py nba        # NBA: every archived night
.venv/bin/python scripts/eval_sports.py nhl
.venv/bin/python scripts/backtest.py 20251214      # one slate, with a "would have switched" timeline
.venv/bin/python -m redzone.lab.optimize           # auto-tuner (search settings on past games)
```
Report cards grade: mid-play cuts (must be 0), commercial time, big plays seen (live or replay),
crunch-time plays seen live, your team's crunch time, switches per hour.

## 6. Safety rails

- Dry-run default; TV control needs a confirmed tap.
- LAN only: the server refuses anything not on your home network. Secrets live in `.env` (git-ignored).
- Kill switch: Pause. Safe mode kicks in after 3 errors in 5 minutes and holds still until you tap Resume.
- Every tune is confirmed by the stick's own playback report; a failed tune retries once, then reports.
- Config edits are validated against limits and versioned; a broken file falls back to defaults.
- Feeds: polite polling, caching, backoff on 403/429; a source that goes backwards gets benched.
- Two-stick mode flips the TV input only at a safe moment, and drops to single-stick if a flip fails.
- Nothing records, saves, or rebroadcasts stream video. Optional microphone calibration keeps only
  loudness numbers in memory.

## 7. Data sources (all free, unofficial — they can change without notice)

ESPN site API (scoreboards, play-by-play with timestamps, win probability), NBA liveData (S3 mirror),
NHL api-web (power plays, empty nets), MLB statsapi (leverage), ESPN Fastcast (push), Apple TV / ESPN
Watch event listings (Prime Video and ESPN+ event links). Real Sports is not used: it requires a login
and its terms forbid automated access.

## 8. Project layout

```
redzone/
  engine.py        the producer loop (poll → merge → score → decide → act → phone)
  scoring/engine.py   0–100 excitement + reasons
  decider/         switch brain (decider.py) and "what's on my screen" (screen.py)
  sources/         ESPN, league feeds, merge/race, event links, http
  tuners/          adb Stick + per-app tuners (YouTube TV, Prime Video, ESPN)
  control.py       single-stick and two-stick hot standby
  tv.py            Samsung input + volume
  channels.py      which channel/app each game is on (your market, Sunday Ticket, overrides)
  bets.py fantasy.py  personal boosts
  recorder.py      flight recorder: real feed lag per source
  lab/             backtests, graders, optimizer
  web/             FastAPI server + phone page
scripts/           doctor, rz (stick tools), screen_lab, eval, backtest, downloads, service install
tests/             pytest (scorer, decider, sources, control, app, audio, fantasy)
```

## 9. Troubleshooting

- **Phone page won't load:** same Wi-Fi? Try the Mac's IP instead of the `.local` name.
- **"unauthorized" stick:** approve the prompt on the TV (pick Always allow), rerun `scripts/doctor.py`.
- **Wrong game/channel:** tap the game → Set channel. The app remembers your correction.
- **It cut mid-play:** tap 👎 right away, then calibrate with "I just saw it" a few times.
- **Prime says unavailable in your region:** that game is blacked out for your area; the app marks it.
