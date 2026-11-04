"""[V7] JSON-constrained decoding: the student can only write its answer's own JSON shape.

V6: the watchdog wrote unusable JSON 37% of the time. V7 decodes greedily under a per-row character
automaton for the answer's schema (keys in training order, json.dumps spacing):

  - keys, quotes, ", " and ": " are fixed literals;
  - an enum field may only spell one of its options (the watchdog's target: no target, a stick host
    from this row's own status JSON, or an app part);
  - a string field takes any text without a quote, newline or backslash, up to a character cap;
  - a number field takes digits (and one '.' for a float).

At each step the model's top-K tokens are tried best first and the first one the automaton accepts
wins, so a merged token like '",' or ' "' is fine (a token-level mask would ban it and push the model
off its training distribution). If none of the top-K fits, or the answer must close now to fit in
max_new_tokens, the shortest legal character is forced. Every answer parses, by construction.

    proc = JsonSchemaProcessor(tok, [schema_for(r["task"], r["messages"][1]["content"]) for r in part],
                               max_new_tokens=192)
    model.generate(..., logits_processor=LogitsProcessorList([proc]))
"""
from __future__ import annotations

import json

REASONER = [("lock_in", ("none", "watch", "must_see")), ("safe_to_leave", ("yes", "no")), ("confidence", float),
            ("storyline", str), ("why", str), ("next", str), ("stay_s", int)]
JUMP = [("decision", ("jump", "stay")), ("reel_s", int), ("why", str)]
ACTIONS = ("none", "notify", "restart_service", "restart_component", "reconnect_stick", "retune_stick",
           "resume_player", "clear_prompt", "refresh_feed", "prune_disk", "safe_mode_off")
COMPONENTS = ("feeds", "screen", "reel", "control", "supervisor")
STR_CAP = {"storyline": 160, "why": 160, "next": 120}      # characters (train p99 58/43/43, watchdog why 100)
NUM_CAP = 6                                                # digits
TOP_K = 48


def watchdog_schema(user_content: str) -> list:
    """action (one of 11), target ("" / a stick named in this row's status / an app part), why."""
    hosts = []
    try:
        s = json.loads(user_content)
        for k in ("on_screen", "standby"):
            if isinstance(s.get(k), str) and s[k]:
                hosts.append(s[k])
        hosts += [h for h in s.get("sticks_offline") or [] if isinstance(h, str)]
    except (ValueError, TypeError, AttributeError):
        pass
    targets = tuple(dict.fromkeys(["", *hosts, *COMPONENTS]))
    return [("action", ACTIONS), ("target", targets), ("why", str)]


def schema_for(task: str, user_content: str = "") -> list:
    if task == "watchdog":
        return watchdog_schema(user_content)
    if str(task).startswith("jump"):
        return JUMP
    return REASONER


# ------------------------------------------------------------------ the character automaton (pure python)
def _numeric(kind) -> bool:
    return kind in (int, float)


def _opener(schema, i: int) -> str:
    key, kind = schema[i]
    return ("{" if i == 0 else ", ") + '"' + key + '": ' + ("" if _numeric(kind) else '"')


def _closer(schema, f: int) -> str:
    return _opener(schema, f + 1) if f + 1 < len(schema) else "}"


class State:
    """Where one answer is: f = field index, phase lit|enum|str|num|done, lit = literal still owed,
    buf = the current value so far."""
    __slots__ = ("schema", "f", "phase", "lit", "buf")

    def __init__(self, schema, f=-1, phase="lit", lit=None, buf=""):
        self.schema, self.f, self.phase, self.buf = schema, f, phase, buf
        self.lit = _opener(schema, 0) if lit is None else lit

    def copy(self) -> State:
        return State(self.schema, self.f, self.phase, self.lit, self.buf)

    def kind(self):
        return self.schema[self.f][1]

    def _enter(self) -> None:
        if self.f + 1 >= len(self.schema):
            self.phase = "done"
            return
        self.f += 1
        k = self.kind()
        self.phase = "enum" if isinstance(k, tuple) else "str" if k is str else "num"
        self.buf = ""

    def _close(self) -> None:
        self.phase, self.lit, self.buf = "lit", _closer(self.schema, self.f), ""

    def feed(self, c: str) -> bool:
        """Take one character (in place); False if the schema forbids it here."""
        p = self.phase
        if p == "done":
            return False
        if p == "lit":
            if not self.lit or c != self.lit[0]:
                return False
            self.lit = self.lit[1:]
            if not self.lit:
                self._enter()
            return True
        if p == "enum":
            opts = self.kind()
            if c == '"' and self.buf in opts:
                self._close()
                return True
            if any(o.startswith(self.buf + c) for o in opts):
                self.buf += c
                return True
            return False
        if p == "str":
            if c == '"':
                self._close()
                return True
            if c in "\n\r\\" or ord(c) < 32 or len(self.buf) >= STR_CAP.get(self.schema[self.f][0], 160):
                return False
            self.buf += c
            return True
        # number
        digits = sum(ch.isdigit() for ch in self.buf)
        if c in "0123456789":
            if digits >= NUM_CAP or self.buf == "0":         # JSON has no leading zeros
                return False
            self.buf += c
            return True
        if c == "." and self.kind() is float and self.buf and "." not in self.buf and digits < NUM_CAP:
            self.buf += c
            return True
        if self.buf and self.buf[-1] != ".":
            self._close()
            return self.feed(c)
        return False

    def feed_text(self, text: str) -> State | None:
        """A copy after `text`, or None if any character is forbidden."""
        s = self.copy()
        for c in text:
            if not s.feed(c):
                return None
        return s

    def forced(self) -> str | None:
        """The one character that heads straight for the end (None when done)."""
        p = self.phase
        if p == "done":
            return None
        if p == "lit":
            return self.lit[0]
        if p == "enum":
            opts = self.kind()
            if self.buf in opts:
                return '"'
            return min((o for o in opts if o.startswith(self.buf)), key=len)[len(self.buf)]
        if p == "str":
            return '"'
        if self.buf and self.buf[-1] != ".":
            return _closer(self.schema, self.f)[0]
        return "0"

    def chars_to_finish(self) -> int:
        """Characters the forced path still needs (an upper bound on tokens left, one token per char)."""
        if self.phase == "done":
            return 0
        n, sch = 0, self.schema
        if self.phase == "lit":
            n += len(self.lit)
            nxt = self.f + 1
        else:
            k = self.kind()
            if self.phase == "enum":
                n += min(len(o) for o in k if o.startswith(self.buf)) - len(self.buf) + 1
            elif self.phase == "str":
                n += 1
            else:
                n += (0 if self.buf and self.buf[-1] != "." else 1)
            n += len(_closer(sch, self.f))
            nxt = self.f + 1
        for i in range(nxt, len(sch)):          # fields not started yet: their value + what follows it
            k = sch[i][1]
            n += (min(len(o) for o in k) + 1) if isinstance(k, tuple) else 1 if k is str else 1
            n += len(_closer(sch, i))
        return n


def pick(state: State, ranked: list[int], texts: list[str], char_tok, left: int, eos: int,
         special: set) -> tuple[int, State]:
    """(token, state after it): the best-ranked token the schema allows and that still leaves room to
    close the answer in the `left` tokens remaining (one forced character per token); else the forced
    character's token. An answer closes in time whenever max_new_tokens >= State(schema).chars_to_finish()
    (112 for the reasoner)."""
    if state.phase == "done":
        return eos, state
    for t in ranked:
        if t >= len(texts) or t in special or not texts[t]:      # (lm_head is wider than the vocab)
            continue
        s = state.feed_text(texts[t])
        if s is not None and s.chars_to_finish() <= left - 1:     # the forced path can still close it
            return t, s
    c = state.forced()
    s = state.copy()
    assert s.feed(c), (c, state.phase, state.buf)
    return char_tok(c), s


# ------------------------------------------------------------------------------------ the HF processor
class _Vocab:
    def __init__(self, tok):
        self.texts = [tok.decode([i]) for i in range(len(tok))]
        self.special = set(getattr(tok, "all_special_ids", []) or []) | set(getattr(tok, "added_tokens_decoder", {}) or {})
        self.eos = tok.eos_token_id if tok.eos_token_id is not None else tok.pad_token_id
        self._char: dict = {}
        self._tok = tok

    def char_tok(self, c: str) -> int:
        if c not in self._char:
            ids = self._tok.encode(c, add_special_tokens=False)
            assert len(ids) == 1, f"{c!r} is not one token"
            self._char[c] = ids[0]
        return self._char[c]


class JsonSchemaProcessor:
    """LogitsProcessor: row b of the batch can only continue schemas[b]'s JSON; greedy decoding then
    takes the only token left unmasked."""

    _VOCABS: dict = {}

    def __init__(self, tok, schemas: list, max_new_tokens: int = 192, top_k: int = TOP_K):
        if id(tok) not in self._VOCABS:
            self._VOCABS[id(tok)] = _Vocab(tok)
        self.v = self._VOCABS[id(tok)]
        self.states = [State(s) for s in schemas]
        self.pending: list = [None] * len(schemas)       # (token we picked, state after it)
        self.max_new, self.top_k, self.step = max_new_tokens, top_k, 0
        self.forced = 0                                   # how often the top-K had nothing legal

    def __call__(self, input_ids, scores):
        import torch
        v = self.v
        if self.step:
            for b, t in enumerate(input_ids[:, -1].tolist()):
                want = self.pending[b]
                if want is None:
                    continue
                if t == want[0]:
                    self.states[b] = want[1]
                elif self.states[b].phase != "done":                  # (never expected) follow the token
                    s = self.states[b].feed_text(v.texts[t])
                    self.states[b] = s if s is not None else self.states[b]
        left = self.max_new - self.step
        self.step += 1
        k = min(self.top_k, scores.shape[1])
        ranked = torch.topk(scores, k, dim=1).indices.tolist()
        out = torch.full_like(scores, float("-inf"))
        for b, st in enumerate(self.states):
            t, s = pick(st, ranked[b], v.texts, v.char_tok, left, v.eos, v.special)
            if st.phase != "done" and t not in ranked[b]:
                self.forced += 1
            self.pending[b] = (t, s)
            out[b, t] = 0.0
        return out
