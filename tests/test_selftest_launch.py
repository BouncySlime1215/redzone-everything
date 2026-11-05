"""Nightly selftest open-the-app check (ESPN on the 4K Plus said "didn't open", Nov 4): a cold start gets up
to autonomy.launch_wait_s to come to the front, and a miss says what was in front instead."""
from types import SimpleNamespace

from redzone.autonomy import Autonomy, Hooks, Paths
from redzone.channels import ChannelMap
from redzone.config import Config

PKG = "com.espn.gtv"


class Stick:
    def __init__(self, fronts):
        self.fronts, self.shells = list(fronts), []

    async def shell(self, cmd, timeout=None):
        self.shells.append(cmd)
        return SimpleNamespace(out="Events injected: 1")

    async def foreground(self):
        return self.fronts.pop(0) if len(self.fronts) > 1 else self.fronts[0]

    async def screen_text(self):
        return "watch live sports"


def make(tmp_path):
    cfg = Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h")
    slept = []

    async def sleep(s):
        slept.append(s)
    a = Autonomy(Hooks(SimpleNamespace(sticks=[], usable=[]), ChannelMap(tmp_path / "ch.json"), cfg=cfg),
                 paths=Paths.under(tmp_path), sleep=sleep)
    return a, cfg, slept


async def test_a_slow_cold_start_still_counts_as_open(tmp_path):
    a, cfg, slept = make(tmp_path)
    launcher = "com.amazon.tv.launcher/.ui.HomeActivity"
    chk = await a._launch_check(Stick([launcher] * 9 + [f"{PKG}/.Splash"]), PKG, "1.0")
    assert chk.ok and len(slept) == 10 <= cfg.get("autonomy.launch_wait_s")


async def test_an_app_that_never_comes_up_says_what_was_in_front(tmp_path):
    a, cfg, slept = make(tmp_path)
    chk = await a._launch_check(Stick(["com.amazon.tv.launcher/.ui.HomeActivity"]), PKG, "1.0")
    assert chk.ok is False and "com.amazon.tv.launcher in front" in chk.message
    assert len(slept) == int(cfg.get("autonomy.launch_wait_s"))
