"""AUDIT-2, Fetcher: the cache stays bounded on a long night, and a good byte fetch ends the host's backoff.

Why: _cache kept one entry per URL ever fetched (game ids change every day), and get_bytes never reset the
backoff that get_json resets on success, so one image failure kept the host in backoff after it recovered.
"""
import httpx

from redzone.sources import http as H


class Clk:
    t = 1000.0

    def __call__(self):
        return self.t


def _fetcher(handler, clk=None):
    return H.Fetcher(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), clock=clk or Clk())


async def test_cache_is_bounded_and_keeps_the_newest():
    clk = Clk()
    f = _fetcher(lambda r: httpx.Response(200, json={"u": str(r.url)}), clk)
    n = H.MAX_CACHE_ENTRIES + 50
    for i in range(n):
        clk.t += 1
        await f.get_json(f"https://example.com/g/{i}")
    assert len(f._cache) <= H.MAX_CACHE_ENTRIES
    assert f.last_good(f"https://example.com/g/{n - 1}") is not None
    assert f.last_good("https://example.com/g/0") is None
    assert len(f._locks) <= H.MAX_CACHE_ENTRIES + 1
