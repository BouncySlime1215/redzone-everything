"""ESPN Fastcast push feed: JSON Patch, payloads, message parsing, in-order sync, the
game-document converter (against real data), the reconnecting client (mock websocket),
and the engine integration (inert when off)."""
import asyncio
import base64
import copy
import glob
import gzip
import json
import zlib
from pathlib import Path

import httpx
import pytest

from redzone.channels import ChannelMap
from redzone.config import Config
from redzone.engine import Engine
from redzone.models import GameState, League, Phase, Team
from redzone.recorder import Recorder
from redzone.sources import espn
from redzone.sources import fastcast as fc
from redzone.sources.fastcast import Message, PatchError, Resync, TopicDoc, apply_patch
from redzone.store import Store

FIX = Path(__file__).parent / "fixtures"
WCBB_TOPIC = "gp-basketball-womens-college-basketball-401918849"


# --- JSON Patch (RFC 6902) -------------------------------------------------------------

def test_add_to_objects_and_arrays():
    doc = {"a": {"b": 1}, "list": [1, 2]}
    out = apply_patch(doc, [{"op": "add", "path": "/a/c", "value": 2},
                            {"op": "add", "path": "/a/b", "value": 9},          # add replaces an existing key
                            {"op": "add", "path": "/list/0", "value": 0},
                            {"op": "add", "path": "/list/-", "value": 3},       # '-' appends
                            {"op": "add", "path": "/list/4", "value": 4}])      # index == len appends too
    assert out == {"a": {"b": 9, "c": 2}, "list": [0, 1, 2, 3, 4]}
    assert doc == {"a": {"b": 1}, "list": [1, 2]}                               # not in place: untouched


def test_remove_replace_and_whole_document():
    doc = {"a": [1, 2, 3], "b": {"c": 1}}
    out = apply_patch(doc, [{"op": "remove", "path": "/a/1"}, {"op": "remove", "path": "/b/c"},
                            {"op": "replace", "path": "/a/0", "value": "x"}])
    assert out == {"a": ["x", 3], "b": {}}
    assert apply_patch(doc, [{"op": "replace", "path": "", "value": [1]}]) == [1]
    assert apply_patch(doc, [{"op": "add", "path": "", "value": {"new": 1}}]) == {"new": 1}


def test_move_and_copy():
    doc = {"a": {"x": [1, {"deep": 1}]}, "list": [1, 2, 3]}
    out = apply_patch(doc, [{"op": "move", "from": "/a/x", "path": "/b"},
                            {"op": "move", "from": "/list/0", "path": "/list/-"},
                            {"op": "copy", "from": "/b/1", "path": "/c"}])
    assert out == {"a": {}, "b": [1, {"deep": 1}], "list": [2, 3, 1], "c": {"deep": 1}}
    out = apply_patch(out, [{"op": "replace", "path": "/c/deep", "value": 2}])
    assert out["b"][1] == {"deep": 1}                                           # a copy, not a shared object
    assert apply_patch({"a": 1}, [{"op": "move", "from": "/a", "path": "/a"}]) == {"a": 1}
    with pytest.raises(PatchError):
        apply_patch({"a": {"b": 1}}, [{"op": "move", "from": "/a", "path": "/a/b/c"}])


def test_test_op_is_strict_json_equality():
    doc = {"n": 1, "f": 1.0, "t": True, "o": {"a": [1, "x"]}}
    apply_patch(doc, [{"op": "test", "path": "/n", "value": 1.0}, {"op": "test", "path": "/o", "value": {"a": [1, "x"]}}])
    for bad in ({"op": "test", "path": "/t", "value": 1}, {"op": "test", "path": "/n", "value": True},
                {"op": "test", "path": "/n", "value": "1"}, {"op": "test", "path": "/o", "value": {"a": [1]}}):
        with pytest.raises(PatchError):
            apply_patch(doc, [bad])


def test_pointer_escapes():
    doc = {"a/b": 1, "m~n": 2, "~1": 3, "": {"": 4}}
    out = apply_patch(doc, [{"op": "replace", "path": "/a~1b", "value": 10},
                            {"op": "replace", "path": "/m~0n", "value": 20},
                            {"op": "replace", "path": "/~01", "value": 30},     # ~01 is '~1', not '/'
                            {"op": "replace", "path": "//", "value": 40}])      # empty keys
    assert out == {"a/b": 10, "m~n": 20, "~1": 30, "": {"": 40}}


@pytest.mark.parametrize("op", [
    {"op": "add", "path": "/missing/x", "value": 1},       # parent doesn't exist
    {"op": "remove", "path": "/nope"},
    {"op": "replace", "path": "/nope", "value": 1},        # replace needs an existing target
    {"op": "add", "path": "/list/01", "value": 1},          # leading zero
    {"op": "add", "path": "/list/9", "value": 1},           # past the end
    {"op": "replace", "path": "/list/-", "value": 1},       # '-' only for add
    {"op": "remove", "path": "/list/2"},
    {"op": "add", "path": "no-slash-and-no-uid-match", "value": 1},
    {"op": "copy", "path": "/x"},                           # copy without from
    {"op": "add", "path": "/x"},                            # add without value
    {"op": "frobnicate", "path": "/x"},
    {"op": "remove", "path": ""},
    {"op": "replace", "path": 7, "value": 1},              # a path that isn't text
    "not an op",
])
def test_bad_operations_fail_cleanly(op):
    doc = {"list": [1, 2], "s": "text"}
    with pytest.raises(PatchError):
        apply_patch(doc, [op])
    assert doc == {"list": [1, 2], "s": "text"}


def test_failed_patch_leaves_original_untouched():
    doc = {"a": 1, "b": [1]}
    with pytest.raises(PatchError):
        apply_patch(doc, [{"op": "replace", "path": "/a", "value": 2}, {"op": "remove", "path": "/zzz"}])
    assert doc == {"a": 1, "b": [1]}


def test_scoreboard_uid_paths():
    """event-topevents patches address events by uid: 's:40~l:54~e:1~c:1/clock'."""
    doc = {"sports": [{"uid": "s:40", "leagues": [{"uid": "s:40~l:54", "events": [
        {"uid": "s:40~l:54~e:1~c:1", "clock": "5:21", "competitors": [{"uid": "s:40~l:54~t:9", "score": "3"}]},
        {"uid": "s:40~l:54~e:2~c:2", "clock": "1:00"}]}]}]}
    cache = {}
    out = apply_patch(doc, [{"op": "replace", "path": "s:40~l:54~e:2~c:2/clock", "value": "0:59"},
                            {"op": "replace", "path": "s:40~l:54~e:1~c:1/competitors/0/score", "value": "5"}],
                      uid_cache=cache)
    events = out["sports"][0]["leagues"][0]["events"]
    assert events[1]["clock"] == "0:59" and events[0]["competitors"][0]["score"] == "5"
    # An insert shifts the cached spot; the uid is re-found, not patched into the wrong event.
    out = apply_patch(out, [{"op": "add", "path": "/sports/0/leagues/0/events/0", "value": {"uid": "s:40~l:54~e:3~c:3"}},
                            {"op": "replace", "path": "s:40~l:54~e:2~c:2/clock", "value": "0:58"}], uid_cache=cache)
    assert out["sports"][0]["leagues"][0]["events"][2] == {"uid": "s:40~l:54~e:2~c:2", "clock": "0:58"}


def test_rfc6902_appendix_examples():
    assert apply_patch({"foo": ["bar", "baz"]}, [{"op": "add", "path": "/foo/1", "value": "qux"}]) == \
        {"foo": ["bar", "qux", "baz"]}
    assert apply_patch({"baz": "qux", "foo": "bar"}, [{"op": "replace", "path": "/baz", "value": "boo"}]) == \
        {"baz": "boo", "foo": "bar"}
    assert apply_patch({"foo": {"bar": "baz", "waldo": "fred"}, "qux": {"corge": "grault"}},
                       [{"op": "move", "from": "/foo/waldo", "path": "/qux/thud"}]) == \
        {"foo": {"bar": "baz"}, "qux": {"corge": "grault", "thud": "fred"}}
    assert apply_patch({"foo": ["all", "grass", "cows", "eat"]}, [{"op": "move", "from": "/foo/1", "path": "/foo/3"}]) == \
        {"foo": ["all", "cows", "eat", "grass"]}
    assert apply_patch({"foo": "bar"}, [{"op": "add", "path": "/child", "value": {"grandchild": {}}}]) == \
        {"foo": "bar", "child": {"grandchild": {}}}
    assert apply_patch({"foo": ["bar"]}, [{"op": "add", "path": "/foo/-", "value": ["abc", "def"]}]) == \
        {"foo": ["bar", ["abc", "def"]]}
    with pytest.raises(PatchError):
        apply_patch({"foo": "bar"}, [{"op": "add", "path": "/baz/bat", "value": "qux"}])


# --- payloads and messages --------------------------------------------------------------

def _b64z(obj, compress=zlib.compress) -> str:
    return base64.b64encode(compress(json.dumps(obj).encode())).decode()


def test_inflate_zlib_gzip_and_raw_deflate():
    ops = [{"op": "add", "path": "/x", "value": 1}]
    raw = zlib.compressobj(wbits=-15)
    deflated = raw.compress(json.dumps(ops).encode()) + raw.flush()
    for b64 in (_b64z(ops), _b64z(ops, gzip.compress), base64.b64encode(deflated).decode()):
        assert json.loads(fc.inflate(b64)) == ops


def test_inflate_refuses_bombs_and_junk():
    bomb = base64.b64encode(zlib.compress(b"0" * 1_000_000)).decode()
    with pytest.raises(fc.FastcastError, match="inflates past"):
        fc.inflate(bomb, limit=10_000)
    with pytest.raises(fc.FastcastError):
        fc.inflate(base64.b64encode(b"not compressed at all").decode())
    with pytest.raises(fc.FastcastError):
        fc.inflate("@@@not base64@@@")


def _p(tc, mid, ops, ts=1793640954563, compressed=True, op="P"):
    inner = _b64z(ops) if compressed else ops
    return json.dumps({"op": op, "tc": tc, "mid": mid,
                       "pl": json.dumps({"ts": ts, "~c": 1 if compressed else 0, "pl": inner})})


def test_parse_real_message_shapes():
    """Shapes captured from the live feed on 2026-11-02."""
    c = fc.parse_message('{"rc":200,"hbi":30,"sid":"213cdcfd0b52490e8cf045fdcf0a0200","op":"C"}')
    assert (c.op, c.sid, c.hbi, c.rc) == ("C", "213cdcfd0b52490e8cf045fdcf0a0200", 30.0, 200)
    nf = fc.parse_message('{"rc":404,"op":"S","pl":"{\\"error\\": \\"No such Topic.\\"}","tc":"gp-football-nfl-1",'
                          '"sid":"x"}')
    assert (nf.op, nf.rc, nf.error, nf.topic) == ("S", 404, "No such Topic.", "gp-football-nfl-1")
    h = fc.parse_message('{"useCDN":true,"op":"H","pl":"https://fcast.espncdn.com/FastcastService/pubsub/profiles/'
                         '12000/topic/t/message/368/checkpoint","mid":368,"tc":"t"}')
    assert h.mid == 368 and h.url.endswith("/368/checkpoint")
    p = fc.parse_message(_p("t", 369, [{"op": "add", "path": "/x", "value": 1}]))
    assert p.op == "P" and p.mid == 369 and p.ops == [{"op": "add", "path": "/x", "value": 1}]
    assert p.ts == pytest.approx(1793640954.563)
    plain = fc.parse_message('{"lt":true,"op":"R","pl":"{\\"ts\\":1793640883922,\\"~c\\":0,\\"pl\\":[{\\"op\\":'
                             '\\"add\\",\\"path\\":\\"/winprobability/-\\",\\"value\\":{\\"homeWinPercentage\\":0.999}}]}",'
                             '"mid":372,"tc":"t"}')
    assert plain.op == "R" and plain.last and plain.ops[0]["path"] == "/winprobability/-"
    assert fc.parse_message('{"op":"I","pl":"0","mid":374,"tc":"t"}').mid == 374
    assert fc.parse_message('{"op":"B"}').op == "B"
    err = fc.parse_message('{"rc":400,"pl":"topicID cannot be null","op":"ERROR"}')
    assert err.op == "ERROR" and err.error == "topicID cannot be null"
    assert fc.parse_message(b'{"op":"U","rc":200,"tc":"t"}').op == "U"


@pytest.mark.parametrize("raw", ["not json", "[1,2]", '{"no":"op"}'])
def test_malformed_messages_raise_fastcast_error(raw):
    with pytest.raises(fc.FastcastError):
        fc.parse_message(raw)


@pytest.mark.parametrize("pl", ["not json", {"ts": 1, "~c": 1, "pl": "!!!"}, {"ts": 1, "~c": 0, "pl": 7},
                                {"ts": 1, "~c": 0}])
def test_unreadable_update_keeps_its_envelope(pl):
    """Its mid is still spoken for: the topic must know to resync now, not a publish cycle later."""
    raw = json.dumps({"op": "P", "tc": "t", "mid": 7, "pl": pl if isinstance(pl, str) else json.dumps(pl)})
    msg = fc.parse_message(raw)
    assert (msg.op, msg.topic, msg.mid) == ("P", "t", 7) and msg.error and msg.ops is None


# --- one topic's document, strictly in order ---------------------------------------------

def _msg(mid, ops=None, op="P", ts=None):
    return Message(op=op, topic="t", mid=mid, ops=ops, ts=ts)


def test_topic_buffers_until_checkpoint_then_applies_in_order():
    t = TopicDoc("t")
    assert not t.feed(_msg(12, [{"op": "add", "path": "/b", "value": 2}]))   # arrives first, out of order
    assert not t.feed(_msg(10, [{"op": "add", "path": "/old", "value": 0}]))  # already in the checkpoint
    assert not t.feed(_msg(11, [{"op": "add", "path": "/a", "value": 1}]))
    t.load({"base": True}, mid=10)
    assert t.doc == {"base": True, "a": 1, "b": 2} and t.mid == 12


def test_topic_gap_and_bad_patch_force_resync():
    t = TopicDoc("t")
    t.load({"x": 1}, mid=5)
    assert not t.feed(_msg(6, op="I"))            # no-op, but it uses up mid 6
    assert t.feed(_msg(7, [{"op": "replace", "path": "/x", "value": 2}], ts=100.0))
    assert t.published_at == 100.0 and not t.feed(_msg(7, [{"op": "replace", "path": "/x", "value": 3}]))
    with pytest.raises(Resync, match="missed"):
        t.feed(_msg(9, [{"op": "replace", "path": "/x", "value": 4}]))
    t.load({"x": 1}, mid=5)
    with pytest.raises(Resync, match="didn't fit"):
        t.feed(_msg(6, [{"op": "remove", "path": "/nope"}]))
    assert t.doc is None                           # half-patched documents are never passed on


def test_unreadable_update_resyncs_unless_already_covered():
    t = TopicDoc("t")
    t.load({"x": 1}, mid=5)
    assert not t.feed(Message(op="P", topic="t", mid=5, error="update has no patch"))   # inside the checkpoint
    with pytest.raises(Resync, match="couldn't be read"):
        t.feed(Message(op="P", topic="t", mid=6, error="update has no patch"))


def test_topic_pending_is_capped(monkeypatch):
    monkeypatch.setattr(fc, "MAX_PENDING", 3)
    t = TopicDoc("t")
    for mid in range(3):
        t.feed(_msg(mid, []))
    with pytest.raises(Resync):
        t.feed(_msg(3, []))


# --- topics and the game document converter ------------------------------------------------

@pytest.mark.parametrize("path", sorted(glob.glob(str(FIX / "espn_*_summary_*.json"))))
def test_game_topic_matches_espn(path):
    lg = League(Path(path).name.split("_")[1])
    data = json.loads(Path(path).read_text())
    gid = espn._get(data, "header", "id")
    assert fc.game_topic(lg, gid) == data["meta"]["gp_topic"]           # the pattern, without a summary
    assert fc.game_topic(lg, gid, data) == data["meta"]["gp_topic"]
    assert fc.topic_league(data["meta"]["gp_topic"]) == lg and fc.topic_event(data["meta"]["gp_topic"]) == gid


def test_topic_names():
    assert fc.parse_topic_arg("nba:401909911") == "gp-basketball-nba-401909911"
    assert fc.parse_topic_arg("event-topevents") == "event-topevents"
    assert fc.topic_league(WCBB_TOPIC) == League.WCBB                   # women's college: NBA rules via League.base
    assert fc.topic_league("gp-basketball-euroleague-1") == League.NBA  # other basketball reads like the NBA
    assert fc.topic_league("event-topevents") is None
    with pytest.raises(ValueError):
        fc.parse_topic_arg("../../etc/passwd")


def _normalize(lg: League, summary: dict) -> dict:
    """Turn a site API summary into Fastcast's game-document form (as seen live): plays become
    {"$key"} references into entities.plays; a play's team and athletes point into entities."""
    doc = copy.deepcopy(summary)
    ents = {"plays": {}, "teams": {}, "athletes": {}}

    def ref_play(p):
        p = copy.deepcopy(p)
        for holder in [p] + [x for x in (p.get("start"), p.get("end")) if isinstance(x, dict)]:
            if isinstance(holder.get("team"), dict) and holder["team"].get("id"):
                ents["teams"][str(holder["team"]["id"])] = holder["team"]
                holder["team"] = {"$key": str(holder["team"]["id"])}
        for part in p.get("participants") or []:
            a = part.get("athlete")
            if isinstance(a, dict) and a.get("id"):
                ents["athletes"][str(a["id"])] = a
                part["athlete"] = {"$key": str(a["id"])}
        ents["plays"][str(p["id"])] = p
        return {"$key": str(p["id"])}

    if lg == League.NFL:   # unverified until a live NFL game: assumed same pattern inside drives
        for dr in espn._get(doc, "drives", "previous") or []:
            dr["plays"] = [ref_play(p) for p in dr.get("plays") or []]
        cur = espn._get(doc, "drives", "current")
        if cur:
            cur["plays"] = [ref_play(p) for p in cur.get("plays") or []]
    else:
        doc["plays"] = [ref_play(p) for p in doc.get("plays") or []]
    doc["entities"] = ents
    return doc


def _view(g: GameState) -> dict:
    s = g.situation
    return {"id": g.game_id, "score": (g.away.score, g.home.score), "teams": (g.away.abbr, g.home.abbr),
            "phase": g.phase, "period": g.period, "clock": g.clock_s, "stoppage": g.stoppage, "half": g.half,
            "last": g.last_play and (g.last_play.id, g.last_play.kind, g.last_play.wallclock),
            "recent": [p.id for p in g.recent_plays], "wp": g.home_win_prob, "spot": g.spotlight,
            "sit": (s.possession, s.down, s.distance, s.yards_to_goal, s.outs, s.runners)}


@pytest.mark.parametrize("path", sorted(glob.glob(str(FIX / "espn_*_summary_*.json"))))
def test_normalized_document_reads_exactly_like_the_summary(path):
    lg = League(Path(path).name.split("_")[1])
    summary = json.loads(Path(path).read_text())
    doc = _normalize(lg, summary)
    before = copy.deepcopy(doc)
    plays = espn.flatten_plays(lg, summary)
    for upto in (len(plays) // 3, len(plays) - 1):   # mid-game too, not just the final
        g_poll = espn.parse_summary(lg, summary, upto=upto, plays=plays)
        data = fc.to_summary(doc)
        g_push = espn.parse_summary(lg, data, upto=upto, plays=espn.flatten_plays(lg, data))
        assert _view(g_push) == _view(g_poll)
    g, fplays = fc.to_game_state(lg, doc, expect_id=summary["header"]["id"])
    assert _view(g) == _view(espn.parse_summary(lg, summary)) and len(fplays) == len(plays)
    assert doc == before                                               # the live document is never edited
    assert fc.to_game_state(lg, doc, expect_id="999")[0] is None      # wrong game: refused


def test_references_resolve_by_field_and_never_guess():
    doc = {"plays": [{"$key": "p1"}], "keyPlays": [{"play": {"$key": "p1"}, "team": {"$key": "7"}}],
           "other": {"$key": "7"},                            # unknown field, id in two collections: left alone
           "entities": {"plays": {"p1": {"id": "p1", "team": {"$key": "7"}}},
                        "teams": {"7": {"id": "7", "abbreviation": "WSH"}},
                        "competitors": {"7": {"id": "7", "homeAway": "home"}}}}
    out = fc.to_summary(doc)
    assert out["plays"][0]["team"]["abbreviation"] == "WSH"
    assert out["keyPlays"][0]["play"]["id"] == "p1" and out["keyPlays"][0]["team"]["abbreviation"] == "WSH"
    assert out["other"] == {"$key": "7"} and "entities" not in out


def test_reference_cycles_stop():
    doc = {"plays": [{"$key": "a"}], "entities": {"plays": {"a": {"id": "a", "play": {"$key": "a"}}}}}
    out = fc.to_summary(doc)
    depth, node = 0, out["plays"][0]
    while isinstance(node, dict) and "play" in node:
        node, depth = node["play"], depth + 1
    assert depth <= fc.REF_DEPTH + 1


def test_only_complete_live_states_are_usable():
    """A live state without plays or clock would sort as 'end of period' and win the merge."""
    from redzone.models import Play
    live = GameState(league=League.NBA, game_id="1", home=Team("A"), away=Team("B"), phase=Phase.LIVE, clock_s=300.0)
    assert not fc.usable(None) and not fc.usable(live)                  # no plays read
    live.last_play = Play("p1")
    assert fc.usable(live)
    live.clock_s = None
    assert not fc.usable(live)                                          # no clock read
    live.league = League.MLB
    assert fc.usable(live)                                              # baseball has no clock
    assert fc.usable(GameState(league=League.NFL, game_id="2", home=Team("A"), away=Team("B"), phase=Phase.POST))
    # An NFL document whose plays aren't where we expect (unverified until tonight) is refused.
    nfl = json.loads((FIX / "espn_nfl_summary_401772931.json").read_text())
    nfl["header"]["competitions"][0]["status"]["type"]["state"] = "in"
    nfl["plays"] = [p for dr in nfl.pop("drives")["previous"] for p in dr["plays"]]
    g, _ = fc.to_game_state(League.NFL, nfl)
    assert g.phase == Phase.LIVE and not fc.usable(g)


def test_record_mode_lines_say_what_matters():
    s = json.loads((FIX / "espn_nfl_summary_401772931.json").read_text())
    s["header"]["competitions"][0]["status"]["type"]["state"] = "in"
    doc = _normalize(League.NFL, s)
    line = fc.describe_update(League.NFL, doc, 2000.0, 1999.92)
    assert line.startswith("ESPN->us 0.08s | DEN 27 - WSH 26") and "218 plays" in line
    assert "NOT USABLE" not in line
    assert fc.describe_update(League.NFL, doc, 2000.0, 1890.0, catchup=True).startswith("caught up from checkpoint")
    s.pop("drives")                                                   # a shape we can't read
    assert "NOT USABLE" in fc.describe_update(League.NFL, s, 2000.0, 1999.9)
    assert fc.describe_update(League.NFL, {"header": 5}, 2000.0, None) == "update has no game header yet"


def _wcbb_recording():
    return fc.recording_path(WCBB_TOPIC, FIX)


def test_real_recording_replays_cleanly():
    """A real live recording (women's college basketball, 2026-11-02): checkpoint + every update
    applies in order with no gaps, and the result converts to a sane game."""
    rep = fc.check(_wcbb_recording())
    assert rep["updates"] >= 3 and not rep["resyncs"] and not rep["missing_checkpoints"]
    fin = rep["final"][WCBB_TOPIC]
    assert fin["phase"] == "live" and fin["plays"] > 100 and fin["last_play"]
    assert all(0 <= lag < 5 for lag in rep["lags"])          # ESPN publish -> received, live


def test_real_document_matches_its_own_header():
    t = TopicDoc(WCBB_TOPIC)
    for line in _wcbb_recording().read_text().splitlines():
        msg = fc.parse_message(json.loads(line)["raw"])
        if msg.op == "H":
            t.load(json.loads(fc.checkpoint_path(WCBB_TOPIC, msg.mid, FIX).read_text()), msg.mid)
        elif msg.op in ("P", "R", "I"):
            t.feed(msg)
    g, plays = fc.to_game_state(League.NBA, t.doc, expect_id="401918849")
    comp = t.doc["header"]["competitions"][0]["competitors"]
    by_side = {c["homeAway"]: int(c["score"]) for c in comp}
    assert (g.home.score, g.away.score) == (by_side["home"], by_side["away"])
    assert g.last_play.id == plays[-1]["id"] and g.last_play.wallclock and g.phase == Phase.LIVE
    teams = [p["team"] for p in plays if "team" in p]
    athletes = [x["athlete"] for p in plays for x in p.get("participants") or []]
    assert teams and athletes                                     # refs filled in: real objects, not $key
    assert all("id" in t and "$key" not in t for t in teams) and all("id" in a for a in athletes)


# --- the client, against a fake ESPN -------------------------------------------------------

class FakeWS:
    def __init__(self, server):
        self.server, self.sent, self.inbox, self.closed = server, [], asyncio.Queue(), False

    async def send(self, text):
        msg = json.loads(text)
        self.sent.append(msg)
        for reply in self.server.react(msg):
            self.inbox.put_nowait(reply)

    async def recv(self):
        item = await self.inbox.get()
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self):
        self.closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True


class FakeESPN:
    """Answers like the real service: C -> session, S -> ack + H (+ replay), U -> ack."""

    def __init__(self, topics=None, hbi=30):
        self.topics = topics or {}     # topic -> {"checkpoint": doc, "mid": n, "replay": [ops...]} or None (404)
        self.hbi, self.sockets, self.subscribes, self.connects = hbi, [], [], 0

    def connect(self, url):
        assert url.startswith("wss://ws.example.go.com:9573/FastcastService/pubsub/profiles/12000?TrafficManager-Token=")
        self.connects += 1
        ws = FakeWS(self)
        self.sockets.append(ws)
        return ws

    def react(self, msg):
        if msg["op"] == "C":
            return [json.dumps({"op": "C", "rc": 200, "sid": "sid1", "hbi": self.hbi})]
        if msg["op"] == "S":
            self.subscribes.append(msg["tc"])
            t = self.topics.get(msg["tc"])
            if t is None:
                return [json.dumps({"op": "S", "rc": 404, "tc": msg["tc"], "pl": '{"error": "No such Topic."}'})]
            out = [json.dumps({"op": "S", "rc": 200, "tc": msg["tc"]}),
                   json.dumps({"op": "H", "tc": msg["tc"], "mid": t["mid"], "pl": t["url"]})]
            out += [_p(msg["tc"], t["mid"] + i + 1, ops, op="R") for i, ops in enumerate(t.get("replay", []))]
            return out
        if msg["op"] == "U":
            return [json.dumps({"op": "U", "rc": 200, "tc": msg["tc"]})]
        return []

    def push(self, raw, sock=-1):
        self.sockets[sock].inbox.put_nowait(raw)


def _http(checkpoints: dict, host_status=200, big=False):
    def handler(req: httpx.Request):
        if req.url.path == "/public/websockethost":
            return httpx.Response(host_status, json={"ip": "ws.example.go.com", "securePort": 9573,
                                                     "token": "MTc5==:4LVK/pa+o="})
        if str(req.url) in checkpoints:
            body = json.dumps(checkpoints[str(req.url)]).encode()
            return httpx.Response(200, content=body + (b" " * (fc.MAX_CHECKPOINT_BYTES + 1) if big else b""))
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _until(cond, timeout=3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not cond():
        if loop.time() > end:
            raise AssertionError("timed out")
        await asyncio.sleep(0.005)


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(fc, "TICK_S", 0.01)


CP = "https://fcast.espncdn.com/FastcastService/pubsub/profiles/12000/topic/gp-basketball-nba-1/message/10/checkpoint"


async def test_client_subscribes_loads_checkpoint_and_applies_updates(fast):
    espn_ = FakeESPN({"gp-basketball-nba-1": {"mid": 10, "url": CP, "replay": [[{"op": "add", "path": "/a", "value": 1}]]}})
    seen = []
    c = fc.FastcastClient(lambda tc, doc, rcv, pub: seen.append((tc, copy.deepcopy(doc), pub)),
                          http=_http({CP: {"start": True}}), connect=espn_.connect)
    c.set_topics(["gp-basketball-nba-1"])
    task = asyncio.create_task(c.run())
    await _until(lambda: seen)
    assert seen[-1][1] == {"start": True, "a": 1}                 # checkpoint + the replay, one update
    espn_.push(_p("gp-basketball-nba-1", 12, [{"op": "add", "path": "/b", "value": 2}], ts=1793640954563))
    await _until(lambda: len(seen) == 2)
    assert seen[-1][1] == {"start": True, "a": 1, "b": 2} and seen[-1][2] == pytest.approx(1793640954.563)
    st = c.status()
    assert st["connected"] and st["live_topics"] == ["gp-basketball-nba-1"] and st["errors"] == 0
    c.set_topics([])                                              # game went final: unsubscribe
    await _until(lambda: {"op": "U", "sid": "sid1", "tc": "gp-basketball-nba-1"} in espn_.sockets[0].sent)
    c.stop()
    await asyncio.wait_for(task, 1)


async def test_client_gap_resubscribes_with_backoff(fast):
    espn_ = FakeESPN({"gp-basketball-nba-1": {"mid": 10, "url": CP}})
    seen = []
    clock = {"t": 1000.0}
    c = fc.FastcastClient(lambda *a: seen.append(a), http=_http({CP: {"x": 0}}), connect=espn_.connect,
                          clock=lambda: clock["t"])
    c.set_topics(["gp-basketball-nba-1"])
    task = asyncio.create_task(c.run())
    await _until(lambda: seen)
    espn_.push(_p("gp-basketball-nba-1", 13, [{"op": "replace", "path": "/x", "value": 1}]))   # 11, 12 missing
    await _until(lambda: c.stats["resyncs"] == 1)
    await _until(lambda: any(m["op"] == "U" for m in espn_.sockets[0].sent))
    await asyncio.sleep(0.05)
    assert espn_.subscribes == ["gp-basketball-nba-1"]           # waits; no instant resubscribe loop
    clock["t"] += fc.RESYNC_RETRY_S + 0.1
    await _until(lambda: len(espn_.subscribes) == 2)             # then a fresh checkpoint
    await _until(lambda: len(seen) == 2)
    c.stop()
    await asyncio.wait_for(task, 1)


async def test_replays_dont_reset_the_backoff(fast):
    """A topic that always breaks right after its replay must keep backing off, not retry every 5s."""
    replay = [[{"op": "add", "path": f"/r{i}", "value": i}] for i in range(4)]
    espn_ = FakeESPN({"gp-basketball-nba-1": {"mid": 10, "url": CP, "replay": replay}})
    clock = {"t": 1000.0}
    c = fc.FastcastClient(lambda *a: None, http=_http({CP: {"x": 0}}), connect=espn_.connect,
                          clock=lambda: clock["t"])
    c.set_topics(["gp-basketball-nba-1"])
    task = asyncio.create_task(c.run())
    waits = []
    for n in range(1, 4):
        await _until(lambda: "gp-basketball-nba-1" in c.status()["live_topics"])
        espn_.push(_p("gp-basketball-nba-1", 99, [{"op": "add", "path": "/y", "value": 1}]))   # gap
        await _until(lambda: c.stats["resyncs"] == n)
        waits.append(c._retry["gp-basketball-nba-1"][0] - clock["t"])
        clock["t"] += waits[-1] + 0.1
    assert waits == [fc.RESYNC_RETRY_S, 2 * fc.RESYNC_RETRY_S, 4 * fc.RESYNC_RETRY_S]
    c.stop()
    await asyncio.wait_for(task, 1)


async def test_topic_not_live_yet_is_retried_slowly(fast):
    espn_ = FakeESPN({})                                          # every topic 404s, like a pre-game topic
    clock = {"t": 1000.0}
    c = fc.FastcastClient(lambda *a: None, http=_http({}), connect=espn_.connect, clock=lambda: clock["t"])
    c.set_topics(["gp-football-nfl-401873036"])
    task = asyncio.create_task(c.run())
    await _until(lambda: espn_.subscribes)
    await asyncio.sleep(0.05)
    assert len(espn_.subscribes) == 1 and c.status()["waiting"] == ["gp-football-nfl-401873036"]
    clock["t"] += fc.NOT_LIVE_RETRY_S + 1
    await _until(lambda: len(espn_.subscribes) == 2)
    await asyncio.sleep(0.05)
    assert len(espn_.subscribes) == 2                             # no hammering in between
    clock["t"] += fc.NOT_LIVE_RETRY_S + 1                         # steady: a bare subscribe is cheap,
    await _until(lambda: len(espn_.subscribes) == 3)              # and the game starts any minute
    c.stop()
    await asyncio.wait_for(task, 1)


async def test_reconnect_backoff_doubles_and_never_spins():
    delays = []
    c = None

    def broken(url):
        raise OSError("network is down")

    async def fake_sleep(s):
        delays.append(s)
        if len(delays) >= 9:
            c.stop()
    c = fc.FastcastClient(lambda *a: None, http=_http({}), connect=broken, sleep=fake_sleep, rng=lambda: 1.0)
    c.set_topics(["gp-basketball-nba-1"])
    await asyncio.wait_for(c.run(), 2)
    assert delays == [4, 8, 16, 32, 64, 128, 256, 300, 300]      # doubling, capped at 5 minutes
    assert "network is down" in c.last_error and not c.connected


async def test_websocket_host_failure_backs_off():
    delays = []
    c = None

    async def fake_sleep(s):
        delays.append(s)
        c.stop()
    c = fc.FastcastClient(lambda *a: None, http=_http({}, host_status=503), connect=lambda u: None,
                          sleep=fake_sleep, rng=lambda: 0.0)
    c.set_topics(["gp-basketball-nba-1"])
    await asyncio.wait_for(c.run(), 2)
    assert delays == [2.0] and "503" in c.last_error             # jitter floor: half of 4s


async def test_healthy_connection_resets_backoff(fast, monkeypatch):
    espn_ = FakeESPN({})
    clock = {"t": 0.0}
    delays = []
    c = None

    async def fake_sleep(s):
        delays.append(s)
        c.stop()

    c = fc.FastcastClient(lambda *a: None, http=_http({}), connect=espn_.connect, clock=lambda: clock["t"],
                          sleep=fake_sleep, rng=lambda: 1.0)
    c.failures = 5
    c.set_topics(["gp-basketball-nba-1"])
    task = asyncio.create_task(c.run())
    await _until(lambda: c.connected)
    clock["t"] += fc.HEALTHY_SESSION_S + 1
    espn_.push(ConnectionResetError("dropped"))
    await asyncio.wait_for(task, 2)
    assert c.failures == 0 and delays == [2.0]


async def test_silent_connection_is_dropped(fast, monkeypatch):
    monkeypatch.setattr(fc, "QUIET_LIMIT_S", 0.0)
    espn_ = FakeESPN({}, hbi=0)
    clock = {"t": 0.0}
    delays = []
    c = None

    async def fake_sleep(s):
        delays.append(s)
        c.stop()
    c = fc.FastcastClient(lambda *a: None, http=_http({}), connect=espn_.connect, clock=lambda: clock["t"],
                          sleep=fake_sleep)
    c.set_topics(["gp-basketball-nba-1"])
    task = asyncio.create_task(c.run())
    await _until(lambda: c.connected)
    clock["t"] += 1.0                                             # nothing at all, not even ESPN's heartbeat
    await asyncio.wait_for(task, 2)
    assert "quiet" in c.last_error and delays


async def test_heartbeats_keep_the_connection(fast, monkeypatch):
    monkeypatch.setattr(fc, "QUIET_LIMIT_S", 0.5)
    espn_ = FakeESPN({}, hbi=0)
    clock = {"t": 0.0}
    c = fc.FastcastClient(lambda *a: None, http=_http({}), connect=espn_.connect, clock=lambda: clock["t"])
    c.set_topics(["gp-basketball-nba-1"])
    task = asyncio.create_task(c.run())
    await _until(lambda: c.connected)
    for _ in range(5):
        clock["t"] += 0.4
        espn_.push('{"op":"B"}')
        await asyncio.sleep(0.03)
    assert c.connected and espn_.connects == 1
    c.stop()
    await asyncio.wait_for(task, 1)


@pytest.mark.playbook("SLATE-02")
async def test_connects_only_while_something_is_live(fast):
    espn_ = FakeESPN({})
    clock = {"t": 0.0}
    c = fc.FastcastClient(lambda *a: None, http=_http({}), connect=espn_.connect, clock=lambda: clock["t"])
    task = asyncio.create_task(c.run())
    await asyncio.sleep(0.05)
    assert espn_.connects == 0                                    # nothing live: no connection at all
    c.set_topics(["gp-basketball-nba-1"])
    await _until(lambda: c.connected)
    c.set_topics([])                                              # last game went final
    await asyncio.sleep(0.05)
    assert c.connected                                            # a short gap between games: stay
    for _ in range(2):                                            # a minute passes, ESPN beating as usual
        n = c.stats["messages"]
        clock["t"] += fc.IDLE_HANGUP_S / 2 + 1
        espn_.push('{"op":"B"}')
        await _until(lambda: c.stats["messages"] > n or not c.connected)
    await _until(lambda: not c.connected)                         # then hang up politely
    assert c.failures == 0 and c.last_error == "" and espn_.connects == 1
    c.stop()
    await asyncio.wait_for(task, 1)


async def test_bad_updates_and_callback_errors_dont_kill_the_feed(fast):
    espn_ = FakeESPN({"gp-basketball-nba-1": {"mid": 10, "url": CP}})
    calls = []

    def explode(*a):
        calls.append(a)
        raise RuntimeError("converter bug")
    c = fc.FastcastClient(explode, http=_http({CP: {"x": 0}}), connect=espn_.connect)
    c.set_topics(["gp-basketball-nba-1"])
    task = asyncio.create_task(c.run())
    await _until(lambda: calls)
    espn_.push("garbage that isn't json")
    espn_.push(_p("gp-basketball-nba-1", 11, [{"op": "replace", "path": "/x", "value": 1}]))
    await _until(lambda: len(calls) == 2)
    assert c.connected and c.stats["errors"] >= 3 and espn_.connects == 1
    c.stop()
    await asyncio.wait_for(task, 1)


async def test_checkpoint_size_cap_and_host_allowlist(fast):
    evil = "https://evil.example.com/checkpoint"
    espn_ = FakeESPN({"gp-basketball-nba-1": {"mid": 10, "url": CP}, "gp-basketball-nba-2": {"mid": 3, "url": evil}})
    seen = []
    c = fc.FastcastClient(lambda *a: seen.append(a), http=_http({CP: {"x": 0}, evil: {"x": 0}}, big=True),
                          connect=espn_.connect)
    c.set_topics(["gp-basketball-nba-1", "gp-basketball-nba-2"])
    task = asyncio.create_task(c.run())
    await _until(lambda: c.stats["errors"] >= 1 and "gp-basketball-nba-2" in c._retry)
    assert not seen and c.status()["live_topics"] == []
    assert fc._checkpoint_ok(CP) and not fc._checkpoint_ok("http://fcast.espncdn.com/x") \
        and not fc._checkpoint_ok("https://espncdn.com.evil.net/x")
    c.stop()
    await asyncio.wait_for(task, 1)


def test_websocket_is_opened_with_size_cap_and_ca_bundle(monkeypatch):
    import websockets
    got = {}
    monkeypatch.setattr(websockets, "connect", lambda url, **kw: got.update(kw, url=url))
    fc._default_connect("wss://x.go.com:9573/y")
    assert got["max_size"] == fc.MAX_MESSAGE_BYTES and got["ssl"] is fc._ssl_context()
    assert got["ping_interval"] and got["open_timeout"]


def test_backoff_bounds():
    for n in range(12):
        lo, hi = fc.backoff_s(n, lambda: 0.0), fc.backoff_s(n, lambda: 1.0)
        assert 1.0 <= lo <= hi <= fc.MAX_BACKOFF_S and lo == hi / 2


# --- engine integration ----------------------------------------------------------------------

@pytest.fixture
def engine(tmp_path):
    cfg = Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist")
    e = Engine(cfg, Store(tmp_path / "rz.db"))
    e.channels = ChannelMap(tmp_path / "channels.json")
    e.control.channels = e.channels
    e.recorder = Recorder(tmp_path)
    for g in espn.parse_scoreboard(League.NBA, json.loads((FIX / "espn_nba_scoreboard_2026-11-02.json").read_text())):
        e.merger.update("espn_scoreboard", g)
    return e


class FakeClient:
    made = []

    def __init__(self, on_update, **kw):
        self.on_update, self.kw, self.topics, self.stopped = on_update, kw, set(), False
        FakeClient.made.append(self)

    def set_topics(self, topics):
        self.topics = set(topics)

    async def run(self):
        await asyncio.sleep(3600)

    def stop(self):
        self.stopped = True

    def status(self):
        return {"connected": True}


def _live(engine, n=2):
    games = [g for g in engine.merger.games.values()][:n]
    for g in games:
        g.phase = Phase.LIVE
    return games


async def test_engine_is_inert_when_off(engine, monkeypatch):
    FakeClient.made = []
    monkeypatch.setattr(fc, "FastcastClient", FakeClient)
    _live(engine)
    assert engine.cfg.get("sources.fastcast") is False            # off by default
    engine.cfg.update({"sources": {"fastcast_check": False}})     # and the live check off too
    engine.sync_fastcast()
    assert FakeClient.made == [] and engine.fastcast is None and engine._fastcast_task is None
    assert engine.snapshot()["health"]["fastcast"] is None
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await engine._next_tick(0.1)                                  # nothing wakes it early
    assert loop.time() - t0 >= 0.09
    assert "espn_fastcast" not in engine.merger.health


async def test_pushed_play_wakes_the_decision_loop(engine):
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    loop.call_later(0.05, engine._fresh.set)                      # a big play arrives mid-sleep
    await engine._next_tick(5.0)
    assert loop.time() - t0 < 1.0 and not engine._fresh.is_set()  # decided right away, flag reset


async def test_engine_subscribes_live_games_and_drops_finished(engine, monkeypatch):
    FakeClient.made = []
    monkeypatch.setattr(fc, "FastcastClient", FakeClient)
    a, b = _live(engine)
    engine.cfg.update({"sources": {"fastcast": True}})
    engine.sync_fastcast()
    client = FakeClient.made[0]
    assert client.topics == {f"gp-basketball-nba-{a.game_id}", f"gp-basketball-nba-{b.game_id}"}
    assert client.kw["http"] is engine.fetcher._client           # shares the app's HTTP client
    b.phase = Phase.POST                                          # final: unsubscribe
    engine.sync_fastcast()
    assert client.topics == {f"gp-basketball-nba-{a.game_id}"} and len(FakeClient.made) == 1
    engine.cfg.update({"sources": {"fastcast": False, "fastcast_check": False}})   # off again, no restart
    engine.sync_fastcast()
    assert client.stopped and engine.fastcast is None


async def test_off_runs_the_live_check_in_shadow_and_never_merges(engine, monkeypatch):
    from redzone.sources import fastcast_check as fcc
    FakeClient.made = []
    monkeypatch.setattr(fc, "FastcastClient", FakeClient)
    monkeypatch.setattr(fcc, "MAX_TOPICS", 1)
    a, b = _live(engine)
    assert engine.cfg.get("sources.fastcast_check") is True       # on by default while fastcast is off
    engine.sync_fastcast()
    client = FakeClient.made[0]
    assert engine._fastcast_mode == "shadow" and len(client.topics) == 1     # a few games only
    assert client.on_update == engine._on_fastcast_shadow
    engine.sync_fastcast()
    assert len(FakeClient.made) == 1 and client.topics == set(engine._fastcast_games)   # same game kept
    engine.cfg.update({"sources": {"fastcast": True}})            # turned on: the shadow client is replaced
    engine.sync_fastcast()
    assert client.stopped and engine._fastcast_mode == "merge" and len(FakeClient.made[-1].topics) == 2
    engine.cfg.update({"sources": {"fastcast": False}})
    engine.sync_fastcast()
    assert engine._fastcast_mode == "shadow"
    engine.fc_check.verdict = {"result": "fail"}                  # a verdict ends the shadow
    engine.sync_fastcast()
    assert engine.fastcast is None and engine._fastcast_mode is None


async def test_shadow_updates_are_graded_not_merged(engine, monkeypatch):
    FakeClient.made = []
    monkeypatch.setattr(fc, "FastcastClient", FakeClient)
    summary = json.loads((FIX / "espn_nba_summary_401909906.json").read_text())
    doc = _normalize(League.NBA, summary)
    doc["header"]["competitions"][0]["status"]["type"]["state"] = "in"
    g0 = GameState(league=League.NBA, game_id="401909906", home=Team("LAL"), away=Team("MEM"), phase=Phase.LIVE)
    engine.merger.update("espn_scoreboard", g0)
    engine.sync_fastcast()
    topic = "gp-basketball-nba-401909906"
    assert topic in FakeClient.made[0].topics
    engine._on_fastcast_shadow(topic, doc, 1000.0, 999.9)
    assert "espn_fastcast" not in engine.merger.games["401909906"].sources and not engine._fresh.is_set()
    assert engine.fc_check.updates == 1 and ("espn_fastcast", "401909906") in engine.fc_check.primed
    engine._on_fastcast_shadow(topic, {"header": {"competitions": [{"competitors": []}]}}, 1001.0, None)
    assert engine.fc_check.errors == 1
    assert engine.snapshot()["health"]["fastcast_check"]["result"] == "pending"


@pytest.mark.playbook("FEED-05")
async def test_engine_takes_pushed_updates_like_a_poll(engine, monkeypatch):
    FakeClient.made = []
    monkeypatch.setattr(fc, "FastcastClient", FakeClient)
    summary = json.loads((FIX / "espn_nba_summary_401909906.json").read_text())
    doc = _normalize(League.NBA, summary)
    doc["header"]["competitions"][0]["status"]["type"]["state"] = "in"   # pretend it's still on
    g0 = GameState(league=League.NBA, game_id="401909906", home=Team("LAL"), away=Team("MEM"), phase=Phase.LIVE)
    engine.merger.update("espn_scoreboard", g0)
    engine.cfg.update({"sources": {"fastcast": True}})
    engine.sync_fastcast()
    topic = "gp-basketball-nba-401909906"
    assert topic in FakeClient.made[0].topics
    engine._on_fastcast(topic, doc, 1000.0, 999.9)
    g = engine.merger.games["401909906"]
    assert "espn_fastcast" in g.sources and g.home.score is not None and g.last_play is not None
    assert engine.merger.health["espn_fastcast"].updates == 1
    assert engine._fresh.is_set()                                 # the game moved: decision loop wakes now
    assert engine.decider.timelines["401909906"]._seen_ids        # timeline saw the plays
    engine._fresh.clear()
    engine._on_fastcast(topic, doc, 1010.0, 1009.9)               # nothing new (odds/stat tweak)
    assert not engine._fresh.is_set() and engine.merger.health["espn_fastcast"].updates == 2
    engine._on_fastcast(topic, {"header": {"competitions": [{"competitors": []}]}}, 1011.0, None)
    assert engine.merger.health["espn_fastcast"].errors == 1      # unusable doc: counted, ignored
    engine._on_fastcast(topic, {**doc, "plays": []}, 1012.0, None)
    assert engine.merger.health["espn_fastcast"].errors == 2      # live but no plays read: refused too
    engine._on_fastcast("gp-basketball-nba-unknown", doc, 1013.0, None)   # not ours: ignored
    assert engine.merger.health["espn_fastcast"].updates == 2
    assert engine.merger.games["401909906"].last_play is not None


def test_setting_is_off_by_default_and_validated(cfg):
    from redzone.config import ConfigError
    assert cfg.get("sources.fastcast") is False
    cfg.update({"sources": {"fastcast": True}})
    assert cfg.get("sources.fastcast") is True
    with pytest.raises(ConfigError, match="on or off"):
        cfg.update({"sources": {"fastcast": "yes"}})


def test_recorder_measures_each_source_separately(tmp_path):
    r = Recorder(tmp_path)
    g = GameState(league=League.NBA, game_id="9", home=Team("A"), away=Team("B"))
    old = [{"id": "1", "wallclock": "2026-11-02T17:00:00Z", "type": {"text": "Jump Shot"}}]
    new = old + [{"id": "2", "wallclock": "2026-11-02T17:00:10Z", "type": {"text": "Jump Shot"}}]
    for src in ("espn_summary", "espn_fastcast"):
        r.plays(g, old, src)       # first sighting per source: backlog, no lag measured
        r.plays(g, new, src)
    rows = [json.loads(x) for x in next(tmp_path.glob("recordings/*/plays.jsonl")).read_text().splitlines()]
    assert sorted(x["source"] for x in rows) == ["espn_fastcast", "espn_summary"]
    assert all(x["play"] == "2" for x in rows)
