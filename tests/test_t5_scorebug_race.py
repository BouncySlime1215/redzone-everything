"""T5 tester (Nov 5): the TNF Prime score-bug live-edge reader and the feed race (crew D #315, lane 2 #326).

Checked, per the T5 order:
  1. The score-bug reader keeps NUMBERS ONLY, never frames: no PNG survives a read, nothing the watcher or
     the read log keeps holds bytes, and every logged field is a number or a short string.
  2. First-seen in the race: a source's first sighting of a score is stamped once, at receipt.
  3. Clock alignment in the race grade: lead = screen onset - source first-seen, positive = source earlier.
  4. Polite polling in the race poller: ETag / If-Modified-Since, and backoff on 403 / 429 / 5xx.

Tests that need code not yet on integration/next (feed_race.py from #315, the read log from #326) skip until
it lands. Findings T5-1..T5-3 were fixed in #315; their tests now run as plain regression tests.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

from redzone.decider.screen import Event
from redzone.models import GameState, League, Phase, Team
from redzone.screen import scorebug
from redzone.screen.scorebug import Bug, Sample, ScoreBugWatcher, read_png
from redzone.screen_ocr import TextBox

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "scorebug"
HOME, AWAY = Team("ORL", "Magic"), Team("WSH", "Wizards")
CLEAN_OCR = [
    TextBox("MI MONUMENTAL SPORTS", 1.0, .380, .762, .238, .039), TextBox("60 59", 1.0, .442, .814, .120, .072),
    TextBox("ORLANDO", 1.0, .682, .828, .062, .030), TextBox("WASHINGTON", 1.0, .254, .828, .082, .034),
    TextBox("MAGICY", 1.0, .638, .856, .136, .063), TextBox("WIZARDS", 1.0, .256, .858, .152, .043),
    TextBox("HALFTIME", 1.0, .464, .897, .074, .021),
]


class _OCR:
    """Records the path it was handed (and whether a PNG was there), returns canned boxes."""

    def __init__(self, rows):
        self.rows, self.paths = rows, []

    def __call__(self, path, timeout=8.0):
        self.paths.append((path, os.path.exists(path), open(path, "rb").read(8) if os.path.exists(path) else b""))
        rows = self.rows

        class R:
            read_ok = bool(rows)
            observations = tuple(rows)
        return R()


def _bytes_inside(obj, seen=None, path="") -> list[str]:
    """Every place inside obj (dicts, lists, dataclasses, plain objects) that holds bytes."""
    seen = set() if seen is None else seen
    if id(obj) in seen:
        return []
    seen.add(id(obj))
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return [path or "<root>"]
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += _bytes_inside(k, seen, f"{path}[key]") + _bytes_inside(v, seen, f"{path}[{k!r}]")
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for i, v in enumerate(obj):
            out += _bytes_inside(v, seen, f"{path}[{i}]")
    elif dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        for f in dataclasses.fields(obj):
            out += _bytes_inside(getattr(obj, f.name), seen, f"{path}.{f.name}")
    elif hasattr(obj, "__dict__") and not isinstance(obj, type) and not callable(obj):
        for k, v in vars(obj).items():
            out += _bytes_inside(v, seen, f"{path}.{k}")
    return out


# ---------------------------------------------------------------- 1. numbers only, never frames

def test_read_png_leaves_no_frame_on_disk():
    png = (FIX / "clean.png").read_bytes()
    ocr = _OCR(CLEAN_OCR)
    bug = read_png(png, HOME, AWAY, ocr)
    assert bug is not None and bug.score == (59, 60)
    (path, existed, head), = ocr.paths
    assert existed and head.startswith(b"\x89PNG")       # the OCR got a real file while it ran...
    assert not os.path.exists(path)                        # ...and it is gone the moment the read returns
    assert not os.path.exists(os.path.dirname(path))
    assert os.path.dirname(os.path.dirname(path)) == tempfile.gettempdir()   # never under data/ or the repo


def test_read_png_removes_the_frame_even_when_ocr_raises():
    png = (FIX / "clean.png").read_bytes()
    where = []

    def boom(path, timeout=8.0):
        where.append(path)
        raise RuntimeError("vision crashed")

    with pytest.raises(RuntimeError):
        read_png(png, HOME, AWAY, boom)
    assert where and not os.path.exists(where[0])


def test_a_blanked_frame_is_never_written_or_read():
    png = (FIX / "yttv_black.png").read_bytes() if (FIX / "yttv_black.png").exists() else None
    if png is None:
        cands = [p for p in FIX.glob("*.png") if scorebug.is_uniform(p.read_bytes())]
        if not cands:
            pytest.skip("no blanked fixture")
        png = cands[0].read_bytes()
    ocr = _OCR(CLEAN_OCR)
    assert read_png(png, HOME, AWAY, ocr) is None
    assert ocr.paths == []


def test_bug_and_sample_hold_only_numbers_and_short_text():
    for cls in (Bug, Sample, scorebug.Frame):
        for f in dataclasses.fields(cls):
            assert "bytes" not in str(f.type).lower(), f"{cls.__name__}.{f.name} could hold a frame"


def _scores(*rows):
    return [Event(w, "score", label=s, spread=sp, score=tuple(int(x) for x in s.split("-")))
            for s, w, sp in rows]


def test_watcher_state_holds_no_bytes_after_a_night_of_reads():
    tl = type("TL", (), {})()
    tl.scores = _scores(("57-58", 900.0, 0), ("59-60", 1000.0, 0))
    tl.clocks = [(1000.0, 2, 600.0), (1060.0, 2, 540.0)]
    w = ScoreBugWatcher()
    got = []
    w.on_sample = got.append
    tune = object()
    for t, bug in ((1020.0, Bug(57, 58, "2ND", "10:00")), (1022.0, Bug(59, 60, "2ND", "9:58")),
                   (1024.0, Bug(59, 60, "2ND", "9:56")), (1026.0, None)):
        w.observe("stick", "prime:abc", tune, t, bug, tl)
    assert got, "the fixture should produce a sample"
    assert _bytes_inside(w) == []
    assert _bytes_inside(got) == []
    json.dumps(w.status())                                   # the status page is plain JSON


def test_engine_scorebug_sample_stores_floats_only(tmp_path):
    from redzone.config import Config
    from redzone.engine import Engine
    from redzone.store import Store
    e = Engine(Config(), Store(tmp_path / "rz.db"))
    calls = []
    e.store.calibration = lambda *a, **k: calls.append((a, k))
    e._scorebug_sample(Sample("prime:abc", 22.5, 1000.0, 1022.5, 1.0, (59, 60)))
    assert calls
    for a, k in calls:
        assert _bytes_inside(a) == [] and _bytes_inside(k) == []
        assert all(isinstance(x, (int, float, str)) for x in a)


def test_read_log_rows_are_numbers_only(tmp_path):
    """#326 (lane 2): every bug read goes to data/scorebug_reads.jsonl. Only numbers / short strings, a fixed
    key set, and never anything that looks like an image."""
    from redzone.config import Config
    from redzone.engine import Engine
    from redzone.store import Store
    e = Engine(Config(), Store(tmp_path / "rz.db"))
    if not hasattr(e, "_log_bug_read"):
        pytest.skip("scorebug read log (#326) not on this branch yet")
    e._bug_log_path = tmp_path / "scorebug_reads.jsonl"
    g = GameState(League.NFL, "401", Team("BAL", "Ravens"), Team("JAX", "Jaguars"), phase=Phase.LIVE)
    png = (FIX / "clean.png").read_bytes()
    f = {"stick": object(), "target": "prime:tnf", "visible": False, "tune": object(), "png": png,
         "t0": 1_793_923_800.123, "t1": 1_793_923_806.6}
    e._log_bug_read(g, f, Bug(7, 3, "2ND", "4:12", "3&7") if "down" in {x.name for x in dataclasses.fields(Bug)}
                    else Bug(7, 3, "2ND", "4:12"))
    raw = e._bug_log_path.read_bytes()
    assert b"PNG" not in raw and b"IHDR" not in raw and len(raw) < 400
    row = json.loads(raw.decode().splitlines()[0])
    assert set(row) <= {"t0", "t1", "league", "game", "target", "visible", "home", "away", "period", "clock", "down"}
    for k, v in row.items():
        assert isinstance(v, (int, float, bool, str)), k
        assert not isinstance(v, str) or len(v) <= 40, k
    assert row["home"] == 7 and row["away"] == 3
    assert row["t0"] <= row["t1"]                            # first-seen = capture start, not OCR end


def test_no_code_path_saves_scorebug_frames():
    """Static guard: the score-bug path never writes image bytes anywhere but read_png's temp dir."""
    src = (ROOT / "redzone" / "screen" / "scorebug.py").read_text()
    assert src.count("open(") == 1 and "TemporaryDirectory" in src
    eng = (ROOT / "redzone" / "engine.py").read_text()
    i = eng.index("async def _scorebug_loop")
    loop = eng[i:eng.index("\n    async def ", i + 10)]
    assert "write_bytes" not in loop and ".png\"" not in loop and "'wb'" not in loop and '"wb"' not in loop


# ---------------------------------------------------------------- 2-4. the feed race (#315)

def _race():
    p = ROOT / "scripts" / "markets" / "feed_race.py"
    if not p.exists():
        pytest.skip("scripts/markets/feed_race.py (#315) not on this branch yet")
    spec = importlib.util.spec_from_file_location("t5_feed_race", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_race_first_seen_is_receipt_of_the_first_changed_poll():
    R = _race()
    c = R.ChangeLog()
    assert c.see("espn_summary", "JAX@BAL", (0, 0), 10.0)["prev"] is None
    assert c.see("espn_summary", "JAX@BAL", (0, 0), 12.0) is None
    r = c.see("espn_summary", "JAX@BAL", (0, 6), 14.0)
    assert r["recv_ts"] == 14.0
    assert c.see("espn_summary", "JAX@BAL", (0, 6), 16.0) is None     # re-seen: first-seen stays 14
    # each source keeps its own first-seen
    assert c.see("espn_scoreboard", "JAX@BAL", (0, 6), 18.0)["prev"] is None


def test_race_lead_sign_and_app_feed_alignment():
    R = _race()
    sync = [json.dumps({"kind": "score", "target": "prime:tnf", "t": 5000.0, "z": 16.0})]
    screens = R.screen_rows(sync, 0, 1e9, "prime")
    assert screens == [{"screen_ts": 5000.0, "app_feed_ts": 4984.0, "target": "prime:tnf"}]
    espn = [{"source": "espn_summary", "recv_ts": 4990.0, "score": [0, 0], "prev": None},
            {"source": "espn_summary", "recv_ts": 5030.0, "score": [0, 6], "prev": [0, 0]}]
    res = R.grade(screens, espn, [], "g")
    assert res["rows"][0]["lead_s"]["app_feed"] == 16.0                 # feed had it 16 s before the screen
    assert res["rows"][0]["lead_s"]["espn_summary"] == -30.0            # ESPN 30 s after
    json.dumps(res)


def test_race_one_market_trigger_counts_for_one_score_only():
    R = _race()
    sync = [json.dumps({"kind": "score", "target": "prime:tnf", "t": t, "z": 16.0}) for t in (1000.0, 1050.0)]
    screens = R.screen_rows(sync, 0, 1e9)
    poly = [{"row": "trigger", "game_key": "g", "recv_ts": 990.0}]
    res = R.grade(screens, [], poly, "g")
    assert res["summary"]["polymarket"]["n"] == 1


def test_race_matches_sightings_by_score_not_just_time():
    R = _race()
    screens = [{"screen_ts": 1000.0, "app_feed_ts": 990.0, "target": "prime:tnf", "score": [0, 6]},
               {"screen_ts": 1060.0, "app_feed_ts": 1050.0, "target": "prime:tnf", "score": [0, 7]}]
    espn = [{"source": "espn_summary", "recv_ts": 900.0, "score": [0, 0], "prev": None},
            {"source": "espn_summary", "recv_ts": 1065.0, "score": [0, 7], "prev": [0, 0]}]
    res = R.grade(screens, espn, [], "g")
    assert "espn_summary" not in res["rows"][0]["lead_s"]
    assert res["rows"][1]["lead_s"]["espn_summary"] == -5.0


class _FakeResp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        return self._body


class _FakeRequests:
    """A stand-in for `requests` with a fake clock: records each GET and its time and headers."""

    def __init__(self, clock, respond):
        self.clock, self.respond, self.calls = clock, respond, []
        outer = self

        class Session:
            def __init__(self):
                self.headers = {}

            def get(self, url, params=None, headers=None, timeout=None):
                outer.calls.append((outer.clock.t, url, dict(headers or {})))
                return outer.respond(url, headers or {})
        self.Session = Session


class _FakeTime:
    def __init__(self, t=0.0):
        self.t = t

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, s)


def _board(away, home):
    return {"events": [{"id": "401", "competitions": [{"competitors": [
        {"homeAway": "home", "score": str(home), "team": {"abbreviation": "BAL"}},
        {"homeAway": "away", "score": str(away), "team": {"abbreviation": "JAX"}}]}]}]}


def _run_poll(R, respond, minutes, tmp_path, monkeypatch):
    clk = _FakeTime(0.0)
    fake = _FakeRequests(clk, respond)
    monkeypatch.setitem(sys.modules, "requests", fake)
    monkeypatch.setattr(R, "time", clk)
    out = tmp_path / "espn.jsonl"
    R.poll_espn({"JAX", "BAL"}, minutes / 60.0, out)
    rows = [json.loads(x) for x in out.read_text().splitlines()] if out.exists() else []
    return fake.calls, rows


def test_race_poller_sends_conditional_gets_and_a_304_is_no_change(tmp_path, monkeypatch):
    R = _race()

    def respond(url, h):
        if h.get("If-None-Match") == '"a"':
            return _FakeResp(304)
        return _FakeResp(200, _board(0, 0) if "scoreboard" in url else {"header": {"competitions": []}},
                         {"ETag": '"a"'})

    calls, rows = _run_poll(R, respond, 1, tmp_path, monkeypatch)
    board = [c for c in calls if "scoreboard" in c[1]]
    assert len(board) >= 10 and all(c[2].get("If-None-Match") == '"a"' for c in board[1:])
    assert len(rows) == 1 and rows[0]["prev"] is None                 # the first poll only; 304s add nothing
    per_s = len(calls) / 60.0
    assert per_s <= 1.05                                               # 2 sources, each every 2 s


@pytest.mark.parametrize("status", [403, 503])
def test_race_poller_backs_off_on_403_and_5xx(status, tmp_path, monkeypatch):
    R = _race()
    calls, _ = _run_poll(R, lambda url, h: _FakeResp(status), 2, tmp_path, monkeypatch)
    assert len(calls) <= 20                                            # 2 min of errors: a handful, not ~120


def test_race_sources_are_public_and_tokenless():
    R = _race()
    src = (ROOT / "scripts" / "markets" / "feed_race.py").read_text().lower()
    assert R.SITE.startswith("https://site.api.espn.com/")
    for bad in ("kalshi", "cookie", "authorization", "bearer", "api_key", "apikey", "token="):
        assert bad not in src, bad
