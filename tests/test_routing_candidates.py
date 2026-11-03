from datetime import datetime
from zoneinfo import ZoneInfo

from redzone.channels import ChannelMap, Where
from redzone.discovery.providers import ProviderBook
from redzone.models import GameState, League, Team


def fixture(tmp_path, broadcasts, league=League.NBA):
    game = GameState(league=league, game_id="test-event", home=Team("PHI"), away=Team("WSH"),
                     broadcasts=broadcasts,
                     start_time=datetime(2026, 11, 1, 16, 25,
                                         tzinfo=ZoneInfo("America/New_York")).timestamp())
    channels = ChannelMap(tmp_path / "channels.json")
    channels.codes = {"abc": "abc-code", "espn": "espn-code", "cbs": "cbs-code"}
    book = ProviderBook(tmp_path / "providers.json")
    book.saw_apps("stick", {"youtubetv": "yttv"})
    return game, channels, book


def test_second_listing_is_available_when_first_provider_link_is_missing(tmp_path):
    game, channels, book = fixture(tmp_path, ["ESPN+", "ABC"])
    base = channels.where(game)
    assert not base.tunable
    pick = book.choose(game, base, channel_map=channels)
    assert pick.target == "youtubetv:abc" and pick.tunable


def test_learned_failure_can_fall_back_to_second_known_channel(tmp_path):
    game, channels, book = fixture(tmp_path, ["ESPN", "ABC"])
    book.skips["youtubetv:espn"] = {"kind": "failed", "at": book._now(), "why": "launch failed"}
    pick = book.choose(game, channels.where(game), channel_map=channels)
    assert pick.target == "youtubetv:abc" and pick.tunable


def test_afternoon_sunday_nfl_never_promotes_generic_local_affiliate(tmp_path):
    game, channels, book = fixture(tmp_path, ["CBS"], League.NFL)
    sources = book.sources(game, channels.where(game), channel_map=channels)
    assert all(s.target != "youtubetv:cbs" for s in sources)
    assert any(s.target == "youtubetv:st" and not s.tunable for s in sources)


def test_manual_event_route_stays_authoritative(tmp_path):
    game, channels, book = fixture(tmp_path, ["ESPN+", "ABC"])
    base = Where("prime:amzn1.dv.gti.owner", "Owner choice", "you set it", True)
    pick = book.choose(game, base, channel_map=channels)
    assert pick.target == base.target


def test_missing_code_is_not_invented_for_second_listing(tmp_path):
    game, channels, book = fixture(tmp_path, ["ESPN+", "ABC"])
    channels.codes.clear()
    pick = book.choose(game, channels.where(game), channel_map=channels)
    assert not pick.tunable


def test_disabled_sunday_ticket_is_not_reintroduced_as_an_alternate(tmp_path):
    game, channels, book = fixture(tmp_path, ["CBS"], League.NFL)
    channels.st_slots[game.game_id] = "known-slot"
    base = channels.where(game, sunday_ticket=False)
    pick = book.choose(game, base, channel_map=channels, sunday_ticket=False)
    assert not pick.tunable
    assert all(not (s.target or "").startswith("youtubetv:st")
               for s in book.sources(game, base, channels, sunday_ticket=False))
