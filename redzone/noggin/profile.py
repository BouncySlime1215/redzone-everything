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
            # NFL only (planner, Nov 4): 3 s sigma, 3 dev seeds + locked: NFL big live +5.4..5.5 dev,
            # +4.9 locked, 0 mid-play; NHL crunch live is -0.9 on locked, so NHL/NBA stay off.
            "noggin.lookahead.enabled=true",
            "noggin.lookahead.leagues=[nfl]",
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
            # [noggin logic, live lab Nov 4] Ad-break anchors feed the filter and its floor is 3 s. NOT a
            # position gain: YouTube TV's position is a per-tune clock (every tune starts near 46,790 s),
            # so it carries no landing information. The old "+4-5 NFL big live, sigma p50 3 s" came from
            # a sim that read positions off a channel origin (fixed in #146). Honest sims, 3 seeds:
            # sigma p50 9 s, 2-sigma coverage >= 95%. YTTV shows no ad text on the Mac, so live break
            # anchors wait on the burst anchor (sync.burst_anchor, scripts/burst_align.py).
            "sync.break_anchor.enabled=true",
            "delay.sync.min_sigma_s=3",
            # [noggin logic, Nick Nov 4 11:00 AM] a missed moment airs within 3 min or not singly: NFL
            # only for now (3 seeds: big live +4, crunch +1.4..2.9; NHL loses 4-7 points of seen-in-3-min).
            "replay.window_s=180",
            "replay.window_leagues=[nfl]",
            # [noggin logic] a crunch stoppage takes only a play you missed, never an instant replay of one
            # you saw: NFL dev 3 seeds crunch live +0.8..2.2, crunch plays under a replay halved, NBA/NHL equal.
            "decider.crunch.instant_replays=false",
            # [whistle detection] NHL leaves also on the play-by-play's dead windows (a whistle whose
            # faceoff landed in the same poll never reached the game state): dev 3 seeds seen-in-3-min
            # +11..14, crunch +0.5..2.8, 0 mid-play; NBA made mid-play cuts, NFL gains nothing.
            "decider.leave.dead_windows=[nhl]",
            # [seek model] live lab, Nov 4: entry rewinds run on the hidden stick before the flip, so the
            # live edge never shows mid-play first (NFL 270-345 such glimpses per dev seed) and the 2-4 s
            # seek stall passes off screen. Dev 3 seeds: 0 mid-play, NFL big live +0.8..1.7 vs seeking on screen.
            "decider.entry.cue=hidden",
            # [controls, cycle 03] Opening a game on the phone tunes it on the hidden stick while you
            # read the sheet, so "Go now" is a flip (press_to_tv_p95). Live default stays off.
            "two_stick.pretune_on_open=true",
            # [noggin logic, cycle 05 promotion blocker] No catch-up starts while any live game is in crunch
            # time or its run-up. Your team's missed play waits too, except in NFL, where it goes 90 s after
            # it aired (its 3-min replay window can't wait out crunch). Locked, 3 seeds, vs live: no line worse.
            "replay.crunch_block=true",
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
