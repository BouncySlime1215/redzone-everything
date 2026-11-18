"""[PARK-GAME-OVER] One channel, one game: the PARK show never keeps a finished game's name on a channel, and never
takes a channel two live games claim.

Nov 11, the first in-app show on the real TV: ESPN carried CLE @ NY, then GS @ HOU (a doubleheader). The show
started at 21:25 with CLE @ NY on ESPN on screen plus two college games. CLE @ NY ended at 21:39:45; the engine
stops polling a finished game, so the show's feed froze at 21.4 s left in the 4th (no End Game row ever arrived),
and with two other games still live the session went on. At 21:55 the show still called the ESPN picture
"CLE @ NY, 4th 21.4, live play" while the picture (and the Eyes watcher) showed GS @ HOU's first quarter. A jump
would have been timed and graded on the wrong game.

Fake sticks and TV (tests/test_park_hands.py `world`), a stand-in engine over recorded summaries: no device.
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace as dc_replace
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from redzone.models import League, Phase  # noqa: E402
from redzone.sources import espn  # noqa: E402
from test_park_hands import world  # noqa: E402,F401  (the fake sticks and TV of the park steps' own tests)

FIX = ROOT / "tests" / "fixtures" / "your_team_crunch"


def _engine(w, tmp_path, targets: list[str], phases: list[Phase]):
    """A stand-in engine: the fixture night's first len(targets) NBA games, game i on targets[i] in phases[i]."""
    ids = [e["id"] for e in json.loads((FIX / "espn_nba_scoreboard_20251212.json").read_text())["events"]][:len(targets)]
    data = {i: json.loads((FIX / f"espn_nba_summary_{i}.json").read_text()) for i in ids}
    games = {i: dc_replace(espn.parse_summary(League.NBA, data[i]), phase=p) for i, p in zip(ids, phases)}
    w.c.cfg.update({"show": {"state_dir": str(tmp_path / "show")}})
    w.c.on_target = targets[0]
    dec = SimpleNamespace(owner_m=SimpleNamespace(state="normal"), target_of=dict(zip(ids, targets)).get, park_show=None)
    return SimpleNamespace(cfg=w.c.cfg, control=w.c, decider=dec, merger=SimpleNamespace(games=games), summaries=data), ids


async def test_a_finished_game_ends_the_show_even_with_two_others_still_live(world, tmp_path):  # noqa: F811
    from redzone.park_run import ParkRun
    w = world
    eng, ids = _engine(w, tmp_path, ["youtubetv:tnt", "youtubetv:espn", "youtubetv:fs1", "youtubetv:tnt"],
                       [Phase.LIVE, Phase.LIVE, Phase.LIVE, Phase.PRE])           # game 3: the channel's next game
    run_ = ParkRun(eng, now=w.clk.now, sleep=w.clk.sleep)
    assert await w.tv.sound_guard.check("session start") == "ok"
    await run_.step()
    assert run_.state == "running" and run_.gids == ids[:3], run_.why
    for _ in range(2):
        await run_.step()
        await run_.clock.sleep(1.0)
    games = eng.merger.games
    games[ids[0]] = dc_replace(games[ids[0]], phase=Phase.POST)                  # the game on screen ends...
    await run_.step()
    assert run_.show is None and run_.state == "waiting", run_.why                # ...and the show does not go on with it
    assert "ended" in run_.why and not run_.owns()
    assert w.a.ps == "playing" and w.b.ps in ("playing", "none")                 # never a stick left paused
    await run_.step()                                                            # its channel's next game is not live yet:
    assert run_.show is None and run_.why == "the game on screen is not a live game the show can take"
    games[ids[3]] = dc_replace(games[ids[3]], phase=Phase.LIVE)                  # the channel's next game goes live
    run_.retry_at = 0.0
    await run_.step()
    assert run_.state == "running" and run_.gids[0] == ids[3], run_.why          # the new show names the right game


async def test_a_channel_two_live_games_claim_is_never_taken(world, tmp_path):  # noqa: F811
    from redzone.park_run import ParkRun
    w = world
    eng, ids = _engine(w, tmp_path, ["youtubetv:tnt", "youtubetv:espn", "youtubetv:espn", "youtubetv:fs1"],
                       [Phase.LIVE] * 4)
    run_ = ParkRun(eng, now=w.clk.now, sleep=w.clk.sleep)
    chosen, why = run_.slate()
    assert [gid for gid, _, _ in chosen] == [ids[0], ids[3]] and why == ""        # espn: which of the two is on it is unknown
    w.c.on_target = "youtubetv:espn"
    chosen, why = run_.slate()
    assert chosen == [] and "claim the channel on screen" in why
