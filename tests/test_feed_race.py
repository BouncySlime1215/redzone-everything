"""A2: the source race times whistles as well as scores, and the recorder writes every row with
its followers' lags, past the merger's 2000-event trim."""
import json

from redzone.lab import timing
from redzone.models import GameState, League, Phase, Stoppage, Team
from redzone.recorder import RACE_SETTLE_S, Recorder
from redzone.sources.merge import Merger, race_labels


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def _g(clock_s, stoppage=Stoppage.NONE, gid="7", hs=1, as_=0):
    return GameState(league=League.NHL, game_id=gid, home=Team("WSH", score=hs), away=Team("NYR", score=as_),
                     phase=Phase.LIVE, period=2, clock_s=clock_s, stoppage=stoppage)


def test_dead_ball_label_ignores_stoppage_kind():
    assert race_labels(_g(600.4, Stoppage.WHISTLE)) == ["score 0-1", "stop p2 600"]
    assert race_labels(_g(600.0, Stoppage.TIMEOUT))[1] == "stop p2 600"
    assert race_labels(_g(600.0)) == ["score 0-1"]
    assert race_labels(_g(600.0, Stoppage.UNKNOWN)) == ["score 0-1"]


def test_whistle_race_measures_the_follower():
    clk = Clock()
    m = Merger(clock=clk)
    m.update("espn_summary", _g(640))
    clk.t += 3
    m.update("nhl", _g(600, Stoppage.WHISTLE))
    clk.t += 31
    m.update("espn_summary", _g(600, Stoppage.WHISTLE))
    stop = [e for e in m.race if e.label.startswith("stop")][0]
    assert stop.first_source == "nhl" and stop.others == {"espn_summary": 31.0}


def test_recorder_waits_for_followers_and_survives_trim(tmp_path):
    clk = Clock()
    m = Merger(clock=clk)
    rec = Recorder(tmp_path)
    for i in range(2100):                       # past the merger's 2000-event trim
        m.update("espn_summary", _g(600, gid=f"x{i}"))
        clk.t += 1
    rec.race(m, now=clk.t)
    n_before = len(m.race)
    m.update("nhl", _g(50, Stoppage.WHISTLE, gid="9"))
    rec.race(m, now=clk.t)                      # too fresh: not written yet
    clk.t += 20
    m.update("espn_summary", _g(50, Stoppage.WHISTLE, gid="9"))
    rec.race(m, now=clk.t + RACE_SETTLE_S)
    path = next(tmp_path.glob("recordings/*/race.jsonl"))
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    stop = [r for r in rows if r["game"] == "9" and r["label"].startswith("stop")]
    assert n_before == 2000 and len(stop) == 1
    assert stop[0]["first"] == "nhl" and stop[0]["others"] == {"espn_summary": 20.0}
    assert stop[0]["league"] == "nhl"
    rec.race(m, now=clk.t + 2 * RACE_SETTLE_S)  # nothing is written twice
    assert len(path.read_text().splitlines()) == len(rows)


def test_race_report_gain_over_espn(tmp_path):
    p = tmp_path / "race.jsonl"
    rows = [{"t": 1, "game": "7", "label": "stop p2 600", "first": "nhl", "others": {"espn_summary": 31.0}},
            {"t": 0.5, "game": "7", "label": "stop p2 600", "first": "nhl", "others": {"espn_summary": 30.0}},
            {"t": 2, "game": "7", "label": "score 0-1", "first": "espn_scoreboard", "others": {"nhl": 4.0}},
            {"t": 3, "game": "8", "label": "score 0-0", "first": "nhl", "others": {}}]
    p.write_text("\n".join(json.dumps(r) for r in rows))
    rep = timing.race_report([p], {"7": "nhl"})
    stop = rep["nhl"]["stop"]
    assert stop["n"] == 1 and stop["sources"]["nhl"]["first_pct"] == 100.0
    assert stop["sources"]["nhl"]["gain_s"]["p50"] == 30.0          # earliest copy of the dup kept
    assert rep["nhl"]["score"]["sources"]["nhl"]["gain_s"]["p50"] == -4.0
