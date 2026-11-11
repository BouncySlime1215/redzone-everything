"""PARK on Prime Video (`park.prime.enabled`, OFF) on fake sticks: flag off decides exactly as before; with it on a
Prime game is tuned by the existing Prime tuner, placed by its epoch position, held by the existing pause-hold, never
sent BACK, has no back route, costs decider.switch_cost.prime, and earns its own K from score-bug reads of its paused
frames. Also: a YouTube TV tune right after Prime on the same stick (the Nov 10 false "video never started").

The fakes model what the repo recorded of Prime: a slow tune (switch_cost 27 s), no rewind (replay.apps), a media
session position that is the frame's wall-clock stamp in ms (sync_edge, Nov 4: 6.4 s behind), a pause that holds
(prime_pause, Nov 7), a score bug readable in a capture (Nick, Nov 5: numbers only). Fake sticks: not measured on a TV.

Why: TNF and Friday NBA are on Prime Video; PARK must take those games without changing anything while the flag is off.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import park_replay as pr  # noqa: E402
from redzone import config  # noqa: E402
from redzone import control as ctl  # noqa: E402
from redzone.channels import ChannelMap  # noqa: E402
from redzone.noggin import park_prime  # noqa: E402
from redzone.noggin.park_timing import GameK  # noqa: E402
from redzone.screen.scorebug import Bug  # noqa: E402
from redzone.sync_edge import EdgeTracker  # noqa: E402
from redzone.tuners import yttv_timing  # noqa: E402
from redzone.tuners.adb import RunResult  # noqa: E402
from redzone.tuners.tuners import TuneResult  # noqa: E402
from test_park_hands import PKG, TV, Labels, Tuner, YTStick, world  # noqa: E402,F401

NIGHT = "20261023"
needs_archive = pytest.mark.skipif(not (ROOT / "data/archive/nba").exists(), reason="pinned eval data not fetched (scripts/fetch_eval_data.py)")
PPKG = "prime.pkg"
GTI = "amzn1.dv.gti.00000000-0000-0000-0000-000000000001"
E_PRIME = 6.4          # the picture behind its frame clock (sync_edge, Nov 4, 19 reads)
K_TRUE = -9.6          # content (frame stamp) minus feed time: the arena runs ~9.6 s ahead of Prime's frame clock


def get_of(d: dict):
    return lambda key, default=None: d.get(key, config.default(key) if default is None else default)


# ---------------------------------------------------------------------------------------------- pure: park_prime
def test_flag_off_takes_youtube_tv_only_and_on_takes_prime_event_links():
    off, on = get_of({"park.prime.enabled": False}), get_of({"park.prime.enabled": True})
    assert config.default("park.prime.enabled") is False
    assert park_prime.takes("youtubetv:espn", off) and not park_prime.takes(f"prime:{GTI}", off)
    assert park_prime.takes(f"prime:{GTI}", on) and not park_prime.takes("prime:leaguepass", on)   # no event link yet
    assert not park_prime.takes("espn:123", on) and not park_prime.takes("", on)


def test_costs_and_the_back_route_come_from_the_existing_single_sources():
    assert park_prime.rewinds("youtubetv:espn", config.default) and not park_prime.rewinds(f"prime:{GTI}", config.default)
    cfg = SimpleNamespace(get=config.default)
    assert park_prime.tune_cost("youtubetv:espn", cfg, yttv_s=14.0, show_delay=35, tune_lag=3) == 14.0
    prime27 = float(config.default("decider.switch_cost.prime"))
    assert prime27 == 27.0
    assert park_prime.tune_cost(f"prime:{GTI}", cfg, yttv_s=14.0, show_delay=35, tune_lag=3) == prime27 + 38.0
    assert park_prime.chunks(60, 25) == [20, 20, 20] and park_prime.chunks(0.6, 25) == [] and park_prime.chunks(1.5, 1) == [1.5]


def test_the_epoch_is_told_from_a_counter():
    assert park_prime.epoch_s(1_794_000_000.5) == 1_794_000_000.5
    assert park_prime.epoch_s(41_234.0) is None and park_prime.epoch_s(None) is None


def _plays(t0_feed: float, clock0: float = 720.0, n: int = 60, step: float = 12.0):
    """A running period on the feed: (feed wallclock, clock s remaining), the clock running the whole time."""
    return [(t0_feed + i * step, clock0 - i * step) for i in range(n)]


def test_k_from_a_paused_frames_bug_clock_and_epoch_is_the_parks_k_with_its_sign():
    tr, target = EdgeTracker(), f"prime:{GTI}"
    feed0 = 1_794_000_000.0
    plays = _plays(feed0)
    for i, c in enumerate((700.0, 650.0, 600.0, 512.0, 431.0)):
        feed_t = feed0 + (720.0 - c)                       # when the arena clock read c
        frame = feed_t + K_TRUE                            # the frame showing it, stamped on Prime's frame clock
        z = park_prime.k_sample(tr, target, c, frame, plays, 0.44)
        assert z == pytest.approx(K_TRUE, abs=1e-6), i
        if i < 4:
            assert park_prime.game_k(tr, target, config.default) is None       # sync.edge.k_min_reads = 5
    k, sd, n = park_prime.game_k(tr, target, config.default)
    assert k == pytest.approx(K_TRUE, abs=1e-3) and n == 5 and sd < 0.5
    assert park_prime.k_sample(tr, target, 800.0, feed0, plays, 0.44) is None   # not inside the feed's running stretch
    assert park_prime.parked_in_stop(431.0, 431.4) is True and park_prime.parked_in_stop(431.0, 433.0) is False
    assert park_prime.parked_in_stop(None, 431.0) is None


# ---------------------------------------------------------------------------------------------- flag off: as before
def _show_hash(show) -> str:
    j = show.judge
    blob = {"records": show.records, "jump_at": show.jump_at, "refused": show.refused, "stayed": show.stayed,
            "tunes": show.tunes_sent, "rule1": (j.mid_entry, j.mid_leave, j.hl_leave), "flips": j.flips}
    return hashlib.sha256(json.dumps(blob, sort_keys=True, default=str).encode()).hexdigest()


# Hashed at 399e9bd7 (origin/park-controller, the commit this branch is rebased on) with _show_hash, recorded night
# 20261023, 2 games, 45 minutes, seed 0: the app's hands and the runner's (which uses the back route).
BASE = {"app": "1c3ba27ba1b501dcbec5f5e5d49f3384445ea8b41ba4fb1716f1e580c8b24ab5",
        "runner": "024152fc5387ff71e05b8c61828717ade971f93780129f01549a27bfba4c8353"}


@needs_archive
@pytest.mark.parametrize("hands", ["app", "runner"])
def test_flag_off_the_replayed_show_decides_byte_for_byte_as_at_the_base(hands):
    show = pr.replay(NIGHT, 2, minutes=45, hands=hands)
    assert _show_hash(show) == BASE[hands]
    assert show.no_back == frozenset()


async def test_flag_off_a_prime_game_is_not_taken_by_the_show_and_nothing_is_tuned(world, tmp_path):  # noqa: F811
    from redzone.park_run import ParkRun
    from test_park_controller import _engine
    w = world
    eng, ids = _engine(w, tmp_path)
    targets = {ids[0]: "youtubetv:tnt", ids[1]: f"prime:{GTI}"}
    eng.decider.target_of = targets.get
    run_ = ParkRun(eng, now=w.clk.now, sleep=w.clk.sleep)
    assert run_.slate() == ([], "fewer than two live games the show can take")      # exactly as before this unit
    out = await w.c.park_tune(f"prime:{GTI}")
    assert out.outcome == "refused" and out.why == "PARK tunes YouTube TV channels with a code or a search only"
    assert w.b.keys == [] and w.tuner.calls == []
    w.c.cfg.update({"park": {"enabled": True, "prime": {"enabled": True}}})
    games, why = run_.slate()
    assert why == "" and [t for _, _, t in games] == ["youtubetv:tnt", f"prime:{GTI}"]


# ---------------------------------------------------------------------------------------------- the Prime stick
class RealClock:
    """Wall time at real speed: prime_pause's own hold sleeps on the real clock (the fake clock of the park steps' tests
    moves only when every task waits on it, which prime_pause's real asyncio.sleep never does)."""

    def __init__(self):
        self.m0, self.t0 = time.monotonic(), 1_794_000_000.0

    def now(self) -> float:
        return self.t0 + (time.monotonic() - self.m0)

    async def sleep(self, s: float) -> None:
        await asyncio.sleep(max(0.0, s))


class PrimeStick(YTStick):
    """A Fire TV stick in Prime Video: its media session position is the frame's own wall-clock stamp in ms (or a
    counter, `epoch=False`); MEDIA_PAUSE / MEDIA_PLAY and the play-pause key hold and resume; BACK on a paused player
    is recorded (it is never to be sent). The score bug of what it shows is read by `Bug reader` below."""

    def __init__(self, host, clock, model, lead, epoch=True):
        super().__init__(host, clock, model, lead)
        self.packages = {"youtubetv": PKG, "prime": PPKG}
        self.epoch = epoch

    def load(self, channel, base_lag=E_PRIME, behind=0.0):
        now = self.clock.now()
        if not channel.startswith("amzn1."):
            self.fg = PKG
            return super().load(channel)
        self.channel, self.loaded_at, self.ps, self.fg = channel, now - 10.0, "playing", PPKG
        self.wall0, self.event = now, now
        if self.epoch:
            self.origin, self.pos0 = 0.0, now - base_lag              # content time = the position: the frame stamp
        else:
            self.pos0 = 3600.0
            self.origin = (now - base_lag) - self.pos0
        self.overlay, self.lefts = False, 0

    def _media(self) -> str:
        if self.fg != PPKG:
            return super()._media()
        up = lambda w: int(round((w - self.boot) * 1000))                  # noqa: E731
        if self.ps == "playing":
            code, pos, upd, speed = 3, int(self.pos0 * 1000), up(self.wall0), 1.0
        else:
            code, pos, upd, speed = 2, int(self.frozen * 1000), up(self.event), 0.0
        return (f"package={PPKG}\nactive=true\nstate=PlaybackState {{state={code}, position={pos}, buffered position=0, "
                f"speed={speed}, updated={upd}, actions=0}}\nmetadata: size=3, description=Live, null, null\n")

    async def _shell(self, cmd, timeout):
        if cmd.startswith("input keyevent"):
            k = cmd.split()[-1].replace("KEYCODE_", "")
            if k == "MEDIA_PAUSE" and self.ps == "playing":
                self.keys.append(k)
                self._pause(0.0)
                return RunResult(True, "", 0.01)
            if k == "MEDIA_PLAY" and self.ps == "paused":
                self.keys.append(k)
                self._play(self.frozen)
                return RunResult(True, "", 0.01)
        return await super()._shell(cmd, timeout)

    async def screencap(self, timeout=8.0):
        return b"\x89PNG fake"

    async def screencap_band(self, from_frac, timeout=8.0):
        return b""


class BugReader:
    """The score bug of the frame a Prime stick shows: the running game clock of the arena second it shows."""

    def __init__(self):
        self.calls = 0

    async def __call__(self, stick, home, away):
        self.calls += 1
        feed_t = stick.content(stick.pos()) - K_TRUE
        c = 720.0 - (feed_t - FEED0)
        return Bug(50, 48, "3RD", f"{int(c // 60)}:{int(c % 60):02d}")


FEED0 = 1_794_000_000.0 - 400.0


class PrimeTuner:
    """The Prime tuner as PARK sees it: the event link (a GTI), then playing."""

    def __init__(self, clock):
        self.clock, self.calls = clock, []

    async def tune(self, stick, ref, target, may_cut=None):
        self.calls.append(ref)
        await self.clock.sleep(0.2)
        stick.load(ref)
        return TuneResult(True, target, 0.2, ["playing"], "")


@pytest.fixture
def pworld(cfg, tmp_path, monkeypatch):
    clk = RealClock()
    clk.t0 = 1_794_000_000.0
    monkeypatch.setattr(yttv_timing, "time", SimpleNamespace(time=clk.now))
    cfg.update({"mode": {"dry_run": False}, "park": {"enabled": True, "prime": {"enabled": True}},
                "buffered_jump": {"hold_chunk_s": 1.2},
                "prime_pause": {"overlay_wait_s": 0.05, "poll_s": 0.05, "settle_s": 2.0}})
    ch = ChannelMap(tmp_path / "ch.json")
    ch.codes.update({"espn": "AAAAAAAAAAA", "tnt": "CCCCCCCCCCC"})
    c = ctl.Controller(cfg, ch)
    a = PrimeStick("a:5555", clk, "Fire TV Stick 4K (2023)", 0.70)
    b = PrimeStick("b:5555", clk, "Fire TV Stick 4K Plus", 0.60)
    c.sticks, c.hdmi = [a, b], {"a:5555": "HDMI1", "b:5555": "HDMI2"}
    c.tv = TV(clk, tmp_path / "sound_incident.json")
    c.tv.flip_s = 0.3
    ytt, prime = Tuner(clk), PrimeTuner(clk)
    monkeypatch.setitem(ctl.TUNERS, "youtubetv", ytt)
    monkeypatch.setitem(ctl.TUNERS, "prime", prime)
    bugs = BugReader()
    c._park_hands = ctl.ParkHands(c, now=clk.now, sleep=clk.sleep, label_reader=Labels(clk), bug_reader=bugs)
    c._park_hands.prime_behind_s = E_PRIME + 2.4            # hold about 2.4 s (real) in two chunks
    plays = _plays(FEED0)
    c._park_hands.bug_ctx[f"prime:{GTI}"] = ("HOME", "AWAY", lambda per: plays)
    a.load("tnt")
    yield SimpleNamespace(c=c, a=a, b=b, tv=c.tv, ytt=ytt, prime=prime, bugs=bugs, clk=clk, hands=c._park_hands)
    assert a.forbidden() == [] and b.forbidden() == [], "PARK never sends volume, mute, power or HOME"


async def test_a_prime_game_is_tuned_by_its_event_link_placed_by_the_epoch_held_and_earns_k(pworld):
    w = pworld
    assert await w.tv.sound_guard.check("session start") == "ok"
    out = await w.c.park_tune(f"prime:{GTI}")
    assert out.ok, out.why
    assert w.prime.calls == [GTI] and w.ytt.calls == []                       # the existing Prime tuner, by the GTI
    assert "epoch read" in out.way and out.numbers["read"] is True and out.numbers["epoch"] is True
    assert out.numbers["held_ok"] and out.numbers["hold_s"] == pytest.approx(2.4, abs=0.6)
    assert w.b.keys.count("MEDIA_PAUSE") == 2 and w.b.keys.count("MEDIA_PLAY") == 2 and "BACK" not in w.b.keys
    assert w.b.ps == "playing" and w.b.behind() == pytest.approx(E_PRIME + 2.4, abs=0.6)   # held to where it was asked
    place = w.hands.place_of(w.b)
    assert place.read and place.anchor == 0.0
    assert out.numbers["bug_reads"] == 2 and out.numbers["k_samples"] == 2
    ks = [r["k"] for r in w.hands.prime_reads if r.get("what") == "bug"]
    assert all(k == pytest.approx(K_TRUE, abs=1.0) for k in ks)               # a whole-second clock: within 1 s
    assert w.hands.prime_k(f"prime:{GTI}") is None                            # 2 reads: under sync.edge.k_min_reads
    w.c.cfg.update({"sync": {"edge": {"k_min_reads": 2, "k_sd_max_s": 0.5}}})
    k = w.hands.prime_k(f"prime:{GTI}")
    assert k is not None and k[0] == pytest.approx(K_TRUE, abs=1.0) and k[2] == 2
    assert all("frame" not in r and "png" not in r for r in w.hands.prime_reads)  # numbers only


async def test_a_parked_prime_stick_gets_no_back_and_the_jump_onto_it_flips(pworld):
    w = pworld
    w.c.on_target = "youtubetv:tnt"
    assert await w.tv.sound_guard.check("session start") == "ok"
    assert (await w.c.park_start()).ok                                        # the picture on screen: YouTube TV, read
    assert (await w.c.park_tune(f"prime:{GTI}")).ok
    n_keys = len(w.b.keys)
    out = await w.c.park_pause(f"prime:{GTI}", None)
    assert out.ok, out.why
    assert w.b.ps == "paused" and "BACK" not in w.b.keys[n_keys:]           # BACK may leave Prime's player: never sent
    again = await w.c.park_tune(f"prime:{GTI}")
    assert again.ok and again.way == "kept: already parked on it" and w.prime.calls == [GTI]
    out = await w.c.park_jump(f"prime:{GTI}")
    assert out.ok, out.why
    assert w.tv.input == "HDMI2" and w.b.ps == "playing" and w.c.on_target == f"prime:{GTI}"


async def test_a_prime_stream_with_a_counter_position_is_placed_unread(pworld):
    w = pworld
    w.b.epoch = False
    assert await w.tv.sound_guard.check("session start") == "ok"
    out = await w.c.park_tune(f"prime:{GTI}")
    assert out.ok and "no epoch" in out.way and out.numbers["read"] is False and "unread padding" in out.why
    assert out.numbers["k_samples"] == 0                                       # no frame time: no K from the bug


async def test_with_the_prime_flag_off_a_prime_tune_is_refused_as_before(pworld):
    w = pworld
    w.c.cfg.update({"park": {"enabled": True, "prime": {"enabled": False}}})
    assert await w.tv.sound_guard.check("session start") == "ok"
    out = await w.c.park_tune(f"prime:{GTI}")
    assert out.outcome == "refused" and out.why == "PARK tunes YouTube TV channels with a code or a search only"
    assert w.prime.calls == [] and w.b.keys == []


async def test_a_sound_incident_stops_a_prime_tune_before_any_key(pworld):
    w = pworld
    assert await w.tv.sound_guard.check("session start") == "ok"
    w.tv.mute, w.tv.volume = "unmuted", 9
    assert await w.tv.sound_guard.check("made for the test") != "ok"
    out = await w.c.park_tune(f"prime:{GTI}")
    assert out.outcome == "refused" and w.prime.calls == [] and w.b.keys == []


async def test_youtube_tv_after_prime_on_the_same_stick_still_lands_through_the_media_session(world):  # noqa: F811
    """Nov 10 (ledger L8): after Prime was in front, the tuner said "video never started" after about 31 s while the
    asked channel played (tuners.app_change_confirm, off). PARK's tune counts what the media session names, so the
    step lands; it costs the 31 s. PARK does not need that flag to be right, only to be fast."""
    w = world
    w.b.fg = PPKG                                                              # Prime was in front on the hidden stick

    class AfterPrime(Tuner):
        async def tune(self, stick, ref, target, may_cut=None):
            self.calls.append(ref)
            app_change = stick.fg != PKG
            await self.clock.sleep(31.0 if app_change else 2.7)
            stick.fg = PKG
            stick.load(target.split(":", 1)[1])
            if app_change:
                return TuneResult(False, target, 31.0, [], "video never started")
            return TuneResult(True, target, 2.7, ["playing"], "")
    t = AfterPrime(w.clk)
    ctl.TUNERS["youtubetv"] = t
    assert await w.tv.sound_guard.check("session start") == "ok"
    out = await w.c.park_tune("youtubetv:espn")
    assert out.ok and "confirmed by what its media session names" in out.way
    assert out.took_s >= 31.0                                                  # right, but slow: the flag is the speed fix


# ---------------------------------------------------------------------------------------------- the show
@pytest.fixture(scope="module")
def loaded():
    return pr.load_replay(NIGHT, 2)


@needs_archive
def test_a_prime_game_has_no_back_route_and_costs_switch_cost_prime_plus_its_hold(loaded):
    show = pr.replay(NIGHT, 2, loaded=loaded, minutes=5, hands="runner", prime=1)
    g = 1
    assert show.no_back == frozenset({g})
    assert show.clip_lag(60, g) is None and show.clip_lag(60, 0) is not None
    P = show.P
    want = pr.prime_cost(P, show.rig.steps)
    assert want == pytest.approx(27.0 + P.show_delay + show.rig.tune_lag)
    assert show.tunes[g][0] == want and show.tunes[0][0] == pytest.approx(2.7 + P.read)


@needs_archive
def test_a_k_earned_by_the_bug_narrows_that_games_padding_and_is_never_taken_twice(loaded):
    lines = []
    show = pr.replay(NIGHT, 2, loaded=loaded, minutes=3, prime=1, prime_k="none", say=lines.append)
    g = 1
    assert not show.tm.g[g].known and show.tm.pad(g) > 20                     # no K: the wide prior
    assert show.set_k(g, GameK(-9.6, 0.5, 6, "bug"))
    assert show.tm.g[g].known and show.tm.pad(g) <= 3
    assert any(line.startswith("K: ") for line in lines)
    assert not show.set_k(g, GameK(5.0, 0.5, 6, "bug")) and show.tm.g[g].k.k == -9.6
    assert not show.set_k(0, GameK(5.0, 0.5, 6, "bug"))                       # a game with its own K keeps it


@needs_archive
@pytest.mark.parametrize("n_prime", [1, 2])
def test_with_prime_games_on_a_45_minute_slice_rule_1_holds_and_every_jump_is_warranted(loaded, n_prime):
    """A regression slice only: on whole 4-game nights Rule #1 still breaks at padding 3 with or without Prime
    (judged with walk-ins off; the coordinator measured the same, Nov 11). Fake sticks."""
    show = pr.replay(NIGHT, 2, loaded=loaded, minutes=45, prime=n_prime, walk="none")
    j = show.judge
    assert (j.mid_entry, j.mid_leave, j.hl_leave) == (0, 0, 0)
    row = pr.result(show)
    assert row["warranted"] == row["warrant_n"]
    fg = pr.graded(show)
    assert (fg["grader_entries_mid_play"], fg["grader_entries_mid_play_strict"], fg["grader_leaves_mid_play"]) == (0, 0, 0)
    assert math.isfinite(row["jumps_per_min"])
