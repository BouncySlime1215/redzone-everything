"""Card D web fixes: the settings API hides secrets and the port (P-14, P-19), /api/v2/vote survives junk (P-17),
Undo says why it did nothing (P-12)."""
import pytest

from test_app import client, engine  # noqa: F401  (fixtures)


def test_p14_get_settings_does_not_contain_the_ntfy_topic(client, engine):
    """Repro: GET /api/settings returned engine.cfg.data whole, so any device on the Wi-Fi could read
    supervisor.ntfy_topic (the one secret of the public ntfy.sh channel)."""
    engine.cfg.update({"supervisor": {"ntfy_topic": "hard-to-guess-topic-123"}})
    r = client.get("/api/settings")
    assert "hard-to-guess-topic-123" not in r.text
    assert r.json()["config"]["supervisor"]["ntfy_topic"] == "(set)"
    engine.cfg.update({"supervisor": {"ntfy_topic": ""}})
    assert client.get("/api/settings").json()["config"]["supervisor"]["ntfy_topic"] == ""
    assert client.get("/api/settings").json()["config"]["mode"]["dry_run"] in (True, False)   # the rest is intact


def test_p19_server_port_is_not_phone_editable(client, engine):
    """Repro: LIMITS made server.port editable, but __main__ takes PORT from the environment first: a control that
    does nothing, and the only one that could move the app off 8778."""
    before = engine.cfg.get("server.port")
    r = client.post("/api/settings", json={"changes": {"server": {"port": 9999}}}).json()
    assert r["ok"] is False and "server.port" in r["message"]
    assert engine.cfg.get("server.port") == before
    assert "server.port" not in client.get("/api/settings").json()["limits"]
    assert client.post("/api/settings", json={"changes": {"decider": {"manual_pause_s": 50}}}).json()["ok"]


def test_p12_rollback_with_nothing_saved_says_so_and_changes_nothing(client, engine):
    before = dict(engine.cfg.overrides)
    r = client.post("/api/settings/rollback").json()
    assert r["ok"] is False and "Nothing to undo" in r["message"]
    assert engine.cfg.overrides == before


@pytest.mark.parametrize("bad", ["x", [1], {"a": 1}, "1.5", None])
def test_p17_vote_with_junk_returns_ok_false_not_a_500(client, engine, bad):
    """Repro: int(body.get("vote") or 0) raised ValueError/TypeError for "x" or [1]: HTTP 500 plus an ERROR
    traceback in service.log (which feed the watchdog's and deploy's error counts)."""
    engine.cfg.update({"phone": {"v2": True}})
    r = client.post("/api/v2/vote", json={"key": "k", "vote": bad})
    assert r.status_code == 200 and r.json()["ok"] is False
