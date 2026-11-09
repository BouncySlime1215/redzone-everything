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
