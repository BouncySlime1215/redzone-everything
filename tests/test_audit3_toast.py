"""AUDIT-3: Engine.toast must not grow a stalled subscriber's queue without bound."""
import asyncio

from redzone.engine import Engine


def test_toast_skips_a_stalled_subscriber():
    class E:
        subscribers = set()
    e = E()
    q = asyncio.Queue()
    e.subscribers.add(q)
    for i in range(500):
        Engine.toast(e, f"t{i}")
    assert q.qsize() <= 25
