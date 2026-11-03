"""Crash-safe state: a file left broken by a crash, a full disk or a bad copy never stops the app
from starting, and nothing is deleted."""
import sqlite3

import pytest

from redzone.config import Config
from redzone.store import Store


def test_a_database_that_isnt_one_is_set_aside_and_a_fresh_one_starts(tmp_path):
    db = tmp_path / "redzone.db"
    db.write_bytes(b"SQLite format 3\x00" + b"\x00" * 50)
    s = Store(db)
    s.switch(ts=1.0, to_game="g", ok=True)
    assert s.recent_switches(5)
    aside = [p.name for p in tmp_path.iterdir() if ".corrupt-" in p.name]
    assert len(aside) == 1                                                  # kept, never deleted
    assert (tmp_path / aside[0]).read_bytes().startswith(b"SQLite format 3")


def test_a_database_we_merely_cant_open_is_never_moved(tmp_path):
    db = tmp_path / "redzone.db"
    db.mkdir()                                   # "unable to open database file": OperationalError
    with pytest.raises(sqlite3.OperationalError):
        Store(db)
    assert db.is_dir() and not [p for p in tmp_path.iterdir() if ".corrupt-" in p.name]


def test_a_healthy_database_is_reopened_as_is(tmp_path):
    db = tmp_path / "redzone.db"
    Store(db).switch(ts=1.0, to_game="g", ok=True)
    assert len(Store(db).recent_switches(5)) == 1


def test_an_unreadable_config_brings_back_your_last_saved_settings(tmp_path):
    c = Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist")
    c.update({"mode": {"dry_run": False}})
    (tmp_path / "config.yaml").write_bytes(b"\x00\x00 truncated")
    c2 = Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist")
    assert c2.get("mode.dry_run") is False                 # not silently back to dry run
    assert c2.rejected and "restored" in c2.rejected[0]


def test_an_unreadable_config_with_no_history_uses_defaults(tmp_path):
    (tmp_path / "config.yaml").write_bytes(b"\x00\x00")
    c = Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist")
    assert c.get("mode.dry_run") is True and c.rejected and "restored" not in c.rejected[0]
