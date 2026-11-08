"""Undo must follow edit order, including rapid saves and existing histories."""
import yaml

from redzone.config import Config


def test_rapid_saves_undo_in_edit_order_when_old_suffix_wraps(tmp_path, monkeypatch):
    clock = iter([1_800_000_000_000_900_000, 1_800_000_000_001_100_000,
                  1_800_000_000_002_200_000])
    monkeypatch.setattr("redzone.config.time.time_ns", lambda: next(clock))
    cfg = Config(tmp_path / "config.yaml", tmp_path / "history")
    for port in [8778, 8779, 8780]:
        cfg.update({"server": {"port": port}})

    assert len(list(cfg.history_dir.glob("*.yaml"))) == 3
    assert cfg.rollback() and cfg.get("server.port") == 8779
    assert cfg.rollback() and cfg.get("server.port") == 8778
    assert cfg.rollback() and cfg.get("server.port") == cfg.defaults["server"]["port"]


def test_same_or_backward_clock_does_not_overwrite_or_reorder_after_reload(tmp_path, monkeypatch):
    clock = iter([1_800_000_000_100_000_000, 1_800_000_000_100_000_000,
                  1_799_999_999_000_000_000])
    monkeypatch.setattr("redzone.config.time.time_ns", lambda: next(clock))
    path, history = tmp_path / "config.yaml", tmp_path / "history"
    cfg = Config(path, history)
    cfg.update({"server": {"port": 8778}})
    cfg.update({"server": {"port": 8779}})
    cfg = Config(path, history)
    cfg.update({"server": {"port": 8780}})

    versions = sorted(history.glob("*.yaml"))
    assert [yaml.safe_load(p.read_text())["server"]["port"] for p in versions] == [8778, 8779, 8780]
    assert cfg.rollback() and cfg.get("server.port") == 8779
    assert cfg.rollback() and cfg.get("server.port") == 8778


def test_new_revisions_undo_into_legacy_history(tmp_path, monkeypatch):
    history = tmp_path / "history"
    history.mkdir()
    (history / "20261102-120000-900000.yaml").write_text("server:\n  port: 8778\n")
    monkeypatch.setattr("redzone.config.time.time_ns", lambda: 1)
    cfg = Config(tmp_path / "config.yaml", history)
    cfg.update({"server": {"port": 8779}})

    assert cfg.rollback() and cfg.get("server.port") == 8778
    assert len(list(history.glob("*.yaml"))) == 1


def test_p12_undo_with_no_history_leaves_the_saved_settings_alone(tmp_path):
    """Repro: rollback() with 0 versions called _save({}) (every override gone: dry_run back on, no teams)
    and still returned False, which the phone showed as 'Nothing to undo'."""
    path = tmp_path / "config.yaml"
    path.write_text("mode:\n  dry_run: false\nserver:\n  port: 8779\n")
    cfg = Config(path, tmp_path / "history")
    assert cfg.get("mode.dry_run") is False
    assert cfg.rollback() is False
    assert "Nothing to undo" in cfg.rollback_note
    assert path.read_text() == "mode:\n  dry_run: false\nserver:\n  port: 8779\n"
    assert cfg.get("mode.dry_run") is False and cfg.get("server.port") == 8779


def test_p12_the_first_undo_goes_back_to_settings_that_predate_the_history(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("mode:\n  dry_run: false\n")
    cfg = Config(path, tmp_path / "history")
    cfg.update({"server": {"port": 8780}})
    assert cfg.rollback() is True
    assert cfg.get("mode.dry_run") is False and cfg.get("server.port") == cfg.defaults["server"]["port"]


def test_p12_undo_keeps_what_the_app_measured_itself(tmp_path):
    cfg = Config(tmp_path / "config.yaml", tmp_path / "history")
    cfg.update({"server": {"port": 8778}})
    cfg.update({"server": {"port": 8779}})
    cfg.update({"decider": {"switch_cost": {"espn": 9.0}}}, record=False)
    cost = cfg.get("decider.switch_cost")
    assert cfg.rollback() and cfg.get("server.port") == 8778
    assert cfg.get("decider.switch_cost") == cost                 # the measurement survived the undo
    assert cfg.rollback() and cfg.get("decider.switch_cost") == cost


def test_p16_history_is_pruned_to_the_newest_versions(tmp_path):
    from redzone.config import Config as C
    cfg = C(tmp_path / "config.yaml", tmp_path / "history")
    cfg.HISTORY_KEEP = 5
    for port in range(8700, 8712):
        cfg.update({"server": {"port": port}})
    assert len(list((tmp_path / "history").glob("*.yaml"))) == 5
    assert cfg.rollback() and cfg.get("server.port") == 8710
