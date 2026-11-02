"""`rz.py bench` end to end against the fake adb, so it can't crash on the real stick tonight."""
import importlib.util
from pathlib import Path

import pytest

from fake_adb import FakeAdb, media
from redzone.channels import ChannelMap
from redzone.tuners import adb
from redzone.tuners.adb import Stick

RZ = Path(__file__).resolve().parent.parent / "scripts" / "rz.py"


@pytest.fixture
def rz():
    spec = importlib.util.spec_from_file_location("rz_script", RZ)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def fake(tmp_path, monkeypatch):
    f = FakeAdb(tmp_path / "dev")
    monkeypatch.setattr(adb, "ADB", str(f.adb))
    monkeypatch.setattr(adb, "SESSION_ON", True)
    return f


async def test_bench_times_everything_and_tunes(rz, fake, tmp_path, capsys):
    fake.set_media("", after_am=media("yttv.pkg", 3, 1000, 5000))
    ch = ChannelMap(tmp_path / "ch.json")
    ch.codes["espn"] = "ESPNCODE"
    st = Stick("fake:5555", name="Living room")
    st.state, st.packages = "connected", {"youtubetv": "yttv.pkg"}
    await rz.bench(st, ch, [], samples=3)
    await st.close()
    out = capsys.readouterr().out
    assert "Always-open shell: working" in out
    assert out.count("median") == 6 and "9 in 10 under" in out
    assert "Full tune: playing" in out
    assert "already on it" in out                 # the second tune to the same channel is instant
    assert any("watch/ESPNCODE" in x for x in fake.device_log())


async def test_bench_without_a_linked_channel_says_how_to_link_one(rz, fake, tmp_path, capsys):
    st = Stick("fake:5555")
    st.state = "connected"
    await rz.bench(st, ChannelMap(tmp_path / "ch.json"), [], samples=3)
    await st.close()
    assert "rz.py link espn" in capsys.readouterr().out


def test_p90():
    from statistics import median
    ms = [float(x) for x in range(1, 21)]
    assert median(ms) == 10.5
    spec = importlib.util.spec_from_file_location("rz_script", RZ)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._p90(ms) == 18.0 and mod._p90([5.0]) == 5.0
