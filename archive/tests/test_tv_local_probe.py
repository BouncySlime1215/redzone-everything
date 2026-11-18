"""scripts/tv_local_probe.py with a fake TV: it times both paths and never needs the real one."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import tv_local_probe as probe  # noqa: E402


class FakeTV:
    host, st_device, local_trusted, last_error = "10.0.0.5", "dev", False, ""

    def __init__(self, local_works=True):
        self.input, self.local_works = "HDMI1", local_works

    async def input_status(self):
        return self.input

    async def key(self, key):
        if self.local_works:
            self.input = key.removeprefix("KEY_")
        return True

    async def st_input(self, src):
        self.input = src
        return True

    def _drop_local(self):
        pass


def _run(monkeypatch, tmp_path, tv):
    monkeypatch.setattr(probe.tvmod.SamsungTV, "from_devices", classmethod(lambda cls: tv))
    monkeypatch.setattr(probe, "CONFIRM_WITHIN_S", 0.2)
    monkeypatch.setattr(probe, "READ_EVERY_S", 0.01)
    monkeypatch.setattr(probe.asyncio, "sleep", _nosleep)
    out = tmp_path / "p.json"
    assert probe.main(["--rounds", "2", "--out", str(out)]) == 0
    return json.loads(out.read_text())["summary"]


async def _nosleep(_s):
    return None


def test_local_keys_that_switch_are_timed(monkeypatch, tmp_path):
    s = _run(monkeypatch, tmp_path, FakeTV())
    assert s["local"] == {**s["local"], "n": 2, "confirmed": 2} and s["smartthings"]["confirmed"] == 2


def test_local_keys_this_tv_ignores_are_reported(monkeypatch, tmp_path):
    s = _run(monkeypatch, tmp_path, FakeTV(local_works=False))
    assert s["local"]["confirmed"] == 0 and s["local"]["median_s"] is None
