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
