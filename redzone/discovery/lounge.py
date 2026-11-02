"""Ask the YouTube TV app on a stick which video id it is playing, over the network, no login.

Why: the video id of a live YouTube TV channel is its watch code. When in-app search lands on a
channel we have no code for, this is the cleanest way to learn the code, and after a deep link it
proves the app really switched (it didn't silently keep the old channel).

How (the flow daoakley/commercial-break uses to read YouTube TV on a Samsung TV, Oct 2026):
  1. DIAL, on the LAN: GET http://<stick>:8009/apps/YouTubeTV -> XML with <state> and <screenId>.
     A GET never launches anything. Fire TV's DIAL server is on port 8009 (Amazon's DIAL docs).
     The YouTube TV DIAL app name on Fire TV is UNVERIFIED (Samsung's is "YouTubeTV").
  2. POST https://www.youtube.com/api/lounge/pairing/get_lounge_token_batch  screen_ids=<id>
     -> a lounge token. No account is involved.
  3. POST https://www.youtube.com/api/lounge/bc/bind as a read-only "remote": the reply carries
     events; 'nowPlaying' has {"videoId": ...}. If it's missing, ask getNowPlaying and read one
     short poll. Then disconnect (TYPE=terminate).
Read-only: no play, seek or queue commands are ever sent.

Guardrails: every request has a short timeout and the whole call a deadline; any failure returns
None (never raises); a stick with no YouTube TV DIAL app is not asked again for 10 minutes.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid

import httpx

log = logging.getLogger(__name__)

DIAL_PORTS = (8009, 60000)
DIAL_APPS = ("YouTubeTV", "YouTube%20TV")      # never plain "YouTube": that's a different app
LOUNGE = "https://www.youtube.com/api/lounge"
NEG_TTL_S = 600.0
CLIENT_NAME = "RedZone"
_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_no_dial: dict[str, float] = {}               # host -> time DIAL last failed


def host_ip(host: str) -> str:
    """'192.168.1.42:5555' -> '192.168.1.42'."""
    return (host or "").rsplit(":", 1)[0] if (host or "").count(":") == 1 else (host or "")


def parse_dial(xml: str) -> dict:
    """The fields we need from a DIAL app description, tolerant of namespaces."""
    out = {}
    for tag in ("state", "screenId", "theme", "name"):
        m = re.search(rf"<(?:[\w-]+:)?{tag}>\s*([^<]*?)\s*</", xml or "")
        if m:
            out[tag] = m.group(1)
    return out


def parse_events(text: str) -> list[tuple[int | None, str, list]]:
    """Lounge replies are chunks '<length>\\n<json array>'. Each item is [aid, [name, *args]].
    The length is ignored (bytes vs characters differ for non-ASCII titles); we parse the next
    JSON value instead. Garbage ends parsing without raising."""
    events: list[tuple[int | None, str, list]] = []
    dec = json.JSONDecoder()
    i, text = 0, text or ""
    while i < len(text):
        m = re.compile(r"\s*\d+\s*\n").match(text, i)
        if not m:
            break
        try:
            arr, i = dec.raw_decode(text, m.end())
        except ValueError:
            break
        for item in arr if isinstance(arr, list) else []:
            if isinstance(item, list) and len(item) == 2 and isinstance(item[1], list) and item[1]:
                events.append((item[0] if isinstance(item[0], int) else None, str(item[1][0]), item[1][1:]))
    return events


def video_id(events) -> str | None:
    """The latest nowPlaying videoId in a list of events."""
    vid = None
    for _aid, name, args in events:
        if name == "nowPlaying" and args and isinstance(args[0], dict):
            v = args[0].get("videoId")
            if isinstance(v, str) and _ID.match(v):
                vid = v
    return vid


def session_ids(events) -> dict:
    """SID ('c' event), gsessionid ('S' event) and the last event id (AID)."""
    out: dict = {}
    for aid, name, args in events:
        if aid is not None:
            out["aid"] = aid
        if name == "c" and args:
            out["sid"] = args[0]
        elif name == "S" and args:
            out["gsession"] = args[0]
    return out


async def dial_screen(client: httpx.AsyncClient, ip: str) -> dict | None:
    """{'state', 'screenId', 'theme'} for the YouTube TV app on the stick, or None."""
    for port in DIAL_PORTS:
        for app in DIAL_APPS:
            try:
                r = await client.get(f"http://{ip}:{port}/apps/{app}", timeout=httpx.Timeout(3.0, connect=1.5))
            except Exception:
                break  # port closed: try the next port, not the next app name
            if r.status_code == 200:
                d = parse_dial(r.text)
                if d.get("screenId"):
                    return d
    return None


async def lounge_token(client: httpx.AsyncClient, screen_id: str) -> str | None:
    try:
        r = await client.post(f"{LOUNGE}/pairing/get_lounge_token_batch", data={"screen_ids": screen_id},
                              timeout=httpx.Timeout(6.0, connect=3.0))
        screens = r.json().get("screens") or [] if r.status_code == 200 else []
        tok = screens[0].get("loungeToken") if screens and isinstance(screens[0], dict) else None
        return tok if isinstance(tok, str) and tok else None
    except Exception as e:
        log.debug("lounge token failed: %s", e)
        return None


def _bind_body(token: str, theme: str | None) -> dict:
    return {"app": "web", "mdx-version": "3", "name": CLIENT_NAME, "id": uuid.uuid4().hex,
            "device": "REMOTE_CONTROL", "capabilities": "que,dsdtr,atp,vsp", "magnaKey": "cloudPairedDevice",
            "ui": "false", "deviceContext": "user_agent=RedZone&os_name=desktop", "theme": theme or "cl",
            "loungeIdToken": token}


def _common(token: str, ids: dict) -> dict:
    return {"name": CLIENT_NAME, "loungeIdToken": token, "SID": ids.get("sid", ""), "AID": ids.get("aid", 0),
            "gsessionid": ids.get("gsession", ""), "device": "REMOTE_CONTROL", "app": "youtube-desktop",
            "VER": "8", "v": "2"}


async def _read_stream(client, url: str, params: dict, deadline: float) -> str:
    """Read a long-poll until nowPlaying shows up or the deadline passes; return what arrived."""
    buf = ""
    left = max(0.5, deadline - time.monotonic())
    try:
        async with client.stream("GET", url, params=params, timeout=httpx.Timeout(left, connect=3.0)) as r:
            async for chunk in r.aiter_text():
                buf += chunk
                if "nowPlaying" in buf and video_id(parse_events(buf)) or time.monotonic() > deadline:
                    break
    except Exception as e:  # ReadTimeout on an idle long-poll is normal
        log.debug("lounge poll ended: %s", e)
    return buf


async def _terminate(client, token: str, ids: dict) -> None:
    try:
        await client.post(f"{LOUNGE}/bc/bind", params={**_common(token, ids), "RID": "3", "CVER": "1",
                                                       "auth_failure_option": "send_error"},
                          data={"ui": "", "TYPE": "terminate",
                                "clientDisconnectReason": "MDX_SESSION_DISCONNECT_REASON_DISCONNECTED_BY_USER"},
                          timeout=httpx.Timeout(3.0, connect=2.0))
    except Exception:
        pass


async def _bind_and_read(client, token: str, theme: str | None, deadline: float) -> str | None:
    r = await client.post(f"{LOUNGE}/bc/bind", data=_bind_body(token, theme),
                          params={"RID": "1", "VER": "8", "CVER": "1", "auth_failure_option": "send_error"},
                          timeout=httpx.Timeout(6.0, connect=3.0))
    events = parse_events(r.text) if r.status_code == 200 else []
    ids = session_ids(events)
    try:
        vid = video_id(events)
        if vid or not (ids.get("sid") and ids.get("gsession")):
            return vid
        await client.post(f"{LOUNGE}/bc/bind", params={**_common(token, ids), "RID": "2"},
                          data={"count": "1", "ofs": "1", "req0__sc": "getNowPlaying"},
                          timeout=httpx.Timeout(4.0, connect=2.0))
        text = await _read_stream(client, f"{LOUNGE}/bc/bind",
                                  {**_common(token, ids), "RID": "rpc", "CI": "0", "TYPE": "xmlhttp"}, deadline)
        return video_id(parse_events(text))
    finally:
        await _terminate(client, token, ids)


async def now_playing(host: str, *, client: httpx.AsyncClient | None = None, deadline_s: float = 10.0) -> str | None:
    """The video id the stick's YouTube TV app is playing, or None. Never raises."""
    ip = host_ip(host)
    if not ip or time.time() - _no_dial.get(ip, 0.0) < NEG_TTL_S:
        return None
    own = client is None
    c = client or httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"})
    try:
        deadline = time.monotonic() + deadline_s
        screen = await dial_screen(c, ip)
        if not screen or screen.get("state", "running") != "running":
            _no_dial[ip] = time.time()
            return None
        token = await lounge_token(c, screen["screenId"])
        return await _bind_and_read(c, token, screen.get("theme"), deadline) if token else None
    except Exception as e:
        log.debug("lounge now_playing failed: %s", e)
        return None
    finally:
        if own:
            await c.aclose()
