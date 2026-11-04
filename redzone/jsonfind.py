"""One parser for a model's JSON answer (the reasoner and the watchdog both use it).

Before: the reasoner took a greedy `\\{.*\\}` (two objects or a trailing brace in the prose broke it)
and the watchdog a lazy `\\{.*?\\}` (any nested object broke it). This one skips <think> blocks and code
fences, scans for brace-balanced candidates (string- and escape-aware) and returns the first one
that parses as a JSON object. Never raises.
"""
from __future__ import annotations

import json
import re

_THINK = re.compile(r"<think>.*?</think>", re.S)


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


def first_json_object(text: str | None) -> dict | None:
    """The first JSON object in a model's answer, or None."""
    if not text:
        return None
    t = _THINK.sub("", text)
    if "<think>" in t:                      # an unterminated think block: what follows it is all thought
        t = t.split("<think>", 1)[0] or t
    for c in _candidates(t):
        try:
            d = json.loads(c)
        except ValueError:
            continue
        if isinstance(d, dict):
            return d
    return None
