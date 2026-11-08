---
id: R-NUMBERS
title: "How busy is a RedZone Sunday? (measured, 2026-11-08)"
date: 2026-11-08
stream: context
author: "unrecorded (2026-11-08 research agent)"
status: reviewed
supersedes: []
sources: 0
verified: 0
affects: []
gate: []
---

# How busy is a RedZone Sunday? (measured, 2026-11-08)

Question: what pace should RedZone-for-everything aim for? Nick's old bar was 75 jumps per 10 minutes. This note
measures what NFL RedZone actually has to show, from our own recorded Sundays, and checks it against the one
published RedZone number.

## Published numbers

- RedZone's 300th-episode graphic (Oct 2026): **17,800 touchdowns in more than 2,000 hours** since 2009, so about
  **8.9 touchdowns per hour** shown ([Floodlight, Oct 20 2026](https://getfloodlight.com/nfl-redzone-hits-300-watching-fewer-commercials-doesnt-mean-seeing-fewer-ads/)).
- Commercials are no longer zero: 2026's first broadcast had four 15-second split-screen ads in about seven hours,
  about **9 seconds of ads per hour**, with football still on screen beside them (same source, citing Sports Business
  Journal). Nov 30 2025 had about 2:40 of split-screen and banner ads plus a ~3-minute sponsor segment.
- RedZone shows up to 8 games at once ("Octobox"), cuts in at the 20-yard line, and goes back for turnovers and
  scores outside the red zone ([Wikipedia](https://en.wikipedia.org/wiki/NFL_RedZone)).
- **No published switch rate** exists (searched; also noted in viewer-experience.md). So we measured it.

## Measured on our data

Source: `data/archive/nfl/summary_*.json` (ESPN play-by-play with wall clocks), 19 Sundays from 2025-09-07 to
2026-11-01, only the 10-minute windows with 4 or more games live (92 window-hours). Events per game, by wall clock:
red-zone trip (first snap of a drive inside the 20), touchdown, other score, turnover. "Switches needed" = the
number of times the next event is in a different game than the last one (the fewest cuts that show every event).

| Per hour | Value |
|---|---|
| Touchdowns | **10.1** (RedZone's own lifetime figure: 8.9, so the method agrees) |
| Red-zone trips | **14.5** |
| All events (TD, other score, turnover, red-zone trip) | **35.6** |
| Switches needed to show them all | **30.1** |

| Switches needed per 10 minutes | Value |
|---|---|
| Median | **5** |
| 75th percentile | 7 |
| 90th percentile | 9 |
| Busiest window | 14 |

Per Sunday: 30-70 touchdowns and 45-97 red-zone trips, depending on how many games and hours (full slates with
the late window are 6+ hours).

## What this means for the pass bar

- **75 jumps in 10 minutes is about 15x what RedZone itself needs.** A good RedZone does about 5 cuts per 10 minutes,
  up to about 9-14 when many games are in the red zone at once.
- **The bar should follow the events, not a fixed count:** show every touchdown (about 1.7 per 10 min) and every
  red-zone trip (about 2.4 per 10 min) live, with as many cuts as the events need, and no more (extra cuts are
  ping-pong).
- **Commercials:** RedZone itself now shows about 9 s of (split-screen) ads per hour. Our 0 commercial seconds is
  stricter than RedZone; keep it.
- This is NFL only. Other sports have different event rates; measure them the same way before setting their bars.

Proposed bar (for Nick to approve, N5 in the fix plan), per 10-minute window with 4+ games live:
1. 0 mid-play joins; 0 high-leverage mid-play leaves; 0 commercial seconds on screen.
2. 100% of touchdowns seen live (or replayed on the hidden stick within the window), at least 95% of red-zone trips.
3. Cuts per 10 minutes within the events' own count: about 5 typical, up to about 9-14 in a busy window.

Reproduce: the analysis is ~60 lines of Python over the archive (in this session's transcript); a script version is a
follow-up if Nick wants it re-run each week.
