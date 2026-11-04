"""Noggin profiles: one named set of flags, scored TOGETHER (planner, Nov 4 2:45 AM).

Every part built on Nov 3-4 ships OFF and was scored alone, so nobody knew what the combined Noggin
scores. A profile is the whole candidate: the config overrides plus the time-shift buffer per league.
`scripts/northstar.py --profile candidate` scores it next to today's config on dev and locked nights,
and every flag decision is made on those combined numbers (dev only; locked is report-only).

    argv("candidate", "nba") -> ["--behind-live", "90", "--set", "noggin.priority.enabled=true", ...]
"""
from __future__ import annotations

PROFILES: dict[str, dict] = {
    "candidate": {
        "about": "Buffer B=90 NBA/NHL and 75 NFL, Priority on, drive-in-20 on, reels T1-T5 on, "
                 "Timing v1 NBA, NHL api-web (wsc) primary feed",
        "behind_live": {"nfl": 75, "nba": 90, "nhl": 90},
        "set": [
            "noggin.priority.enabled=true",
            "redzone_mode.drive_in_20=true",
            "replay.redzone_way=true",
            "noggin.timing.leagues=[nba]",
            "sources.nhl_primary=true",
            # [noggin logic, cycle 01] A finished game's exit waits for the target's next dead ball
            # (no blind 180 s cap), and a crunch-time entry lands on the last whistle with a rewind
            # when one is in reach, else waits for one (cycle 02: "wait", rule #1; the lookahead below
            # wins back the big plays the wait costs). docs/timing.md.
            "decider.entry.crunch_now=wait",
            "decider.entry.max_wait_s={}",
            # [noggin logic, cycle 02] Clairvoyance (GAME-04): pre-tune the game whose big play the
            # feed already shows ahead of the screen, and land on its last dead ball before it airs.
            "noggin.lookahead.enabled=true",
            # [noggin logic, cycle 03, rule #1] A fresh tune comes up 0-6 s behind the live edge
            # (syncsim's YouTube TV prior, upper end; Thursday's probe refits it): entry margins take
            # that late side, and a switch off a finished game never takes the thin urgent landing.
            "decider.entry.landing_lag_s=6",
            "decider.entry.ended_strict=true",
            # [noggin logic, Nick Nov 4 10:45 AM] Possession-start entry for crunch time: cue to the last
            # dead ball before the possession, never wait while one is in reach, never mid-play. Dev
            # (3 seeds, tune jitter 6) and locked: identical to "wait", which already rewinds there.
            "decider.entry.possession_start=crunch",
            # [noggin logic, rule #1] A lone substitution (its stoppage missing from the data) opens no
            # dead window: the last candidate mid-play entry (NBA GS @ DEN) rewound 70 s into one.
            "decider.entry.confirmed_dead_only=true",
            # [controls, cycle 03] Opening a game on the phone tunes it on the hidden stick while you
            # read the sheet, so "Go now" is a flip (press_to_tv_p95). Live default stays off.
            "two_stick.pretune_on_open=true",
            # [noggin logic, cycle 05 promotion blocker] No catch-up starts while any live game is in crunch
            # time or its run-up. Your team's missed play waits too, except in NFL, where it goes 90 s after
            # it aired (its 3-min replay window can't wait out crunch). Locked, 3 seeds, vs live: no line worse.
            "replay.crunch_block=true",
            # [noggin logic, cycle 06] ...and until a game the feed ended in crunch time (an OT winner) has
            # aired: dev 3 seeds NHL crunch on replay 7/6/0 -> 0/0/0, crunch live +3.6/+2.7/+0.1, 0 mid-play;
            # big seen in 10 min -4.1..4.5 (catch-ups wait out the OT tail). NFL crunch +0.1..0.8, NBA equal.
            "replay.crunch_block_tail=true",
            "replay.crunch_block_mine_leagues=[nfl]",
        ],
    },
}


def names() -> list[str]:
    return sorted(PROFILES)


def argv(name: str, league: str) -> list[str]:
    """Extra scorecard arguments (scripts/eval.py / eval_sports.py) for this profile and league."""
    p = PROFILES[name]
    out = []
    b = (p.get("behind_live") or {}).get(league)
    if b is not None:
        out += ["--behind-live", str(b)]
    for kv in p.get("set") or []:
        out += ["--set", kv]
    return out
