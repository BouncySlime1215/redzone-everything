from redzone.tuners.adb import parse_playback
from redzone.tuners.identity import matchup_evidence


def test_yttv_actual_format_keeps_title_after_playback_state():
    report = parse_playback("""package=com.amazon.firetv.youtube.tv
state=PlaybackState {state=3, position=34, speed=1.0, updated=3006333}
metadata: size=4, description=Philadelphia Eagles at Washington Commanders, NBC 4, null
""", "com.amazon.firetv.youtube.tv")
    assert report["title"] == "Philadelphia Eagles at Washington Commanders"
    assert report["subtitle"] == "NBC 4" and report["description"] == ""
    assert report["speed"] == 1 and report["position"] == 34
    assert report["updated"] == 3006333


def test_prime_generic_title_never_borrows_another_sessions_matchup():
    text = """package=com.amazon.firebat
metadata: size=3, description=PrimeVideo, null, null
state=PlaybackState {state=3, position=34, speed=1.0, updated=3006333}
package=com.amazon.firetv.youtube.tv
state=PlaybackState {state=3, position=42, speed=1.0, updated=3006334}
metadata: size=4, description=Philadelphia Eagles at Washington Commanders, NBC 4, null
"""
    assert parse_playback(text, "com.amazon.firebat")["title"] == "PrimeVideo"
    assert parse_playback(text, "com.amazon.firetv.youtube.tv")["title"].startswith("Philadelphia")


def test_title_with_comma_is_not_truncated():
    report = parse_playback("""package=app
state=PlaybackState {state=3, position=34, speed=1.0, updated=10}
metadata: size=4, description=Game, alternate broadcast, Network, null
""")
    assert report["title"] == "Game, alternate broadcast"
    assert report["subtitle"] == "Network"


def test_actual_matchup_metadata_is_evidence_but_not_date_or_video_proof():
    report = {"title": "Philadelphia Eagles at Washington Commanders", "read_ok": True,
              "read_started": 100, "read_finished": 100.1}
    result = matchup_evidence(report, ["Washington Commanders"], ["Philadelphia Eagles"], now=101)
    assert result["status"] == "matchup_match"
    assert not result["event_date_verified"] and not result["video_verified"]
    assert matchup_evidence(report, ["Washington Commanders"], ["Philadelphia Eagles"], now=104)["status"] == "unknown"


def test_generic_prime_or_other_matchup_never_confirms_requested_game():
    report = {"title": "PrimeVideo", "read_ok": True, "read_started": 100, "read_finished": 100.1}
    assert matchup_evidence(report, ["Lakers"], ["Raptors"], now=101)["status"] == "unknown"
    report["title"] = "Lakers vs. Celtics"
    assert matchup_evidence(report, ["Lakers"], ["Raptors"], now=101)["status"] == "unknown"
