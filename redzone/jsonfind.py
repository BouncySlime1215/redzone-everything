"""One parser for a model's JSON answer (the reasoner and the watchdog both use it).

Before: the reasoner took a greedy `\\{.*\\}` (two objects or a trailing brace in the prose broke it)
and the watchdog a lazy `\\{.*?\\}` (any nested object broke it). This one skips <think> blocks and code
fences, scans for brace-balanced candidates (string- and escape-aware) and returns the first one
that parses as a JSON object (and that the caller's `accept` likes: a schema echoed before the
answer is skipped). One repair: trailing commas ({"a": 1,}), the slip small models make most.
Never raises.

Why: first added in d741c8e2 (2026-11-04) as "Eval honesty: night-level gate, leak checks, no locked games in the e2e night (research B9-B10, C18)"; no separate decision is recorded for this module.
"""
from __future__ import annotations

import json
import re
from typing import Callable

_THINK = re.compile(r"<think>.*?</think>", re.S)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def _loads(c: str):
    try:
        return json.loads(c)
    except ValueError:
        pass
    fixed = _TRAILING_COMMA.sub(r"\1", c)
    if fixed != c:
        try:
            return json.loads(fixed)
        except ValueError:
            pass
    return None


def _candidates(t: str):
    for start in (i for i, ch in enumerate(t) if ch == "{"):
        depth, in_str, esc = 0, False, False
        for j in range(start, len(t)):
            ch = t[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    yield t[start:j + 1]
                    break


def first_json_object(text: str | None, accept: Callable[[dict], bool] | None = None) -> dict | None:
    """The first JSON object in a model's answer (that `accept` likes, when given), or None."""
    if not text or not isinstance(text, str):
        return None
    t = _THINK.sub("", text)
    if "</think>" in t:                     # the opening tag was cut off: everything before is thought
        t = t.rsplit("</think>", 1)[1]
    if "<think>" in t:                      # an unterminated think block: what follows it is all thought
        t = t.split("<think>", 1)[0] or t
    for c in _candidates(t):
        d = _loads(c)
        if not isinstance(d, dict):
            continue
        try:
            if accept is None or accept(d):
                return d
        except Exception:                   # a picky accept() never breaks parsing
            continue
    return None
