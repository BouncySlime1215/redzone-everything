"""AUDIT-7: the provider book keeps owner overrides through a corrupt file and does not hammer a dead stick."""
import asyncio
from types import SimpleNamespace

from redzone.discovery import providers as prov


def test_corrupt_providers_json_is_set_aside_not_overwritten(tmp_path):
    f = tmp_path / "providers.json"
    f.write_text('{"subs": {"peacock": {"subscribed": false, "by": "you"')   # truncated
    book = prov.ProviderBook(f, clock=lambda: 1000.0)
    book.save()
    kept = list(tmp_path.glob("providers.corrupt-*.json"))
    assert len(kept) == 1 and "peacock" in kept[0].read_text()


def test_a_failed_scan_waits_before_it_is_retried(tmp_path, monkeypatch):
    t = [10_000.0]
    book = prov.ProviderBook(tmp_path / "p.json", clock=lambda: t[0])
    calls = []

    async def dead(st):
        calls.append(t[0])
        return {}
    monkeypatch.setattr(prov, "scan_apps", dead)
    st = SimpleNamespace(host="h1", packages={})
    for _ in range(10):                      # ten 15 s steps
        asyncio.run(book.refresh_sticks([st]))
        t[0] += 15
    assert len(calls) == 1
    t[0] += prov.SCAN_RETRY_S
    asyncio.run(book.refresh_sticks([st]))
    assert len(calls) == 2
