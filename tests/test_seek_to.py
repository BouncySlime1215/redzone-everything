"""[T7 SEEK_TO] redzone/seek_to.py and its use in Controller._rewind: exact seeks, landing measured on the stick's
clock, corrections, the key fallback, and the never-raise / never-double-rewind rules."""
import asyncio
import json
import re
from types import SimpleNamespace

import pytest

from redzone import seek_to as st
from redzone.tuners.adb import RunResult

PKG = "com.amazon.firetv.youtube.tv"


class Cfg(dict):
    def get(self, k, d=None):
        return super().get(k, d)


def cfg(**kw):
    base = {"replay.seek_to.enabled": True, "replay.seek_to.timeout_s": 4.0, "replay.seek_to.retries": 1,
            "replay.seek_to.tolerance_s": 1.0, "replay.seek_step_s": 15, "replay.seek_wake_presses": 1,
            "replay.seek_stall_min_s": 2, "replay.seek_stall_max_s": 4, "replay.seek_key": "KEYCODE_MEDIA_REWIND"}
    base.update({f"replay.seek_to.{k}": v for k, v in kw.items()})
    return Cfg(base)


class SimStick:
    """A player on a stick that speaks rzseek `list` / `seekrel`: position runs at 1x on the stick clock (advanced
    50 ms per command); each SEEK_TO lands `bias` s off what was asked (+ = further back)."""

    def __init__(self, dex_size=1234, actions=383, bias=(0.0,), hang=False, title="NBA TV", title_after=None,
                 no_session=False, push_ok=True, dex_on_stick=True):
        self.host, self.name, self.packages = "stickA", "stickA", {"youtubetv": PKG}
        self.dex_size, self.actions, self.bias = dex_size, actions, list(bias)
        self.hang, self.title, self.title_after = hang, title, title_after
        self.no_session, self.push_ok, self.dex_on_stick = no_session, push_ok, dex_on_stick
        self.clock_ms, self.pos_ms, self.upd_ms = 5_000_000, 46_800_000, 5_000_000
        self.cmds, self.pushed = [], 0
        self.tuned = {"target": "youtubetv:nbatv", "report": {"position": 1, "updated": 1}}

    def _tick(self, ms=50):
        self.clock_ms += ms
        self.pos_ms += ms

    async def shell(self, cmd, timeout=8.0):
        self.cmds.append(cmd)
        self._tick()
        if "input" in cmd:
            self.tuned = None
        if self.hang:
            return RunResult(False, "timeout/error: ", timeout)
        if cmd.startswith("stat -c"):
            return RunResult(True, "" if self.dex_size is None else str(self.dex_size), 0.01)
        if "RzSeek" in cmd and not self.dex_on_stick:
            return RunResult(False, "Error: Could not find or load main class RzSeek", 0.2)
        if "RzSeek list" in cmd:
            if self.no_session:
                return RunResult(True, "", 0.05)
            row = {"pkg": PKG, "state": 3, "position": self.pos_ms, "updated": self.upd_ms, "speed": 1.0,
                   "actions": self.actions, "elapsed": self.clock_ms, "pos_now": self.pos_ms,
                   "wall_ms": 1, "title": self.title, "subtitle": "NBA TV", "duration": -1}
            return RunResult(True, "WARNING: linker noise\n" + json.dumps(row) + "\n", 0.05)
        m = re.search(r"RzSeek seekrel (\S+) (-?\d+)", cmd)
        if m:
            if m.group(1) != PKG:
                return RunResult(True, '{"error":"no session for that package"}\n', 0.05)
            if not self.actions & 256:
                return RunResult(True, json.dumps({"pkg": PKG, "error": "no SEEK_TO in actions"}) + "\n", 0.05)
            delta = int(m.group(2))
            bias = self.bias.pop(0) if self.bias else 0.0
            frm = self.pos_ms
            self.pos_ms = frm + delta - int(bias * 1000)
            self.upd_ms = self.clock_ms
            if self.title_after:
                self.title = self.title_after
            return RunResult(True, json.dumps({"pkg": PKG, "seek_to": frm + delta, "from": frm,
                                               "elapsed": self.clock_ms}) + "\n", 0.05)
        if "input keyevent" in cmd:
            return RunResult(True, "", 0.3)
        return RunResult(True, "", 0.01)

    async def run(self, *args, timeout=8.0):
        self.pushed += 1
        if self.push_ok:
            self.dex_on_stick = True
        return RunResult(self.push_ok, "pushed" if self.push_ok else "error: device offline", 0.5)

    async def key(self, *codes):
        for c in codes:
            await self.shell(f"input keyevent {c}")
        return RunResult(True, "", 0.01)


@pytest.fixture
def dex(tmp_path):
    p = tmp_path / "rzseek.dex"
    p.write_bytes(b"x" * 1234)
    st.forget_dex()
    yield p
    st.forget_dex()


def run(coro):
    return asyncio.run(coro)


def test_parse_and_landing_math():
    text = 'junk\n{"pkg":"a","pos_now":5}\n{bad\n{"error":"no session for that package"}\n'
    assert st.parse_list(text) == [{"pkg": "a", "pos_now": 5}]
    assert st.parse_rows(text)[-1] == {"error": "no session for that package"}
    # 10 s of stick clock passed; no seek would be at +10 s; it sits 20 s behind where it started -> 30 s back
    assert st.landed_back_s({"pos_now": 100_000, "elapsed": 0}, {"pos_now": 80_000, "elapsed": 10_000}) == 30.0
    assert st.landed_back_s({"pos_now": 100_000, "elapsed": 0}, {"pos_now": 80_000, "elapsed": 10_000},
                            playing=False) == 20.0
    assert st.landed_back_s({"pos_now": -1, "elapsed": 0}, {"pos_now": 1, "elapsed": 1}) is None
    assert st.landed_back_s({}, {"pos_now": 1, "elapsed": 1}) is None


def test_ad_suspect_rules():
    game = {"title": "Heat at Warriors", "pos_now": 46_800_000}
    assert st.is_ad_suspect(game, {"title": "Ad", "pos_now": 46_770_000})
    assert st.is_ad_suspect(game, [{"title": "Something else"}])
    assert st.is_ad_suspect({"duration": 30_000}, {"duration": 15_000})
    assert st.is_ad_suspect(game, {"title": "Heat at Warriors", "pos_now": 4_000})      # a new item restarted
    assert not st.is_ad_suspect(game, {"title": "Heat at Warriors", "pos_now": 46_770_000})
    assert not st.is_ad_suspect({"title": None}, {"title": None})
    assert not st.is_ad_suspect({"title": "Bradley Beal"}, {"title": "Bradley Beal"})   # 'ad' inside a word


def test_key_model_is_priced_per_app():
    c = cfg()
    assert st.key_model("youtubetv", c).sent(30.0) == 3    # 2 counted 15 s presses + the swallowed one
    assert st.key_model("prime", c).sent(45.0) == 5        # 10 s a press, nothing swallowed: 50 s, never short
    assert st.key_model("prime", c).sent(40.0) == 4


def test_exact_seek_lands_within_tolerance(dex):
    s = SimStick(bias=(0.3,))
    r = run(st.seek_to(s, 30.0, app="youtubetv", cfg=cfg(dex_path=str(dex))))
    assert r.ok and r.method == "seek_to" and r.attempts == 1 and r.presses == 0, r
    assert r.landed_s == pytest.approx(30.3, abs=0.01) and r.err_s == pytest.approx(0.3, abs=0.01)
    assert not r.timed_out and not r.ad_suspect
    assert s.pushed == 0                                   # same size already on the stick
    assert not any("input keyevent" in c for c in s.cmds)  # no key press at all


def test_dex_pushed_once_when_missing(dex):
    s = SimStick(dex_on_stick=False)
    r1 = run(st.seek_to(s, 15.0, app="youtubetv", cfg=cfg(dex_path=str(dex))))
    r2 = run(st.seek_to(s, 15.0, app="youtubetv", cfg=cfg(dex_path=str(dex))))
    assert s.pushed == 1 and r1.method == r2.method == "seek_to"


def test_stale_dex_is_replaced(dex):
    s = SimStick(dex_size=999)
    run(st.seek_to(s, 15.0, app="youtubetv", cfg=cfg(dex_path=str(dex))))
    assert s.pushed == 1


def test_off_landing_gets_one_correction_seek(dex):
    s = SimStick(bias=(-4.0, 0.2))                         # first lands 4 s short, the correction is close
    r = run(st.seek_to(s, 30.0, app="youtubetv", cfg=cfg(dex_path=str(dex))))
    seeks = [c for c in s.cmds if "seekrel" in c]
    assert r.ok and r.method == "seek_to" and r.attempts == 2 and len(seeks) == 2, r
    assert abs(int(seeks[1].split()[-1]) + 4000) <= 200     # the second asks for the ~4 s it was short
    assert r.err_s == pytest.approx(0.2, abs=0.3)


def test_still_short_after_retries_is_topped_up_with_presses_for_the_rest(dex):
    s = SimStick(bias=(-20.0, -10.0))                      # SEEK_TO moves 10 s, then 10 s more
    r = run(st.seek_to(s, 30.0, app="youtubetv", cfg=cfg(dex_path=str(dex))))
    assert r.method == "seek_to+keys" and r.ok and "short" in r.reason, r
    assert r.presses == 2                                  # 10 s left: 1 counted + 1 swallowed, not the whole 30


def test_too_far_back_is_reported_not_ok(dex):
    s = SimStick(bias=(3.0,))
    r = run(st.seek_to(s, 30.0, app="youtubetv", cfg=cfg(dex_path=str(dex), retries=0)))
    assert not r.ok and r.method == "seek_to" and r.err_s == pytest.approx(3.0, abs=0.3) and r.presses == 0


@pytest.mark.parametrize("kw,why", [({"actions": 1}, "lacks SEEK_TO"), ({"no_session": True}, "no session")])
def test_no_seek_to_falls_back_to_keys(dex, kw, why):
    s = SimStick(**kw)
    r = run(st.seek_to(s, 30.0, app="youtubetv", cfg=cfg(dex_path=str(dex))))
    assert r.ok and r.method == "keys" and why in r.reason, r
    assert r.presses == 3


def test_missing_dex_falls_back_to_keys(tmp_path):
    st.forget_dex()
    s = SimStick(dex_on_stick=False)
    r = run(st.seek_to(s, 30.0, app="youtubetv", cfg=cfg(dex_path=str(tmp_path / "nope.dex"))))
    assert r.method == "keys" and "not runnable" in r.reason


def test_failed_push_falls_back_to_keys(dex):
    s = SimStick(dex_on_stick=False, push_ok=False)
    r = run(st.seek_to(s, 30.0, app="youtubetv", cfg=cfg(dex_path=str(dex))))
    assert r.method == "keys"


def test_flag_off_is_the_key_path(dex):
    s = SimStick()
    r = run(st.seek_to(s, 30.0, app="youtubetv", cfg=cfg(dex_path=str(dex), enabled=False)))
    assert r.method == "keys" and not any("RzSeek" in c for c in s.cmds)


def test_unreadable_stick_sends_no_keys(dex):
    st._DEX["stickA"] = True
    s = SimStick(hang=True)
    r = run(st.seek_to(s, 30.0, app="youtubetv", cfg=cfg(dex_path=str(dex))))
    assert r.timed_out and r.method == "keys"   # nothing was ever read: SEEK_TO never ran, the presses did
    s2 = SimStick()
    s2.title_after = "Advertisement"
    r2 = run(st.seek_to(s2, 30.0, app="youtubetv", cfg=cfg(dex_path=str(dex))))
    assert r2.ad_suspect and not r2.ok and r2.presses == 0, r2


def test_never_raises(dex):
    s = SimStick()

    async def boom(cmd, timeout=8.0):
        raise RuntimeError("adb exploded")
    s.shell = boom
    r = run(st.seek_to(s, 30.0, app="youtubetv", cfg=cfg(dex_path=str(dex))))
    assert not r.ok and "exception" in r.reason


def test_no_package_falls_back(dex):
    s = SimStick()
    r = run(st.seek_to(s, 30.0, app="prime", cfg=cfg(dex_path=str(dex))))
    assert r.method == "keys" and "no package" in r.reason


# ------------------------------------------------------------------------------------- Controller wiring

def _controller(tmp_path, enabled):
    from redzone import control as ctl
    from redzone.channels import ChannelMap
    from redzone.config import Config
    c = ctl.Controller(Config(tmp_path / "c.yaml"), ChannelMap(tmp_path / "ch.json"))
    c.cfg.set("replay.seek_to.enabled", enabled) if hasattr(c.cfg, "set") else None
    return c


def _with_flag(c, on, dex):
    real = c.cfg.get

    def get(k, d=None):
        if k == "replay.seek_to.enabled":
            return on
        if k == "replay.seek_to.dex_path":
            return str(dex)
        return real(k, d)
    c.cfg = SimpleNamespace(get=get, data=getattr(c.cfg, "data", {}))
    return c


def test_controller_rewind_uses_seek_to_and_logs_the_row(tmp_path, dex):
    c = _with_flag(_controller(tmp_path, True), True, dex)
    c.seek_log_path = tmp_path / "seeks.jsonl"
    s = SimStick(dex_size=1234, bias=(0.4,))
    s.model = "AFTKM"
    c._package = lambda stick, target: PKG
    assert run(c._rewind(s, 30.0, "youtubetv:nbatv"))
    assert s.cmds[-1] == "input keyevent KEYCODE_MEDIA_PLAY"            # plays after the seek, like the key path
    assert not any("MEDIA_REWIND" in x for x in s.cmds)
    assert s.tuned is None
    landed = c._seek_sample(s, "youtubetv:nbatv", 30.0, {"position": 1, "updated": 1})
    assert landed == pytest.approx(30.4, abs=0.01)
    row = json.loads((tmp_path / "seeks.jsonl").read_text())
    assert row["method"] == "seek_to" and row["sent"] == 0 and row["err_s"] == pytest.approx(0.4, abs=0.01)


def test_controller_rewind_flag_off_keeps_the_key_path(tmp_path, dex):
    c = _with_flag(_controller(tmp_path, False), False, dex)
    s = SimStick(dex_size=1234)
    assert run(c._rewind(s, 30.0, "youtubetv:nbatv"))
    assert not any("seekwait" in x for x in s.cmds)
    assert any("KEYCODE_MEDIA_REWIND" in x for x in s.cmds)


def test_key_rewind_row_uses_the_anchor_taken_before_the_presses(tmp_path, dex):
    """Before T7 the presses cleared stick.tuned, so _seek_sample never wrote a key-press row."""
    c = _with_flag(_controller(tmp_path, False), False, dex)
    c.seek_log_path = tmp_path / "seeks.jsonl"
    s = SimStick()
    s.model = "AFTKM"
    s.tuned = {"target": "youtubetv:nbatv", "report": {"position": 600_000, "updated": 1_000_000}}
    assert run(c._rewind(s, 30.0, "youtubetv:nbatv"))
    assert s.tuned is None
    landed = c._seek_sample(s, "youtubetv:nbatv", 30.0, {"position": 590_000, "updated": 1_020_000})
    assert landed == 30.0
    row = json.loads((tmp_path / "seeks.jsonl").read_text())
    assert row["method"] == "keys" and row["sent"] == 3


def test_controller_rewind_fails_safe_when_seek_to_position_unknown(tmp_path, dex):
    c = _with_flag(_controller(tmp_path, True), True, dex)
    st._DEX["stickA"] = True
    s = SimStick(hang=True)
    c._package = lambda stick, target: PKG
    assert run(c._rewind(s, 30.0, "youtubetv:nbatv")) is False
    assert not any("KEYCODE_MEDIA_PLAY" in x for x in s.cmds)


# ------------------------------------------------------------------------------------- bench report

def test_bench_report_gates_per_app_channel_method(tmp_path, capsys):
    from scripts import seek_bench as sb
    rows = [{"app": "youtubetv", "channel": "nbatv", "method": "seek_to", "asked_s": 30, "landed_s": 30 + e,
             "err_s": e, "settle_s": 1.0, "ad": False} for e in (0.2, -0.3, 0.5, 0.1)]
    rows += [{"app": "youtubetv", "channel": "nbatv", "method": "keys", "asked_s": 30, "landed_s": 35.0,
              "err_s": 5.0, "settle_s": 4.0, "ad": False} for _ in range(3)]
    rows += [{"app": "prime", "channel": "tnf", "method": "seek_to", "asked_s": 30, "landed_s": 30.0, "err_s": 0.0,
              "settle_s": 1.0, "ad": True}]
    # a logged app row (data/seeks.jsonl shape): no app/channel/method fields
    rows += [{"target": "youtubetv:espn", "sent": 3, "asked_s": 30.0, "landed_s": 33.0}]
    res = {(r["app"], r["channel"], r["method"]): r for r in sb.summarize(rows)}
    assert res[("youtubetv", "nbatv", "seek_to")]["verdict"] == "PASS"
    assert res[("youtubetv", "nbatv", "keys")]["verdict"] == "FAIL"
    assert res[("prime", "tnf", "seek_to")]["verdict"] == "INSUFFICIENT"
    assert res[("youtubetv", "espn", "keys")]["err_mean"] == 3.0
    f = tmp_path / "b.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    assert sb.main(["report", str(f)]) == 0
    assert "| youtubetv | nbatv | seek_to | 4 |" in capsys.readouterr().out


def test_bench_app_names_and_refusal(monkeypatch, capsys):
    from scripts import seek_bench as sb
    assert sb.app_of("com.amazon.firetv.youtube.tv") == "youtubetv"
    assert sb.app_of("com.amazon.firebat") == "prime"
    assert sb.app_of("com.peacocktv.peacockandroid") == "peacock"
    monkeypatch.setattr(sb, "screen_state", lambda url=sb.STATE_URL: {"hidden": set(), "ok": False, "why": "down"})
    assert sb.main(["run", "1.2.3.4:5555", "--channel", "nbatv"]) == 2
    assert "REFUSED" in capsys.readouterr().err
