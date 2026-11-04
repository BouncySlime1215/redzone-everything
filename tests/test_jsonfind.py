from redzone.jsonfind import first_json_object
from redzone.scoring import breaks


def test_nested_object_survives():          # the old lazy {.*?} cut it at the inner brace
    assert first_json_object('ok {"a": {"b": 1}, "c": 2} done') == {"a": {"b": 1}, "c": 2}


def test_first_of_two_objects_and_a_trailing_brace():   # the old greedy {.*} spanned both
    assert first_json_object('{"x": 1} then {"y": 2}') == {"x": 1}
    assert first_json_object('{"x": 1} and a stray }') == {"x": 1}


def test_braces_inside_strings_and_escapes():
    assert first_json_object(r'{"why": "a } brace and a \" quote", "n": 1}') == {"why": 'a } brace and a " quote', "n": 1}


def test_think_blocks_are_skipped():
    assert first_json_object('<think>maybe {"x": 0}</think>{"x": 1}') == {"x": 1}
    assert first_json_object('{"x": 2}<think>then {"x": 3}') == {"x": 2}


def test_garbage_and_non_objects_give_none():
    for t in (None, "", "no json", "[1, 2]", "{not json}", '{"a": 1'):
        assert first_json_object(t) is None
    assert first_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_blend_log_odds_is_symmetric_and_bounded():
    assert abs(breaks._mix_logit(0.7, 0.3, 0.5) - 0.5) < 1e-9
    assert breaks._mix_logit(0.9, 0.9, 0.3) == __import__("pytest").approx(0.9)
    assert 0 < breaks._mix_logit(0.0, 1.0, 0.5) < 1


# --- malformed and nested model output (research C18: one parser for every answer) ---------------

def test_deeply_nested_and_arrays_come_back_whole():
    t = 'ok {"action": "notify", "meta": {"k": {"deep": [1, {"x": 2}]}}, "why": "w"} done'
    assert first_json_object(t) == {"action": "notify", "meta": {"k": {"deep": [1, {"x": 2}]}}, "why": "w"}


def test_malformed_output():
    assert first_json_object('{"a": 1,}') == {"a": 1}                   # trailing comma repaired
    assert first_json_object('{"a": [1, 2,], }') == {"a": [1, 2]}
    assert first_json_object("{'a': 1}") is None                        # single quotes are not JSON
    assert first_json_object('{bad} {"a": 1}') == {"a": 1}              # skip a broken candidate
    assert first_json_object('{"a": {"b": 1}, oops}') == {"b": 1}       # parent broken: inner object
    assert first_json_object('{"a": "unterminated}') is None
    assert first_json_object(r'{"s": "back\\slash", "q": "\"}\""}') == {"s": "back\\slash", "q": '"}"'}
    assert first_json_object(b'{"a": 1}') is None                       # not text: None, never raises


def test_think_block_with_its_opening_tag_cut_off():
    assert first_json_object('still thinking {"a": 1}</think>{"b": 2}') == {"b": 2}
    assert first_json_object('<think>x</think><think>y</think>{"c": 3}') == {"c": 3}


def test_accept_skips_a_schema_echo_and_never_raises():
    t = 'format: {"action": "..."} answer: {"action": "prune_disk", "why": "full"}'
    assert first_json_object(t, accept=lambda d: d["action"] != "...") == {"action": "prune_disk", "why": "full"}
    assert first_json_object('{"a": 1}', accept=lambda d: d["missing"]) is None


def test_reasoner_and_watchdog_parse_through_it():
    from agent.policy import parse_answer as watchdog_parse
    from redzone.reasoner.reasoner import parse_answer as reasoner_parse
    n = reasoner_parse('{"lock_in": "watch", "why": "close game"} (scale: {0..3})')
    assert n is not None and n.lock_in == "watch"
    assert reasoner_parse('{"lock_in": "maybe"} {"lock_in": "must_see"}').lock_in == "must_see"
    v = watchdog_parse('{"action": "notify", "extra": {"k": 1}, "why": "disk"}')
    assert v is not None and v.action == "notify" and v.why == "disk"
    v = watchdog_parse('format: {"action": "..."} answer: {"action": "prune_disk", "why": "full"}')
    assert v is not None and v.action == "prune_disk"
