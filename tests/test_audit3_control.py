"""AUDIT-3: control helpers."""
import time

from redzone import control as ctl
from redzone.channels import ChannelMap


def test_a_steady_hold_is_still_restored_after_a_restart_an_hour_later(cfg, tmp_path, monkeypatch):
    a = ctl.Controller(cfg, ChannelMap(tmp_path / "ch.json"))
    a.buffer_log_path = tmp_path / "buffer.jsonl"
    t0 = time.time()
    a._set_held("prime:x", 78.9)                       # saved now; nothing changes for an hour
    for k in range(1, 61):                             # keepalive heartbeat, a minute apart
        monkeypatch.setattr(time, "time", lambda k=k: t0 + 61 * k)
        a.touch_held()
    b = ctl.Controller(cfg, ChannelMap(tmp_path / "ch.json"))
    b.buffer_log_path = tmp_path / "buffer.jsonl"
    assert b.restore_held(now=t0 + 61 * 60 + 120) == {"prime:x": 78.9}


def test_a_latency_file_with_the_wrong_shape_means_start_fresh(tmp_path):
    for body in ("[]", "null", '{"samples": [1, 2], "preloaded": "x"}', '{"samples": {"youtubetv": 5}}'):
        p = tmp_path / "lat.json"
        p.write_text(body)
        book = ctl.LatencyBook()
        book.load(p)                                   # must not raise into Controller.start()
        assert book.samples == {} and book.preloaded == {}
