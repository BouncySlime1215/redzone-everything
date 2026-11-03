# Tonight at home (about 30 minutes, mostly waiting)

Do these in order, **before the first game**. Everything runs in Terminal from the repo folder.
Steps marked ⏳ run by themselves; you can walk away.

```sh
cd ~/redzone-everything        # or wherever the repo lives on the Mac
```

## 1. Power and network (2 min)

- [ ] **Mac on wall power.** On battery, the resource governor slows background work and the
      deploy's test run takes much longer.
- [ ] **Both Fire TV sticks on their wall adapters**, not the TV's USB ports. A TV USB port cuts
      power when the TV sleeps, and the stick reboots.
- [ ] **Quit Tailscale** (menu bar icon > Quit). It takes over the home network's routes, so the
      Mac can't reach the sticks or the TV.
- [ ] Check: `.venv/bin/python -m redzone.network`. It should say you're **home**. If it names a
      route problem, run `.venv/bin/python -m redzone.network --fix` (it asks for your Mac
      password).

## 2. TV settings (3 min, with the remote)

The menu names are for a Samsung 6 Series; yours may differ slightly.

- [ ] **Find the TV's IP:** Settings > General > Network > Network Status > IP Settings. Note it
      down; step 4 needs it only if the TV isn't found by itself.
- [ ] **Anynet+ (HDMI-CEC) = On:** Settings > General > External Device Manager > Anynet+ (HDMI-CEC).
- [ ] **IP Remote = On:** Settings > General > Network > Expert Settings > IP Remote. On the same
      page, set **Power On with Mobile = On**.
- [ ] **Let the Mac connect:** Settings > General > External Device Manager > Device Connection
      Manager > Access Notification = **First Time Only**.

## 3. Deploy tonight's release ⏳ (about 25 min; no game live)

Run this only when **no game is on** and none starts in the next 15 minutes. If one is live it
just stops and says so (exit 2), and nothing changes. It:

1. runs the tests on the new code off to the side
2. switches over
3. restarts the app
4. watches its health for 10 minutes
5. rolls everything back by itself if things got worse

The Mac still runs this morning's code, which doesn't have the deploy script yet. So the first
time, this one command fetches the script and runs it (copy the whole line):

```sh
git fetch origin integration/release && git show FETCH_HEAD:scripts/deploy.py > /tmp/rz_deploy.py && .venv/bin/python -c "import sys,pathlib; sys.path.insert(0,'/tmp'); import rz_deploy as d; sys.exit(d.main(['deploy','--wait','60'], root=pathlib.Path.cwd()))"
```

From tomorrow on it's just `.venv/bin/python scripts/deploy.py deploy`.

The last line it prints tells you what happened:

| Exit | Meaning | What to do |
|---|---|---|
| 0 | deployed and healthy | go to step 4 |
| 2 | a game is live | run it again after the game (or let `--wait 60` wait up to an hour) |
| 3 | tests failed, nothing changed | nothing; the app keeps running the old code |
| 4 | it got worse and rolled back by itself | nothing; send the coordinator the last 20 lines |
| 5 | the rollback failed | `.venv/bin/python scripts/deploy.py status`, then tell the coordinator |
| 1 | refused | it prints why (edited files, a new requirements.txt, low disk) |

If it says `requirements.txt changed`, run `.venv/bin/pip install -r requirements.txt` and run the
command again with `'--deps-ok'` added after `'60'`.

## 4. Let the Mac switch the TV directly (2 min, watch the TV)

After step 3 finishes with exit 0 (not during its 10-minute watch):

- [ ] `.venv/bin/python scripts/tv_setup.py`. When the TV asks, press **Allow** with the remote. It
      then flips the TV between the sticks four times and asks **y/n** each time; answer what you
      see. Say **y** when it offers to turn HDMI-CEC off on the sticks.
- [ ] If it prints a cause instead (wrong subnet, IP Remote off, pairing denied), fix that TV menu
      from step 2 and run it again. If it can't find the TV, add `--ip <the IP from step 2>`.
- [ ] Restart the app so it uses the new path:
      `launchctl kickstart -k gui/$(id -u)/com.redzone.everything`

## 5. Check (1 min)

- [ ] On your phone, tap one game. It should appear in about a second, with no Fire TV home
      screen in between.
- [ ] `tail -1 data/tv_input.jsonl` shows `"verified": true` or `"trusted": true`.

## 6. Probes for the next upgrades (optional, a few minutes each)

Only run these if the file exists (`ls scripts/<name>`). They arrive with tonight's release once
they're merged.

- [ ] **YouTube TV multiview:** `.venv/bin/python scripts/multiview_probe.py`. It opens a
      multiview on the hidden stick and times expanding a tile.
- [ ] **Stick tuning and preflight:** `.venv/bin/python scripts/stick_tune.py preflight`. It
      reports signal strength, free memory, CEC and app versions, and changes nothing.

## If something looks wrong

- Undo the deploy: `.venv/bin/python scripts/deploy.py rollback`
- Stop the app touching the TV: open the phone page and tap **Pause**, or run
  `launchctl bootout gui/$(id -u)/com.redzone.everything`
- What's running: `.venv/bin/python scripts/deploy.py status`
