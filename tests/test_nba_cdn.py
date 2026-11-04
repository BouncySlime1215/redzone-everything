"""NBA liveData play-by-play: real event times (timeActual), ESPN-style kinds so the screen
model's dead-ball rules match, and a shadow log in plays.jsonl under its own source."""
import json

from redzone.decider.screen import _is_marker
from redzone.models import GameState, League, Phase, Stoppage, Team
from redzone.recorder import Recorder
from redzone.sources import leagues

PBP = json.load(open("tests/fixtures/nba_cdn_playbyplay_0022600157.json"))
ACTS = PBP["game"]["actions"]


def _with(*extra):
    return {"game": {"gameId": PBP["game"]["gameId"], "actions": ACTS + list(extra)}}


def _act(n, **kw):
    a = dict(ACTS[200], actionNumber=n)
    a.update(kw)
    return a


def test_first_look_is_history_then_plays_carry_real_time():
    seen = {}
    assert leagues.nba_plays(PBP, seen, now=1.0) == []
    out = leagues.nba_plays(_with(_act(99999)), seen, now=5.0)
    assert [p.id for p in out] == ["nba99999"]
    p = out[0]
    assert p.wallclock and abs(p.wallclock - leagues._iso_ts(ACTS[200]["timeActual"])) < 1e-6
    assert p.seen_at == 5.0 and p.source == "nba_cdn"


def test_kinds_follow_the_screen_models_dead_ball_rules():
    k = leagues._nba_kind
    assert _is_marker(k({"actionType": "timeout", "subType": "full"}), League.NBA)
    assert _is_marker(k({"actionType": "foul", "subType": "personal", "descriptor": "shooting"}), League.NBA)
    assert _is_marker(k({"actionType": "freethrow", "subType": "1 of 2"}), League.NBA)
    assert not _is_marker(k({"actionType": "freethrow", "subType": "2 of 2"}), League.NBA)
    assert not _is_marker(k({"actionType": "2pt", "subType": "Jump Shot"}), League.NBA)
    assert _is_marker(k({"actionType": "substitution", "subType": "in"}), League.NBA)


def test_stoppage_from_the_last_action():
    s = leagues.nba_stoppage
    assert s({"actionType": "timeout", "subType": "full"}) == Stoppage.TIMEOUT
    assert s({"actionType": "freethrow", "subType": "1 of 2"}) == Stoppage.WHISTLE
    assert s({"actionType": "freethrow", "subType": "2 of 2"}) == Stoppage.NONE
    assert s({"actionType": "period", "subType": "end", "period": 2}) == Stoppage.HALFTIME
    assert s({"actionType": "2pt", "subType": "Jump Shot"}) == Stoppage.NONE


def test_shadow_rows_land_in_plays_jsonl_once(tmp_path):
    rec = Recorder(tmp_path)
    g = GameState(league=League.NBA, game_id="401", home=Team("LAL", score=1), away=Team("TOR", score=0),
                  phase=Phase.LIVE)
    seen = {}
    rec.feed_plays(g, leagues.nba_plays(PBP, seen))
    newer = _with(_act(99999))
    rec.feed_plays(g, leagues.nba_plays(newer, seen, now=leagues._iso_ts(ACTS[200]["timeActual"]) + 9.0))
    rec.feed_plays(g, leagues.nba_plays(newer, seen))
    rows = [json.loads(x) for x in next(tmp_path.glob("recordings/*/plays.jsonl")).read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["source"] == "nba_cdn" and rows[0]["lag"] == 9.0
