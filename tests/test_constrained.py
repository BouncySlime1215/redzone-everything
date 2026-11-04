"""[V7] JSON-constrained decoding: every answer parses and fits its schema, whatever the model ranks."""
import json
import random

from agent.kaggle.constrained import JUMP, REASONER, State, pick, schema_for, watchdog_schema

# a toy BPE-ish vocabulary with the merges that matter: '",' ' "' '": "' '{"' and words
VOCAB = ['<eos>', '{"', '": "', '": ', '",', ' "', '"', ',', '}', ' ', '.', 'none', 'watch', 'must', '_see',
         'yes', 'no', 'lock', '_in', 'safe', '_to', '_leave', 'confidence', 'storyline', 'why', 'next', 'stay',
         '_s', 'tied', ' game', ' late', '"}', '\n', '\\', 'notify', 'action', 'target', 'feeds', 'reel',
         'decision', 'jump', 'reel_s', 'x'] + [chr(c) for c in range(32, 127)] + ["100", "110"]
VOCAB = list(dict.fromkeys(VOCAB))
EOS = 0
TID = {t: i for i, t in enumerate(VOCAB)}


def char_tok(c):
    return TID[c]


def decode(schema, rank_fn, max_new=192):
    st, out = State(schema), []
    for step in range(max_new):
        t, st = pick(st, rank_fn(step, out), VOCAB, char_tok, max_new - step, EOS, {EOS})
        if t == EOS:
            break
        out.append(t)
    return "".join(VOCAB[t] for t in out), st


def check(schema, text):
    d = json.loads(text)
    assert list(d) == [k for k, _ in schema]
    for k, kind in schema:
        if isinstance(kind, tuple):
            assert d[k] in kind, (k, d[k])
        else:
            assert isinstance(d[k], kind if kind is not float else (int, float)), (k, d[k])
    return d


def test_random_rankings_always_parse():
    rng = random.Random(7)
    sch = [REASONER, JUMP, watchdog_schema(json.dumps({"on_screen": "100.110.165.150:5555", "standby": "b:1"}))]
    for trial in range(300):
        schema = sch[trial % 3]
        text, st = decode(schema, lambda s, o: rng.sample(range(len(VOCAB)), 48))
        assert st.phase == "done", text
        check(schema, text)


def test_merged_tokens_are_taken_when_the_model_prefers_them():
    # the model wants exactly the training string; merged '",' and ' "' must be accepted, not forced apart
    want = '{"decision": "jump", "reel_s": 45, "why": "tied game late"}'

    def ranker(step, out):
        done = "".join(VOCAB[t] for t in out)
        rest = want[len(done):]
        best = sorted((t for t in range(len(VOCAB)) if VOCAB[t] and rest.startswith(VOCAB[t]) and t != EOS),
                      key=lambda t: -len(VOCAB[t]))
        return best + [EOS] if rest else [EOS]
    text, st = decode(JUMP, ranker)
    assert text == want and st.phase == "done"


def test_token_budget_closes_the_answer():
    rng = random.Random(1)
    assert State(REASONER).chars_to_finish() == 112
    for max_new in (112, 130, 160):
        # a model that only ever wants to ramble inside strings
        text, st = decode(REASONER, lambda s, o: [TID["x"], TID["a"]] + rng.sample(range(len(VOCAB)), 10), max_new)
        assert st.phase == "done", (max_new, text)
        check(REASONER, text)


def test_enum_and_number_rules():
    s = State(REASONER).feed_text('{"lock_in": "')
    assert s.feed_text("maybe") is None and s.feed_text("must") is not None
    assert s.feed_text('watch"') is not None and s.feed_text('wat"') is None
    s = State(REASONER).feed_text('{"lock_in": "none", "safe_to_leave": "no", "confidence": ')
    assert s.feed_text(",") is None and s.feed_text("0.9,") is not None and s.feed_text("0.9.1") is None
    assert s.feed_text('"') is None


def test_watchdog_targets_come_from_the_row():
    sch = watchdog_schema(json.dumps({"on_screen": "h1:5555", "standby": "h2:5555", "sticks_offline": ["h3:5555"]}))
    targets = dict(sch)["target"]
    assert "" in targets and {"h1:5555", "h2:5555", "h3:5555", "feeds"} <= set(targets)
    assert dict(watchdog_schema("not json"))["target"][0] == ""
    assert schema_for("watchdog", "{}")[0][0] == "action"
    assert schema_for("jump_blind") is JUMP and schema_for("reasoner_blind_valid") is REASONER


class _Tok:
    """The tokenizer surface the processor uses."""
    all_special_ids = [EOS]
    eos_token_id = EOS
    pad_token_id = EOS

    def __len__(self):
        return len(VOCAB)

    def decode(self, ids):
        return "".join(VOCAB[i] for i in ids)

    def encode(self, s, add_special_tokens=False):
        return [TID[s]] if s in TID else [TID[c] for c in s]


def test_processor_greedy_loop(monkeypatch):
    """The LogitsProcessor glue, as model.generate drives it (numpy stands in when torch is absent)."""
    import sys
    import types

    import numpy as np
    try:
        import torch  # noqa: F401
        arr = __import__("torch").tensor
    except ImportError:
        fake = types.SimpleNamespace(
            topk=lambda x, k, dim=1: types.SimpleNamespace(indices=np.argsort(-x, axis=1)[:, :k]),
            full_like=lambda x, v: np.full_like(x, v, dtype=float))
        monkeypatch.setitem(sys.modules, "torch", fake)
        arr = np.array
    from agent.kaggle.constrained import JsonSchemaProcessor
    rng = np.random.default_rng(3)
    schemas = [REASONER, JUMP, watchdog_schema('{"on_screen": "h1:5555"}')]
    proc = JsonSchemaProcessor(_Tok(), schemas, max_new_tokens=160)
    ids = arr(np.zeros((3, 4), dtype=np.int64))
    out = [[] for _ in schemas]
    for _ in range(160):
        scores = proc(ids, arr(rng.normal(size=(3, len(VOCAB)))))
        nxt = [int(x) for x in np.asarray(scores).argmax(axis=1)]
        for b, t in enumerate(nxt):
            if not out[b] or out[b][-1] != EOS:
                out[b].append(t)
        ids = arr(np.concatenate([np.asarray(ids), np.array(nxt)[:, None]], axis=1))
    for sch, toks in zip(schemas, out):
        assert toks[-1] == EOS
        check(sch, "".join(VOCAB[t] for t in toks[:-1]))
