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
