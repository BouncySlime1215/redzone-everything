"""[feed race] scripts/feed_race.py: parsers and the lead grade (no network)."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("feed_race", Path(__file__).parent.parent / "scripts" / "feed_race.py")
fr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fr)


def _espn(state="in", hs="7", as_="3"):
    return {"events": [{"competitions": [{"status": {"type": {"state": state}}, "competitors": [
        {"homeAway": "home", "score": hs, "team": {"location": "North Carolina"}},
        {"homeAway": "away", "score": as_, "team": {"location": "Duke"}}]}]}]}


def test_espn_site_and_cdn_parse_the_same_live_game():
    assert fr.parse_espn(_espn()) == {"duke@northcarolina": (3, 7)}
    assert fr.parse_espn({"content": {"sbData": _espn()}}) == {"duke@northcarolina": (3, 7)}
    assert fr.parse_espn(_espn(state="post")) == {}


def test_ncaa_parses_to_the_same_key():
    doc = {"games": [{"game": {"gameState": "live", "home": {"score": "7", "names": {"short": "North Carolina"}},
                               "away": {"score": "3", "names": {"short": "Duke"}}}}]}
    assert fr.parse_ncaa(doc) == {"duke@northcarolina": (3, 7)}


def test_lead_counts_only_changes_after_we_started_watching():
    r = fr.Race()
    r.saw("espn_site", {"g": (0, 0)}, 0)
    r.saw("espn_cdn", {"g": (0, 0)}, 0)          # first sightings: no lag in them
    r.saw("espn_cdn", {"g": (0, 7)}, 100)
    r.saw("espn_site", {"g": (0, 7)}, 106)
    r.saw("espn_cdn", {"g": (3, 7)}, 200)
    r.saw("espn_site", {"g": (3, 7)}, 203)
    g = fr.grade(r.first)["espn_cdn"]
    assert g["paired"] == 2 and g["lead_p50_s"] == 4.5 and g["coverage_pct"] == 100.0 and g["behind_pct"] == 0
