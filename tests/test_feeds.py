"""Feed staleness from real fetch times (redzone/sources/feeds.py): an ESPN outage is flagged within
one poll and 5 s of the data being due, a stale game is never entered, and it clears on recovery."""
import httpx

from redzone.models import GameState, League, Phase, Team
from redzone.platform.faults import failing_transport
from redzone.sources.feeds import FeedBook
from redzone.sources.http import FEED_STALE_S, Fetcher
from redzone.sources.merge import Merger

URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event=1"
BOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def game(clock_s=900.0, phase=Phase.LIVE):
    return GameState(league=League.NFL, game_id="1", home=Team("A", score=0), away=Team("B", score=0),
                     phase=phase, period=1, clock_s=clock_s)


def setup(cadence=3.0, urls=(URL,)):
    clk = Clock()
    down = {"on": False}
    f = Fetcher(client=httpx.AsyncClient(transport=failing_transport(lambda: 503 if down["on"] else 200)),
                clock=clk)
    m = Merger(clock=clk)
    m.update("espn_summary", game())
    book = FeedBook(m, f, lambda g: list(urls), lambda gid: cadence, clock=clk)
    return clk, down, f, m, book


async def test_fresh_while_polls_come_back():
    clk, down, f, m, book = setup()
    for _ in range(5):
        await f.get_json(URL)
        assert not book.stale("1")
        clk.t += 3.0
    await f.close()


async def test_an_outage_is_flagged_at_the_first_failed_poll_even_with_the_cached_copy_replayed():
    clk, down, f, m, book = setup()
    await f.get_json(URL)
    down["on"] = True
    clk.t += 3.0
    body = await f.get_json(URL)
    assert body is not None                       # the Fetcher hands back the last good copy...
    m.update("espn_summary", game())              # ...and the poll loop re-feeds it to the merger
    assert not m.is_stale("1")                    # the merger alone can't see the outage
    assert book.stale("1")                        # flagged 3 s in: the host is backing off and data is due
    await f.close()


async def test_no_request_at_all_is_flagged_five_seconds_after_the_data_was_due():
    clk, down, f, m, book = setup(cadence=10.0)
    await f.get_json(URL)
    clk.t += 10.0 + FEED_STALE_S
    assert not book.stale("1")
    clk.t += 0.5
    assert book.stale("1")
    await f.close()


async def test_freshest_source_wins_scoreboard_or_push():
    clk, down, f, m, book = setup(urls=(URL, BOARD))
    await f.get_json(URL)
    clk.t += 20.0
    await f.get_json(BOARD)                       # the board still came back: the game is visible
    assert not book.stale("1")
    clk.t += 20.0
    m.update("espn_fastcast", game(clock_s=880.0))   # a real push
    assert not book.stale("1")
    await f.close()


async def test_recovery_clears_it_at_the_next_good_poll():
    clk, down, f, m, book = setup()
    await f.get_json(URL)
    down["on"] = True
    for _ in range(10):
        clk.t += 3.0
        await f.get_json(URL)
    assert book.stale("1")
    down["on"] = False
    for _ in range(4):                            # ESPN outage backoff is capped at 10 s
        clk.t += 3.0
        await f.get_json(URL)
        m.update("espn_summary", game(clock_s=900.0 - clk.t + 1000.0))
    assert not book.stale("1")
    await f.close()


async def test_finished_or_unknown_games():
    clk, down, f, m, book = setup()
    assert book.stale("nope")
    m.games["1"].phase = Phase.POST
    clk.t += 999
    assert not book.stale("1")
    await f.close()


async def test_never_fetched_by_url_leaves_it_to_the_merger():
    clk, down, f, m, book = setup()
    assert not book.stale("1")                    # demos/tests feed the merger directly
    clk.t += 61
    assert book.stale("1")                        # ...and the merger's 60 s rule still applies
    await f.close()


async def test_snapshot_matches_the_contract():
    from redzone.contracts.feeds import FeedSnapshot
    clk, down, f, m, book = setup()
    await f.get_json(URL)
    snap = book.snapshot(clk.t)
    assert isinstance(snap, FeedSnapshot) and snap.games["1"].fetched_at == clk.t
    assert not snap.games["1"].stale and "1" in snap.live()
    await f.close()


async def test_engine_never_enters_a_game_whose_feed_went_dark(cfg, tmp_path):
    from redzone.engine import Engine
    from redzone.store import Store
    e = Engine(cfg, Store(tmp_path / "t.db"))
    g = game()
    e.merger.update("espn_summary", g)
    e._poll_every["1"] = 3.0
    url = e._feed_urls(g)[0]
    e.fetcher._cache[url] = type("C", (), {"body": {}, "etag": None, "fetched_at": e.merger.games["1"].updated_at - 9})()
    assert e.feed_book.stale("1")                     # 9 s old at a 3 s cadence
    await e.close()


async def test_a_dark_feed_never_makes_the_game_on_screen_unwatchable(cfg, tmp_path):
    """An ESPN blip flags every game stale; the game on screen must stay a candidate (else the
    decider sees 'nothing watchable' and may leave for a catch-up reel)."""
    from redzone.engine import Engine
    from redzone.store import Store
    e = Engine(cfg, Store(tmp_path / "t.db"))
    e.feed_book.stale = lambda gid, now=None: True     # every feed dark
    e.merger.is_stale = lambda gid: False          # ...but no 60 s no-progress yet
    e.decider.current = "1"
    calls = []
    real = e.decider.decide
    e.decider.decide = lambda cands, *a, **k: (calls.append(cands), real(cands, *a, **k))[1]
    e.merger.update("espn_summary", game())
    e.merger.update("espn_summary", GameState(league=League.NFL, game_id="2", home=Team("C", score=0),
                                              away=Team("D", score=0), phase=Phase.LIVE, period=1, clock_s=900.0))
    e.decide()
    assert calls, "decide() never reached the decider"
    cands = calls[-1]
    assert "1" in cands and not cands["1"].stale
    assert "2" in cands and cands["2"].stale
    await e.close()


async def test_a_subclass_that_swaps_in_its_own_merger_is_followed(cfg, tmp_path):
    """The replay demo replaces engine.merger after Engine.__init__ (CI caught every demo game
    reading stale: 201 entries -> 14)."""
    from redzone.engine import Engine
    from redzone.store import Store
    e = Engine(cfg, Store(tmp_path / "t.db"))
    e.merger = Merger()
    e.merger.update("espn_summary", game())
    assert not e.feed_book.stale("1")
    await e.close()
