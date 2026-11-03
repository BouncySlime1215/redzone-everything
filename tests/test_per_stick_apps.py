"""Per-stick app capability (Peacock signed in on one stick only) and Peacock as a last resort."""
from redzone.channels import ChannelMap
from redzone.discovery import providers as prov
from redzone.models import GameState, League, Phase, Team

A, B = "192.168.1.181", "192.168.1.150"
PICK_PLAN = prov.Verdict("upsell", "pick a plan").message("Peacock")
SIGNED_OUT = prov.Verdict("signin", "sign in").message("Peacock")


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def game(home="CLE", away="OKC", gid="g1", broadcasts=()):
    return GameState(league=League.NBA, game_id=gid, home=Team(home), away=Team(away),
                     phase=Phase.LIVE, broadcasts=list(broadcasts))


def src(provider, target, tunable=True, **kw):
    return prov.Source(provider, target, kw.pop("label", provider), tunable, **kw)


def two_sticks(path, clock=None):
    book = prov.ProviderBook(path / "p.json", clock=clock or Clock())
    both = {"peacock": "com.peacock.peacockfiretv", "youtubetv": "com.amazon.firetv.youtube.tv"}
    book.saw_apps(A, dict(both))
    book.saw_apps(B, dict(both))
    return book


def test_a_wall_on_one_stick_never_marks_the_whole_app_out(tmp_path):
    book = two_sticks(tmp_path)
    assert book.learn("peacock:x", True, "", "g1", host=A) == ""
    note = book.learn("peacock:y", False, PICK_PLAN, "g2", host=B)   # .150: "Pick a plan / Sign in"
    assert "other stick" in note
    st = book.states()["peacock"]
    assert st.usable and st.blocked == (B,)
    assert not book.can_play(B, "peacock:y") and not book.can_play(B, "peacock:z")
    assert book.can_play(A, "peacock:y") and book.can_play(B, "youtubetv:nbc")
    assert "peacock:y" not in book.active_skips()                    # the link stays a candidate
    again = prov.ProviderBook(tmp_path / "p.json", clock=book._now)   # survives a restart
    assert not again.can_play(B, "peacock:y") and again.states()["peacock"].blocked == (B,)


def test_signin_on_one_stick_marks_only_that_stick_until_it_plays(tmp_path):
    book = two_sticks(tmp_path)
    book.learn("peacock:y", False, SIGNED_OUT, "g2", host=B)
    assert not book.can_play(B, "peacock:q") and book.states()["peacock"].usable
    book.learn("peacock:q", True, "", "g3", host=B)                  # signed in on the TV since
    assert book.can_play(B, "peacock:q") and book.states()["peacock"].blocked == ()


def test_walled_on_every_stick_is_app_wide(tmp_path):
    book = two_sticks(tmp_path)
    book.learn("peacock:y", False, SIGNED_OUT, "g2", host=B)
    book.learn("peacock:y", False, SIGNED_OUT, "g2", host=A)         # no other stick left
    st = book.states()["peacock"]
    assert not st.usable and "signed out" in st.why_not()


def test_one_unexplained_wall_skips_only_that_link_on_that_stick(tmp_path):
    clock = Clock()
    book = two_sticks(tmp_path, clock)
    book.learn("peacock:y", False, PICK_PLAN, "g2", host=B)          # never played anywhere yet
    assert not book.can_play(B, "peacock:y") and book.can_play(B, "peacock:z")
    assert "peacock:y" not in book.active_skips()
    book.learn("peacock:y", False, PICK_PLAN, "g2", host=A)          # the other stick too: the link
    assert book.active_skips()["peacock:y"]["kind"] == "upsell"
    (tmp_path / "b").mkdir()
    book2 = two_sticks(tmp_path / "b", clock)
    book2.learn("peacock:1", False, PICK_PLAN, "g1", host=B)
    book2.learn("peacock:2", False, PICK_PLAN, "g2", host=B)         # two links: the stick
    assert not book2.can_play(B, "peacock:3") and book2.states()["peacock"].usable
    clock.t += prov.RECHECK_S["upsell"] + 1
    assert book2.can_play(B, "peacock:3")                            # re-tried on its own later


def test_multi_tier_apps_never_mark_a_stick_for_one_add_on(tmp_path):
    book = two_sticks(tmp_path)
    book.saw_apps(A, {"prime": "com.amazon.firebat"})
    book.saw_apps(B, {"prime": "com.amazon.firebat"})
    book.learn("prime:ok", True, "", "g1", host=A)
    wall = prov.Verdict("upsell", "subscribe to watch").message("Prime Video")
    book.learn("prime:nesn", False, wall, "g2", host=B)
    assert not book.can_play(B, "prime:nesn") and book.can_play(B, "prime:other")


def test_unknown_host_keeps_the_old_app_wide_learning(tmp_path):
    book = two_sticks(tmp_path)
    book.learn("peacock:y", False, SIGNED_OUT, "g2")
    assert not book.states()["peacock"].usable
    assert book.can_play(None, "peacock:y") and book.can_play(A, None)


def test_scan_says_missing_app_cannot_play(tmp_path):
    book = prov.ProviderBook(tmp_path / "p.json", clock=Clock())
    book.saw_apps(A, {"peacock": "com.peacock.peacockfiretv"})
    book.saw_apps(B, {"youtubetv": "com.amazon.firetv.youtube.tv"})
    assert book.can_play(A, "peacock:1") and not book.can_play(B, "peacock:1")
    assert book.can_play("10.0.0.9", "peacock:1")                     # never scanned: unknown = yes


def test_garbage_records_never_block_a_tune(tmp_path):
    book = two_sticks(tmp_path)
    book.stick_subs[B] = {"peacock": {"at": "soon"}}
    assert book.can_play(B, "peacock:1")


def test_peacock_games_go_to_nbcsn_and_nbc_on_youtube_tv(tmp_path):
    cm = ChannelMap(tmp_path / "ch.json")
    cm.codes.update({"nbcsn": "aaaaaaaaaaa", "nbc": "bbbbbbbbbbb"})
    book = prov.ProviderBook(tmp_path / "p.json", clock=Clock())
    g = game(gid="g9", broadcasts=["Peacock"])
    book.links[g.game_id] = {"providers": {"peacock": [{"ref": "main", "feed": "national"}]}}
    p = book.choose(g, cm.where(g), channel_map=cm)
    assert p.target == "youtubetv:nbcsn" and p.tunable and "NBCSN" in p.why
    assert [a.target for a in p.alternates][:2] == ["youtubetv:nbc", "peacock:main"]


def test_peacock_is_only_used_when_no_youtube_tv_code_exists(tmp_path):
    cm = ChannelMap(tmp_path / "ch.json")
    cm.codes.update({"nbc": "bbbbbbbbbbb"})
    g = game(gid="g9", broadcasts=["Peacock"])
    srcs = prov.gather_sources(g, cm.where(g), {"peacock": [{"ref": "main"}]}, channel_map=cm)
    assert prov.best_source(g, srcs).target == "youtubetv:nbc"       # NBCSN has no code: NBC
    cm.codes.clear()
    srcs = prov.gather_sources(g, cm.where(g), {"peacock": [{"ref": "main"}]}, channel_map=cm)
    assert prov.best_source(g, srcs).target == "peacock:main"        # last resort, still played
    assert not prov.peacock_on_yttv(game(broadcasts=["ESPN"]), cm)


def test_peacock_loses_even_with_your_teams_feed():
    g = game(broadcasts=["NBCSN", "Peacock"])
    p = prov.best_source(g, [src("peacock", "peacock:1", feed="home"),
                             src("youtubetv", "youtubetv:nbcsn", kind="channel", feed="national")],
                         my_teams={"CLE"})
    assert p.provider == "youtubetv"
