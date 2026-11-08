"""tuners.yttv_local_search applies without a restart when tuners.yttv_local_search_live is on (Nov 7)."""
from redzone.config import Config
from redzone.engine import Engine
from redzone.store import Store


def _engine(tmp_path, **tuners):
    cfg = Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist")
    if tuners:
        cfg.update({"tuners": tuners})
    return Engine(cfg, Store(tmp_path / "rz.db"))


def test_shipped_off():
    c = Config()
    assert c.get("tuners.yttv_local_search_live", None) is False
    assert c.get("tuners.yttv_local_search", None) is False


def test_gate_off_keeps_the_startup_value(tmp_path):
    e = _engine(tmp_path)
    assert e.channels.local_search is False
    e.cfg.update({"tuners": {"yttv_local_search": True}})      # what POST /api/settings does
    assert e._apply_live_settings() is False
    assert e.channels.local_search is False                     # still needs a restart, as before


def test_gate_on_applies_a_settings_change_and_undo(tmp_path):
    e = _engine(tmp_path, yttv_local_search_live=True)
    assert e.channels.local_search is False
    e.cfg.update({"tuners": {"yttv_local_search": True}})
    assert e._apply_live_settings() is True and e.channels.local_search is True
    assert e._apply_live_settings() is False                    # no change, no churn
    e.cfg.rollback()                                            # Undo the flag
    assert e._apply_live_settings() is True and e.channels.local_search is False


def test_startup_value_still_read_with_gate_on(tmp_path):
    e = _engine(tmp_path, yttv_local_search_live=True, yttv_local_search=True)
    assert e.channels.local_search is True
    assert e._apply_live_settings() is False
