"""Every streaming provider: app discovery, deep links, tuners with fake sticks, the source
chooser, paywall / sign-in / blackout detection, and the eventlinks additions (replayed
from Apple TV + ESPN responses saved on 2026-11-02)."""
import json
from pathlib import Path

import httpx
import pytest

from redzone.channels import ChannelMap, Where
from redzone.discovery import providers as prov
from redzone.models import GameState, League, Phase, Team
from redzone.sources import eventlinks as el
from redzone.tuners import providers as more
from redzone.tuners.adb import RunResult, Stick
from redzone.tuners.tuners import TuneResult

FX = Path(__file__).parent / "fixtures"
APPLE = json.loads((FX / "eventlinks_apple_2026-11-02.json").read_text())
ESPN = json.loads((FX / "eventlinks_espn_airings_2026-11-02.json").read_text())
SAMPLES = json.loads((FX / "eventlinks_apple_playables_sample.json").read_text())["samples"]

OKC_CLE_MAIN = "fb28161e-3214-32be-ab33-3dff8f648c84"     # Peacock "Thunder vs. Cavaliers"
OKC_CLE_PERF = "46b781f7-2e6b-30fc-83a4-65d6975379b5"     # Peacock "Performance View" alt feed


def game(home="CLE", away="OKC", league=League.NBA, gid="g1", broadcasts=(), home_name="", away_name="",
         start=None):
    return GameState(league=league, game_id=gid, home=Team(home, name=home_name), away=Team(away, name=away_name),
                     phase=Phase.LIVE, broadcasts=list(broadcasts), start_time=start)


def okc_cle():
    return game("CLE", "OKC", gid="401909906", broadcasts=["NBCSN"], home_name="Cleveland Cavaliers",
                away_name="Oklahoma City Thunder", start=1793836800)


class Clock:
    """Fake monotonic/wall clock; sleeping advances it (tests run in milliseconds)."""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.t += s


class FakeStick(Stick):
    """Scripted stick: `screen` is what uiautomator would read, `on_key` can change it,
    playback turns "playing" when `playing()` says so."""

    def __init__(self, packages=None, screen="", launch_out="Status: ok\nComplete", playing=None, pm=""):
        super().__init__("10.0.0.5:5555", name="den stick", os="Fire OS 8")
        self.state = "connected"
        self.packages = dict(packages or {})
        self.screen, self.launch_out, self.pm = screen, launch_out, pm
        self.playing = playing or (lambda st: False)
        self.cmds, self.keys, self.on_key, self.screen_reads = [], [], None, 0

    async def shell(self, cmd, timeout=8.0):
        self.cmds.append(cmd)
        if cmd.startswith("am start"):
            out = self.launch_out(cmd) if callable(self.launch_out) else self.launch_out
            return RunResult(True, out, 0.01)
        if cmd == "pm list packages":
            return RunResult(bool(self.pm), self.pm, 0.01)
        return RunResult(True, "", 0.01)

    async def key(self, *codes):
        self.keys.extend(codes)
        if self.on_key:
            self.on_key(self, codes)

    async def playback(self, package=None):
        return {"state": "playing" if self.playing(self) else "none", "pkg": package, "position": 0}

    async def screen_text(self):
        self.screen_reads += 1
        return self.screen

    @property
    def launches(self):
        return [c for c in self.cmds if c.startswith("am start")]


def tuner(cls, clock):
    return cls(sleep=clock.sleep, clock=clock)


# --- 1. which apps are installed ---------------------------------------------------------

PM_OUTPUT = "\n".join("package:" + p for p in [
    "com.amazon.tv.launcher", "com.amazon.firetv.youtube", "com.amazon.firetv.youtube.tv",
    "com.amazon.firebat", "com.espn.gtv", "com.peacock.peacockfiretv", "com.fubo.firetv.screen",
    "com.cbs.ott", "com.hbo.hbonow", "com.netflix.ninja", "com.gotv.nflgamecenter.us.lite",
    "com.nbaimd.gametime.nba2011.amazon", "com.apple.atve.amazon.appletv", "com.amazon.venezia"])


def test_installed_packages_map_to_providers():
    roles = prov.roles_from_packages(prov.parse_package_list(PM_OUTPUT))
    assert roles == {
        "youtubetv": "com.amazon.firetv.youtube.tv", "prime": "com.amazon.firebat", "espn": "com.espn.gtv",
        "peacock": "com.peacock.peacockfiretv", "fubo": "com.fubo.firetv.screen", "paramount": "com.cbs.ott",
        "max": "com.hbo.hbonow", "netflix": "com.netflix.ninja", "nflplus": "com.gotv.nflgamecenter.us.lite",
        "nba": "com.nbaimd.gametime.nba2011.amazon", "appletv": "com.apple.atve.amazon.appletv"}


def test_package_mapping_edge_cases():
    # Plain YouTube is not YouTube TV; Android TV builds and renamed builds still match.
    assert prov.roles_from_packages(["com.amazon.firetv.youtube"]) == {}
    assert prov.roles_from_packages(["com.google.android.youtube.tvunplugged", "com.wbd.stream",
                                     "com.peacocktv.peacockandroid"]) == {
        "youtubetv": "com.google.android.youtube.tvunplugged", "max": "com.wbd.stream",
        "peacock": "com.peacocktv.peacockandroid"}
    assert prov.roles_from_packages(["com.peacock.peacockfiretv.v2"])["peacock"] == "com.peacock.peacockfiretv.v2"
    assert prov.roles_from_packages([None, "", "  "]) == {}


async def test_scan_apps_never_raises():
    assert await prov.scan_apps(FakeStick(pm=PM_OUTPUT)) == prov.roles_from_packages(prov.parse_package_list(PM_OUTPUT))
    assert await prov.scan_apps(FakeStick(pm="")) == {}            # adb failed: "don't know"

    class Broken(FakeStick):
        async def shell(self, cmd, timeout=8.0):
            raise OSError("adb vanished")
    assert await prov.scan_apps(Broken()) == {}


async def test_refresh_sticks_scans_rarely_and_restores_after_restart(tmp_path):
    clock = Clock(5000.0)
    book = prov.ProviderBook(tmp_path / "p.json", clock=clock)
    st = FakeStick({"prime": "custom.prime.pkg"}, pm=PM_OUTPUT)
    added = await book.refresh_sticks([st])
    assert "peacock" in added[st.host] and st.packages["prime"] == "custom.prime.pkg"   # identify()'s pick kept
    assert st.packages["peacock"] == "com.peacock.peacockfiretv"
    await book.refresh_sticks([st])
    assert st.cmds.count("pm list packages") == 1                   # not due again for 30 min
    fresh = FakeStick({}, pm=PM_OUTPUT)                               # app restarted: new Stick object
    await prov.ProviderBook(tmp_path / "p.json", clock=clock).refresh_sticks([fresh])
    assert fresh.packages["fubo"] == "com.fubo.firetv.screen" and fresh.cmds == []
    clock.t += prov.SCAN_EVERY_S + 1
    await book.refresh_sticks([st])
    assert st.cmds.count("pm list packages") == 2


# --- 2. deep links ------------------------------------------------------------------------

def _sample(service):
    return next(s["playable"] for s in SAMPLES if s["playable"]["serviceName"] == service)


def _punch(p):
    return p["punchoutUrls"].get("play") or p["punchoutUrls"].get("open")


def test_peacock_link_matches_apples_exactly():
    pl = APPLE["events"]["umc.cse.1xkb26oj1fnifcpnmf5r15w14"]["data"]["playables"]
    apple_url = next(_punch(p) for p in pl.values() if p["externalId"] == OKC_CLE_MAIN)
    t = more.PeacockTuner()
    assert t.url(OKC_CLE_MAIN) == apple_url
    assert t.url(apple_url) == apple_url                              # a full link passes through
    fire = t.command(OKC_CLE_MAIN, "com.peacock.peacockfiretv")
    assert fire.startswith("am start -W -n com.peacock.peacockfiretv/com.peacock.peacocktv.AmazonMainActivity")
    assert f"-d '{apple_url}'" in fire
    android = t.command(OKC_CLE_MAIN, "com.peacocktv.peacockandroid")
    assert "-n " not in android and android.endswith(" com.peacocktv.peacockandroid")


def test_fubo_link_is_the_form_seen_working():
    assert more.FuboTuner().url("10179") == \
        "https://link.fubo.tv/al1%3Fv%3D1%26a%3Dplay%26t%3Dchannel%26channel_id%3D10179"
    assert more.FuboTuner().valid("10179") and not more.FuboTuner().valid("espn")


def test_links_built_from_apple_refs():
    pplus = el._apple_item({}, _sample("US Digital Sports"))
    assert more.ParamountTuner().url(pplus["ref"]) == _punch(_sample("US Digital Sports")).split("?")[0]
    mx = el._apple_item({}, _sample("WarnerMedia Global Digital Service US"))
    assert more.MaxTuner().url(mx["ref"]) == _punch(_sample("WarnerMedia Global Digital Service US")).split("?")[0]
    assert "-n com.hbo.hbonow/com.wbd.beam.BeamActivity" in more.MaxTuner().command(mx["ref"], "com.hbo.hbonow")
    nfl = el._apple_item({}, _sample("National Event Availability"))
    assert more.NflTuner().url(nfl["ref"]) == _punch(_sample("National Event Availability"))
    assert more.NflTuner().url("redzone") == "nflctv://nflredzone"
    nba = el._apple_item({}, _sample("NBA"))
    assert more.NbaTuner().url(nba["ref"]) == _punch(_sample("NBA")).split("?")[0]   # no ?source=atv-search
    assert more.NbaTuner().valid(nba["ref"]) and not more.NbaTuner().valid("12")
    dz = el._apple_item({}, _sample("DAZN US"))
    assert more.DaznTuner().url(dz["ref"]) == dz["url"]
    assert more.AppleTvTuner().url("https://tv.apple.com/us/sporting-event/x/umc.cse.1") == \
        "https://tv.apple.com/us/sporting-event/x/umc.cse.1"


def test_netflix_command_uses_fire_tv_extras():
    t = more.NetflixTuner()
    cmd = t.command("81234567", "com.netflix.ninja")
    assert "-n com.netflix.ninja/.MainActivity" in cmd and "-d 'netflix://title/81234567'" in cmd
    assert "-e amzn_deeplink_data 81234567" in cmd and "-f 0x10000020" in cmd
    assert "-e source 30" in t.command("81234567", "com.netflix.ninja", use_component=False)
    assert not t.valid("81234567; reboot")


def test_shell_quoting_guard():
    assert more._shell_url("https://x/a'b\nc") == "https://x/abc"


# --- 3. tuners on a fake stick ---------------------------------------------------------------

async def test_tune_plays_first_try():
    clock = Clock()
    st = FakeStick({"peacock": "com.peacock.peacockfiretv"}, playing=lambda s: bool(s.launches))
    res = await tuner(more.PeacockTuner, clock).tune(st, OKC_CLE_MAIN, f"peacock:{OKC_CLE_MAIN}")
    assert res.ok and len(st.launches) == 1 and st.keys == ["KEYCODE_WAKEUP"]


@pytest.mark.parametrize("url", ["https://example.com/watch", "https://tv.apple.com.evil.test/event",
                                  "https://tv.apple.com:444/event", "http://tv.apple.com/event"])
async def test_external_event_link_cannot_leave_its_provider(url):
    st = FakeStick({"appletv": "com.apple.atve.amazon.appletv"})
    res = await tuner(more.AppleTvTuner, Clock()).tune(st, url, "appletv:game")
    assert not res.ok and not st.cmds and not st.keys


async def test_unchanged_previous_video_never_confirms_new_provider_game():
    st = FakeStick({"peacock": "com.peacock.peacockfiretv"}, playing=lambda s: True)
    res = await tuner(more.PeacockTuner, Clock()).tune(st, OKC_CLE_MAIN, "peacock:new-game")
    assert not res.ok and st.tuned is None


async def test_generic_live_label_does_not_authorize_a_selection():
    st = FakeStick({"peacock": "com.peacock.peacockfiretv"}, screen="Home | Live | Sports | Play")
    res = await tuner(more.PeacockTuner, Clock()).tune(st, OKC_CLE_MAIN, "peacock:new-game")
    assert not res.ok and "KEYCODE_DPAD_CENTER" not in st.keys


async def test_tune_stops_at_profile_picker_without_configured_identity():
    clock = Clock()
    st = FakeStick({"fubo": "com.fubo.firetv.screen"}, screen="Who’s watching? | Nick | Guest",
                   playing=lambda s: "KEYCODE_DPAD_CENTER" in s.keys)
    res = await tuner(more.FuboTuner, clock).tune(st, "10179", "fubo:10179")
    assert not res.ok and "no preferred profile configured" in res.error
    assert "KEYCODE_DPAD_CENTER" not in st.keys and len(st.launches) == 1


@pytest.mark.parametrize("screen,kind,err", [
    ("Start your free trial | Sign In | Peacock Premium", "signin", "signed out — it showed “sign in”"),
    ("Sign in to watch | Activate your device at peacocktv.com/tv", "signin", "signed out — it showed “sign in”"),
    ("This game is not available in your area due to league restrictions", "blackout", "blacked out"),
])
async def test_tune_stops_at_walls_without_pressing_ok(screen, kind, err):
    clock = Clock()
    st = FakeStick({"peacock": "com.peacock.peacockfiretv"}, screen=screen)
    res = await tuner(more.PeacockTuner, clock).tune(st, OKC_CLE_MAIN, f"peacock:{OKC_CLE_MAIN}")
    assert not res.ok and err in res.error
    assert "KEYCODE_DPAD_CENTER" not in st.keys and len(st.launches) == 1     # no retry into a wall
    assert prov.parse_verdict(res.error).kind == kind
    if kind == "blackout":
        assert any("blackout" in s for s in res.steps)                       # Controller._blackout_check sees it


async def test_never_presses_ok_on_a_purchase_screen():
    clock = Clock()
    st = FakeStick({"paramount": "com.cbs.ott"}, screen="Watch Live | Rent for $4.99")
    res = await tuner(more.ParamountTuner, clock).tune(st, "nfl/abc", "paramount:nfl/abc")
    assert not res.ok and "KEYCODE_DPAD_CENTER" not in st.keys
    assert any("didn't press OK" in s for s in res.steps)


async def test_scheduled_watch_live_press():
    clock = Clock()
    st = FakeStick({"max": "com.hbo.hbonow"}, screen="Watch Live | From Beginning",
                   playing=lambda s: "KEYCODE_DPAD_CENTER" in s.keys)
    res = await tuner(more.MaxTuner, clock).tune(st, "d07e2dc3-9c68-5019-9f99-481f39bebaf6", "max:x")
    assert res.ok and st.keys.count("KEYCODE_DPAD_CENTER") == 1


async def test_missing_activity_falls_back_to_plain_view_without_force_stop():
    clock = Clock()
    st = FakeStick({"peacock": "com.peacock.peacockfiretv"},
                   launch_out=lambda cmd: "Error: Activity class {x} does not exist." if " -n " in cmd else "Status: ok",
                   playing=lambda s: len(s.launches) >= 2)
    res = await tuner(more.PeacockTuner, clock).tune(st, OKC_CLE_MAIN, "peacock:x")
    assert res.ok and " -n " in st.launches[0] and " -n " not in st.launches[1]
    assert not any(c.startswith("am force-stop") for c in st.cmds)


async def test_video_never_starts_retries_once_without_force_stop():
    clock = Clock()
    st = FakeStick({"nba": "com.nbaimd.gametime.nba2011.amazon"})
    res = await tuner(more.NbaTuner, clock).tune(st, "0022600162", "nba:0022600162")
    assert not res.ok and res.error == "NBA app: video never started"
    assert len(st.launches) == 2 and not any(c.startswith("am force-stop") for c in st.cmds)


async def test_refuses_missing_app_and_odd_links():
    clock = Clock()
    st = FakeStick({})
    res = await tuner(more.PeacockTuner, clock).tune(st, OKC_CLE_MAIN, "peacock:x")
    assert not res.ok and "isn't installed" in res.error and st.cmds == []
    st = FakeStick({"peacock": "com.peacock.peacockfiretv"})
    res = await tuner(more.PeacockTuner, clock).tune(st, "x'; reboot; '", "peacock:x")
    assert not res.ok and "doesn't look right" in res.error and st.cmds == []


async def test_diagnosing_wrapper_names_the_reason():
    class Inner:
        app = role = "youtubetv"

        def __init__(self, ok):
            self.ok = ok

        async def tune(self, stick, ref, target):
            return TuneResult(self.ok, target, 1.0, ["deep link #1: ok"], "" if self.ok else "video never started")

    st = FakeStick(screen="You've been signed out | Sign in")
    res = await more.DiagnosingTuner(Inner(False)).tune(st, "AAAAAAAAAAA", "youtubetv:espn")
    assert res.error.startswith("YouTube TV: signed out — it showed") and "video never started" in res.error
    st = FakeStick(screen="Sign in")
    assert (await more.DiagnosingTuner(Inner(True)).tune(st, "A", "youtubetv:espn")).ok and st.screen_reads == 0
    assert more.DiagnosingTuner(Inner(True)).role == "youtubetv"                 # attributes pass through


def test_register_adds_tuners_and_wraps_builtins_once():
    from redzone.tuners.tuners import YouTubeTVTuner, PrimeTuner, EspnTuner
    tuners = {"youtubetv": YouTubeTVTuner(), "prime": PrimeTuner(), "espn": EspnTuner()}
    keep = object()
    tuners["peacock"] = keep
    added = more.register(tuners)
    assert "fubo" in added and "peacock" not in added and tuners["peacock"] is keep
    assert isinstance(tuners["youtubetv"], more.DiagnosingTuner)
    more.register(tuners)
    assert not isinstance(tuners["youtubetv"].inner, more.DiagnosingTuner)       # no double wrap
    assert set(more.EXTRA_TUNERS) | {"youtubetv", "prime", "espn"} == prov.TUNER_APPS


# --- 4. reading the screen -------------------------------------------------------------------

@pytest.mark.parametrize("text,kind", [
    ("Start Your Free Trial", "upsell"),
    ("Upgrade to Premium Plus to watch", "upsell"),
    ("This channel isn't included in your plan", "upsell"),
    ("Sign In | Forgot password?", "signin"),
    ("To activate your device visit fubo.tv/connect and enter code ABCD", "signin"),
    ("Blackout: this game is unavailable in your area", "blackout"),
    ("Sign in | Due to league restrictions this game isn't available here", "blackout"),
    ("Something went wrong. Error code 1004", "error"),
    ("This video is currently unavailable to watch in your location.", "blackout"),   # Prime's geo wall
    ("This video is currently unavailable", "error"),
    ("Who's watching? | Nick", None),
    ("Live | Thunder vs. Cavaliers | Play", None),
    ("Current game | Subscribed | Parental controls", None),       # no "rent"/"subscribe" false hits
    ("", None),
])
def test_classify_screen(text, kind):
    v = prov.classify_screen(text)
    assert (v.kind if v else None) == kind


@pytest.mark.parametrize("text,paid", [
    ("Start your free trial", True), ("$12.99/mo", True), ("Buy | Rent", True), ("Subscribe", True),
    ("Watch Live | From Beginning", False), ("Who's watching? | Nick", False), ("Parental controls", False),
])
def test_purchase_guard(text, paid):
    assert prov.is_purchase_screen(text) is paid


def test_verdict_round_trip():
    v = prov.Verdict("upsell", "start your free trial")
    assert prov.parse_verdict("x | " + v.message("Peacock") + " | y") == v
    assert prov.parse_verdict("video never started") is None


# --- 5. choosing a source ------------------------------------------------------------------

def src(provider, target, tunable=True, **kw):
    return prov.Source(provider, target, kw.pop("label", provider), tunable, **kw)


def test_youtube_tv_wins_when_it_can_tune():
    g = game(broadcasts=["ESPN"])
    p = prov.best_source(g, [src("peacock", "peacock:1"), src("prime", "prime:amzn1.x"),
                             src("youtubetv", "youtubetv:espn", kind="channel", feed="national", label="ESPN · YouTube TV"),
                             src("espn", "espn:abc")])
    assert p.target == "youtubetv:espn" and p.tunable and "rewind" in p.why
    assert [a.provider for a in p.alternates] == ["espn", "peacock", "prime"]  # League Pass blackout risk


def test_provider_order_prime_espn_peacock_fubo():
    g = game()
    order = ["fubo", "peacock", "espn", "prime", "max"]
    picks = []
    cands = [src(k, f"{k}:1") for k in order]
    while cands:
        best = prov.best_source(g, cands).provider
        picks.append(best)
        cands = [c for c in cands if c.provider != best]
    assert picks == ["prime", "espn", "peacock", "fubo", "max"]


def test_tunable_beats_better_app_that_cannot_tune_yet():
    g = game(broadcasts=["NBCSN"])
    p = prov.best_source(g, [src("youtubetv", "youtubetv:nbcsn", False, kind="channel", feed="national"),
                             src("peacock", f"peacock:{OKC_CLE_MAIN}")])
    assert p.provider == "peacock" and p.tunable


def test_unavailable_apps_are_skipped_with_reasons():
    g = game()
    states = {"youtubetv": prov.ProviderState("youtubetv", installed=False),
              "prime": prov.ProviderState("prime", True, False, "Prime Video: signed out — sign in on the TV once"),
              "peacock": prov.ProviderState("peacock", True)}
    p = prov.best_source(g, [src("youtubetv", "youtubetv:espn"), src("prime", "prime:x"),
                             src("peacock", "peacock:1")], states)
    assert p.provider == "peacock"
    assert "YouTube TV isn't installed on your Fire TV sticks." in p.reasons
    assert any("signed out" in r for r in p.reasons)
    only = prov.best_source(g, [src("youtubetv", "youtubetv:espn")], {"peacock"})   # a set of usable apps
    assert only.target is None and "isn't installed" in only.label


def test_blackout_is_never_picked_and_geo_blocked_only_as_last_resort():
    g = game()
    p = prov.best_source(g, [src("prime", "prime:a", blackout=True)])
    assert p.target is None and "blacked out" in p.label
    p = prov.best_source(g, [src("prime", "prime:a", geo_ok=False, confidence="sure"),
                             src("peacock", "peacock:b", geo_ok=True)])
    assert p.provider == "peacock"
    p = prov.best_source(g, [src("prime", "prime:a", geo_ok=False, confidence="sure")])
    assert p.target == "prime:a" and "may be blacked out" in p.label and p.confidence == "likely"


def test_your_team_gets_its_own_broadcast_others_get_national():
    g = game(home="ORL", away="WSH", gid="wiz")
    feeds = [src("prime", "prime:magic", feed="home", team="ORL"), src("prime", "prime:generic"),
             src("prime", "prime:wiz", feed="away", team="WSH"), src("prime", "prime:mobile", feed="mobile")]
    p = prov.best_source(g, feeds, my_teams={"nba": ["WSH"], "nfl": ["WSH"]})
    assert p.target == "prime:wiz" and "your team's own broadcast" in p.why
    assert prov.best_source(g, feeds).target == "prime:magic"            # neutral: home before away
    nat = feeds + [src("espn", "espn:nat", feed="national")]
    assert prov.best_source(g, nat).target == "espn:nat"                # national first for others
    geo = [src("prime", "prime:wiz", feed="away", geo_ok=False), src("prime", "prime:generic")]
    assert prov.best_source(g, geo, my_teams={"WSH"}).target == "prime:generic"   # safety before preference


def test_alt_casts_and_spanish_feeds_come_last():
    g = okc_cle()
    p = prov.best_source(g, [src("peacock", f"peacock:{OKC_CLE_PERF}", feed="alt"),
                             src("peacock", f"peacock:{OKC_CLE_MAIN}")])
    assert p.target == f"peacock:{OKC_CLE_MAIN}"


def test_league_pass_is_a_last_resort_during_national_tv():
    g = okc_cle()   # NBCSN national: League Pass feeds on Prime are blacked out in the US
    p = prov.best_source(g, [src("prime", "prime:lp"), src("peacock", f"peacock:{OKC_CLE_MAIN}")])
    assert p.provider == "peacock"
    p = prov.best_source(game(home="ORL", away="WSH"), [src("prime", "prime:lp"), src("peacock", "peacock:x")])
    assert p.provider == "prime"                                       # no national TV: Prime first


def test_your_phone_choice_always_wins_and_empty_is_unknown():
    g = game()
    p = prov.best_source(g, [src("youtubetv", "youtubetv:espn"),
                             src("fubo", "fubo:10179", confidence="you set it")])
    assert p.target == "fubo:10179" and p.confidence == "you set it"
    p = prov.best_source(g, [])
    assert p.target is None and p.confidence == "unknown" and not p.tunable
    assert isinstance(p, Where)                                        # drop-in for ChannelMap.where()


def test_channel_fallbacks_fubo_and_label_only():
    g = game(broadcasts=["ESPN", "Peacock"])
    cands = prov.channel_sources(g)
    assert [c.target for c in cands] == ["fubo:10179", None]
    assert cands[1].label == "Peacock (game link not found yet)" and not cands[1].tunable


# --- 6. eventlinks additions (saved Apple TV / ESPN responses) --------------------------------

@pytest.mark.parametrize("service,provider,ref", [
    ("Peacock Sports 2 US", "peacock", "ba794e0d-8780-362b-8584-90ec8c7d9313"),
    ("WarnerMedia Global Digital Service US", "max", "d07e2dc3-9c68-5019-9f99-481f39bebaf6"),
    ("US Digital Sports", "paramount", "scottish-professional-football-league/80269d4b-7ee4-4f4f-9745-e1c006dd9a34"),
    ("National Event Availability", "nflplus", "ab789e15-4feb-11f1-abca-2c54536568a9"),
    ("NBA", "nba", "0022600175"),
    ("DAZN US", "dazn", "1axjor1jwx4ns11s2p1q759hen"),
    ("NBC v2  (NBC Sports)", "nbcsports", "30000392"),
    ("CBS Sports Non-linear Availability Feed (NATIONAL)", "cbssports", "LET-211534712"),
    ("TNTEast", "tnt", "2600940"),
    ("truTV East", "trutv", "2600940"),
    ("Marquee", "marquee", "lxyqo320261110ev50"),
    ("ACC Network", "espn", "6e6fbddd-e955-4e8e-97d7-656db9ff34e8"),
])
def test_apple_rows_expose_provider_and_ref(service, provider, ref):
    it = el._apple_item({"league": "x"}, _sample(service))
    assert it["provider"] == provider and it["ref"] == ref
    assert it["brand"] == _sample(service)["channelId"]


def test_apple_row_extras():
    acc = el._apple_item({}, _sample("ACC Network"))
    assert acc["channel"] == "acc"                                     # ESPN linear channel (playChannel)
    assert el._apple_item({}, _sample("Marquee"))["alt"]               # a postgame show, not the game
    assert el._apple_item({}, _sample("ESPN Unlimited"))["alt"]        # "Above the Rim" alt-cast
    assert not el._apple_item({}, _sample("ESPN on ABC"))["alt"]
    assert el._apple_item({}, _sample("L2V US")) is None               # replay rows have no link


def test_provider_fallbacks_by_brand_scheme_and_host():
    assert el._provider_for("Peacock Sports 3 Renamed", "", "tvs.sbd.1000371") == "peacock"
    assert el._provider_for("Mystery", "pplus://www.paramountplus.com/live-tv/x") == "paramount"
    assert el._provider_for("Mystery", "https://play.hbomax.com/sport/x") == "max"
    assert el._provider_for("Mystery Service", "foo://bar") == "mystery_service"   # unchanged fallback
    assert el._provider_for("ESPN+ V2", "sportscenter://x") == "espnplus"          # old rules first


def _transport(fail_detail=()):
    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host == "uts-api.itunes.apple.com" and path.endswith("/shelves/uts.col.search.SE"):
            term = request.url.params.get("searchTerm")
            return httpx.Response(200, json=APPLE["shelves"].get(term, {"data": {"shelf": {"items": []}}}))
        if host == "uts-api.itunes.apple.com" and "/sporting-events/" in path:
            eid = path.rsplit("/", 1)[-1]
            if eid in fail_detail or eid not in APPLE["events"]:
                return httpx.Response(500)
            return httpx.Response(200, json=APPLE["events"][eid])
        if host == "watch.graph.api.espn.com":
            return httpx.Response(200, json={"data": ESPN["data"]})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


async def _links(monkeypatch, **kw):
    monkeypatch.setattr(el, "_cache", {})
    async with httpx.AsyncClient(transport=_transport(**kw)) as client:
        return await el.fetch_event_links(client, "2026-11-02")


async def test_fetch_event_links_from_saved_responses(monkeypatch):
    links = await _links(monkeypatch)
    by = {}
    for it in links:
        by.setdefault((it["home"], it["provider"]), []).append(it)
    assert {p for h, p in by if h == "Cleveland Cavaliers"} == {"peacock", "prime"}
    assert {p for h, p in by if h == "Orlando Magic"} == {"prime", "dazn", "nba"}
    assert {p for h, p in by if h == "Seattle Seahawks"} == {"espn", "prime", "nflplus"}
    assert not any(it["home"] == "Florida Panthers" and it["provider"] == "max" for it in links)  # Nov 4: other day
    peacock = {it["ref"]: it for it in by[("Cleveland Cavaliers", "peacock")]}
    assert set(peacock) == {OKC_CLE_MAIN, OKC_CLE_PERF, "560c6bf9-da8d-3426-8d59-7ff3d5ad1647"}
    assert not peacock[OKC_CLE_MAIN]["alt"] and peacock[OKC_CLE_PERF]["alt"]
    assert peacock[OKC_CLE_MAIN]["event_url"].startswith("https://tv.apple.com/us/sporting-event/")
    espn = by[("Seattle Seahawks", "espn")]
    assert all(it["source"] == "espn" and it["ref"] == it["play_id"] for it in espn)   # Apple dupes dropped
    assert sorted((it["channel"], it["alt"]) for it in espn) == [("espn1", False), ("espn2", True),
                                                                  ("espndeportes", True)]
    assert all(it["ref"] == it["gti"] for it in links if it["provider"] == "prime")
    assert not any("Commanders" in it["home"] for it in links)        # ESPN re-air skipped


async def test_shelf_fallback_when_a_detail_call_fails(monkeypatch):
    links = await _links(monkeypatch, fail_detail={"umc.cse.4vtmy8md3rfeo8x2iqwtrul0y"})
    wiz = [it for it in links if it["home"] == "Orlando Magic"]
    assert wiz and all(it["ref"] for it in wiz)


# --- 7. the book: end to end with today's links -----------------------------------------------

async def test_choose_end_to_end_okc_cle(monkeypatch, tmp_path):
    links = await _links(monkeypatch)
    g = okc_cle()
    found = el.match(links, "nba", {g.away.abbr, g.away.name}, {g.home.abbr, g.home.name}, espn_event_id=g.game_id)
    assert found["peacock"]["ref"] == OKC_CLE_PERF          # match() alone would pick the alt feed...
    book = prov.ProviderBook(tmp_path / "p.json", clock=Clock(1793836800.0))
    assert book.remember(g, found) == 5
    cm = ChannelMap(tmp_path / "ch.json")
    pick = book.choose(g, cm.where(g))
    assert pick.target == f"peacock:{OKC_CLE_MAIN}"        # ...the chooser takes the main broadcast
    assert pick.tunable and pick.confidence == "sure" and pick.provider == "peacock"
    cm.codes["nbcsn"] = "AAAAAAAAAAA"                        # once YouTube TV's NBCSN channel is known
    assert book.choose(g, cm.where(g)).target == "youtubetv:nbcsn"
    book.saw_apps("10.0.0.5:5555", {"youtubetv": "com.amazon.firetv.youtube.tv", "prime": "com.amazon.firebat"})
    cm.codes.clear()
    pick = book.choose(g, cm.where(g))                     # Peacock not on the sticks: say so
    assert pick.provider == "prime" and "may be blacked out" in pick.label
    assert "Peacock isn't installed on your Fire TV sticks." in pick.reasons


def test_learning_from_tunes(tmp_path):
    clock = Clock(1_800_000_000.0)
    book = prov.ProviderBook(tmp_path / "p.json", clock=clock)
    g = okc_cle()
    wall = prov.Verdict("upsell", "start your free trial").message("Peacock")
    assert "for this game" in book.learn("peacock:a", False, wall, g.game_id)
    assert book.states()["peacock"].usable                  # one paywall: just that game is skipped
    assert "doesn't seem to be subscribed" in book.learn("peacock:b", False, wall)
    st = book.states()["peacock"]
    assert st.subscribed is False and "start your free trial" in st.why_not()
    p = book.choose(g, None)
    assert p.provider != "peacock"
    clock.t += prov.RECHECK_S["upsell"] + 1                 # days later: try Peacock again on its own
    assert book.states()["peacock"].subscribed is None
    assert prov.ProviderBook(tmp_path / "p.json", clock=clock).states()["peacock"].subscribed is None


def test_learning_signin_blackout_and_failures(tmp_path):
    clock = Clock(1_800_000_000.0)
    book = prov.ProviderBook(tmp_path / "p.json", clock=clock)
    msg = prov.Verdict("signin", "sign in").message("Fubo")
    assert "signed out" in book.learn("fubo:10179", False, msg)
    assert book.states()["fubo"].subscribed is False
    assert book.learn("fubo:10179", True) == "Fubo works again" and book.states()["fubo"].subscribed is None
    black = prov.Verdict("blackout", "blackout").message("Prime Video")
    book.learn("prime:x", False, black, "g1")
    s = book.sources(game(gid="g1"), Where("prime:x", "Prime Video", "sure", True))[0]
    assert s.blackout and prov.best_source(game(), [s]).target is None
    assert book.learn("espn:1", False, "video never started") == ""
    assert "failed twice" in book.learn("espn:1", False, "video never started")
    s = book.sources(game(gid="g2"), Where("espn:1", "ESPN+ · ESPN app", "sure", True))[0]
    assert not s.tunable
    book.learn("espn:1", True)
    assert "espn:1" not in book.skips
    for t in ("prime:a", "prime:b", "prime:c"):             # add-ons vary per game on Prime
        book.learn(t, False, prov.Verdict("upsell", "free trial").message("Prime Video"))
    assert book.states()["prime"].subscribed is None


def test_book_file_round_trip_and_bad_files(tmp_path):
    path = tmp_path / "p.json"
    clock = Clock(1_800_000_000.0)
    book = prov.ProviderBook(path, clock=clock)
    book.saw_apps("a:5555", {"peacock": "com.peacock.peacockfiretv", "junk": "x"})
    book.set_subscribed("fubo", False, "cancelled")
    book.remember(game(gid="old", start=clock.t - prov.LINKS_KEEP_S - 10), {"peacock": {"ref": "p", "alts": []}})
    book.remember(game(gid="new", start=clock.t), {"peacock": {"ref": "q", "alts": [{"ref": "r"}, "junk"]}})
    book.save()
    again = prov.ProviderBook(path, clock=clock)
    assert again.installed == {"a:5555": {"peacock": "com.peacock.peacockfiretv"}}
    assert again.states()["fubo"].subscribed is False and again.states()["fubo"].why_not() == "cancelled"
    assert again.states()["netflix"].installed is False and again.states()["peacock"].usable
    again.prune()
    assert set(again.links) == {"new"} and len(again.links["new"]["providers"]["peacock"]) == 2
    path.write_text("{not json")
    assert prov.ProviderBook(path).links == {}
    path.write_text("[1, 2]")
    assert prov.ProviderBook(path).states()["peacock"].installed is None


def test_choose_survives_garbage_records(tmp_path):
    book = prov.ProviderBook(tmp_path / "p.json")
    book.links["g1"] = {"providers": {"peacock": ["junk", None, {"ref": None}], "mystery": [{"ref": "x"}]}}
    p = book.choose(game(gid="g1"), Where("youtubetv:espn", "ESPN · YouTube TV", "sure", True))
    assert p.target == "youtubetv:espn"


def test_outcomes_without_a_tune_teach_nothing(tmp_path):
    from types import SimpleNamespace
    from redzone.control import Outcome
    from redzone.discovery.runtime import DiscoveryRuntime
    rt = DiscoveryRuntime.__new__(DiscoveryRuntime)
    rt.book = prov.ProviderBook(tmp_path / "p.json", clock=Clock(1_800_000_000.0))
    rt.engine = SimpleNamespace(toast=lambda *a: None)
    for detail in ("no safe moment to cut — kept the current game", "safe exit window closed during preparation",
                   "switch cancelled; playback not confirmed", "TV input is unknown; verify the visible stick"):
        rt.on_outcome(Outcome(False, "youtubetv:espn", 0.0, detail, False), "mnf")
        rt.on_outcome(Outcome(False, "youtubetv:espn", 0.0, detail, False, screen_unchanged=True), "mnf")
    assert "youtubetv:espn" not in rt.book.skips and not rt.book._fails
    rt.on_outcome(Outcome(False, "youtubetv:espn", 0.0, "video never started", False, path="tuned",
                          screen_unchanged=True), "mnf")
    assert rt.book._fails.get("youtubetv:espn")             # a hidden-stick tune that failed still counts


def test_local_nba_game_is_blacked_out_on_every_league_pass_feed(tmp_path):
    book = prov.ProviderBook(tmp_path / "p.json", clock=Clock(1_800_000_000.0))
    g = game("CHA", "DET", gid="401909908")
    book.links[g.game_id] = {"providers": {"prime": [{"gti": f"amzn1.dv.gti.{i}", "geo_ok": False}
                                                     for i in range(7)]}}
    assert all(s.blackout for s in book.sources(g) if s.provider == "prime")
    assert book.choose(g).target is None


def test_blackout_or_failure_skips_the_whole_game_on_that_app(tmp_path):
    book = prov.ProviderBook(tmp_path / "p.json", clock=Clock(1_800_000_000.0))
    g = game("IND", "ATL", gid="g9")
    book.links[g.game_id] = {"providers": {"prime": [{"gti": "a", "geo_ok": False}, {"gti": "b", "geo_ok": False},
                                                     {"gti": "c", "geo_ok": True}]}}
    book.learn("prime:a", False, "video never started", g.game_id)
    book.learn("prime:a", False, "video never started", g.game_id)
    by = {s.target: s for s in book.sources(g)}
    assert not by["prime:b"].tunable and by["prime:c"].tunable    # Apple-flagged feeds dropped, others kept
    book.learn("prime:c", False, prov.Verdict("blackout", "unavailable to watch in your location")
               .message("Prime Video"), g.game_id)
    assert all(s.blackout for s in book.sources(g) if s.provider == "prime")
    assert all(s.tunable for s in book.sources(game("IND", "ATL", gid="other")))


def test_failed_preloads_teach_the_book(tmp_path):
    from types import SimpleNamespace
    from redzone.discovery.runtime import DiscoveryRuntime
    rt = DiscoveryRuntime.__new__(DiscoveryRuntime)
    rt.book = prov.ProviderBook(tmp_path / "p.json", clock=Clock(1_800_000_000.0))
    toasts = []
    rt.engine = SimpleNamespace(toast=lambda *a: toasts.append(a))
    g = game("CLE", "OKC", gid="g7")
    rt.book.links[g.game_id] = {"providers": {"peacock": [{"ref": "main"}]}}
    assert rt.book.game_for("peacock:main") == "g7" and rt.book.game_for("youtubetv:espn") is None
    rt.on_preload_fail("peacock:main", "video never started")
    rt.on_preload_fail("peacock:main", "video never started")
    assert rt.book.skips["peacock:main"]["kind"] == "failed" and toasts
    rt.on_preload_fail(None, "x")                            # never raises


def test_wizards_feed_is_only_called_their_own_when_the_link_says_so(tmp_path):
    cm = ChannelMap(tmp_path / "ch.json")
    g = game("ORL", "WSH", gid="g8", broadcasts=["Monumental", "FanDuel SN FL"])
    feeds = [{"gti": "orl", "geo_ok": True}, {"gti": "wiz", "geo_ok": True, "feed_name": "Monumental Sports Network"}]
    cm.apply_links(g, {"prime": {**feeds[0], "alts": feeds[1:]}}, {"WSH"})
    assert cm.prime_gti[g.game_id] == "wiz"                  # the feed that says Monumental
    book = prov.ProviderBook(tmp_path / "p.json", clock=Clock(1_800_000_000.0))
    book.links[g.game_id] = {"providers": {"prime": feeds}}
    first = book.sources(g, Where("prime:wiz", "Monumental (Prime Video)", "likely", True))[0]
    assert first.feed == "away"
    book.links[g.game_id] = {"providers": {"prime": [{"gti": "wiz", "geo_ok": True}]}}
    first = book.sources(g, Where("prime:wiz", "Monumental (Prime Video)", "likely", True))[0]
    assert first.feed is None and "unverified" in first.label


def test_peacock_alt_camera_feeds_rank_as_alternates():
    g = okc_cle()
    for title in ("Thunder vs. Cavaliers: Performance View", "Action Cams", "ISO Cam: Shai"):
        s = prov._link_source(g, "peacock", "peacock", {"ref": "x", "title": title})
        assert s.feed == "alt", title
    assert prov._link_source(g, "peacock", "peacock", {"ref": "x", "title": "Thunder vs. Cavaliers"}).feed is None
    mnf = game("SEA", "CHI", league=League.NFL, broadcasts=["ESPN"])
    assert prov._link_source(mnf, "espn", "espn", {"play_id": "1", "network": "ESPN Deportes"}).feed == "alt"
    assert prov._link_source(mnf, "espn", "espn", {"play_id": "1", "title": "En Español: Bears vs. Seahawks"}
                             ).feed == "alt"


def test_nhl_network_without_a_code_uses_espn_plus_and_prime_channels_are_not_sure(tmp_path):
    cm = ChannelMap(tmp_path / "ch.json")
    g = game("BOS", "VGK", league=League.NHL, gid="g5", broadcasts=["NHL Net", "NESN"])
    assert cm.where(g).target == "youtubetv:nhlnetwork"     # nothing better known yet
    cm.espn_ids[g.game_id] = "abc"
    assert cm.where(g).target == "espn:abc"
    cm.codes["nhlnetwork"] = "CODE"
    assert cm.where(g).target == "youtubetv:nhlnetwork"
    s = prov._link_source(g, "prime", "prime", {"gti": "amzn1.x", "geo_ok": True, "title": "Golden Knights vs. Bruins"})
    assert s.confidence == "likely"
