"""Tonight's plan: the slate -> sources, gaps and windows; pre-game checks on the hidden stick only."""
import asyncio
import json
from datetime import date, datetime

import pytest

from redzone import plan as P
from redzone.channels import ChannelMap
from redzone.config import Config
from redzone.discovery.providers import Pick, Source
from redzone.engine import Engine
from redzone.models import GameState, League, Phase, Team
from redzone.store import Store
from redzone.tuners.tuners import TuneResult
from test_hot_standby import FakeStick, FakeTuner, two  # noqa: F401  (fixture)

DAY = date(2026, 11, 2)


def at(h, m=0, d=DAY):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=P.ET).timestamp()


def game(gid, away, home, start, league=League.NBA, phase=Phase.PRE, nets=()):
    return GameState(league=league, game_id=gid, home=Team(home), away=Team(away), phase=phase,
                     start_time=start, broadcasts=list(nets))


@pytest.fixture
def ch(tmp_path):
    c = ChannelMap(tmp_path / "ch.json")
    c.codes.update({"espn": "AAAAAAAAAAA"})
    c.prime_gti["wiz"] = "amzn1.dv.gti.wiz"
    return c


def slate():
    return [
        game("espn", "BOS", "NYK", at(19, 30), nets=["ESPN"]),                 # ready, code known
        game("tnt", "LAL", "GSW", at(22), nets=["TNT"]),                      # no YouTube TV code
        game("wiz", "MIA", "WSH", at(19), nets=["Monumental"]),               # your team, Prime GTI
        game("lp", "UTA", "DEN", at(21)),                                     # League Pass, no link yet
        game("pea", "PHI", "DET", at(19, 5), league=League.MLB, nets=["Peacock"]),  # not set up
        game("old", "CHI", "MIL", at(19), phase=Phase.POST),                  # over: left out
        game("tmw", "DAL", "PHX", at(19, d=date(2026, 11, 3))),               # tomorrow: left out
    ]


def build(ch, games=None, now=None):
    return P.build_plan(games or slate(), lambda g: ch.where(g), my_teams={"nba": ["WSH"]}, day=DAY,
                        now=now or at(9))


def by_id(plan):
    return {e["game_id"]: e for e in plan["games"]}


def test_plan_lists_tonights_games_with_source_status_priority_and_gaps(ch):
    plan = build(ch)
    g = by_id(plan)
    assert set(g) == {"espn", "tnt", "wiz", "lp", "pea"}
    assert [e["game_id"] for e in plan["games"]][:2] == ["wiz", "pea"]       # by start, your team first
    assert g["wiz"]["mine"] and g["wiz"]["priority"] == "your team (WSH)"
    assert g["wiz"]["source"]["target"] == "prime:amzn1.dv.gti.wiz" and g["wiz"]["status"] == "ready"
    assert g["espn"]["status"] == "ready" and g["espn"]["start_et"] == "7:30 PM"
    assert g["tnt"]["status"] == "needs setup"
    assert any("No YouTube TV code for TNT" in x for x in g["tnt"]["gaps"])
    assert any("Prime Video event link" in x for x in g["lp"]["gaps"])
    assert g["pea"]["status"] == "skip" and g["pea"]["source"] is None and g["pea"]["check"]["state"] == "n/a"
    assert "No YouTube TV code for TNT: paste its link in Settings" in plan["gaps"]


def test_a_tunable_fallback_makes_a_game_ready():
    g = game("x", "A", "B", at(20))
    pick = Pick("prime:leaguepass", "League Pass", "likely", False, provider="prime",
                alternates=[Source("espn", "espn:123", "ESPN+ · ESPN app", True)])
    plan = P.build_plan([g], lambda _: pick, day=DAY, now=at(9))
    e = plan["games"][0]
    assert e["status"] == "ready" and P._target(e) == "espn:123"
    assert e["fallbacks"][0]["label"] == "ESPN+ · ESPN app"
    assert any("ESPN app" in x for x in plan["gaps"])                          # the known ESPN+ gap


def test_blackout_is_reported_not_planned():
    g = game("x", "CHA", "BOS", at(19))
    from redzone.channels import Where
    plan = P.build_plan([g], lambda _: Where(None, "Prime — blacked out here (local team)", "likely", False),
                        day=DAY, now=at(9))
    assert plan["games"][0]["status"] == "blackout"


def test_windows_group_starts_within_15_minutes(ch):
    plan = build(ch)
    assert [len(w["games"]) for w in plan["windows"]] == [2, 1, 1, 1]        # 7:00+7:05, 7:30, 9:00, 10:00
    assert plan["windows"][0]["check_from"] == at(19) - P.LEAD_S


def test_due_expire_and_record(ch):
    plan = build(ch)
    assert P.due(plan, at(18)) == []
    assert P.due(plan, at(18, 45)) == ["prime:amzn1.dv.gti.wiz"]
    assert P.due(plan, at(19, 15)) == ["youtubetv:espn"]                      # 7 PM games are too close now
    assert P.record_check(plan, "youtubetv:espn", True, "played in 4s", at(19, 15)) == 1
    P.expire(plan, at(19, 15), "not checked: dry run")
    g = by_id(plan)
    assert g["espn"]["check"]["state"] == "verified"
    assert g["wiz"]["check"] == {"state": "not checked", "target": "prime:amzn1.dv.gti.wiz",
                                 "at": at(19, 15), "detail": "not checked: dry run"}


def test_checks_survive_a_rebuild_only_while_the_source_is_the_same(ch, tmp_path):
    plan = build(ch)
    P.record_check(plan, "youtubetv:espn", False, "video never started", at(19))
    P.save(plan, tmp_path)
    assert json.loads(P.path_for(DAY, tmp_path).read_text())["day"] == "2026-11-02"
    again = P.carry_checks(build(ch), P.load(DAY, tmp_path))
    assert by_id(again)["espn"]["check"]["state"] == "failed"
    ch.codes["espn"] = "ZZZZZZZZZZZ"
    ch.overrides["espn"] = "youtubetv:espn2"
    moved = P.carry_checks(build(ch), P.load(DAY, tmp_path))
    assert by_id(moved)["espn"]["check"]["state"] in ("pending", "n/a")


# --- the hidden-stick check -----------------------------------------------------------------

async def test_verify_hidden_tunes_only_the_hidden_stick(two):
    c, tuner = two
    visible, hidden = c.main, c.hidden
    task = c.verify_hidden("youtubetv:espn")
    res = await task
    assert res.ok and tuner.calls == [(hidden.host, "youtubetv:espn")]
    assert visible.host not in [h for h, _ in tuner.calls]
    assert c.standby == ("youtubetv:espn", None) and c.hidden_ready


async def test_verify_hidden_refuses_without_two_sticks_dry_run_or_busy(two, cfg):
    c, tuner = two
    c.hdmi.pop(c.sticks[1].host)                                              # one stick: no checks
    assert c.verify_hidden("youtubetv:espn") is None
    c.hdmi[c.sticks[1].host] = "HDMI2"
    c.input_unknown = True
    assert c.verify_hidden("youtubetv:espn") is None
    c.input_unknown = False
    cfg.update({"mode": {"dry_run": True}})
    assert c.verify_hidden("youtubetv:espn") is None
    cfg.update({"mode": {"dry_run": False}})
    assert c.verify_hidden("youtubetv:nolink") is None                       # no code: nothing to tune
    tuner.slow["youtubetv:espn"] = 0.2
    first = c.verify_hidden("youtubetv:espn")
    assert first is not None and c.verify_hidden("youtubetv:fox") is None     # busy
    await first
    assert tuner.calls == [(c.hidden.host, "youtubetv:espn")]


async def test_a_switch_cancels_a_running_check(two):
    c, tuner = two
    tuner.slow["youtubetv:espn"] = 0.5
    task = c.verify_hidden("youtubetv:espn")
    await asyncio.sleep(0.05)
    c.submit("youtubetv:fox")
    await asyncio.wait({task})
    assert task.cancelled()
    await asyncio.wait_for(c._task, 3)


async def test_a_sleeping_hidden_stick_is_not_woken_for_a_check(two):
    c, tuner = two
    c.sticks[1].power = "Asleep"
    res = await c.verify_hidden("youtubetv:espn")
    assert res is None and tuner.calls == [] and "KEYCODE_WAKEUP" not in c.sticks[1].log


# --- the engine's job -----------------------------------------------------------------------

class FakeControl:
    def __init__(self, ok=True, refuse=False):
        self.two_stick, self.hidden, self.input_unknown = True, object(), False
        self.ok, self.refuse, self.calls = ok, refuse, []

    def _busy(self):
        return False

    def verify_hidden(self, target):
        if self.refuse:
            return None
        self.calls.append(target)

        async def run():
            return TuneResult(self.ok, target, 4.0, ["playing"], "" if self.ok else "video never started")
        return asyncio.ensure_future(run())


@pytest.fixture
def eng(tmp_path, ch):
    cfg = Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist")
    cfg.update({"mode": {"dry_run": False}, "teams": {"nba": ["WSH"]}})
    e = Engine(cfg, Store(tmp_path / "rz.db"))
    e.channels = ch
    e.control = FakeControl()
    e.discovery.choose = lambda g, base: base
    for g in slate():
        e.merger.update("espn_scoreboard", g)
    clock = [at(18, 45)]
    e.tonight.clock = lambda: clock[0]
    e.toasts = []
    e.toast = lambda text, level="info": e.toasts.append(text)
    return e, clock


async def test_engine_checks_the_next_window_and_saves_the_plan(eng, tmp_path):
    e, clock = eng
    await e.tonight.step()
    assert e.control.calls == ["prime:amzn1.dv.gti.wiz"]
    saved = json.loads(P.path_for(DAY, tmp_path).read_text())
    wiz = next(x for x in saved["games"] if x["game_id"] == "wiz")
    assert wiz["check"]["state"] == "verified" and wiz["check"]["detail"] == "played in 4s"
    await e.tonight.step()                                                    # nothing else due yet
    assert e.control.calls == ["prime:amzn1.dv.gti.wiz"]
    view = e.tonight.view()
    assert view["counts"]["verified"] == 1 and view["games"][0]["check"] == "verified"


async def test_engine_waits_while_autopilot_is_busy_or_dry(eng):
    e, clock = eng
    e._pending_decision = object()
    await e.tonight.step()
    assert e.control.calls == [] and e.tonight.plan["note"] == "a switch is in flight"
    e._pending_decision = None
    e.cfg.update({"mode": {"dry_run": True}})
    await e.tonight.step()
    assert e.control.calls == [] and "dry run" in e.tonight.plan["note"]
    e.cfg.update({"mode": {"dry_run": False}})
    live = game("live", "SEA", "POR", at(18), phase=Phase.LIVE)
    e.merger.update("espn_scoreboard", live)
    await e.tonight.step()
    assert e.control.calls == [] and "games are on" in e.tonight.plan["note"]


async def test_engine_records_a_failed_check_and_tells_you(eng):
    e, clock = eng
    e.control.ok = False
    await e.tonight.step()
    wiz = by_id(e.tonight.plan)["wiz"]
    assert wiz["check"]["state"] == "failed" and "video never started" in wiz["check"]["detail"]
    assert e.toasts and "Pre-game check failed" in e.toasts[0]


async def test_engine_marks_games_that_started_unchecked(eng):
    e, clock = eng
    e.control.refuse = True
    e.cfg.update({"mode": {"dry_run": True}})
    await e.tonight.step()
    clock[0] = at(19, 1)
    await e.tonight.step()
    wiz = by_id(e.tonight.plan)["wiz"]
    assert wiz["check"]["state"] == "not checked" and "dry run" in wiz["check"]["detail"]


async def test_plan_tonight_script_writes_the_plan_without_touching_engine_files(tmp_path, monkeypatch, capsys):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "plan_tonight", Path(__file__).resolve().parent.parent / "scripts" / "plan_tonight.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (tmp_path / "channels.json").write_text(json.dumps({"codes": {"espn": "AAAAAAAAAAA"}}))
    before = (tmp_path / "channels.json").read_text()

    async def fake_slate(fetcher, day, leagues):
        return slate()
    monkeypatch.setattr(mod, "slate", fake_slate)
    monkeypatch.setattr(mod.time, "time", lambda: at(12))
    assert await mod.main(["--data", str(tmp_path), "--no-links", "--date", "2026-11-02"]) == 0
    out = capsys.readouterr().out
    assert "BOS @ NYK" in out and "ESPN · YouTube TV" in out and "gap: No YouTube TV code for TNT" in out
    saved = json.loads(P.path_for(DAY, tmp_path).read_text())
    assert {e["game_id"] for e in saved["games"]} == {"espn", "tnt", "wiz", "lp", "pea"}
    assert (tmp_path / "channels.json").read_text() == before
