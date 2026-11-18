# Provider routing and navigation evidence

The router compares known broadcast channels and matched event links for each game. A
launchable link is a routing candidate, not proof that the device displayed that game.
Missing links, unavailable subscriptions, unreadable profile controls, and unsupported
provider layouts remain explicit failures rather than guessed selections.

## Implemented safeguards

- Conflicting ESPN event IDs are rejected. Name-only event matches use the game's expected
  start time; ambiguous repeated matchups are withheld. Alternate broadcasts of the same
  identified event remain candidates.
- All known YouTube TV broadcast options participate in selection. Sunday Ticket market
  restrictions and the user's disabled-subscription setting remain in force. NFL afternoon
  routing uses Eastern time independently of the Mac's timezone.
- Exact profile preferences live in local configuration. Automatic profile selection
  requires readable profile labels and focus; inaccessible Cobalt controls stop for owner
  selection. The selected household names are not stored in source defaults.
  Prime search handles a cold picker through this guarded selector and reopens search once
  after the exact profile is selected. This path has automated coverage; the actual Prime
  picker still needs a readable-focus device proof before claiming unattended cold starts.
- Media-session reads retain title, subtitle, description, speed, and read timestamps.
  A static playback base position is not a frozen-video diagnosis. Fresh matching title
  metadata establishes only the named matchup, not its date, live status, or video frames.
  An explicit zero playback speed cannot confirm readiness even if the provider reports
  `playing`: this combination was observed during a real YouTube TV reload.
- Local Apple Vision OCR can read exposed menu/player overlays when accessibility text is
  empty. Images stay local. Protected video planes remain black; OCR does not recover them.

## Bounded Prime replay trial

`scripts/prime_search_trial.py` is a supervised diagnostic, separate from the live engine.
It accepts a query, two displayed team names, and a recording date. It uses the observed
Prime search layout, checks geometric focus before selecting a matching Replay card,
and requires the foreground detail panel to show the correct full date, teams, and included
access before selecting Watch Replay. It does not buy, subscribe, pair devices, change
display inputs, or enable live mode.

Example, after selecting an already authorized device:

```sh
.venv/bin/python scripts/prime_search_trial.py \
  --host SELECTED_ADB_SERIAL --query Clippers --home Warriors --away Clippers \
  --date 2026-11-01 --out data/lab/prime_trial.json --execute --play
```

The initial search must visibly reset to the observed empty-query layout. The traversal
through its six-column keyboard is specific to that observed layout; it is not a universal
Prime API. A changed or unreadable layout stops the trial. Search cards without years are
restricted to the current-year trial; the detail panel must still confirm the full date.

After the single Watch action, the runner waits through black/rating frames and can request
the player overlay with at most two delayed, nonselect Down presses. Success requires a
fresh matchup title near the top, progress/duration controls near the bottom, no remaining
search/detail menu, and a playing media report. The report still sets `video_verified=false`.
Capture ordering is checked; repeated image contents alone do not imply a stale capture.

## Physical results so far

The first attempt stopped at search launch because the stick was in its screensaver. No
selection was sent. The runner now wakes the stick and verifies an Awake report first.

The second attempt autonomously woke the stick, searched, identified the November 1
Clippers–Warriors replay, checked the full date and included-access message, and selected
Watch Replay at **24.916 seconds**. Player controls appeared, but the title did not appear
before the final check expired. The runner correctly reported failure at **43.354 seconds**.
Its early overlay request occurred during the rating screen.

The next attempts exposed two startup-loading races (search history before recommendation
rows; query suggestions before sports cards), then Prime's change from Watch Replay to
Resume after viewing progress was created. Each stopped without blindly selecting an
unknown control. The runner now waits for the required observed states and recognizes the
focused Resume button. The saved detail screenshot passes foreground identity validation
and is rejected as a player screen despite containing multiple airing times and matchup text.

**The sixth attempt passed the bounded navigation proof in 29.686 seconds.** It started
from the provider menu, performed its own search and selection, verified the full November 1
date and included access, selected Resume, and read `Clippers vs. Warriors / Warriors
Broadcast` on the player overlay with progress/duration controls. Prime also reported
playing. The game was left playing, with no further navigation. No overlay probe was needed
on this successful resume run. This verifies one unattended route on the observed layout;
it does not verify video frames, live latency, or every game/provider.

Local reports `data/lab/prime_search_clippers_warriors*.json` retain all attempts and their
timed steps. They are excluded from source upload. The revised checks also cover delayed
rating overlays, foreground-only identity, stale captures, and repeated selections in tests.

A second matchup, Raptors–Lakers on November 1, passed in **35.367 seconds** without
manual selections. A subsequent October 31 request sent **no selection presses** and
stopped after **35.954 seconds** at access-related recommendation content while searching
past mismatched dates. This demonstrates refusal to select the wrong date, not an
exhaustive proof that no matching recording exists. Restoring the November 1 Raptors–Lakers
recording then passed in **41.432 seconds**. A cold OCR compilation race exposed by the
first Raptors attempt is now prevented by preparing local OCR before capturing device frames.

These observations remain limited to the observed Prime layout. Device recovery and
a live-game trial with independently measured display timing remain required. Later HDMI
tests caused the hidden Prime session to pause; an activity-only resume did not claim the
TV input, and a hidden media Play command did not restore ready playback. A previously
successful player must therefore be checked again before any future input switch.

## App integration

The Lab now offers an explicit recorded-playback session for a saved Fire OS stick, query,
two displayed team names, and date. It uses the same bounded Prime navigator. Starting a
session drains existing controller/discovery work and pauses live decisions before enabling
the selected device. No other stick is connected or scanned by this path. A persisted guard
forces dry-run and paused mode after a process crash during recorded playback.

While the session is active, the app displays recorded progress and identity evidence;
live-feed timing, calibration, manual live selection, and autonomous live switching are
isolated. Exiting returns to dry-run/paused mode and leaves the physical picture alone.
The session lifecycle has automated coverage; an end-to-end run through the installed
service/UI is still required. The physical runs above exercised the shared navigator CLI.

Prime replay navigation is not a live fallback tuner and does not establish YouTube TV
library coverage or every provider/game. Fast input control, exact live identity, hidden
player readiness, and independently measured display timing remain separate requirements.

## YouTube TV recorded-session integration review

The recorded-session API and Lab remain Prime-only. A YouTube TV option would
currently promise a complete route that the implementation does not provide.
`run_yttv_search` opens search and inspects a Library result row or a team's
Completed catalog. `finish_yttv_watch` starts later, with an already focused,
dated Watch action. These are different entry states and cannot simply be
chained together.

The archived November 2 navigation frames show two distinct paths:

- Search → Washington Commanders → Completed exposes both team names and
  “Recorded 19 hours ago.” Selecting that card opens the replay chooser, but
  the caption does not establish the calendar event date.
- Library → a Washington Commanders team tile → its side panel exposes
  “Watch Nov 1 • WAS vs PHI.” The tile supplies recent-recording evidence;
  the focused side-panel action supplies the matchup and month/day. The year
  remains an explicit inference, not a year read from the provider.

The second route is the promising integration path. Before enabling it in the
app, the navigator must demonstrate entry into Library from the actual starting
screen, find the requested team's exact tile through a bounded traversal, retain
only that tile's caption, and open the side panel after fresh focus confirmation.
It must stop if the latest Watch action names another game or date; the current
evidence does not show how to select an arbitrary older dated event. Team
abbreviations must come from an identified game/team record or explicit supplied
aliases, never from logos or arbitrary abbreviation guesses.

The archived Start-from-beginning screen also shows that the chooser heading
may disappear while the focused action remains visible. The helper currently
requires the heading again immediately before selection and may safely stop on
that layout. A device-observed transition and scoped foreground geometry are
needed before broadening this check; simply accepting a matching string anywhere
would weaken the selection guarantee.

Once the complete navigation path exists, integration should reuse the existing
recorded-session lock, discovery suspension, restart guard, selected-stick
connection check, configured profile propagation, and revocable `may_run` guard.
Provider selection must choose a complete runner and compatible proof schema.
The UI must display explicit date evidence separately from inferred year,
transport readiness, player-title observation, and unverified video frames.
Required acceptance cases include a second matchup, a nonmatching requested
date, missing recording, cold profile picker, changed focus, and control revoked
between capture and selection. No new provider control was exposed by this review.
