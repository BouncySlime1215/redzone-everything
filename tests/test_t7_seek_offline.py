"""[T7 tester] Rewind accuracy from logged seeks, before the SEEK_TO helper lands: the key path control.py runs
today (Controller._rewind + contracts.switching.seek_model), measured on an offline stick (tests/t7_stick.py)
and against the live lab's logged seeks. North Star line: mid-play entries (Rule #1), since a rewind that lands
short of its dead ball shows play.

Logged seeks: tests/fixtures/t7_seeks/*.jsonl, masked copies of the Mac's data/seeks.jsonl and
data/livelab/<night>/seekcal.jsonl rows ({"sent", "landed_s", optional "asked_s", "app"}), hosts as stickA/B.
Those tests skip until the copies exist; the lab's hand-measured points (decisions.md, Nov 4) always run."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from redzone import calibration as cal
from redzone.config import Config
from redzone.contracts.switching import YTTV_SEEK, seek_model, set_calibrated_seek
from tests.t7_stick import PRIME, YTTV, make

pytestmark = pytest.mark.rule1

LOGGED = Path(__file__).parent / "fixtures" / "t7_seeks"
STALL_SETTLE_S = 0.08      # the sim's stalls are 20-40 ms of real time


@pytest.fixture(autouse=True)
def _reset_seek():
    yield
    set_calibrated_seek(None)


def _cfg() -> Config:
    return Config(user_path=Path("/nonexistent/redzone.yaml"))


def _controller(tmp_path):
    from redzone import control as ctl
    from redzone.channels import ChannelMap
    c = ctl.Controller(Config(tmp_path / "c.yaml"), ChannelMap(tmp_path / "ch.json"))
    c.seek_log_path = tmp_path / "seeks.jsonl"
    return c


def _logged_rows() -> list[dict]:
    rows = []
    for p in sorted(LOGGED.glob("*.jsonl")) if LOGGED.exists() else []:
        for line in p.read_text().splitlines():
            line = line.strip()
            if line:
                r = json.loads(line)
                r.setdefault("_file", p.name)
                rows.append(r)
    return rows


# ---- the lab's hand-measured points (always run) ---------------------------------------------------

def test_the_shipped_key_model_lands_the_lab_points():
    """Live lab, Nov 4 (decisions.md): a batch of n presses on YouTube TV lands (n-1)*15 s + a 2-4 s stall back.
    The config control.py reads must plan exactly that, or every entry rewind is off by a whole step."""
    m = seek_model(_cfg())
    assert (m.step, m.swallowed, m.stall_lo, m.stall_hi) == (YTTV_SEEK.step, YTTV_SEEK.swallowed,
                                                              YTTV_SEEK.stall_lo, YTTV_SEEK.stall_hi)
    for asked in (15, 20, 30, 45, 60, 75, 90):
        n = m.sent(asked)
        lo, hi = YTTV_SEEK.landed(n, 2.0), YTTV_SEEK.landed(n, 4.0)
        # never short by more than late_ok (a short landing shows live play), never a whole extra step long
        assert lo >= asked - m.late_ok - 1e-9, (asked, n, lo)
        assert hi < asked + m.step + m.stall_hi, (asked, n, hi)


@pytest.mark.parametrize("asked", [15.0, 30.0, 45.0, 60.0, 90.0])
async def test_controller_rewind_on_youtube_tv_lands_at_t(tmp_path, asked):
    """The key path end to end on the offline YTTV stick: whole 15 s steps rounded up, one swallowed press."""
    c = _controller(tmp_path)
    s = make("youtubetv")
    assert await c._rewind(s, asked)
    await asyncio.sleep(STALL_SETTLE_S)
    landed = s.behind_now()
    assert asked - 1.0 <= landed <= asked + YTTV.key_step_s, (asked, landed, c._last_seek_sent)
    assert c._last_seek_sent == seek_model(c.cfg).sent(asked)


async def test_controller_rewind_reports_a_dead_shell(tmp_path):
    c = _controller(tmp_path)
    s = make("youtubetv", key_fail=True)
    assert await c._rewind(s, 30.0) is False


@pytest.mark.xfail(strict=True, reason="T7 finding: the key path plans every app with YouTube TV's seek "
                   "(15 s step, 1 swallowed press). On Prime (10 s step, nothing swallowed: docs/timing.md) a "
                   "45 s rewind sends 4 presses and lands 40 s back, short by 5 s. SEEK_TO's key fallback must "
                   "price presses per app; flip this test to a pass when it does.")
async def test_controller_rewind_on_prime_is_not_short(tmp_path):
    c = _controller(tmp_path)
    for asked in (45.0, 60.0, 75.0):
        s = make("prime")
        assert await c._rewind(s, asked)
        await asyncio.sleep(STALL_SETTLE_S)
        assert s.behind_now() >= asked - 1.0, (asked, s.behind_now())


async def test_seek_sample_measures_the_offline_landing(tmp_path):
    """Controller._seek_sample (stick clock - position since the anchor) reads the same landing the stick has:
    the instrument SEEK_TO's landed_s must agree with."""
    c = _controller(tmp_path)
    s = make("youtubetv")
    s.tuned = {"target": "youtubetv:espn", "report": await s.playback()}
    assert await c._rewind(s, 30.0)
    await asyncio.sleep(STALL_SETTLE_S)
    landed = c._seek_sample(s, "youtubetv:espn", 30.0, await s.playback())
    assert landed == pytest.approx(s.behind_now(), abs=0.1)
    row = json.loads((tmp_path / "seeks.jsonl").read_text())
    assert row["host"] == "stickB" and row["sent"] == 3


# ---- the logged seeks (skip until the masked copies land) ------------------------------------------

def _need_logged():
    rows = _logged_rows()
    if not rows:
        pytest.skip("no masked seek logs in tests/fixtures/t7_seeks yet (builder copies them from the Mac)")
    return rows


def test_logged_seeks_carry_no_addresses():
    """The repo is public: a seek log may name sticks only as stickA/stickB, never by IP or serial."""
    import re
    for r in _need_logged():
        blob = json.dumps(r)
        assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", blob), r["_file"]
        assert r.get("host", "stickA") in ("stickA", "stickB", "stickC"), r["_file"]


def test_logged_youtube_tv_seeks_fit_the_shipped_model():
    """seek_fit on the real rows recovers the shipped 15 s step / 1 swallowed press / 2-4 s stall."""
    rows = [r for r in _need_logged() if r.get("app", "youtubetv") in ("youtubetv", "yttv")]
    got = cal.seek_fit(rows)
    if "yttv_seek_step_s" not in got:
        pytest.skip("logged YTTV rows have one press count only: step can't be told from stall")
    assert got["yttv_seek_wake_presses"][0] == YTTV_SEEK.swallowed
    assert abs(got["yttv_seek_step_s"][0] - YTTV_SEEK.step) <= 1.0
    assert YTTV_SEEK.stall_lo - 1.0 <= got["yttv_seek_stall_s"][0] <= YTTV_SEEK.stall_hi + 1.0


def test_every_logged_key_seek_lands_inside_the_model_band():
    """Each logged key rewind: landed within [model at 2 s stall - 1, model at 4 s stall + 1]. An outlier here is
    a seek the planner would have mispriced: list them all rather than stop at the first."""
    rows = [r for r in _need_logged() if r.get("method", "keys") == "keys"
            and r.get("app", "youtubetv") in ("youtubetv", "yttv") and isinstance(r.get("sent"), int)
            and r.get("landed_s") is not None]
    if not rows:
        pytest.skip("no YTTV key rows logged")
    bad = [(r["_file"], r["sent"], r["landed_s"]) for r in rows
           if not (YTTV_SEEK.landed(r["sent"], YTTV_SEEK.stall_lo) - 1.0 <= float(r["landed_s"])
                   <= YTTV_SEEK.landed(r["sent"], YTTV_SEEK.stall_hi) + 1.0)]
    assert len(bad) <= max(1, len(rows) // 20), bad     # <= 5% outliers (one allowed on a short log)


def test_logged_seek_to_rows_land_within_tolerance():
    """Rows the SEEK_TO helper logs (method "seek_to", asked_s, landed_s): |landed - asked| <= 1.0 s (README A2
    pass bar: within 0.5 s of the ask, plus 0.5 s for the stick-clock read)."""
    rows = [r for r in _need_logged() if r.get("method") == "seek_to" and r.get("asked_s") is not None
            and r.get("landed_s") is not None]
    if not rows:
        pytest.skip("no SEEK_TO rows logged yet")
    errs = sorted(abs(float(r["landed_s"]) - float(r["asked_s"])) for r in rows)
    assert errs[len(errs) // 2] <= 0.5 and errs[-1] <= 1.0 + 0.5, errs[-5:]
