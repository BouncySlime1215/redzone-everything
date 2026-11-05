"""[T7 tester] Contract tests for the SEEK_TO helper (redzone/seek_to.py, builder thread "Build SEEK_TO and check
rewind reliability"), driven on the offline stick in tests/t7_stick.py. Skipped until the module exists.

Contract (agreed with the builder, Nov 5): `async seek_to(stick, back_s, *, app, channel=None, cfg, clock=None,
pkg=None) -> SeekResult(ok, method "seek_to"|"keys"|"none", asked_s, landed_s, err_s, attempts, timed_out,
settle_s, ad_suspect, reason)`; config replay.seek_to.{enabled, timeout_s, retries, tolerance_s, dex_path}; key
fallback when the dex is missing, the session lacks SEEK_TO, or the command fails without moving; never raises.

What each group guards (North Star: mid-play entries, Rule #1):
  * landing at T: |landed - asked| <= tolerance on both apps, back and forward, and landed_s is the truth
  * timeout and retry: one hung or off-target seek is retried/corrected; the helper never blocks past its budget
  * key fallback: every SEEK_TO failure reaches the key path, and the fallback never double-moves the picture
  * ads: a forced ad pod after a seek is flagged, not reported as a good landing
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from redzone.config import Config
from tests.t7_stick import PRIME_PKG, YTTV, YTTV_PKG, make

seek_to_mod = pytest.importorskip("redzone.seek_to", reason="SEEK_TO helper not landed yet (branch claude/seek-to)")
seek_to = seek_to_mod.seek_to

pytestmark = pytest.mark.rule1

TOL = 1.0
TIMEOUT = 0.3            # replay.seek_to.timeout_s for tests (shipped default 4.0)
SETTLE = 0.08


@pytest.fixture
def cfg(tmp_path):
    dex = tmp_path / "rzseek.dex"
    dex.write_bytes(b"dex\n035\0")     # a stand-in: the helper may check the local dex before pushing it
    c = Config(user_path=Path("/nonexistent/redzone.yaml"))
    c.data.setdefault("replay", {})["seek_to"] = {"enabled": True, "timeout_s": TIMEOUT, "retries": 1,
                                                  "tolerance_s": TOL, "dex_path": str(dex)}
    return c


APPS = [("youtubetv", YTTV_PKG, "espn"), ("prime", PRIME_PKG, "tnf")]


async def _go(stick, back_s, cfg, app, pkg, channel="espn", **kw):
    r = await seek_to(stick, back_s, app=app, channel=channel, cfg=cfg, pkg=pkg, **kw)
    await asyncio.sleep(SETTLE)
    return r


# ---- landing at T --------------------------------------------------------------------------------

@pytest.mark.parametrize("app,pkg,channel", APPS)
@pytest.mark.parametrize("back", [7.0, 30.0, 45.0, 62.5, 90.0])
async def test_seek_to_lands_at_t(cfg, app, pkg, channel, back):
    s = make(app)
    r = await _go(s, back, cfg, app, pkg, channel)
    assert r.ok and r.method == "seek_to", r
    assert abs(s.behind_now() - back) <= TOL, (back, s.behind_now(), r)
    assert s.key_presses == 0, "a good SEEK_TO must not also press keys"


@pytest.mark.parametrize("app,pkg,channel", APPS)
async def test_landed_s_is_what_the_stick_really_did(cfg, app, pkg, channel):
    """landed_s / err_s are measured on the stick's clock, not echoed from the ask: the sim lands 0.7 s long."""
    s = make(app, seek_error_s=[0.7])
    r = await _go(s, 30.0, cfg, app, pkg, channel)
    assert r.asked_s == 30.0
    assert r.landed_s == pytest.approx(s.behind_now(), abs=0.25), r
    assert r.err_s == pytest.approx(r.landed_s - r.asked_s, abs=1e-6)


async def test_forward_seek_moves_toward_live(cfg):
    s = make("youtubetv", behind_s=60.0)
    r = await _go(s, -20.0, cfg, "youtubetv", YTTV_PKG)
    assert r.ok and abs(s.behind_now() - 40.0) <= TOL, (s.behind_now(), r)


async def test_forward_past_the_live_edge_is_not_reported_as_a_landing(cfg):
    """Asked 30 s forward from 10 s behind: the picture can only reach the live edge. ok must not claim -30."""
    s = make("youtubetv", behind_s=10.0)
    r = await _go(s, -30.0, cfg, "youtubetv", YTTV_PKG)
    assert s.behind_now() >= -0.1
    assert not (r.ok and abs(r.err_s) <= TOL and r.landed_s <= -29), r


# ---- timeout and retry ---------------------------------------------------------------------------

async def test_one_timeout_is_retried_and_lands(cfg):
    s = make("youtubetv", timeouts=1)
    r = await _go(s, 30.0, cfg, "youtubetv", YTTV_PKG)
    assert r.ok and r.method == "seek_to" and r.attempts >= 2, r
    assert abs(s.behind_now() - 30.0) <= TOL


async def test_an_off_target_seek_gets_one_correction(cfg):
    """First seekTo lands 3 s long (an app snapping to a segment): one correction seek brings it within TOL."""
    s = make("youtubetv", seek_error_s=[3.0, 0.0])
    r = await _go(s, 30.0, cfg, "youtubetv", YTTV_PKG)
    assert r.method == "seek_to" and r.attempts == 2, r
    assert abs(s.behind_now() - 30.0) <= TOL and abs(r.err_s) <= TOL, (s.behind_now(), r)


async def test_corrections_stop_at_the_retry_budget(cfg):
    s = make("youtubetv", seek_error_s=[3.0, 3.0, 3.0, 3.0])
    r = await _go(s, 30.0, cfg, "youtubetv", YTTV_PKG)
    assert s.seeks <= 1 + int(cfg.get("replay.seek_to.retries")), (s.seeks, r)
    assert not (r.ok and r.method == "seek_to" and abs(r.err_s) > TOL), "an out-of-tolerance SEEK_TO must not report ok"


async def test_timeouts_every_time_fall_back_to_keys(cfg):
    s = make("youtubetv", timeouts=99)
    r = await _go(s, 30.0, cfg, "youtubetv", YTTV_PKG)
    assert r.method == "keys" and r.timed_out, r
    assert s.key_presses > 0 and s.behind_now() >= 30.0 - 1.0


async def test_a_hung_adb_call_never_blocks_past_the_budget(cfg):
    """rzseek blocks and ignores the per-call timeout (a wedged adb session). The helper must guard itself:
    the switch it serves is waiting on it."""
    s = make("youtubetv", hang_forever=True)
    budget = TIMEOUT * (1 + int(cfg.get("replay.seek_to.retries"))) + 2.0
    t0 = time.monotonic()
    r = await asyncio.wait_for(_go(s, 30.0, cfg, "youtubetv", YTTV_PKG), timeout=budget + 5.0)
    assert time.monotonic() - t0 <= budget, time.monotonic() - t0
    assert r.timed_out and r.method in ("keys", "none"), r


# ---- key fallback ----------------------------------------------------------------------------------

@pytest.mark.parametrize("fault,why", [
    ({"dex_missing": True}, "dex not on the stick"),
    ({"seek_actions": 383 & ~256}, "session lacks SEEK_TO"),
    ({"no_session": True}, "no session for the package"),
])
async def test_seek_to_failures_fall_back_to_keys(cfg, fault, why):
    s = make("youtubetv", **fault)
    r = await _go(s, 30.0, cfg, "youtubetv", YTTV_PKG)
    assert r.method == "keys", (why, r)
    assert s.key_presses > 0, why
    assert 30.0 - 1.0 <= s.behind_now() <= 30.0 + YTTV.key_step_s, (why, s.behind_now())
    assert r.reason, "the fallback must say why"


@pytest.mark.parametrize("back", [30.0, 45.0, 60.0, 75.0])
async def test_prime_key_fallback_is_priced_for_prime(cfg, back):
    """Prime steps 10 s a press (docs/timing.md), not YouTube TV's 15 s with a swallowed press. A fallback priced
    with the YTTV model lands 45 -> 40 s, 75 -> 70 s back: short of the dead ball, so live play shows."""
    s = make("prime", dex_missing=True)
    r = await _go(s, back, cfg, "prime", PRIME_PKG, "tnf")
    assert r.method == "keys", r
    assert back - 1.0 <= s.behind_now() <= back + 10.0, (back, s.behind_now())


async def test_unsupported_seek_to_is_not_retried(cfg):
    """No SEEK_TO in the session's actions is permanent: retrying it only delays the switch."""
    s = make("youtubetv", seek_actions=383 & ~256)
    await _go(s, 30.0, cfg, "youtubetv", YTTV_PKG)
    seek_calls = [c for c in s.calls if "RzSeek seek" in c]
    assert len(seek_calls) <= 1, seek_calls


async def test_a_noop_seek_is_caught_and_falls_back(cfg):
    """seekTo accepted (JSON echo) but the picture never moved: only the stick clock can tell. Must not report ok
    as seek_to, and the final picture must still be back about 30 s."""
    s = make("youtubetv", noop_seeks=99)
    r = await _go(s, 30.0, cfg, "youtubetv", YTTV_PKG)
    assert r.method == "keys", r
    assert 30.0 - 1.0 <= s.behind_now() <= 30.0 + YTTV.key_step_s, s.behind_now()


async def test_fallback_after_a_partial_move_never_double_moves(cfg):
    """SEEK_TO moved 20 of 30 s and then the stick stopped answering rzseek. The key fallback must price the
    REMAINING 10 s from the measured position, not resend 30 s (that lands ~50 s back, a whole play early,
    or for a forward seek, onto live play). Bound: never more than one key step past the ask."""
    s = make("youtubetv", partial_then_fail=2 / 3)
    r = await _go(s, 30.0, cfg, "youtubetv", YTTV_PKG)
    assert s.behind_now() <= 30.0 + YTTV.key_step_s + 0.5, (s.behind_now(), r)
    assert s.behind_now() >= 30.0 - 1.0, (s.behind_now(), r)


async def test_disabled_runs_the_key_path_only(cfg):
    cfg.data["replay"]["seek_to"]["enabled"] = False
    s = make("youtubetv")
    r = await _go(s, 30.0, cfg, "youtubetv", YTTV_PKG)
    assert r.method == "keys" and not any("RzSeek" in c for c in s.calls), r


async def test_the_shipped_default_is_off():
    c = Config(user_path=Path("/nonexistent/redzone.yaml"))
    assert not c.get("replay.seek_to.enabled"), "SEEK_TO ships OFF behind its flag until the hidden-stick run passes"


async def test_it_never_raises(cfg):
    s = make("youtubetv", raise_on_shell=True)
    r = await _go(s, 30.0, cfg, "youtubetv", YTTV_PKG)
    assert not r.ok and r.reason, r


async def test_zero_rewind_does_nothing(cfg):
    s = make("youtubetv")
    r = await _go(s, 0.0, cfg, "youtubetv", YTTV_PKG)
    assert s.seeks == 0 and s.key_presses == 0 and abs(s.behind_now()) <= 0.1, r


# ---- ads -----------------------------------------------------------------------------------------

async def test_a_forced_ad_after_the_seek_is_flagged(cfg):
    """Some app/channel pairs drop a forced ad pod on a rewind: the session restarts as a new item. The landing
    can't be trusted then, and the per-channel ads table depends on this flag."""
    s = make("youtubetv", ad_on_seek=True)
    r = await _go(s, 30.0, cfg, "youtubetv", YTTV_PKG)
    assert r.ad_suspect, r
    assert not (r.ok and r.method == "seek_to" and abs(r.err_s) <= TOL and not r.ad_suspect)


async def test_a_clean_seek_is_not_flagged_as_an_ad(cfg):
    s = make("prime")
    r = await _go(s, 30.0, cfg, "prime", PRIME_PKG, "tnf")
    assert not r.ad_suspect, r


# ---- pure helpers ----------------------------------------------------------------------------------

LIST_YTTV = ('{"pkg":"com.amazon.firetv.youtube.tv","state":3,"position":46760000,"updated":50000061,"speed":1.0,'
             '"actions":383,"elapsed":50000561,"pos_now":46760500,"wall_ms":1793889049571}')
LIST_PRIME = ('{"pkg":"com.amazon.firebat","state":3,"position":1793889000000,"updated":50000000,"speed":1.0,'
              '"actions":1049455,"elapsed":50001000,"pos_now":1793889001000,"wall_ms":1793889049571}')


def test_parse_list_reads_every_session_and_skips_noise():
    text = "\n".join(["WARNING: linker: something", LIST_YTTV, "", LIST_PRIME, '{"error":"no session for that package"}',
                      "java.lang.SecurityException: at android.media.session..."])
    got = seek_to_mod.parse_list(text)
    assert [g["pkg"] for g in got] == [YTTV_PKG, PRIME_PKG]
    assert got[1]["position"] > 1e12 and got[0]["actions"] & 256


def test_parse_list_of_nothing_is_empty():
    assert seek_to_mod.parse_list("") == []
    assert seek_to_mod.parse_list("Error: Could not find or load main class RzSeek") == []


def test_landed_back_s_is_clock_minus_position():
    """10 s of stick clock, position moved 20 s back from its run-on: (10 - (-20)) = 30 s behind where it was."""
    before = {"elapsed": 50_000_000, "pos_now": 46_760_000}
    after = {"elapsed": 50_010_000, "pos_now": 46_750_000}
    assert seek_to_mod.landed_back_s(before, after) == pytest.approx(20.0, abs=1e-6)
    same = {"elapsed": 50_010_000, "pos_now": 46_770_000}      # played straight through: 0 back
    assert seek_to_mod.landed_back_s(before, same) == pytest.approx(0.0, abs=1e-6)


def test_landed_back_s_on_prime_epoch_positions():
    before = {"elapsed": 50_000_000, "pos_now": 1_793_889_000_000}
    after = {"elapsed": 50_002_000, "pos_now": 1_793_888_972_000}    # 2 s later, frame 28 s earlier
    assert seek_to_mod.landed_back_s(before, after) == pytest.approx(30.0, abs=1e-6)


def test_is_ad_suspect():
    before = {"pkg": YTTV_PKG, "state": 3, "position": 46_760_000, "pos_now": 46_760_000, "elapsed": 50_000_000}
    clean = [dict(before, position=46_730_500, pos_now=46_731_000, elapsed=50_001_000)]
    ad = [dict(before, position=0, pos_now=1_000, elapsed=50_001_000)]
    assert not seek_to_mod.is_ad_suspect(before, clean)
    assert seek_to_mod.is_ad_suspect(before, ad)
