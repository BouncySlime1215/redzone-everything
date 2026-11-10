# Personal intake (iPhone metadata), off by default

Why: decisions.md 2026-11-09 (personal data). Metadata only (app name and time, what was watched), local only, never committed.
The show does not read it yet. Data lands in `data/personal/` (git-ignored).

## Route 1: live app-open events (iOS Shortcuts)

Apple gives apps no access to other apps' usage, so the phone sends it. Needs the phone on the home Wi-Fi.

1. Mac: set `personal.enabled: true` in `data/config.yaml` and restart the app. Off: the endpoint answers 404.
2. Required: put `REDZONE_PERSONAL_TOKEN=<a value you choose>` in the Mac app's environment yourself and restart. Without it the endpoint answers 503 and writes nothing. Never paste it in chat or commit it.
3. iPhone: Shortcuts, Automation tab, `+`, **App**. Choose the app, tick **Is Opened**, pick **Run Immediately**, Next.
4. Add action **Get Contents of URL**. URL: `http://<MAC-ADDRESS>:<PORT>/api/personal/app-open` (the Mac's local address and the app's port, `server.port` in default_config.yaml; a `.local` name works).
5. Tap Show More: Method **POST**, Request Body **JSON**, add two Text fields: `app` = the app's name (type it), `timestamp` = a **Current Date** variable set to format **ISO 8601**. Exactly these two keys; anything else gets a 400.
6. Headers, add `X-RedZone-Token` = your value (required; a wrong or missing one gets a 401).
7. Done, repeat per app. Check: open the app, then `wc -l data/personal/events.jsonl` on the Mac goes up by 1.

Guard: the same LAN-only rule as every route (private addresses only, 403 otherwise), JSON only, 2 KB cap counted as the body arrives, token required.

## Route 2: exports you download yourself

1. Apple: privacy.apple.com, request a copy; Google Takeout: YouTube history as JSON; Spotify: extended streaming history. Unzip each into one folder (the importer opens no zips).
2. Run `python scripts/personal_import.py <folder> --dry-run`, then again without `--dry-run`. It prints counts only. A `--data-dir` that isn't git-ignored in this repo is refused; the default `data/` is.
3. Files over 50 MB, symlinks and unknown types are skipped. Re-running skips rows already stored.
4. Apple's export layout varies; only columns named in `scripts/personal_import.py` are read. If rows read is 0, tell the next session the header names.

## See it

`python -c "import json;from pathlib import Path;from redzone.personal.profile import profile;print(json.dumps(profile(Path('data')),indent=1))"`

Team words (optional): `data/personal/teams.json` as `{"nfl:ABBR": ["phrase"]}`. Never commit it.

## Remove

Delete `data/personal/`. Nothing else holds the data.
