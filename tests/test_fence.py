"""Leak fence (redzone/lab/fence.py, task F-09): fenced nights, the model guard and hash disjointness.

The refusal test is the F-09 bar: a model trained inside the held-out window is refused, with a message
that says why. The archive-backed test runs wherever the pinned eval data is unpacked (CI does it).
"""
import json
from pathlib import Path

import numpy as np
import pytest

from redzone.lab import fence, splits

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = ROOT / "data" / "archive"


def _days(start, end):
    from datetime import date, timedelta
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    return [(a + timedelta(d)).strftime("%Y%m%d") for d in range((b - a).days + 1)]


def test_fences_are_the_planned_windows():
    assert (fence.LOCKED.start, fence.LOCKED.end) == ("20251212", "20260105")
    assert (fence.TUNING.start, fence.TUNING.end) == ("20261001", "20261031")
    assert fence.fenced("2025-12-12") is fence.LOCKED and fence.fenced("20260105") is fence.LOCKED
    assert fence.fenced("20251211") is None and fence.fenced("20260106") is None
    assert fence.fenced("2026-10-15") is fence.TUNING and fence.fenced("20261101") is None
    assert "20251225" in fence.LOCKED and "20261031" in fence.TUNING
    with pytest.raises(ValueError):
        fence.night_key("20251332")


def test_a_training_set_with_fenced_nights_is_refused():
    nights = _days("2025-12-01", "2025-12-20")
    with pytest.raises(fence.FenceError, match=r"REFUSED training set: 9 locked night"):
        fence.assert_train_nights(nights)
    with pytest.raises(fence.FenceError, match="tuning"):
        fence.assert_train_nights(["20260930", "20261001"])
    clean = fence.trainable(nights + _days("2026-09-28", "2026-10-03"))
    fence.assert_train_nights(clean)
    assert "20251211" in clean and "20251212" not in clean and "20261001" not in clean


def _npz(path: Path, meta: dict) -> Path:
    np.savez(path, meta=np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8))
    return path


def test_a_model_trained_inside_the_held_out_window_is_refused(tmp_path):
    """The F-09 done-bar: the loader refuses it and names the reason."""
    p = _npz(tmp_path / "nba_inside.npz", {"training_cutoff": "2025-12-20"})
    with pytest.raises(fence.FenceError) as e:
        fence.load_guarded(p, eval_nights=["20251215", "20251220", "20260101"])
    msg = str(e.value)
    assert msg.startswith(f"REFUSED {p}:")
    assert "training_cutoff 20251220 is inside the locked window 20251212..20260105: trained inside the held-out window" in msg
    assert "on or after the first eval night 20251215" in msg


def test_refusals_cover_every_way_through_the_fence(tmp_path):
    lock = _days("2025-12-12", "2026-01-05")
    # no cutoff at all (the state of every shipped model today)
    assert fence.model_problems({})[0].startswith("no training_cutoff")
    assert fence.model_problems({}, require_cutoff=False) == []
    # trained inside the tuning window
    assert any("inside the tuning window" in x for x in fence.model_problems({"training_cutoff": "2026-10-10"}))
    # cutoff after the locked window without a night list: nothing proves the locked nights were left out
    assert any("lists no train_nights" in x for x in fence.model_problems({"training_cutoff": "2026-02-01"}))
    # a night list that includes locked nights
    meta = {"training_cutoff": "2026-02-01", "train_nights": _days("2025-11-01", "2026-02-01")}
    assert any("locked night(s) in the training set" in x for x in fence.model_problems(meta))
    # a stamp that no longer matches its list
    good = fence.trainable(_days("2025-11-01", "2026-02-01"))
    meta = {"training_cutoff": "2026-02-01", "train_nights": good, "train_nights_sha256": fence.nights_sha256(good[1:])}
    assert any("does not match" in x for x in fence.model_problems(meta))
    # a list that runs past its own cutoff
    assert any("after training_cutoff" in x for x in fence.model_problems({"training_cutoff": "2025-11-10",
                                                                            "train_nights": ["20251111"]}))
    # scored on nights it trained on (time order and hash overlap)
    meta = {"training_cutoff": "2025-11-30", "train_nights": _days("2025-11-01", "2025-11-30")}
    bad = fence.model_problems(meta, eval_nights=["20251120"])
    assert any("first eval night 20251120" in x for x in bad) and any("also train nights" in x for x in bad)
    # legacy stamp is read the same way
    assert any("inside the locked window" in x for x in fence.model_problems({"trained_through": "20251225"}))
    # the clean cases pass
    fence.guard_model({"training_cutoff": "2025-12-11"}, eval_nights=lock)
    fence.guard_model({"training_cutoff": "2026-11-05", "train_nights": fence.trainable(_days("2025-10-21", "2026-11-05")),
                       "train_nights_sha256": fence.nights_sha256(fence.trainable(_days("2025-10-21", "2026-11-05")))},
                      eval_nights=["20261110", "20261111"])


def test_read_meta_reads_npz_and_sidecars(tmp_path):
    assert fence.read_meta(_npz(tmp_path / "a.npz", {"training_cutoff": "2025-01-01"}))["training_cutoff"] == "2025-01-01"
    np.savez(tmp_path / "b.npz", x=np.zeros(1))
    assert fence.read_meta(tmp_path / "b.npz") == {}
    (tmp_path / "b.meta.json").write_text(json.dumps({"trained_through": "20250101"}))
    assert fence.cutoff_of(fence.read_meta(tmp_path / "b.npz")) == "20250101"


def test_disjointness_is_by_content_hash(tmp_path):
    a, b, c = (tmp_path / f"summary_{i}.json" for i in (1, 2, 3))
    a.write_text('{"id": 1}')
    b.write_text('{"id": 2}')
    c.write_text('{"id": 1}')                        # the same game filed under another id and night
    assert fence.disjoint_by_hash([a], [b]) == {}
    hits = fence.disjoint_by_hash([a, b], [c])
    assert list(hits.values()) == [str(c)]
    assert fence.disjoint_by_hash(["20251201"], ["20251201", "20251202"], key=lambda n: n) != {}


def test_trainable_nights_and_the_locked_window_are_disjoint_by_hash():
    every = _days("2025-10-01", "2026-11-12")
    train = fence.trainable(every)
    locked = [n for n in every if n in fence.LOCKED]
    assert len(locked) == 25
    assert fence.disjoint_by_hash(train, locked, key=lambda n: n) == {}
    assert fence.nights_sha256(train) == fence.nights_sha256(reversed(train))


def test_pinned_archive_train_and_locked_games_are_disjoint_by_hash():
    """Checks every league whose pinned archive is unpacked (CI unpacks it); no archive, nothing to check."""
    from redzone.lab import evaldata
    if not ARCHIVE.exists():
        return
    checked = 0
    for lg in ("nfl", "nba", "nhl"):
        d = ARCHIVE / lg
        if not d.exists():
            continue
        an = splits.archive_nights(d, evaldata.window_end(lg, None))
        s = splits.league_split(lg, an)
        fit, test = fence.game_files(d, an, s.train + s.valid), fence.game_files(d, an, s.test)
        assert fence.disjoint_by_hash(fit, test) == {}, lg
        checked += len(test)
    assert checked > 0


def test_fence_check_script_reports_without_failing_on_unstamped_models(capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("fence_check", ROOT / "scripts" / "fence_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rows = mod.model_rows()
    assert rows and all(s in ("OK", "REFUSED") for s, _, _ in rows)
    assert mod.main([]) == (1 if any(r[0] == "FAIL" for r in mod.archive_rows()) else 0)
    assert "model redzone/" in capsys.readouterr().out
