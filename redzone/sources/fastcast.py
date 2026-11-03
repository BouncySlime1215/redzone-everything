"""ESPN Fastcast: the push feed behind espn.com's live gamecasts.

Polling the summary every few seconds means a new play waits, on average, half a poll
interval plus a download before we see it. Fastcast pushes each change the moment ESPN
publishes it (~0.1s from ESPN's timestamp to us, measured 2026-11-02). Every second of
head start is a second of switch lead time, so this runs as an extra source NEXT TO the
polling: whichever is further ahead wins in the Merger, and if this feed breaks, polling
carries on as if it never existed.

Protocol (undocumented; observed live 2026-11-02, may change without notice):

    GET https://fastcast.semfs.engsvc.go.com/public/websockethost -> {ip, securePort, token}
    wss://{ip}:{securePort}/FastcastService/pubsub/profiles/12000?TrafficManager-Token={token}
    -> {"op":"C"}                         <- {"op":"C","sid":..,"hbi":30,"rc":200}
    -> {"op":"S","sid":..,"tc":topic}     <- {"op":"S","rc":200}  (404 "No such Topic." before a game is live)
                                          <- {"op":"H","mid":N,"pl":<checkpoint URL>}  full document as of mid N
                                          <- {"op":"R","mid":N+1..}  replay since the checkpoint ("lt":true on the last)
                                          <- {"op":"P","mid":..}     live changes, about every 10s per game topic
                                          <- {"op":"I","mid":..}     no-op that still uses up a mid
                                          <- {"op":"B"}              server heartbeat, every 10s
    -> {"op":"U","sid":..,"tc":topic}     <- {"op":"U","rc":200}
    (A client {"op":"H"} is NOT a heartbeat: the server answers {"op":"ERROR","rc":400}.)

P/R payloads: pl is a JSON string {"ts": ms, "~c": 0|1, "pl": ops}; ~c=1 means ops is
base64(zlib(JSON)). ops are JSON Patch (RFC 6902) against the checkpoint. mids count up by
exactly one per topic, so a gap means we missed something: we throw the document away and
resubscribe for a fresh checkpoint instead of guessing.

Game topics are "gp-<sport>-<league>-<event id>" (the summary's meta.gp_topic). The game
document is the site API summary in normalized form: `plays` is a list of {"$key": id}
references into entities.plays, and a play's team/athletes point into entities.teams/
athletes. to_summary() fills the references back in so espn.parse_summary reads it as is.

Verified live 2026-11-02 on women's college basketball (same document as NBA games): every
update applied in order, and at the same meta.lastUpdatedAt the pushed game parsed
identically to the polled summary. Push was 0.01-0.21s behind ESPN's publish time; even
1-second polling saw a new play ~3.7s later (the summary is CDN-cached up to 10s).
NOT verified until those games are live: NFL (are plays $key refs inside drives.previous/
current? is the top-level `situation` with down & distance there?), NHL, MLB, and whether
big-league topics publish more often than every 10s. usable() keeps a document we can't
fully read out of the Merger, so a surprise there costs nothing but the head start.

    data/venv/bin/python -m redzone.sources.fastcast --games
    data/venv/bin/python -m redzone.sources.fastcast --record nba:401909911
    data/venv/bin/python -m redzone.sources.fastcast --check data/recordings/fastcast_<topic>.jsonl

Turn it on with `sources: {fastcast: true}` in data/config.yaml, or from the Mac:
    curl -X POST localhost:8777/api/settings -H 'content-type: application/json' \\
         -d '{"changes": {"sources": {"fastcast": true}}}'
No restart needed. Health then lists espn_fastcast, and data/recordings/<day>/plays.jsonl
logs every play once per source, so each feed's real lag can be compared.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import copy
import functools
import json
import logging
import random
import re
import ssl
import sys
import time
import zlib
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

import httpx

from redzone.models import GameState, League, Phase
from redzone.sources import espn

log = logging.getLogger(__name__)

HOST_URL = "https://fastcast.semfs.engsvc.go.com/public/websockethost"
WS_PATH = "/FastcastService/pubsub/profiles/12000"
RECORDINGS = Path(__file__).resolve().parents[2] / "data" / "recordings"

# Guardrails: caps so a broken or hostile feed can't eat memory, and backoffs so it can't spin.
MAX_MESSAGE_BYTES = 4_000_000      # one websocket message (seen: 0.1-2 KB)
MAX_INFLATED_BYTES = 8_000_000     # one decompressed payload
MAX_CHECKPOINT_BYTES = 16_000_000  # one checkpoint document (seen: 350-750 KB)
MAX_PENDING = 500                  # messages held while a checkpoint downloads
MAX_TOPICS = 40                    # more than every live game at once
MIN_BACKOFF_S = 2.0                # reconnect waits double from here...
MAX_BACKOFF_S = 300.0              # ...up to five minutes
HEALTHY_SESSION_S = 60.0           # a connection that lasted this long resets the backoff
RESYNC_RETRY_S = 2.0               # first wait before resubscribing a topic that went out of sync
NOT_LIVE_RETRY_S = 30.0            # wait before retrying a topic ESPN doesn't have (yet)
TOPIC_MAX_RETRY_S = 300.0
HEALTHY_UPDATES = 3                # a topic that applied this many live messages in order is in sync again
QUIET_LIMIT_S = 45.0               # ESPN beats every 10s; this long with nothing = dead connection
TICK_S = 1.0                       # how often the session loop wakes to (un)subscribe
IDLE_HANGUP_S = 60.0               # nothing live this long: hang up until something is
OPEN_TIMEOUT_S = 10.0
WARN_EVERY_S = 60.0                # the same warning at most once a minute
REF_DEPTH = 2                      # a play, then its team/athletes; their own refs are never read
_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
_SPORT = {League.NFL: "football", League.NBA: "basketball", League.NHL: "hockey", League.MLB: "baseball"}
_TOPIC_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}")   # also a filename in --record
_HOSTNAME_RE = re.compile(r"[A-Za-z0-9.-]{1,253}")
_TOKEN_RE = re.compile(r"[A-Za-z0-9+/=:._-]{1,512}")
_INDEX_RE = re.compile(r"0|[1-9][0-9]*")
_CHECKPOINT_HOSTS = ("espncdn.com", "espn.com", "go.com")


class FastcastError(Exception):
    """Something about the feed was unusable. Always handled; never reaches the engine."""


class PatchError(FastcastError):
    """A JSON Patch operation didn't fit the document (RFC 6902 says the whole patch fails)."""


class Resync(FastcastError):
    """A topic's document can't be trusted any more; start over from a fresh checkpoint."""


class Reload(FastcastError):
    """ESPN sent a checkpoint address instead of a patch, and it's newer than what we have:
    load it in place (no resubscribe needed)."""

    def __init__(self, url: str, mid: int | None):
        super().__init__(f"newer checkpoint {mid}")
        self.url, self.mid = url, mid


# --- topics ----------------------------------------------------------------------

def game_topic(league: League, event_id: str, summary: dict | None = None) -> str:
    """The game's topic: the summary's own meta.gp_topic when we have it, else the known pattern."""
    t = espn._get(summary, "meta", "gp_topic") if isinstance(summary, dict) else None
    if isinstance(t, str) and t.startswith("gp-") and _TOPIC_RE.fullmatch(t):
        return t
    return f"gp-{espn.PATH[league].replace('/', '-')}-{event_id}"


def topic_event(topic: str) -> str | None:
    tail = topic.rsplit("-", 1)[-1]
    return tail if tail.isdigit() else None


def topic_league(topic: str) -> League | None:
    """Which league's parsing rules fit a game topic (any basketball topic reads like the NBA)."""
    parts = topic.split("-")
    if len(parts) < 4 or parts[0] != "gp":
        return None
    path = "-".join(parts[1:-1])
    exact = next((lg for lg, p in espn.PATH.items() if p.replace("/", "-") == path), None)
    return exact or next((lg for lg, sport in _SPORT.items() if sport == parts[1]), None)


def parse_topic_arg(s: str) -> str:
    """'nba:401909911' -> 'gp-basketball-nba-401909911'; a full topic name passes through."""
    lg, sep, eid = s.partition(":")
    if sep and lg.lower() in League._value2member_map_ and eid.isdigit():
        return game_topic(League(lg.lower()), eid)
    if not _TOPIC_RE.fullmatch(s):
        raise ValueError(f"'{s}' isn't a topic name (try nba:<ESPN game id>)")
    return s


# --- JSON Patch (RFC 6902) --------------------------------------------------------

def _tokens(path: str) -> list[str]:
    """RFC 6901 pointer -> reference tokens. '' is the whole document; ~1 is '/', ~0 is '~'."""
    if path == "":
        return []
    if not isinstance(path, str) or not path.startswith("/"):
        raise PatchError(f"bad path {path!r}")
    # Order matters: '~01' must become '~1', not '/'.
    return [t.replace("~1", "/").replace("~0", "~") for t in path[1:].split("/")]


def _find_uid(doc, uid: str) -> list[str] | None:
    """Breadth-first: the shallowest object whose "uid" is `uid` (events beat nested teams)."""
    queue = deque([(doc, [])])
    while queue:
        node, path = queue.popleft()
        if isinstance(node, dict):
            if node.get("uid") == uid:
                return path
            queue.extend((v, path + [k]) for k, v in node.items() if isinstance(v, (dict, list)))
        elif isinstance(node, list):
            queue.extend((v, path + [str(i)]) for i, v in enumerate(node) if isinstance(v, (dict, list)))
    return None


def _uid_at(doc, tokens: list[str]) -> str | None:
    try:
        node = _walk(doc, tokens)
    except PatchError:
        return None
    return node.get("uid") if isinstance(node, dict) else None


def _resolve(doc, path: str, uid_cache: dict | None) -> list[str]:
    """Standard pointers, plus ESPN's scoreboard form 'uid/rest' (event-topevents patches say
    "s:40~l:54~e:401923434~c:401923434/clock": the object whose uid that is, then /clock)."""
    if not isinstance(path, str):
        raise PatchError(f"bad path {path!r}")
    if path == "" or path.startswith("/"):
        return _tokens(path)
    uid, _, rest = path.partition("/")
    cache = uid_cache if uid_cache is not None else {}
    base = cache.get(uid)
    if base is None or _uid_at(doc, base) != uid:   # cached spot moved (an insert shifted it)
        base = _find_uid(doc, uid)
        if base is None:
            raise PatchError(f"nothing has uid {uid}")
        cache[uid] = base
    return base + (_tokens("/" + rest) if rest else [])


def _index(lst: list, token: str, append_ok: bool) -> int:
    if token == "-":
        if append_ok:
            return len(lst)
        raise PatchError("'-' only means 'append' for add")
    if not _INDEX_RE.fullmatch(token):
        raise PatchError(f"bad array index {token!r}")
    i = int(token)
    if i > len(lst) or (i == len(lst) and not append_ok):
        raise PatchError(f"array index {i} is past the end ({len(lst)})")
    return i


def _child(node, token: str):
    if isinstance(node, dict):
        if token not in node:
            raise PatchError(f"no key {token!r}")
        return node[token]
    if isinstance(node, list):
        return node[_index(node, token, append_ok=False)]
    raise PatchError(f"can't go inside a {type(node).__name__} at {token!r}")


def _walk(doc, tokens: list[str]):
    for t in tokens:
        doc = _child(doc, t)
    return doc


def _add(doc, tokens: list[str], value):
    if not tokens:
        return value
    parent = _walk(doc, tokens[:-1])
    last = tokens[-1]
    if isinstance(parent, list):
        parent.insert(_index(parent, last, append_ok=True), value)
    elif isinstance(parent, dict):
        parent[last] = value
    else:
        raise PatchError(f"can't add inside a {type(parent).__name__}")
    return doc


def _remove(doc, tokens: list[str]):
    """Remove and return the value (move needs it)."""
    if not tokens:
        raise PatchError("can't remove the whole document")
    parent = _walk(doc, tokens[:-1])
    last = tokens[-1]
    if isinstance(parent, list):
        return parent.pop(_index(parent, last, append_ok=False))
    if isinstance(parent, dict):
        if last not in parent:
            raise PatchError(f"no key {last!r} to remove")
        return parent.pop(last)
    raise PatchError(f"can't remove from a {type(parent).__name__}")


def _replace(doc, tokens: list[str], value):
    if not tokens:
        return value
    parent = _walk(doc, tokens[:-1])
    last = tokens[-1]
    if isinstance(parent, list):
        parent[_index(parent, last, append_ok=False)] = value
    elif isinstance(parent, dict):
        if last not in parent:
            raise PatchError(f"no key {last!r} to replace")
        parent[last] = value
    else:
        raise PatchError(f"can't replace inside a {type(parent).__name__}")
    return doc


def json_equal(a, b) -> bool:
    """JSON equality for 'test': 1 == 1.0, but true is not 1 (Python says it is)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(json_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(json_equal(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def _apply_one(doc, op: dict, uid_cache: dict | None):
    if not isinstance(op, dict) or "path" not in op:
        raise PatchError(f"not an operation: {str(op)[:80]}")
    kind = op.get("op")
    path = _resolve(doc, op["path"], uid_cache)
    if kind in ("add", "replace", "test") and "value" not in op:
        raise PatchError(f"{kind} without a value")
    if kind == "add":
        return _add(doc, path, op["value"])
    if kind == "remove":
        _remove(doc, path)
        return doc
    if kind == "replace":
        return _replace(doc, path, op["value"])
    if kind in ("move", "copy"):
        if "from" not in op:
            raise PatchError(f"{kind} without 'from'")
        src = _resolve(doc, op["from"], uid_cache)
        if kind == "move":
            if src == path:
                return doc
            if path[:len(src)] == src:
                raise PatchError("can't move something inside itself")
            return _add(doc, path, _remove(doc, src))
        # Deep copy: otherwise both spots share one object and a later patch to one edits both.
        return _add(doc, path, copy.deepcopy(_walk(doc, src)))
    if kind == "test":
        if not json_equal(_walk(doc, path), op["value"]):
            raise PatchError(f"test failed at {op['path']}")
        return doc
    raise PatchError(f"unknown op {kind!r}")


def apply_patch(doc, ops: list, *, in_place: bool = False, uid_cache: dict | None = None):
    """Apply JSON Patch operations in order and return the new document.

    in_place=False (default) leaves `doc` untouched. in_place=True is for the feed, where
    copying a 400 KB document every 10 seconds per game is waste; there, a failure leaves the
    document half-patched, so the caller MUST throw it away (TopicDoc does).
    """
    if not isinstance(ops, list):
        raise PatchError("a patch is a list of operations")
    if not in_place:
        doc = copy.deepcopy(doc)
    for op in ops:
        doc = _apply_one(doc, op, uid_cache)
    return doc


# --- messages --------------------------------------------------------------------

def inflate(b64: str, limit: int = MAX_INFLATED_BYTES) -> bytes:
    """base64 -> zlib/gzip/raw-deflate -> bytes, refusing anything that inflates past `limit`."""
    try:
        raw = base64.b64decode(b64)
    except (binascii.Error, ValueError, TypeError) as e:
        raise FastcastError(f"bad base64 ({e})") from None
    for wbits in (zlib.MAX_WBITS | 32, -zlib.MAX_WBITS):   # zlib or gzip header, then raw deflate
        d = zlib.decompressobj(wbits)
        try:
            out = d.decompress(raw, limit)
        except zlib.error:
            continue
        if d.unconsumed_tail:
            raise FastcastError(f"payload inflates past {limit} bytes")
        return out
    raise FastcastError("payload isn't compressed data")


@dataclass
class Message:
    op: str
    topic: str = ""
    mid: int | None = None
    sid: str = ""
    rc: int | None = None
    hbi: float | None = None
    ts: float | None = None        # when ESPN published it (epoch s)
    ops: list | None = None        # JSON Patch operations (P/R)
    doc: Any = None                # a whole document (inline checkpoint, or a P that replaces everything)
    url: str = ""                  # checkpoint address (H, or a P/R that points at one)
    ref_mid: int | None = None     # a P/R pointer: the mid that checkpoint is as of
    last: bool = False             # last message of a replay
    error: str = ""


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _json(v, what: str):
    if not isinstance(v, str):
        return v
    try:
        return json.loads(v)
    except ValueError:
        raise FastcastError(f"{what} isn't JSON") from None


def _payload(msg: Message, pl) -> None:
    outer = _json(pl, "update")
    if not isinstance(outer, dict):
        raise FastcastError("update has no payload")
    if isinstance(outer.get("edgeUrl"), str):
        # A pointer instead of a patch (seen at halftime, 2026-11-02): {"useCDN":true,
        # "eventId":"614","edgeUrl":".../message/614/checkpoint"} = "the document as of 614 is there".
        msg.url, msg.ref_mid = outer["edgeUrl"], _int(outer.get("eventId"))
        return
    ts = outer.get("ts")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        msg.ts = ts / 1000.0 if ts > 1e11 else float(ts)
    inner = outer.get("pl")
    if outer.get("~c") and isinstance(inner, str):
        inner = _json(inflate(inner).decode("utf-8", "replace"), "compressed update")
    else:
        inner = _json(inner, "update")
    if isinstance(inner, list):
        msg.ops = inner
    elif isinstance(inner, dict):
        msg.doc = inner   # never seen; treated as a full replacement document
    else:
        raise FastcastError("update has no patch")


def parse_message(raw: str | bytes) -> Message:
    """One websocket frame -> Message. Raises FastcastError for anything malformed."""
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", "replace")
    m = _json(raw, "message")
    if not isinstance(m, dict) or not isinstance(m.get("op"), str):
        raise FastcastError("message has no op")
    msg = Message(op=m["op"], topic=str(m.get("tc") or ""), mid=_int(m.get("mid")), sid=str(m.get("sid") or ""),
                  rc=_int(m.get("rc")), last=bool(m.get("lt")))
    pl = m.get("pl")
    if msg.op == "C":
        hbi = m.get("hbi")
        msg.hbi = float(hbi) if isinstance(hbi, (int, float)) and not isinstance(hbi, bool) else None
    elif msg.op == "H":
        if isinstance(pl, str) and pl.startswith("http"):
            msg.url = pl
        else:
            msg.doc = _json(pl, "checkpoint")
    elif msg.op in ("P", "R"):
        try:
            _payload(msg, pl)
        except FastcastError as e:
            # Keep the envelope: this mid is spoken for, so the topic must resync right away
            # (TopicDoc.feed) instead of finding the hole a whole publish cycle later.
            msg.error, msg.ops, msg.doc = str(e), None, None
    elif msg.rc is not None and msg.rc != 200:
        err = pl
        try:
            err = json.loads(pl).get("error", pl) if isinstance(pl, str) else pl
        except (ValueError, AttributeError):
            pass
        msg.error = str(err)[:200]
    return msg


# --- one topic's document ------------------------------------------------------------

class TopicDoc:
    """A checkpoint plus every change since, applied strictly in mid order.

    Shared by the live client and --check, so a recording replays exactly like the real thing.
    """

    def __init__(self, name: str):
        self.name = name
        self.doc = None
        self.mid: int | None = None
        self.published_at: float | None = None
        self.pending: list[Message] = []
        self.updates = 0
        self._uids: dict = {}

    @property
    def ready(self) -> bool:
        return self.doc is not None

    def reset(self) -> None:
        self.doc, self.mid, self.pending, self._uids = None, None, [], {}

    def load(self, doc, mid: int | None) -> None:
        """Install a checkpoint, then everything that arrived while it downloaded."""
        if not isinstance(doc, (dict, list)):
            raise Resync("checkpoint isn't a JSON document")
        self.doc, self.mid, self._uids = doc, mid, {}
        pending, self.pending = sorted(self.pending, key=lambda m: -1 if m.mid is None else m.mid), []
        for i, m in enumerate(pending):
            try:
                self.feed(m)
            except Reload:
                self.pending = pending[i + 1:]   # they apply on top of the newer checkpoint
                raise

    def feed(self, msg: Message) -> bool:
        """Apply one P/R/I message. True if the document changed. Raises Resync to start over."""
        if self.doc is None:
            if len(self.pending) >= MAX_PENDING:
                raise Resync("too many updates arrived while the checkpoint downloaded")
            self.pending.append(msg)
            return False
        if msg.mid is not None and self.mid is not None:
            if msg.mid <= self.mid:
                return False                    # already part of the checkpoint
            if msg.mid != self.mid + 1:
                raise Resync(f"missed update(s) {self.mid + 1}-{msg.mid - 1}")
        if msg.error:
            raise Resync(f"an update couldn't be read ({msg.error})")
        if msg.url:   # a pointer to a checkpoint instead of a patch
            if msg.ref_mid is not None and self.mid is not None and msg.ref_mid <= self.mid:
                self.mid = msg.mid if msg.mid is not None else self.mid
                return False   # we already have that state (seen: it points at the mid just before it)
            self.doc, self._uids = None, {}   # hold what follows until that checkpoint is in
            raise Reload(msg.url, msg.ref_mid if msg.ref_mid is not None else msg.mid)
        if msg.mid is not None:
            self.mid = msg.mid
        if msg.doc is not None:
            self.doc, self._uids = msg.doc, {}
        elif msg.ops:
            try:
                self.doc = apply_patch(self.doc, msg.ops, in_place=True, uid_cache=self._uids)
            except Exception as e:  # half-applied: unusable, never pass it on
                self.doc = None
                raise Resync(f"an update didn't fit ({e})") from None
        else:
            return False                        # 'I' or an empty patch
        if msg.ts:
            self.published_at = msg.ts
        self.updates += 1
        return True


# --- game document -> GameState ------------------------------------------------------

def _entity(ents: dict, field_name: str, key) -> Any:
    """Which entity a {"$key"} means depends on the field it sits in: plays/play -> plays,
    team -> teams, athlete -> athletes. Unknown fields resolve only if exactly one collection
    has that id (team ids also appear under competitors, so guessing would be wrong)."""
    key = str(key)
    for name in (field_name, field_name + "s"):
        coll = ents.get(name)
        if isinstance(coll, dict) and key in coll:
            return coll[key]
    hits = [c[key] for c in ents.values() if isinstance(c, dict) and key in c]
    return hits[0] if len(hits) == 1 else None


def _fill(o, field_name: str, ents: dict, depth: int):
    """Copy-on-write: only containers on the way to a reference are rebuilt; everything else
    is shared with the live document. A copying version took 23 ms per update on a real
    454 KB game; this takes a few. The last level of references is used as is, unwalked."""
    if isinstance(o, dict):
        if len(o) == 1 and "$key" in o:
            hit = _entity(ents, field_name, o["$key"])
            if hit is None:
                return o
            return _fill(hit, field_name, ents, depth - 1) if depth > 1 else hit
        out = None
        for k, v in o.items():
            if isinstance(v, (dict, list)):
                nv = _fill(v, k, ents, depth)
                if nv is not v:
                    if out is None:
                        out = dict(o)
                    out[k] = nv
        return o if out is None else out
    if isinstance(o, list):
        out = None
        for i, v in enumerate(o):
            if isinstance(v, (dict, list)):
                nv = _fill(v, field_name, ents, depth)
                if nv is not v:
                    if out is None:
                        out = list(o)
                    out[i] = nv
        return o if out is None else out
    return o


def to_summary(doc) -> dict:
    """A game-topic document with its {"$key": id} references filled in from doc["entities"]:
    the shape of the site API summary, ready for espn.parse_summary.

    Never edits `doc`, but shares untouched parts with it, and the live feed keeps patching
    `doc` in place: read the result right away, don't keep it. Play order is kept as sent:
    like the summary it is game-time order, with a late-logged play where it happened
    (sorting by sequenceNumber would break NBA and MLB)."""
    if not isinstance(doc, dict):
        return {}
    ents = doc.get("entities") if isinstance(doc.get("entities"), dict) else {}
    return {k: _fill(v, k, ents, REF_DEPTH) for k, v in doc.items() if k != "entities"}


def to_game_state(league: League, doc, expect_id: str | None = None) -> tuple[GameState | None, list[dict]]:
    """GameState + plays (for the flight recorder) from a game-topic document.

    `expect_id` guards against a mixed-up topic: a document for some other game gives None.
    """
    data = to_summary(doc)
    plays = espn.flatten_plays(league, data)
    g = espn.parse_summary(league, data, plays=plays)
    if g is None or (expect_id is not None and g.game_id != str(expect_id)):
        return None, plays
    return g, plays


def usable(g: GameState | None) -> bool:
    """Only a state as complete as a polled summary may enter the Merger. A live game must
    come with its plays and (outside baseball) its clock. Why: a document we can't fully read
    (say NFL's turns out shaped differently tonight) has no clock, and no clock sorts as 'end
    of the period', so it would look further ahead than every real source and win the merge."""
    if g is None:
        return False
    if g.phase != Phase.LIVE:
        return True
    return g.last_play is not None and (g.clock_s is not None or g.league == League.MLB)


# --- the client ------------------------------------------------------------------------

UpdateFn = Callable[[str, Any, float, "float | None"], None]


@functools.lru_cache(maxsize=1)
def _ssl_context() -> ssl.SSLContext:
    """python.org's macOS Python ships without a CA bundle, so the default context rejects
    ESPN's certificate (seen 2026-11-02). httpx uses certifi; do the same."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _default_connect(url: str):
    import websockets
    return websockets.connect(url, ssl=_ssl_context(), max_size=MAX_MESSAGE_BYTES, max_queue=64,
                              open_timeout=OPEN_TIMEOUT_S, ping_interval=20, ping_timeout=20,
                              close_timeout=3, user_agent_header=_BROWSER_UA)


def backoff_s(failures: int, rng: Callable[[], float] = random.random) -> float:
    """Wait before reconnect attempt `failures` (0 = right after a healthy connection).
    Doubles from 2s to 5 min, with jitter so a restart doesn't hit ESPN in lockstep."""
    base = min(MAX_BACKOFF_S, MIN_BACKOFF_S * (2 ** max(0, failures)))
    return base * (0.5 + 0.5 * rng())


def _checkpoint_ok(url: str) -> bool:
    """Only fetch checkpoints from ESPN's own hosts, over https."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host == h or host.endswith("." + h) for h in _CHECKPOINT_HOSTS)


@dataclass
class _Sub:
    name: str
    doc: TopicDoc
    state: str = "subscribing"          # subscribing -> subscribed -> loading -> live
    loader: asyncio.Task | None = None
    since: float = 0.0
    in_order: int = 0                   # live messages applied in order since subscribing


class FastcastClient:
    """One websocket to ESPN carrying any number of topics.

    on_update(topic, doc, received_at, published_at) gets a topic's whole document after
    every change. The document is live: read it, don't keep or edit it. Never raises into
    the caller: errors are logged, counted in status(), and lead to a backed-off reconnect
    or resubscribe, never a tight loop.
    """

    def __init__(self, on_update: UpdateFn, *, on_raw: Callable[[str, float], None] | None = None,
                 on_checkpoint: Callable[[str, int | None, str, Any], None] | None = None,
                 http: httpx.AsyncClient | None = None, connect: Callable | None = None,
                 clock: Callable[[], float] = time.time, sleep: Callable | None = None,
                 rng: Callable[[], float] = random.random):
        self._on_update = on_update
        self._on_raw = on_raw
        self._on_checkpoint = on_checkpoint
        self._http = http
        self._own_http = http is None
        self._connect = connect or _default_connect
        self._clock = clock
        self._sleep = sleep
        self._rng = rng
        self.wanted: set[str] = set()
        self.topics: dict[str, _Sub] = {}
        self.connected = False
        self.failures = 0
        self.last_message_at = 0.0
        self.last_error = ""
        self.stats = {"connects": 0, "messages": 0, "updates": 0, "errors": 0, "resyncs": 0, "reloads": 0}
        self._retry: dict[str, tuple[float, int]] = {}   # topic -> (retry at, failures)
        self._outbox: list[dict] = []
        self._sid = ""
        self._ws = None
        self._quiet_limit = QUIET_LIMIT_S
        self._stopping = False
        self._stop_event: asyncio.Event | None = None
        self._warned: dict[str, float] = {}

    # ---- public

    def set_topics(self, topics: Iterable[str]) -> None:
        """What we want subscribed. The session loop catches up within a second."""
        self.wanted = {t for t in topics if isinstance(t, str) and _TOPIC_RE.fullmatch(t)}

    def doc(self, topic: str):
        sub = self.topics.get(topic)
        return sub.doc.doc if sub else None

    def status(self) -> dict:
        now = self._clock()
        live = sorted(n for n, s in self.topics.items() if s.state == "live")
        return {"connected": self.connected, "live_topics": live,
                "waiting": sorted(self.wanted - set(live)),
                "last_message_s": round(now - self.last_message_at, 1) if self.last_message_at else None,
                "reconnects": max(0, self.stats["connects"] - 1), "updates": self.stats["updates"],
                "errors": self.stats["errors"], "resyncs": self.stats["resyncs"], "reloads": self.stats["reloads"],
                "last_error": self.last_error}

    async def run(self) -> None:
        """Connect while there is something to watch, reconnect with backoff. Returns after stop()."""
        self._stop_event = asyncio.Event()
        while not self._stopping:
            if not self.wanted:      # no live games: no connection (and no reconnect warnings)
                await asyncio.sleep(TICK_S)
                continue
            started = self._clock()
            try:
                if await self._session():
                    continue         # hung up because nothing is wanted: not a failure
                reason = "ESPN closed the connection"
            except asyncio.CancelledError:
                raise
            except Exception as e:   # every failure mode ends here: backoff, then try again
                reason = _describe(e)
            if self._stopping:
                break
            self.failures = 0 if self._clock() - started >= HEALTHY_SESSION_S else self.failures + 1
            self.last_error = reason
            delay = backoff_s(self.failures, self._rng)
            self._warn("disconnect", "Fastcast: %s; reconnecting in %.0fs (polling carries on)", reason, delay)
            await self._nap(delay)

    def stop(self) -> None:
        self._stopping = True
        if self._stop_event is not None:
            self._stop_event.set()
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                asyncio.get_running_loop().create_task(_quiet_close(ws))
            except RuntimeError:
                pass

    async def aclose(self) -> None:
        self.stop()
        if self._own_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    # ---- connection

    async def _nap(self, seconds: float) -> None:
        if self._sleep is not None:
            await self._sleep(seconds)
            return
        try:
            await asyncio.wait_for(self._stop_event.wait(), seconds)
        except asyncio.TimeoutError:
            pass

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=True,
                                           headers={"User-Agent": _BROWSER_UA, "Accept": "application/json"})
        return self._http

    async def _ws_url(self) -> str:
        r = await self._client().get(HOST_URL)
        if r.status_code != 200:
            raise FastcastError(f"websocket host lookup said HTTP {r.status_code}")
        h = _json(r.text, "websocket host")
        host, port, token = (h.get("ip"), h.get("securePort"), h.get("token")) if isinstance(h, dict) else (None,) * 3
        if not (isinstance(host, str) and _HOSTNAME_RE.fullmatch(host) and isinstance(port, int)
                and not isinstance(port, bool) and 0 < port < 65536 and isinstance(token, str)
                and _TOKEN_RE.fullmatch(token)):
            raise FastcastError("websocket host lookup returned something unexpected")
        # Token goes in raw, exactly like ESPN's own web client sends it.
        return f"wss://{host}:{port}{WS_PATH}?TrafficManager-Token={token}"

    async def _session(self) -> bool:
        """One connection, until it fails (raises) or nothing has been wanted for a minute (True)."""
        url = await self._ws_url()
        async with self._connect(url) as ws:
            self._ws = ws
            try:
                await ws.send(json.dumps({"op": "C"}))
                hello = parse_message(await asyncio.wait_for(ws.recv(), OPEN_TIMEOUT_S))
                if hello.op != "C" or not hello.sid:
                    raise FastcastError("ESPN didn't start a session")
                self._sid = hello.sid
                self._quiet_limit = max(QUIET_LIMIT_S, 1.5 * (hello.hbi or 0))
                self.connected = True
                self.stats["connects"] += 1
                self.last_message_at = idle_since = self._clock()
                log.info("Fastcast connected")
                while not self._stopping:
                    if self.wanted:
                        idle_since = self._clock()
                    elif self._clock() - idle_since > IDLE_HANGUP_S:
                        return True
                    self._reconcile()
                    await self._flush(ws)
                    try:
                        raw = await asyncio.wait_for(ws.recv(), TICK_S)
                    except asyncio.TimeoutError:
                        if self._clock() - self.last_message_at > self._quiet_limit:
                            raise FastcastError("ESPN went quiet (no heartbeat)") from None
                        continue
                    self._handle(raw)
                    await self._flush(ws)
                return False
            finally:
                self.connected = False
                self._ws = None
                for sub in self.topics.values():
                    if sub.loader:
                        sub.loader.cancel()
                self.topics.clear()   # a new connection resubscribes everything from scratch
                self._outbox.clear()

    async def _flush(self, ws) -> None:
        while self._outbox:
            await ws.send(json.dumps(self._outbox.pop(0)))

    def _reconcile(self) -> None:
        """Subscribe to wanted topics, unsubscribe from the rest (sends go out on the next flush)."""
        now = self._clock()
        for name in [n for n in self.topics if n not in self.wanted]:
            self._drop(name)
            self._retry.pop(name, None)
        for name in sorted(self.wanted - set(self.topics)):
            if len(self.topics) >= MAX_TOPICS:
                self._warn("max_topics", "Fastcast: more than %d topics wanted; skipping the rest", MAX_TOPICS)
                break
            if self._retry.get(name, (0.0, 0))[0] > now:
                continue
            self.topics[name] = _Sub(name, TopicDoc(name), since=now)
            self._outbox.append({"op": "S", "sid": self._sid, "tc": name})

    def _drop(self, name: str) -> None:
        sub = self.topics.pop(name, None)
        if sub is None:
            return
        if sub.loader:
            sub.loader.cancel()
        if sub.state != "subscribing" and self.connected:
            self._outbox.append({"op": "U", "sid": self._sid, "tc": name})

    def _later(self, name: str, reason: str, first_wait: float, double: bool = True) -> None:
        """Drop a topic now and resubscribe after a wait (no tight loops). The wait doubles
        when retrying costs ESPN a checkpoint download; a bare subscribe is one tiny message."""
        n = self._retry.get(name, (0.0, 0))[1] + 1
        wait = min(TOPIC_MAX_RETRY_S, first_wait * 2 ** (n - 1)) if double else first_wait
        self._retry[name] = (self._clock() + wait, n)
        self._drop(name)
        self._warn(f"later:{name}", "Fastcast %s: %s; trying again in %.0fs", name, reason, wait)

    # ---- messages

    def _handle(self, raw) -> None:
        now = self._clock()
        self.last_message_at = now
        self.stats["messages"] += 1
        if self._on_raw is not None:
            try:
                self._on_raw(raw if isinstance(raw, str) else bytes(raw).decode("utf-8", "replace"), now)
            except Exception as e:
                self._warn("on_raw", "Fastcast recorder failed: %s", e)
        try:
            msg = parse_message(raw)
        except FastcastError as e:
            self.stats["errors"] += 1
            self._warn("parse", "Fastcast: skipped a message (%s)", e)
            return
        sub = self.topics.get(msg.topic) if msg.topic else None
        if msg.op == "S" and sub is not None:
            if msg.rc == 200:
                sub.state = "subscribed"
            else:
                # Usually "No such Topic.": ESPN hasn't opened this game yet. Retry steadily, so
                # we're on it within half a minute of it existing.
                self._later(msg.topic, msg.error or f"subscribe answered {msg.rc}", NOT_LIVE_RETRY_S,
                            double=False)
        elif msg.op == "H" and sub is not None:
            self._start_load(sub, msg)
        elif msg.op in ("P", "R", "I") and sub is not None:
            try:
                changed = sub.doc.feed(msg)
            except Reload as r:
                self._reload(sub, r)
                return
            except Resync as e:
                self.stats["resyncs"] += 1
                self._later(sub.name, str(e), RESYNC_RETRY_S)
                return
            if msg.op != "R" and sub.doc.ready:
                # Live messages applied in order (replays don't count: a feed that always breaks
                # right after the replay would otherwise reset its own backoff every time).
                sub.in_order += 1
                if sub.in_order == HEALTHY_UPDATES:
                    self._retry.pop(sub.name, None)   # in sync: the next problem starts the backoff over
            if changed:
                self._emit(sub, now)
        elif msg.op == "ERROR":
            self.stats["errors"] += 1
            self._warn("server_error", "Fastcast: ESPN said %s", msg.error or msg.rc)
        # B (heartbeat), C, U and anything new: nothing to do beyond noting the connection is alive.

    def _start_load(self, sub: _Sub, msg: Message) -> None:
        """H: a fresh checkpoint for a new subscription (its replay follows it)."""
        sub.doc.reset()
        if msg.doc is not None:   # inline checkpoint (never seen; cheap to support)
            self._install(sub, msg.doc, msg.mid, "")
            return
        self._fetch_checkpoint(sub, msg.url, msg.mid)

    def _reload(self, sub: _Sub, r: Reload) -> None:
        """A newer checkpoint mid-stream: load it in place; updates that arrive meanwhile wait."""
        self.stats["reloads"] += 1
        self._fetch_checkpoint(sub, r.url, r.mid)

    def _fetch_checkpoint(self, sub: _Sub, url: str, mid: int | None) -> None:
        if sub.loader:
            sub.loader.cancel()
        sub.state = "loading"
        if not _checkpoint_ok(url):
            self._later(sub.name, f"checkpoint isn't on an ESPN host ({url[:60]})", TOPIC_MAX_RETRY_S)
            return
        sub.loader = asyncio.get_running_loop().create_task(self._load(sub, url, mid))

    async def _load(self, sub: _Sub, url: str, mid: int | None) -> None:
        try:
            doc = await self._fetch_json(url)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if self.topics.get(sub.name) is sub:
                self.stats["errors"] += 1
                self._later(sub.name, f"couldn't download the checkpoint ({_describe(e)})", RESYNC_RETRY_S)
            return
        if self.topics.get(sub.name) is sub:
            sub.loader = None
            self._install(sub, doc, mid, url)

    def _install(self, sub: _Sub, doc, mid: int | None, url: str) -> None:
        if self._on_checkpoint is not None:
            try:
                self._on_checkpoint(sub.name, mid, url, doc)
            except Exception as e:
                self._warn("on_checkpoint", "Fastcast checkpoint recorder failed: %s", e)
        try:
            sub.doc.load(doc, mid)
        except Reload as r:   # the replay pointed at something newer still
            self._reload(sub, r)
            return
        except Resync as e:
            self.stats["resyncs"] += 1
            self._later(sub.name, str(e), RESYNC_RETRY_S)
            return
        sub.state = "live"
        self._emit(sub, self._clock())

    async def _fetch_json(self, url: str):
        """Checkpoint download, size-capped while streaming (the cap counts unzipped bytes)."""
        chunks, size = [], 0
        async with self._client().stream("GET", url) as r:
            if r.status_code != 200:
                raise FastcastError(f"HTTP {r.status_code}")
            async for chunk in r.aiter_bytes():
                size += len(chunk)
                if size > MAX_CHECKPOINT_BYTES:
                    raise FastcastError(f"checkpoint bigger than {MAX_CHECKPOINT_BYTES} bytes")
                chunks.append(chunk)
        return _json(b"".join(chunks).decode("utf-8", "replace"), "checkpoint")

    def _emit(self, sub: _Sub, received_at: float) -> None:
        if sub.doc.doc is None:
            return
        self.stats["updates"] += 1
        try:
            self._on_update(sub.name, sub.doc.doc, received_at, sub.doc.published_at)
        except Exception as e:
            self.stats["errors"] += 1
            self._warn(f"update:{sub.name}", "Fastcast update for %s couldn't be used: %s", sub.name, e,
                       exc=True)

    def _warn(self, key: str, fmt: str, *args, exc: bool = False) -> None:
        """Log a warning at most once a minute per kind, so a broken feed can't flood the log."""
        now = self._clock()
        if now - self._warned.get(key, -1e18) >= WARN_EVERY_S:
            self._warned[key] = now
            log.warning(fmt, *args, exc_info=exc)
        else:
            log.debug(fmt, *args)


async def _quiet_close(ws) -> None:
    try:
        await ws.close()
    except Exception:
        pass


def _describe(e: BaseException) -> str:
    text = str(e).strip()
    return f"{type(e).__name__}: {text[:160]}" if text else type(e).__name__


# --- command line: record, check, list games ----------------------------------------------

def recording_path(topic: str, root: Path = RECORDINGS) -> Path:
    return root / f"fastcast_{topic}.jsonl"


def checkpoint_path(topic: str, mid: int | None, root: Path = RECORDINGS) -> Path:
    return root / f"fastcast_{topic}.checkpoint_{mid}.json"


def _clock_text(g: GameState) -> str:
    if g.clock_s is None:
        return f"P{g.period or '?'}"
    m, s = divmod(int(g.clock_s), 60)
    return f"P{g.period or '?'} {m}:{s:02d}"


def describe_update(league: League, doc, received_at: float, published_at: float | None,
                    catchup: bool = False) -> str:
    """One plain-English line per update: score, clock, last play, and how late it reached us.

    "ESPN->us" is publish-to-receive (the push itself). "after it happened" is play wallclock
    to receive: the whole chain, mostly ESPN's own data entry (~20s on 2026-11-02 WCBB games).
    """
    try:
        g, plays = to_game_state(league, doc)
    except Exception as e:  # the converter is what we're verifying; say so plainly
        return f"couldn't read the game from this update ({_describe(e)})"
    if g is None:
        return "update has no game header yet"
    if catchup:
        push = "caught up from checkpoint"   # a replay of the last minute or two, not a live lag
    else:
        push = f"ESPN->us {received_at - published_at:.2f}s" if published_at else "ESPN->us ?"
    lp = g.last_play
    age = f"{received_at - lp.wallclock:.1f}s after it happened" if lp and lp.wallclock else "no wallclock"
    text = (lp.text if lp else "")[:70]
    from redzone.views import situation   # NFL down & distance, MLB outs: unverified fields, so show them
    sit = situation(g)["text"]
    warn = "" if usable(g) else " | NOT USABLE (no plays or clock read): the app would ignore this"
    return (f"{push} | {g.away.abbr} {g.away.score} - {g.home.abbr} {g.home.score} | {_clock_text(g)} | "
            f"{g.phase.value} {g.stoppage.value}{f' | {sit}' if sit else ''} | {len(plays)} plays | "
            f"last play {age}: {text}{warn}")


async def record(topic: str, minutes: float = 0.0, root: Path = RECORDINGS) -> int:
    """Save every raw message for one topic (plus each checkpoint) and print a line per update."""
    root.mkdir(parents=True, exist_ok=True)
    out = recording_path(topic, root)
    league = topic_league(topic)
    _say(f"Recording {topic}\n  -> {out}\n  (Ctrl-C to stop{f'; stops after {minutes:g} min' if minutes else ''})")
    f = open(out, "a", buffering=1)
    fresh = set()   # topics whose next update is the catch-up right after a checkpoint

    def on_raw(text: str, t: float) -> None:
        f.write(json.dumps({"t": round(t, 3), "raw": text}, separators=(",", ":")) + "\n")

    def on_checkpoint(tc: str, mid: int | None, url: str, doc) -> None:
        p = checkpoint_path(tc, mid, root)
        p.write_text(json.dumps(doc, separators=(",", ":")))
        fresh.add(tc)
        _say(f"{_now()}  checkpoint {mid} saved ({p.stat().st_size // 1024} KB) -> {p.name}")

    def on_update(tc: str, doc, received_at: float, published_at: float | None) -> None:
        catchup = tc in fresh
        fresh.discard(tc)
        line = (describe_update(league, doc, received_at, published_at, catchup) if league
                else "update (not a game topic)")
        _say(f"{_now()}  {line}")

    client = FastcastClient(on_update, on_raw=on_raw, on_checkpoint=on_checkpoint)
    client.set_topics([topic])
    task = asyncio.create_task(client.run())
    try:
        end = time.time() + minutes * 60 if minutes else None
        last_state = None
        while end is None or time.time() < end:
            await asyncio.sleep(1)
            st = (client.connected, topic in client.status()["live_topics"])
            if st != last_state:
                last_state = st
                _say(f"{_now()}  {'connected' if st[0] else 'not connected'}"
                     f"{', subscribed' if st[1] else ', waiting for the topic'}"
                     f"{f' (last problem: {client.last_error})' if client.last_error else ''}")
    finally:   # Ctrl-C lands here too: the file is line-buffered, so nothing recorded is lost
        task.cancel()
        f.close()
        _say(f"Saved {client.stats['messages']} messages, {client.stats['updates']} updates, "
             f"{client.stats['resyncs']} resyncs -> {out}\n"
             f"Check it with: data/venv/bin/python -m redzone.sources.fastcast --check {out}")
        await client.aclose()
    return 0


def check(path: Path) -> dict:
    """Replay a recording offline with the same code the app uses. Every update must apply."""
    topics: dict[str, TopicDoc] = {}
    report = {"messages": 0, "updates": 0, "resyncs": [], "missing_checkpoints": [], "lags": [], "final": {}}
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
            msg = parse_message(row["raw"])
        except (ValueError, KeyError, FastcastError):
            continue
        report["messages"] += 1
        t = topics.setdefault(msg.topic, TopicDoc(msg.topic)) if msg.topic else None
        if t is None:
            continue
        try:
            if msg.op == "H":
                t.reset()
                cp = checkpoint_path(msg.topic, msg.mid, path.parent)
                if msg.doc is not None:
                    t.load(msg.doc, msg.mid)
                elif cp.exists():
                    t.load(json.loads(cp.read_text()), msg.mid)
                else:
                    report["missing_checkpoints"].append(cp.name)
            elif msg.op in ("P", "R", "I") and t.feed(msg):
                report["updates"] += 1
                if msg.op == "P" and msg.ts:
                    report["lags"].append(round(row["t"] - msg.ts, 3))
        except Resync as e:
            report["resyncs"].append(f"{msg.topic} mid {msg.mid}: {e}")
            t.reset()
    for name, t in topics.items():
        lg = topic_league(name)
        if t.ready and lg:
            g, plays = to_game_state(lg, t.doc)
            if g:
                report["final"][name] = {"score": f"{g.away.abbr} {g.away.score} - {g.home.abbr} {g.home.score}",
                                         "clock": _clock_text(g), "phase": g.phase.value, "plays": len(plays),
                                         "last_play": g.last_play.text[:80] if g.last_play else ""}
    return report


async def list_games() -> int:
    """Upcoming and live games with their topic names (a topic exists only once a game is live)."""
    async with httpx.AsyncClient(timeout=10, headers={"User-Agent": _BROWSER_UA}) as c:
        for lg in League:
            try:
                r = await c.get(espn.scoreboard_url(lg))
                games = espn.parse_scoreboard(lg, r.json()) if r.status_code == 200 else []
            except (httpx.HTTPError, ValueError):
                games = []
            for g in games:
                if g.phase == Phase.POST:
                    continue
                when = datetime.fromtimestamp(g.start_time).strftime("%a %-I:%M %p") if g.start_time else ""
                live = "LIVE " if g.phase == Phase.LIVE else g.phase.value.ljust(5)
                _say(f"{lg.value}:{g.game_id}  {live} {g.matchup:<12} {when:>12}   {game_topic(lg, g.game_id)}")
    return 0


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _say(*args) -> None:
    """print, flushed: when the output is piped to a file or `tee`, lines show up as they happen."""
    print(*args, flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m redzone.sources.fastcast",
                                 description="ESPN Fastcast push feed: record and check it against live games.")
    ap.add_argument("--games", action="store_true", help="list today's games and their topic names")
    ap.add_argument("--record", metavar="TOPIC", help="record a topic, e.g. nba:401909911 or gp-football-nfl-401873036")
    ap.add_argument("--minutes", type=float, default=0.0, help="stop recording after this many minutes")
    ap.add_argument("--check", metavar="FILE", help="replay a recording offline and report whether every update applied")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if a.games:
        return asyncio.run(list_games())
    if a.record:
        try:
            topic = parse_topic_arg(a.record)
        except ValueError as e:
            _say(e)
            return 2
        try:
            return asyncio.run(record(topic, a.minutes))
        except KeyboardInterrupt:
            return 0
    if a.check:
        rep = check(Path(a.check))
        lags = sorted(rep["lags"])
        _say(f"{rep['messages']} messages, {rep['updates']} updates applied, {len(rep['resyncs'])} out-of-sync")
        if lags:
            _say(f"ESPN publish -> received: median {lags[len(lags) // 2]:.2f}s, worst {lags[-1]:.2f}s")
        for r in rep["resyncs"][:10]:
            _say("  out of sync:", r)
        for m in rep["missing_checkpoints"][:5]:
            _say("  missing checkpoint file:", m)
        for name, fin in rep["final"].items():
            _say(f"  {name}: {fin['score']} | {fin['clock']} | {fin['phase']} | {fin['plays']} plays | {fin['last_play']}")
        ok = rep["updates"] > 0 and not rep["resyncs"] and not rep["missing_checkpoints"]
        _say("PASS: every update applied cleanly" if ok else "CHECK FAILED: see above")
        return 0 if ok else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
