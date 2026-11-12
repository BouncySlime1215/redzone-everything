"""[park.verify_game] The game on the picture: after every tune PARK reads the words on it with the EXPECTED game's teams
and every other live game's. Another game's two teams = the wrong game (refused, re-picked); nothing readable = UNKNOWN
(the game's K kept back: the wide padding); flag off = nothing read, nothing changed.

Nov 11: the show called ESPN's picture "CLE @ NY" while it showed GS @ HOU (tests/test_park_run_channel.py). Fake sticks,
TV and text reader (tests/test_park_hands.py `world`), recorded summaries: no device.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix(), (ROOT / "tests").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from redzone.models import Phase  # noqa: E402
from redzone.noggin import park_verify as pv  # noqa: E402
from redzone.noggin.park_timing import GameK  # noqa: E402
from test_park_hands import world  # noqa: E402,F401  (the fake sticks and TV of the park steps' own tests)
from test_park_run_channel import _engine  # noqa: E402

T = lambda abbr, name: SimpleNamespace(abbr=abbr, name=name)    # noqa: E731
GSW, HOU, CLE, NYK = T("GS", "Golden State Warriors"), T("HOU", "Houston Rockets"), T("CLE", "Cleveland Cavaliers"), T("NY", "New York Knicks")


# ---------------------------------------------------------------------------------------------- the verdict (pure)
def test_the_verdict_from_the_words_on_the_picture():
    others = {"GS @ HOU": (HOU, GSW)}
    assert pv.verdict(["NBA Basketball", "Cavaliers at Knicks", "8:41 PM"], (NYK, CLE), others) == (pv.VERIFIED, "")
    assert pv.verdict(["NBA Basketball", "Warriors at Rockets"], (NYK, CLE), others) == (pv.WRONG, "GS @ HOU")
    assert pv.verdict(None, (NYK, CLE), others) == (pv.UNKNOWN, "")                      # nothing read (a blank frame)
    assert pv.verdict(["LIVE", "1:07:12"], (NYK, CLE), others) == (pv.UNKNOWN, "")       # no team named
    assert pv.verdict(["Cavaliers at Knicks", "Up next: Warriors at Rockets"], (NYK, CLE), others) == (pv.UNKNOWN, "")
    assert pv.verdict(["Rockets"], (NYK, CLE), others) == (pv.UNKNOWN, "")               # one team is not a game


# ---------------------------------------------------------------------------------------------- one tune
def _labels(eng, ids):
    gs = eng.merger.games
    return {gid: (gs[gid].home, gs[gid].away, f"{gs[gid].away.abbr} @ {gs[gid].home.abbr}") for gid in ids}


def _words(home, away) -> list[str]:
    return ["NBA Basketball", f"{away.name} at {home.name}", "LIVE"]


async def test_a_tune_that_shows_another_game_is_refused_and_its_place_dropped(world, tmp_path):  # noqa: F811
    w = world
    eng, ids = _engine(w, tmp_path, ["youtubetv:tnt", "youtubetv:espn", "youtubetv:fs1"], [Phase.LIVE] * 3)
    lab = _labels(eng, ids)
    w.c.cfg.update({"park": {"verify_game": True}})
    h = w.hands
    h.expect = {"youtubetv:espn": lab[ids[1]]}
    h.live_names = {v[2]: v[:2] for v in lab.values()}
    shown = []

    async def reader(stick, target):
        shown.append(target)
        return _words(*lab[ids[2]][:2])                        # the espn tune shows game 2 (fs1's game)
    h.text_reader = reader
    assert await w.tv.sound_guard.check("session start") == "ok"
    out = await w.c.park_tune("youtubetv:espn")
    assert shown == ["youtubetv:espn"]
    assert out.outcome == "failed" and out.why.startswith("wrong game") and lab[ids[2]][2] in out.why
    assert h.verdicts["youtubetv:espn"][:2] == (pv.WRONG, lab[ids[2]][2])
    assert h.place_of(w.c.hidden) is None and w.c.hidden_target != "youtubetv:espn"    # nothing planned on it


async def test_a_tune_with_nothing_readable_is_unknown_and_kept_and_the_youtube_tv_check_adds_no_time(world, tmp_path):  # noqa: F811
    w = world
    eng, ids = _engine(w, tmp_path, ["youtubetv:tnt", "youtubetv:espn"], [Phase.LIVE] * 2)
    lab = _labels(eng, ids)
    assert await w.tv.sound_guard.check("session start") == "ok"
    off = await w.c.park_tune("youtubetv:espn")                # flag off: the reference tune
    w.c.cfg.update({"park": {"verify_game": True}})
    h = w.hands
    h.expect = {"youtubetv:espn": lab[ids[1]]}
    h.live_names = {v[2]: v[:2] for v in lab.values()}

    async def blank(stick, target):
        return None                                            # a DRM-blank frame / no read: nothing to name a game by
    h.text_reader = blank
    on = await w.c.park_tune("youtubetv:espn", fresh=True)
    assert on.ok and h.verdicts["youtubetv:espn"][0] == pv.UNKNOWN and "game unknown" in on.way
    assert on.numbers["check_s"] == 0.0                        # YouTube TV: the read's own words, no screenshot, no wait
    assert abs(on.took_s - off.took_s) < 0.5, (on.took_s, off.took_s)


async def test_flag_off_no_words_are_read_and_the_tune_is_as_before(world, tmp_path):  # noqa: F811
    w = world
    called = []

    async def reader(stick, target):
        called.append(target)
        return ["anything"]
    w.hands.text_reader = reader
    assert await w.tv.sound_guard.check("session start") == "ok"
    out = await w.c.park_tune("youtubetv:espn")
    assert out.ok and called == [] and "game" not in out.way and "check_s" not in out.numbers
    assert w.hands.verdicts == {}


# ---------------------------------------------------------------------------------------------- the show
async def test_the_show_re_picks_without_a_channel_proven_to_carry_another_game(world, tmp_path):  # noqa: F811
    from redzone.park_run import ParkRun
    w = world
    eng, ids = _engine(w, tmp_path, ["youtubetv:tnt", "youtubetv:espn", "youtubetv:fs1"], [Phase.LIVE] * 3)
    lab = _labels(eng, ids)
    w.c.cfg.update({"park": {"verify_game": True}})

    async def reader(stick, target):
        if target == "youtubetv:espn":
            return _words(*lab[ids[2]][:2])                    # espn shows fs1's game
        g = {"youtubetv:tnt": ids[0], "youtubetv:fs1": ids[2]}[target]
        return _words(*lab[g][:2])
    w.hands.text_reader = reader
    run_ = ParkRun(eng, now=w.clk.now, sleep=w.clk.sleep)
    assert await w.tv.sound_guard.check("session start") == "ok"
    await run_.step()
    assert run_.state == "running" and run_.gids == ids, run_.why
    assert w.hands.verdicts["youtubetv:tnt"][0] == pv.VERIFIED  # the stream on screen was checked when it was placed
    await w.c.park_tune("youtubetv:espn")                      # the show tunes espn on the hidden stick
    await run_.step()
    assert run_.show is None and "re-picking" in run_.why and lab[ids[2]][2] in run_.why
    assert (ids[1], "youtubetv:espn") in run_.wrong
    chosen, _ = run_.slate()
    assert [gid for gid, _, _ in chosen] == [ids[0], ids[2]]   # re-picked without espn's game


async def test_an_unconfirmed_game_has_its_k_kept_back_until_its_picture_confirms_it(world, tmp_path):  # noqa: F811
    from redzone.park_run import ParkRun
    w = world
    eng, ids = _engine(w, tmp_path, ["youtubetv:tnt", "youtubetv:espn"], [Phase.LIVE] * 2)
    lab = _labels(eng, ids)
    w.c.cfg.update({"park": {"verify_game": True}})

    async def reader(stick, target):
        return _words(*lab[ids[0]][:2]) if target == "youtubetv:tnt" else None
    w.hands.text_reader = reader
    run_ = ParkRun(eng, now=w.clk.now, sleep=w.clk.sleep)
    assert await w.tv.sound_guard.check("session start") == "ok"
    await run_.step()
    assert run_.state == "running", run_.why
    show = run_.show
    assert not show.tm.g[1].known                              # espn's game: nothing confirmed, so no K (wide padding)
    run_.held_k[1] = GameK(10.0, 0.5, 8, "table")              # as if the K file had one for it
    w.hands.verdicts["youtubetv:espn"] = (pv.UNKNOWN, "", lab[ids[1]][2])
    await run_.step()
    assert not show.tm.g[1].known                              # UNKNOWN keeps it back
    w.hands.verdicts["youtubetv:espn"] = (pv.VERIFIED, "", lab[ids[1]][2])
    await run_.step()
    assert show.tm.g[1].known and show.tm.g[1].k.k == 10.0     # the picture confirmed it: the K is used


def test_a_stale_channel_proof_expires(world, tmp_path):  # noqa: F811
    from redzone.park_run import ParkRun
    w = world
    eng, ids = _engine(w, tmp_path, ["youtubetv:tnt", "youtubetv:espn"], [Phase.LIVE] * 2)
    run_ = ParkRun(eng, now=w.clk.now, sleep=w.clk.sleep)
    run_.wrong[(ids[1], "youtubetv:espn")] = w.clk.now() + float(w.c.cfg.get("park.verify_hold_s"))
    assert run_.slate()[0] == []                               # one game left: no show
    run_.wrong[(ids[1], "youtubetv:espn")] = w.clk.now() - 1
    assert [gid for gid, _, _ in run_.slate()[0]] == ids
    assert run_.wrong == {}
