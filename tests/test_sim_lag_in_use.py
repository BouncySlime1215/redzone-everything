"""[sim lag in use] the sims draw feed lag from the source the config really decides from."""
import random

from redzone.lab.timing import LagSampler, load_profile, sources_in_use


class Cfg(dict):
    def get(self, k, d=None):
        return super().get(k, d)


def test_sources_follow_the_flags():
    assert sources_in_use(Cfg()) == {}
    assert sources_in_use(Cfg({"sources.nhl_primary": True})) == {"nhl": "nhl_wsc"}
    assert sources_in_use(Cfg({"sources.nba_primary": True})) == {"nba": "nba_cdn"}


def test_nhl_wsc_profile_is_faster_and_others_keep_espn():
    prof = load_profile()
    espn, wsc = LagSampler(prof), LagSampler(prof, use={"nhl": "nhl_wsc", "nba": "nba_cdn"})
    rng = random.Random(1)
    a = sorted(espn.draw("nhl", rng) for _ in range(2000))
    b = sorted(wsc.draw("nhl", rng) for _ in range(2000))
    assert 14.0 < b[1000] < 19.5 and a[1000] > 40.0          # p50 near Nick's 16.7 s vs ESPN's ~50 s
    assert "nba" not in wsc.use                                # no nba_cdn profile yet: NBA stays on ESPN's
    assert wsc._q("nba") == espn._q("nba")
