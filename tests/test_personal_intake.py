"""Personal intake: the live endpoint (flag, guard, payload), the allow-list, the export importer, the profile, the git-ignore.

Why: decisions.md 2026-11-09 (personal data): metadata only, local only, off by default. All data here is synthetic.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from redzone.personal import profile, store
from redzone.web.server import build_app
from test_app import client, engine  # noqa: F401  (fixtures)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import personal_import  # noqa: E402

URL = "/api/personal/app-open"
GOOD = {"app": "TestApp", "timestamp": "2026-11-09T20:15:00Z"}


def _path(engine):
    return store.store_path(Path(engine.cfg.user_path).parent)


def _on(engine):
    engine.cfg.update({"personal": {"enabled": True}})


def test_flag_off_is_404_and_writes_nothing(client, engine):
    assert engine.cfg.get("personal.enabled") is False
    r = client.post(URL, json=GOOD)
    assert r.status_code == 404
    assert not _path(engine).exists() and not _path(engine).parent.exists()


def test_good_event_is_stored_when_on(client, engine):
    _on(engine)
    assert client.post(URL, json=GOOD).json() == {"ok": True}
    rows = store.read_all(_path(engine))
    assert rows == [{"ts": "2026-11-09T20:15:00Z", "service": "TestApp", "title": "", "tags": [], "duration_s": None, "source": "shortcut"}]


@pytest.mark.parametrize("bad", [
    {}, {"app": "A"}, {"timestamp": "2026-11-09T20:15:00Z"},
    {**GOOD, "message": "secret body"}, {**GOOD, "url": "http://x"},
    {"app": "", "timestamp": GOOD["timestamp"]}, {"app": "A", "timestamp": "not a date"},
    {"app": ["A"], "timestamp": GOOD["timestamp"]}, {"app": "A", "timestamp": None}, [1, 2], "text", 5,
])
def test_bad_payload_rejected_and_nothing_written(client, engine, bad):
    _on(engine)
    assert client.post(URL, json=bad).status_code == 400
    assert not _path(engine).exists()


def test_oversize_and_non_json_rejected(client, engine):
    _on(engine)
    assert client.post(URL, content=b"x" * 5000, headers={"content-type": "application/json"}).status_code == 413
    assert client.post(URL, content=b"{nope", headers={"content-type": "application/json"}).status_code == 400
    assert client.post(URL, content=b"a=b", headers={"content-type": "text/plain"}).status_code == 415
    assert not _path(engine).exists()


def test_guard_non_lan_address_refused(engine):
    """The repo's LAN-only middleware covers this route; if it were removed or skipped for it, this fails."""
    _on(engine)
    c = TestClient(build_app(engine), base_url="http://192.168.1.5", client=("8.8.8.8", 5000))   # private Host so only the client-address check can refuse
    assert c.post(URL, json=GOOD).status_code == 403
    assert not _path(engine).exists()


def test_guard_cross_site_refused(client, engine):
    _on(engine)
    assert client.post(URL, json=GOOD, headers={"origin": "https://evil.example"}).status_code == 403
    assert not _path(engine).exists()


def test_token_when_set_is_required(client, engine, monkeypatch):
    _on(engine)
    monkeypatch.setenv("REDZONE_PERSONAL_TOKEN", "synthetic-test-value")
    assert client.post(URL, json=GOOD).status_code == 401
    assert client.post(URL, json=GOOD, headers={"x-redzone-token": "wrong"}).status_code == 401
    assert not _path(engine).exists()
    assert client.post(URL, json=GOOD, headers={"x-redzone-token": "synthetic-test-value"}).status_code == 200


def test_allow_list_drops_other_fields():
    planted = {"ts": "2026-11-09T20:15:00Z", "service": "s", "title": "t", "tags": ["c"], "source": "youtube",
               "message_body": "PLANTED-BODY", "ip_addr": "PLANTED-IP", "url": "PLANTED-URL"}
    c = store.clean(planted)
    assert set(c) == set(store.FIELDS)
    assert "PLANTED" not in json.dumps(c)


def test_importer_never_stores_planted_fields(tmp_path):
    (tmp_path / "in").mkdir()
    yt = [{"header": "YouTube", "title": "Watched Testville Rockets highlights", "titleUrl": "https://PLANTED-URL",
           "subtitles": [{"name": "Test Channel", "url": "https://PLANTED-URL"}], "time": "2026-11-08T01:02:03.000Z",
           "details": [{"name": "PLANTED-DETAIL"}], "message_body": "PLANTED-BODY"}]
    sp = [{"ts": "2026-11-08T02:00:00Z", "ms_played": 90000, "master_metadata_track_name": "Song", "ip_addr": "PLANTED-IP",
           "master_metadata_album_artist_name": "Artist", "conn_country": "PLANTED-CC", "username": "PLANTED-USER"}]
    (tmp_path / "in" / "watch-history.json").write_text(json.dumps(yt))
    (tmp_path / "in" / "Streaming_History_Audio_2026.json").write_text(json.dumps(sp))
    (tmp_path / "in" / "media.csv").write_text("Event Start Timestamp,Item Name,Email Body,Play Duration Milliseconds\n"
                                              "2026-11-07T03:00:00Z,Show,PLANTED-BODY,60000\n")
    s = personal_import.run(tmp_path / "in", tmp_path / "d")
    assert s["written"] == 3
    raw = store.store_path(tmp_path / "d").read_text()
    assert "PLANTED" not in raw and "http" not in raw
    assert personal_import.run(tmp_path / "in", tmp_path / "d")["written"] == 0   # re-import is idempotent
    rows = {r["service"]: r for r in store.read_all(store.store_path(tmp_path / "d"))}
    assert rows["youtube"]["tags"] == ["Test Channel"] and rows["youtube"]["title"] == "Testville Rockets highlights"
    assert rows["spotify"]["duration_s"] == 90.0


@pytest.mark.parametrize("name,content", [
    ("watch-history.json", "{not json"), ("watch-history.json", "[1, null, 'x', {}]"), ("watch-history.json", "[" * 100000),
    ("Streaming_History_Audio_1.json", '[{"ts": "garbage", "ms_played": "x"}, 5, {"ts": 1e400}]'),
    ("media.csv", "a,b\n1\n\x00\x00,\n"), ("media.csv", ""), ("other.json", "{}"), ("watch-history.json", '"str"'),
])
def test_malformed_exports_do_not_crash(tmp_path, name, content):
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / name).write_text(content)
    s = personal_import.run(tmp_path / "in", tmp_path / "d")
    assert s["written"] == 0


def test_oversize_file_and_symlink_skipped(tmp_path, monkeypatch):
    (tmp_path / "in").mkdir()
    real = tmp_path / "real.json"
    real.write_text(json.dumps([{"title": "Watched A", "time": "2026-11-08T01:02:03Z"}]))
    (tmp_path / "in" / "watch-history.json").symlink_to(real)
    assert personal_import.run(tmp_path / "in", tmp_path / "d")["written"] == 0
    monkeypatch.setattr(personal_import, "MAX_BYTES", 5)
    assert personal_import.run(real, tmp_path / "d")["written"] == 0


def test_profile_summary(tmp_path):
    d = tmp_path / "d"
    (d / "personal").mkdir(parents=True)
    (d / "personal" / "teams.json").write_text(json.dumps({"nfl:TST": ["testville rockets"]}))
    store.append(store.store_path(d), [
        {"ts": "2026-11-08T01:00:00Z", "service": "youtube", "title": "Testville Rockets vs Foo NFL recap", "tags": ["Test Channel"], "duration_s": 3600, "source": "youtube"},
        {"ts": "2026-11-08T01:30:00Z", "service": "spotify", "title": "Song", "duration_s": 1800, "source": "spotify"},
        {"ts": "2026-11-08T13:00:00Z", "service": "TestApp"},
    ])
    p = profile.profile(d)
    assert p["events"] == 3 and p["hours_by_service"]["youtube"] == 1.0 and p["hours_by_service"]["spotify"] == 0.5
    assert p["sports_by_team"] == {"nfl:TST": 1} and p["sports_by_league"]["nfl"] == 1
    assert p["active_hours"] == {1: 2, 13: 1}


def test_store_path_is_git_ignored():
    r = subprocess.run(["git", "check-ignore", "-q", "data/personal/events.jsonl"], cwd=ROOT)
    assert r.returncode == 0, "data/personal/ must be git-ignored"
    r = subprocess.run(["git", "check-ignore", "-q", "data/personal/teams.json"], cwd=ROOT)
    assert r.returncode == 0


def test_config_flag_defaults_off(engine):
    assert engine.cfg.get("personal.enabled") is False


def test_profile_command_reads_the_local_store(tmp_path, capsys):
    from scripts import personal_import
    assert personal_import.main(["--profile", "--data-dir", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip().startswith("{")
