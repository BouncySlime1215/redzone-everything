"""Corpus v1 tokeniser (B-02): vocab v1 round trip on a fixture game, a pinned vocab fingerprint, coverage counters, bucket bounds.

Why: B-02 done-when 3: `detokenise(tokenise(game))` must reproduce the event sequence, and the token ids must never move without a version bump,
because a shard built under another layout would train a model on ids that mean something else.
"""
from __future__ import annotations

import datetime as dt
import math
import zlib

import numpy as np
import pytest

from redzone.lab.corpus import tokenise as tk
from redzone.lab.corpus.records import GameRec
from scripts import noggin_tokens as NT


def slot(name: str) -> int:
    return zlib.crc32(name.encode()) % 2048


def play(i: int, kind: str, per: int, clock: str | None, wall_s: int | None, hs: int | None, as_: int | None, scoring: bool = False, sv: int = 0) -> dict:
    p: dict = {"id": str(i), "sequenceNumber": str(i), "type": {"text": kind}, "text": kind, "period": {"number": per},
               "clock": {"displayValue": clock or ""}, "scoringPlay": scoring, "scoreValue": sv}
    if wall_s is not None:
        p["wallclock"] = (dt.datetime(2024, 2, 3, 1, 0, 0) + dt.timedelta(seconds=wall_s)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if hs is not None:
        p["homeScore"], p["awayScore"] = hs, as_
    return p


def fixture_game() -> GameRec:
    plays = [
        play(0, "Jump Shot", 1, "11:30", 0, 2, 0, True, 2),
        play(1, "Defensive Rebound", 1, "11:20", 15, 2, 0),
        play(2, "Timeout", 1, "10:55", 45, 2, 0),
        play(3, "Three Point Jumper", 2, "6:00", 105, 2, 3, True, 3),
        play(4, "Personal Foul", 3, "0:30", 120, 2, 3),
        play(5, "Substitution", 4, None, None, None, None),            # no stamp, no clock, no score: gap unknown, score carried
        play(6, "End Game", 4, "0:00", 7320, None, None),
    ]
    return GameRec("espn", "history-shard", "nba", 2024, "401", "20240202", True, "WSH", "BOS", plays)


# Hand-derived from the vocab v1 rules in the scripts/noggin_tokens.py docstring: gap bucket = int(log2(1 + seconds) * 2.6) capped at 31,
# clock bucket = int(16 * elapsed share of the period), margin = home - away clipped to 20, points 0..7 and the side that scored.
EXPECTED = [
    dict(kind="jump_shot", dt_bucket=None, period=1, clock_bucket=0, margin=2, pts_side="home", pts=2, stop="none"),
    dict(kind="defensive_rebound", dt_bucket=10, period=1, clock_bucket=0, margin=2, pts_side=None, pts=0, stop="none"),
    dict(kind="timeout", dt_bucket=12, period=1, clock_bucket=1, margin=2, pts_side=None, pts=0, stop="timeout"),
    dict(kind="three", dt_bucket=15, period=2, clock_bucket=8, margin=-1, pts_side="away", pts=3, stop="none"),
    dict(kind="personal_foul", dt_bucket=10, period=3, clock_bucket=15, margin=-1, pts_side=None, pts=0, stop="whistle"),
    dict(kind="substitution", dt_bucket=None, period=4, clock_bucket=0, margin=-1, pts_side=None, pts=0, stop="whistle"),
    dict(kind="end_game", dt_bucket=31, period=4, clock_bucket=15, margin=-1, pts_side=None, pts=0, stop="end_period"),
]


def test_round_trip_reproduces_the_event_sequence():
    rec = fixture_game()
    names = tk.NameTables()
    t = tk.tokenise_game(rec, names)
    assert t.tokens.dtype == np.uint16 and t.events == len(EXPECTED) == len(rec.plays)
    game = tk.detokenise_game(t.tokens, names)
    assert game["league"] == "nba" and game["home"] == ["WSH"] and game["away"] == ["BOS"]
    assert len(game["events"]) == len(EXPECTED)
    for got, want in zip(game["events"], EXPECTED):
        assert got["kind_slot"] == slot(want["kind"]) and got["kind"] == [want["kind"]]
        assert {k: got[k] for k in want if k != "kind"} == {k: want[k] for k in want if k != "kind"}
    assert np.array_equal(tk.retokenise(game), t.tokens)           # the decoded game encodes back to the same ids, exactly


def test_the_same_game_gives_the_same_bytes_and_the_tokens_are_in_the_vocab():
    a, b = tk.tokenise_game(fixture_game()), tk.tokenise_game(fixture_game())
    assert a.tokens.tobytes() == b.tokens.tobytes()
    assert int(a.tokens.max()) < NT.VOCAB_SIZE and a.tokens[0] == NT.BOS0 + NT.LEAGUES.index("nba") and a.tokens[-1] == NT.EOS
    assert (len(a.tokens) - 4) % NT.TOKENS_PER_EVENT == 0


def test_vocab_v1_ids_are_pinned():
    """Changing any id offset or slot count changes this fingerprint: bump VOCAB_VERSION, rebuild every corpus, then update the pin."""
    assert NT.VOCAB_VERSION == 1 and NT.VOCAB_SIZE == 10511 and NT.TOKENS_PER_EVENT == 6
    assert tk.vocab_fingerprint() == "06a74e5ff6a13dfbc0b0139e5c83f94286ebb7c7385f86d0a48e5e5b8649c115"
    lay = tk.vocab_layout()
    assert lay["offsets"]["KIND0"] == 2058 and lay["offsets"]["STOP0"] == 10499 and lay["slots"]["kind"] == 2048


def test_coverage_counters_name_what_is_unknown():
    rec = fixture_game()
    rec.plays.append(play(7, "", 4, "0:00", None, None, None))        # no kind at all, no stamp
    t = tk.tokenise_game(rec)
    c = t.counters
    assert c["events"] == 8 and c["kind_blank"] == 1
    assert c["unk_dt"] == 2 and c["unk_dt_after_first"] == 1       # the first event never has a previous one: not counted as unknown
    assert c["unknown_events"] == 2                                 # the substitution (no gap) and the blank-kind event; the rest are clean
    assert c["clock_missing"] == 1 and c["margin_clipped"] == 0 and c["stop_unknown"] == 0
    assert tk.counters_from_tokens(t.tokens, [0])["unk_dt"] == 2


def test_counters_from_tokens_agree_with_the_tokeniser_over_two_games():
    a, b = tk.tokenise_game(fixture_game()), tk.tokenise_game(fixture_game())
    both = np.concatenate([a.tokens, b.tokens])
    got = tk.counters_from_tokens(both, [0, len(a.tokens)])
    assert got["events"] == 14 and got["unk_dt_after_first"] == 2 * a.counters["unk_dt_after_first"] and got["unk_dt"] == 2 * a.counters["unk_dt"]


def test_clipped_margin_is_counted_and_decoded_as_the_cap():
    rec = fixture_game()
    rec.plays[-1]["homeScore"], rec.plays[-1]["awayScore"] = 60, 2
    t = tk.tokenise_game(rec)
    assert t.counters["margin_clipped"] == 1
    assert tk.detokenise_game(t.tokens)["events"][-1]["margin"] == 20


@pytest.mark.parametrize("seconds", [0.0, 0.4, 1, 3, 10, 15, 45, 300, 3600, 5000])
def test_gap_bucket_bounds_contain_the_gap(seconds):
    b = NT.dt_token(seconds) - NT.DT0
    lo, hi = tk.dt_bounds(b)
    assert lo - 1e-9 <= seconds < hi + 1e-9
    assert b == min(31, int(math.log2(1 + seconds) * 2.6))


@pytest.mark.parametrize("bad", [[], [NT.EOS], [NT.BOS0, NT.TEAM0, NT.TEAM0, NT.EOS, NT.EOS], [5, 5, 5, NT.EOS]])
def test_detokenise_refuses_what_is_not_a_game(bad):
    with pytest.raises(tk.TokenError):
        tk.detokenise_game(bad)


def test_detokenise_refuses_a_team_token_of_another_league_family():
    t = tk.tokenise_game(fixture_game()).tokens.copy()
    t[1] = NT.TEAM0 + NT.BASES.index("nhl") * NT.TEAM_SLOTS          # an nhl team slot in an nba game
    with pytest.raises(tk.TokenError):
        tk.detokenise_game(t)


def test_name_tables_report_shared_slots_and_round_trip_through_json():
    names = tk.NameTables()
    names.add_kind("nba", 5, "a")
    names.add_kind("nba", 5, "b")
    names.add_kind("nba", 6, "c")
    names.add_kind("nba", 6, "c")
    assert names.shared_kind_events() == 2 and names.kind_names("nba", 5) == ["a", "b"]
    again = tk.NameTables.from_json(names.to_json())
    assert again.to_json() == names.to_json() and again.shared_kind_events() == 2
