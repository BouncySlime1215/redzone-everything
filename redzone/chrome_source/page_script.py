"""The page script for the YouTube TV web player (`#movie_player`): one small JavaScript text per call.

Every script is one expression that returns a JSON string of numbers and short states only: no titles, no stats, no
account or device details. The player calls used were tried by hand in Chrome on Nick's Mac on Nov 10 (MEASURED):
getPlayerState, getProgressState (current, ingestionTime, isAtLiveHead, seekableStart, seekableEnd), pauseVideo,
playVideo, seekTo(seconds, true), seekToLiveHead, mute. ASSUMED, not tried: unMute and isMuted (the standard
counterparts of mute), the `.ytp-error` overlay as the sign of an error, and whether ingestionTime is in seconds or
milliseconds (both are handled).

A content second "on YouTube's clock" is an epoch time as ingestionTime gives it: the time YouTube took in the frame.
The player seeks by media time (`current`), so a seek goes through the pair read in the same call:
    media second = current + (target epoch - ingestionTime).

Why: the stream backend has to pause, resume and seek a tab to an exact second, and read where it is, with nothing
but text it can hand to Chrome; keeping that text in one place makes it reviewable and lets the tests name each call.
"""
from __future__ import annotations

import json

STATE_NAMES = {-1: "unstarted", 0: "ended", 1: "playing", 2: "paused", 3: "buffering", 5: "cued"}
MS_FROM = 1e11           # an epoch larger than this is in milliseconds (1e11 s is the year 5138)

# pl = the player; st() = the state every call returns. Numbers are rounded to the millisecond.
_PRELUDE = """(function(){
var pl=document.getElementById('movie_player');
function num(x){return (typeof x==='number'&&isFinite(x))?Math.round(x*1000)/1000:null;}
function ep(x){return (typeof x==='number'&&isFinite(x))?(x>%(ms)s?x/1000:x):null;}
function st(){
if(!pl||typeof pl.getProgressState!=='function'||typeof pl.getPlayerState!=='function')return {player:0};
var s=pl.getProgressState()||{};var e=document.querySelector('.ytp-error');
return {player:1,state:pl.getPlayerState(),current:num(s.current),epoch:num(ep(s.ingestionTime)),
live:s.isAtLiveHead?1:0,start:num(s.seekableStart),end:num(s.seekableEnd),
muted:(typeof pl.isMuted==='function')?(pl.isMuted()?1:0):null,error:(e&&e.offsetParent!==null)?1:0};
}
function go(to){var s0=st();
if(to===null||s0.start===null||s0.end===null||to<s0.start||to>s0.end)return {player:1,ok:0,why:'out_of_range',to:num(to),start:s0.start,end:s0.end};
pl.seekTo(to,true);var s1=st();s1.ok=1;s1.to=num(to);return s1;}
var a=%(args)s;
var out=(function(){if(!st().player)return {player:0};%(body)s})();
return JSON.stringify(out);
})()"""

_BODY = {
    "state": "return st();",
    "pause": "pl.pauseVideo();return st();",
    "play": "pl.playVideo();return st();",
    "mute": "pl.mute();return st();",
    "unmute": "if(typeof pl.unMute!=='function')return {player:1,ok:0,why:'no_unmute'};pl.unMute();return st();",
    "live": "pl.seekToLiveHead();return st();",
    "seek": "return go(a.t);",                                        # a.t = a media second
    "seek_epoch": "var s=st();if(s.epoch===null||s.current===null)return {player:1,ok:0,why:'no_clock'};"
                  "return go(s.current+(a.t-s.epoch));",              # a.t = an epoch second on YouTube's clock
}
COMMANDS = tuple(_BODY)


def build(cmd: str, **args) -> str:
    """The script for one call. It starts with a tag (`/*rz:<cmd> <args json>*/`) so a log or a fake can tell which call
    it is without running it. Arguments are numbers only."""
    if cmd not in _BODY:
        raise ValueError(f"no page-script call named {cmd!r}")
    for k, v in args.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"page-script argument {k} must be a number")
    a = json.dumps(args, sort_keys=True)
    return f"/*rz:{cmd} {a}*/" + _PRELUDE % {"ms": "1e11", "args": a, "body": _BODY[cmd]}


def parse_tag(script: str) -> tuple[str, dict]:
    """(cmd, args) of a script made by build()."""
    if not script.startswith("/*rz:"):
        raise ValueError("not a RedZone page script")
    head = script[5:script.index("*/")]
    cmd, _, a = head.partition(" ")
    return cmd, json.loads(a or "{}")


def epoch_s(v) -> float | None:
    """ingestionTime as epoch seconds, whether the player gives seconds or milliseconds."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return v / 1000.0 if v > MS_FROM else float(v)


def media_second(current: float, epoch: float, target_epoch: float) -> float:
    """The media time to seek to so the frame taken in at `target_epoch` is on screen, given one (current, epoch) pair
    read together. The same sum the `seek_epoch` script does in the page."""
    return current + (target_epoch - epoch)


def state_name(code) -> str:
    return STATE_NAMES.get(code, "unknown")
