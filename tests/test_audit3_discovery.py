"""AUDIT-3: ProviderBook.prune must bound every table it grows."""
from redzone.discovery.providers import LINKS_KEEP_S, SKIP_S, ProviderBook


def _book(tmp_path, t):
    return ProviderBook(tmp_path / "p.json", clock=lambda: t[0])


def test_prune_forgets_stale_stick_walls_and_fail_counts(tmp_path):
    t = [1_000_000.0]
    b = _book(tmp_path, t)
    b.stick_skips = {"a": {"x:1": {"kind": "upsell", "at": t[0]}}, "b": {"x:2": {"kind": "upsell", "at": t[0]}}}
    b._fails = {"x:1": [t[0]], "x:2": [t[0]]}
    t[0] += SKIP_S["upsell"] + 1
    b.stick_skips["b"]["x:3"] = {"kind": "upsell", "at": t[0]}
    b._fails["x:3"] = [t[0]]
    b.prune()
    assert b.stick_skips == {"b": {"x:3": {"kind": "upsell", "at": t[0]}}}
    assert list(b._fails) == ["x:3"]


def test_links_without_a_start_time_expire_too(tmp_path):
    t = [1_000_000.0]
    b = _book(tmp_path, t)
    b.links = {"g1": {"start": None, "providers": {}}, "g2": {"start": t[0], "providers": {}}}
    b.prune()                      # first sight of g1: stamped, kept
    assert "g1" in b.links and b.links["g1"]["seen"] == t[0]
    t[0] += LINKS_KEEP_S + 1
    b.prune()
    assert b.links == {}
