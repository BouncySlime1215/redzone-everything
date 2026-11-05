"""Weather (Open-Meteo) and CFBD context scripts (worker B3): offline fixtures only, no network, no key."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "context" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


wx = _load("weather")
cfbd = _load("cfbd_context")


def _hourly():
    return wx.parse_hourly(json.loads((FIX / "open_meteo" / "archive_metlife_2023.json").read_text()))


def _game(gid="2023_01_X_NYJ", kick="2023-09-10T18:25:00Z", roof="outdoors", venue_roof="outdoors"):
    return pd.DataFrame([{"game_id": gid, "season": 2023, "kickoff_utc": pd.Timestamp(kick), "venue_id": "NYC01",
                          "roof": roof, "venue_roof": venue_roof, "lat": 40.8135, "lon": -74.0745}])


def test_parse_and_kickoff_hour():
    h = _hourly()
    assert str(h.index.tz) == "UTC" and len(h) == 6
    df = pd.DataFrame([f.row() for f in wx.facts_for(_game(), h, "open-meteo-archive")])
    v = df.set_index("field")["value"]
    assert v["temp_f"] == 72.4 and v["wind_mph"] == 8.8 and v["precip_mm"] == 1.2 and v["snow"] == 0
    assert (df.asof_utc == "2023-09-10T18:24:00Z").all()
    assert (df.kind == "new").all() and df.license.str.contains("Open-Meteo CC BY 4.0").all()


def test_forecast_after_kickoff_is_dropped():
    h = _hourly()
    assert wx.facts_for(_game(), h, "open-meteo-forecast", asof_utc=pd.Timestamp("2023-09-10T18:30:00Z")) == []
    ok = wx.facts_for(_game(), h, "open-meteo-forecast", asof_utc=pd.Timestamp("2023-09-10T12:00:00Z"))
    assert ok and all(f.asof_utc == "2023-09-10T12:00:00Z" for f in ok)


def test_units_are_checked():
    js = json.loads((FIX / "open_meteo" / "archive_metlife_2023.json").read_text())
    js["hourly_units"]["temperature_2m"] = "°C"
    with pytest.raises(ValueError):
        wx.parse_hourly(js)


def test_plan_one_call_per_venue_season_and_skip_domes():
    g = pd.concat([_game("a"), _game("b", kick="2023-12-24T18:00:00Z"), _game("c", roof="closed", venue_roof="retractable"),
                   _game("d", roof=None, venue_roof="dome")], ignore_index=True)
    plan = wx.plan_calls(g)
    assert len(plan) == 1 and plan.iloc[0]["games"] == 2
    assert plan.iloc[0]["start"] == "2023-09-10" and plan.iloc[0]["end"] == "2023-12-24"
    url = wx.archive_url(40.8135, -74.0745, "2023-09-10", "2023-12-24")
    assert url.startswith(wx.ARCHIVE) and "temperature_unit=fahrenheit" in url and "wind_speed_unit=mph" in url


def test_venues_csv_is_sane():
    v = wx.load_venues()
    assert v["lat"].between(-90, 90).all() and v["lon"].between(-180, 180).all()
    assert not v.duplicated(["league", "venue_id"]).any()
    assert {"GNB00", "NYC01", "KAN00"} <= set(v[v.league == "nfl"].venue_id)
    assert len(v[(v.league == "mlb") & v.last_season.isna()]) >= 28


def test_mlb_kickoff_from_daynight_and_tz():
    g = pd.DataFrame([{"game_id": "BOS202407040", "season": 2024, "kickoff_utc": pd.Timestamp("2024-07-04 23:00", tz="UTC"),
                       "venue_id": "BOS07", "daynight": "N", "roof": None}])
    out = wx.with_venues(g, wx.load_venues(league="mlb"))
    assert out.iloc[0]["kickoff_utc"] == pd.Timestamp("2024-07-04 23:10", tz="UTC")   # 19:10 EDT


def _cfb_frame(tmp_path):
    cl = cfbd.Client(cache=None, offline_dir=FIX / "cfbd")
    facts, games = cfbd.season_facts(cl, 2023)
    return pd.DataFrame([f.row() for f in facts]), games, cl


def test_cfbd_rankings_use_prior_week_only(tmp_path):
    df, _, cl = _cfb_frame(tmp_path)
    assert cl.calls == 0
    ap = df[df.field == "ap_rank"].set_index(["game_id", "team"])["value"]
    assert ("401001", "North Carolina") not in ap.index            # week 1: no poll before it
    assert ap[("401002", "North Carolina")] == 21                   # week-1 poll for the week-2 game
    assert ap[("401003", "North Carolina")] == 17
    assert ap[("401009", "North Carolina")] == 15                   # bowl: last regular poll, never the final (2)
    co = df[df.field == "coaches_rank"].set_index(["game_id", "team"])["value"]
    assert co[("401002", "North Carolina")] == 20


def test_cfbd_postgame_numbers_only_from_earlier_games(tmp_path):
    df, games, _ = _cfb_frame(tmp_path)
    ex = df[df.field == "prev_game_excitement"].set_index(["game_id", "team"])["value"]
    assert ("401001", "North Carolina") not in ex.index
    assert ex[("401002", "North Carolina")] == pytest.approx(4.2)
    assert ex[("401003", "North Carolina")] == pytest.approx(7.9)
    att = df[df.field == "prev_home_attendance"].set_index(["game_id", "team"])["value"]
    assert att[("401003", "North Carolina")] == 49000
    kick = games.set_index("game_id")["kickoff_utc"]
    assert (pd.to_datetime(df.asof_utc, utc=True) < df.game_id.map(kick)).all()


def test_cfbd_media_weather_records_talent(tmp_path):
    df, _, _ = _cfb_frame(tmp_path)
    g = df[df.team.isna()]
    tv = g[g.field == "tv_network"].set_index("game_id")["value_str"]
    nat = g[g.field == "national_tv"].set_index("game_id")["value"]
    assert tv["401001"] == "abc" and nat["401001"] == 1
    assert tv["401002"] == "acc_network" and nat["401002"] == 0
    assert set(g[g.field == "temp_f"].game_id) == {"401001"}         # the indoor game has no weather row
    t = df[(df.field == "talent") & (df.team == "Duke")]["value"]
    assert (t == 640.5).all()
    assert df[(df.field == "prev_season_win_pct") & (df.team == "North Carolina")]["value"].iloc[0] == pytest.approx(9 / 14, abs=1e-4)
    assert (df.license.str.contains("credit CFBD")).all() and set(df.kind) == {"new"}
    assert not any("lines" in s for s in df.source)


def test_cfbd_venues_hook_and_no_key(tmp_path, monkeypatch):
    v = cfbd.venues_csv(cfbd.Client(offline_dir=FIX / "cfbd"))
    assert set(v.columns) >= {"league", "venue_id", "lat", "lon", "tz", "roof"} and set(v.roof) == {"outdoors", "dome"}
    monkeypatch.delenv("CFBD_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        cfbd.Client(cache=tmp_path).get("/games", year=2023)


def test_cfbd_cli_offline_writes_shard(tmp_path, capsys):
    c = cfbd.main(["facts", "--years", "2023", "--offline-dir", str(FIX / "cfbd"), "--out", str(tmp_path)])
    assert c["facts"] > 0 and c["cfbd_calls"] == 0
    df = pd.read_parquet(tmp_path / "cfb-context" / "2023.parquet")
    assert list(df.columns)[:4] == ["game_id", "team", "asof_utc", "field"]
