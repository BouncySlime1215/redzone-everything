"""AUDIT-7: what the store keeps is what the delay book keeps (live-edge relative), so a restart does not
count a hold twice."""
from types import SimpleNamespace

import pytest

from redzone.decider.screen import DelayBook
from redzone.engine import Engine
from redzone.store import Store

T = "prime:wiz"
NOW = 1_800_000_000.0


def _eng(tmp_path):
    b = DelayBook({"prime": 40.0}, 8.0, clock=lambda: NOW)
    b.get(T)
    b.set_behind(T, 60.0)
    store = Store(tmp_path / "rz.db")
    e = SimpleNamespace(delays=b, store=store, _sync_measure=lambda *a, **k: None,
                        sync_probe=SimpleNamespace(frame_k=lambda *a, **k: None))
    return e, b, store


@pytest.mark.parametrize("fn,kind", [(Engine._boundary_sample, "boundary"), (Engine._scorebug_sample, "screen")])
def test_held_sample_is_stored_without_the_hold(tmp_path, fn, kind):
    e, b, store = _eng(tmp_path)
    s = SimpleNamespace(target=T, sample=100.0, flip_wall=NOW, onset=NOW, error_s=0.5)
    fn(e, s)
    rows = store.calibrations(since=0)
    assert rows[0][2] == pytest.approx(40.0) and rows[0][3] == kind
    fresh = DelayBook({"prime": 40.0}, 8.0)
    fresh.load(rows)
    pool = fresh.bounds if kind == "boundary" else fresh.screens
    assert pool[T][0][1] == pytest.approx(40.0)
