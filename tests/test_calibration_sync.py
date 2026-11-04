"""[calib.auto] Lane 2's sync half: the YouTube TV seek and each channel's delay, re-measured every night
from live telemetry (seeks.jsonl, sync.jsonl) and read back at start. No one's input."""
import json
import random
from datetime import date, timedelta
from pathlib import Path

import pytest

from redzone import calibration as cal
from redzone.config import Config
from redzone.contracts.switching import YTTV_SEEK, seek_model, set_calibrated_seek
from redzone.decider.screen import DelayBook


@pytest.fixture(autouse=True)
def _reset_seek():
    yield
    set_calibrated_seek(None)


def _cfg(auto=True):
    c = Config(user_path=Path("/nonexistent/redzone.yaml"))
    c.data.setdefault("calib", {})["auto"] = auto
    return c


def _seek_rows(rng, n=24, model=YTTV_SEEK):
    rows = []
    for i in range(n):
        sent = rng.choice([1, 2, 3, 4, 5, 6])
        landed = model.landed(sent, rng.uniform(model.stall_lo, model.stall_hi)) if sent > model.swallowed else \
            rng.uniform(0.0, 0.05)
        rows.append({"t": 1000.0 + i, "sent": sent, "landed_s": round(landed, 2)})
    return rows


def test_seek_fit_recovers_the_lab_measurement():
    """The Nov 4 hand calibration: 1 press = 0 s, then 15 s a press plus a 2-4 s stall."""
    got = cal.seek_fit(_seek_rows(random.Random(4)))
    assert got["yttv_seek_wake_presses"][0] == 1
    assert abs(got["yttv_seek_step_s"][0] - 15.0) < 0.6 and 1.5 <= got["yttv_seek_stall_s"][0] <= 4.5


def test_delay_fit_takes_out_what_the_tune_was_held_behind():
    rows = [{"t": 10.0 + i, "target": "youtubetv:espn", "kind": "break", "used": True, "z": 26.0 + b + (i % 3) * 0.2,
             "behind": b} for i, b in enumerate([0, 0, 90, 90, 15, 0, 90])]
    rows += [{"t": 30.0, "target": "youtubetv:espn", "kind": "tune", "used": True, "z": 99, "behind": 0},
             {"t": 31.0, "target": "youtubetv:espn", "kind": "break", "used": False, "z": 70, "behind": 0},
             {"t": 32.0, "target": "youtubetv:fox", "kind": "break", "used": True, "z": 30, "behind": 0}]
    got = cal.delay_fit(rows)
    v, n, _, sd = got["delay_s:youtubetv:espn"]
    assert abs(v - 26.2) < 0.21 and n == 7 and sd >= 0.5
    assert "delay_s:youtubetv:fox" not in got           # one anchor is not a measurement


def test_a_night_is_written_guarded_and_read_back_only_when_on(tmp_path):
    d0 = date(2026, 11, 1)
    rng = random.Random(7)
    for i in range(4):
        cal.merge(tmp_path, d0 + timedelta(days=i), cal.seek_fit(_seek_rows(rng)), section="sync")
    # a night where the stick misbehaved (say an app update made a press 30 s): refused, never read back
    weird = SeekModelish = type(YTTV_SEEK)(step=30.0, swallowed=1, stall_lo=2.0, stall_hi=4.0)
    bad = cal.merge(tmp_path, d0 + timedelta(days=4), cal.seek_fit(_seek_rows(rng, model=weird)), section="sync")
    assert not bad["yttv_seek_step_s"]["accepted"]
    assert cal.sync_prior(_cfg(False), tmp_path) == {"seek": None, "delays": {}}
    sp = cal.sync_prior(_cfg(True), tmp_path)
    assert abs(sp["seek"].step - 15.0) < 0.6 and sp["seek"].swallowed == 1 and SeekModelish.step == 30.0
    set_calibrated_seek(sp["seek"])
    assert seek_model(_cfg()).step == sp["seek"].step


def test_a_calibrated_channel_starts_its_filter_from_last_nights_delay():
    book = DelayBook({"youtubetv": 45.0}, 15.0)
    book.attach_sync()
    book.calibrated = {"youtubetv:espn": (26.2, 0.8)}
    assert book._sync_prior("youtubetv:espn") == (26.2, 2.0)       # never surer than the 2 s floor
    book.set_behind("youtubetv:espn", 90.0)
    assert book._sync_prior("youtubetv:espn")[0] == pytest.approx(116.2)
    assert book._sync_prior("youtubetv:fox") == (45.0, 15.0)        # uncalibrated: as before


def test_the_sync_log_carries_the_tunes_offset(tmp_path):
    book = DelayBook({"youtubetv": 45.0}, 15.0)
    book.attach_sync(log_path=tmp_path / "sync.jsonl")
    book.get("youtubetv:espn")
    book.set_behind("youtubetv:espn", 90.0, known_sd=0.3)
    rows = [json.loads(x) for x in (tmp_path / "sync.jsonl").read_text().splitlines()]
    assert rows and rows[-1]["behind"] == 90.0


def test_the_controller_logs_what_each_seek_really_did(tmp_path):
    """One row per measured seek: presses sent, and how far behind the stick's own clock says it landed."""
    from types import SimpleNamespace

    from redzone import control as ctl
    from redzone.channels import ChannelMap
    c = ctl.Controller(Config(tmp_path / "c.yaml"), ChannelMap(tmp_path / "ch.json"))
    c.seek_log_path = tmp_path / "seeks.jsonl"
    c._last_seek_sent = 2
    stick = SimpleNamespace(host="b:5555", model="AFTKM",
                            tuned={"target": "youtubetv:espn", "report": {"position": 600_000, "updated": 1_000_000}})
    # 20 s of the stick's clock, position moved 3 s back: 15 s step + 2 s stall + ... = 23 s behind
    landed = c._seek_sample(stick, "youtubetv:espn", 18.0, {"position": 597_000, "updated": 1_020_000})
    assert landed == 23.0
    row = json.loads((tmp_path / "seeks.jsonl").read_text())
    assert row["sent"] == 2 and row["landed_s"] == 23.0 and row["model"] == "AFTKM"
    assert c._seek_sample(stick, "youtubetv:fox", 18.0, {"position": 1, "updated": 2}) is None
