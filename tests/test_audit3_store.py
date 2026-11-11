"""AUDIT-3: Store._x must read its rows while holding the lock."""
import threading

from redzone.store import Store


def test_concurrent_reads_and_writes_do_not_mix_rows(tmp_path):
    st = Store(tmp_path / "s.db")
    st.snapshot([(float(i), "g", "NFL", 1.0, 1.0, "r", "s") for i in range(200)])
    errs = []

    def writer():
        for i in range(300):
            st.feedback("g", 1, "switch")

    def reader():
        for _ in range(300):
            try:
                rows = st._x("SELECT ts FROM snapshots").fetchall()
                if len(rows) != 200:
                    errs.append(len(rows))
            except Exception as e:  # noqa: BLE001
                errs.append(repr(e))
    ts = [threading.Thread(target=f) for f in (writer, reader, reader)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs
