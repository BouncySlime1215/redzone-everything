"""[EXP-003] One event language (redzone/contracts/events.py): every sport's plays map to shared primitives,
and the same Event packs into the NFM's vocab v1 ids exactly as #169's tokenizer writes them."""
import importlib.util
import json
from pathlib import Path

import pytest

from redzone.contracts import events as E
from redzone.models import League, Stoppage
from redzone.sources import espn

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
GAMES = [("nfl", "espn_nfl_summary_401772894.json"), ("nba", "espn_nba_summary_401909860.json"),
         ("nhl", "espn_nhl_summary_401892578.json"), ("cbb", "espn_cbb_summary_401820788.json")]


def _plays(lg, name):
    return espn.flatten_plays(League(lg), json.loads((FIX / name).read_text()))


@pytest.mark.parametrize("lg,name", GAMES)
def test_every_sport_maps_under_one_percent_unmapped(lg, name):
    plays = _plays(lg, name)
    assert plays and E.unmapped_share(League(lg), plays) < 0.01


def test_primitives_are_shared_words_across_sports():
    P = E.Primitive
    assert E.primitive_of("rush") is E.primitive_of("hit") is P.PLAY
    assert E.primitive_of("lost_ball_turnover") is E.primitive_of("takeaway") is E.primitive_of("punt") is P.POSSESSION
    assert E.primitive_of("shooting_foul") is E.primitive_of("tripping") is E.primitive_of("penalty") is P.FOUL
    assert E.primitive_of("full_timeout") is E.primitive_of("stoppage") is P.STOPPAGE
    assert E.primitive_of("end_period") is E.primitive_of("period_start") is E.primitive_of("two-minute_warning") \
        is P.PERIOD
    assert E.primitive_of("jump_shot") is E.primitive_of("missed") is P.SHOT
    assert E.primitive_of("substitution") is P.ADMIN
    assert E.primitive_of("jump_shot", scored=True) is P.SCORE
    assert E.primitive_of("") is E.primitive_of("zzz") is P.UNMAPPED


def test_events_carry_score_clock_and_stoppage():
    evs = E.events(League.NBA, _plays("nba", "espn_nba_summary_401909860.json"))
    assert all(isinstance(e.stop, Stoppage) and 0.0 <= e.clock_frac < 1.0 for e in evs)
    scored = [e for e in evs if e.primitive is E.Primitive.SCORE]
    assert scored and all(e.pts_home + e.pts_away > 0 for e in scored)
    assert evs[-1].home == sum(e.pts_home for e in evs) or evs[-1].home >= evs[0].home


def test_vocab_v1_layout_fits_uint16():
    assert E.VOCAB_SIZE < 65536 and E.TOKENS_PER_EVENT == len(E.tokens_v1(E.events(
        League.NHL, _plays("nhl", "espn_nhl_summary_401892578.json"))[0]))


@pytest.mark.skipif(not (ROOT / "scripts" / "noggin_tokens.py").exists(), reason="#169's tokenizer not merged yet")
@pytest.mark.parametrize("lg,name", GAMES)
def test_tokens_match_169_tokenizer_exactly(lg, name):
    spec = importlib.util.spec_from_file_location("noggin_tokens", ROOT / "scripts" / "noggin_tokens.py")
    nt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nt)
    plays = _plays(lg, name)
    mine = [t for e in E.events(League(lg), plays) for t in E.tokens_v1(e)]
    assert mine == nt.encode_game(lg, plays, "H", "A")[3:-1].tolist()
