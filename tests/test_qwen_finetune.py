"""The Qwen fine-tune: leak gate first, no test night in training, resume, the contract's numbers. Fakes only: no torch, no GPU."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agent.kaggle import qwen_common as C
from agent.kaggle import qwen_finetune as QF
from redzone.contracts import brain
from redzone.noggin import gamevalue as G
from redzone.noggin import value_shards as V

KAGGLE = Path(QF.__file__).parent
SOURCES = [KAGGLE / "qwen_common.py", KAGGLE / "qwen_finetune.py", KAGGLE / "qwen_eval.py", KAGGLE.parents[1] / "scripts" / "mac_brain_memory_test.py"]


def features(**over) -> dict:
    row = dict.fromkeys(G.GV_FEATURES, 0.0)
    row.update(period=4.0, clock_left=133.0, game_left=133.0, late=1.0, abs_margin=3.0)
    return {**row, **over}


def frame(nights: list[str], per_night: int = 8) -> pd.DataFrame:
    rows = []
    for n in nights:
        for i in range(per_night):
            rows.append({**features(total_points=float(i)), "league": "nfl", "season": 2023, "game_id": f"{n}-{i % 2}", "game_key": "", "night": n,
                         "source_night_id": f"nfl:{n}", "source": "nflverse_pbp", "license": "CC-BY 4.0", "plan": "A", "decision_t": 1000.0 + i, "feed_cut_t": 990.0 + i,
                         "feed_head_t": 980.0 + i, "feature_t_max": 980.0 + i, "lag_s": 10.0, "label_full_window": 1, "label_first_t": float("nan"),
                         "value_next_3m": float(i % 3) * 2.0, "p_tier1_60s": float(i % 2)})
    return pd.DataFrame(rows)[list(V.VALUE_COLUMNS)]


TRAIN, CALIB, TEST = ["20230910", "20230917", "20230924"], ["20231001"], ["20231008"]


@pytest.fixture
def shards(tmp_path):
    root = tmp_path / "shards"
    (root / "nfl").mkdir(parents=True)
    frame(TRAIN + CALIB + TEST).to_parquet(root / "nfl" / "2023.parquet")
    (root / "nfl" / "2023.json").write_text(json.dumps({"rows_sha256": "abc", "features_version": V.FEATURES_VERSION}))
    (root / "splits.json").write_text(json.dumps({"nfl": {"train": TRAIN, "calib": CALIB, "test": TEST}}))
    return root


class Report:
    def __init__(self, fails=(), raw_rows=10):
        self.fails, self.checked = list(fails), {"shards": 1, "rows": 40, "prefix_rows": 5, "raw_rows": raw_rows}

    def fail(self, _check, msg):
        self.fails.append(msg)


@pytest.fixture
def leak_ok(monkeypatch):
    monkeypatch.setattr(QF.KG, "fetch_raw", lambda *a, **k: None)
    monkeypatch.setattr(QF.LC, "check_dir", lambda *a, **k: Report())


class Fake:
    """Stands in for TorchLora: records what it was given, saves and restores a step counter."""

    def __init__(self, loss=0.5):
        self.loss, self.seen, self.restored, self.exported = loss, [], None, False

    def train_step(self, batch):
        self.seen.append(batch)
        return self.loss

    def save(self, path):
        (path / "weights").write_text("w")

    def restore(self, path):
        self.restored = path

    def export(self, path):
        (path / "peft").mkdir(parents=True, exist_ok=True)
        self.exported = True

    def digit_probs(self, prompts):
        out = np.full((len(prompts), 10), 0.01)
        out[:, 2] = 0.91
        return out

    def yes_probs(self, prompts):
        return np.full(len(prompts), 0.25)

    def words(self, prompts):
        return ["late, 3 point game"] * len(prompts)


def args(shards, out, *extra):
    return ["--shards", str(shards), "--out", str(out), "--raw", str(out / "raw"), "--leagues", "nfl", "--resume-from", str(out / "none"), *extra]


def go(shards, out, fake=None, *extra, clock=lambda: 0.0):
    fake = fake or Fake()
    code = QF.main(args(shards, out, *extra), backend_factory=lambda *a: fake, clock=clock, log=lambda *a, **k: None)
    return code, fake


def test_refuses_on_the_mac_without_touching_data(tmp_path, monkeypatch):
    monkeypatch.setattr(QF.sys, "platform", "darwin")
    monkeypatch.setattr(QF, "leak_gate", lambda *a, **k: pytest.fail("the Mac must not get this far"))
    assert QF.main(args(tmp_path, tmp_path / "o")) == QF.EXIT_MAC


def test_leak_failure_refuses_before_any_row_is_read(shards, tmp_path, monkeypatch):
    monkeypatch.setattr(QF.KG, "fetch_raw", lambda *a, **k: None)
    monkeypatch.setattr(QF.LC, "check_dir", lambda *a, **k: Report(fails=["locked night"]))
    monkeypatch.setattr(QF.KG, "load_rows", lambda *a, **k: pytest.fail("read a row before the leak check passed"))
    code, fake = go(shards, tmp_path / "o")
    assert code == QF.EXIT_REFUSED and not fake.seen


def test_no_raw_audit_counts_as_a_failed_leak_check(shards, tmp_path, monkeypatch):
    monkeypatch.setattr(QF.KG, "fetch_raw", lambda *a, **k: None)
    monkeypatch.setattr(QF.LC, "check_dir", lambda *a, **k: Report(raw_rows=0))
    assert go(shards, tmp_path / "o")[0] == QF.EXIT_REFUSED


def test_shards_without_splits_cover_are_refused(shards, tmp_path, leak_ok):
    (shards / "splits.json").write_text(json.dumps({"nfl": {"train": TRAIN, "calib": CALIB, "test": []}}))
    assert go(shards, tmp_path / "o")[0] == QF.EXIT_REFUSED


def test_nba_refused_without_the_personal_flag(shards, tmp_path, leak_ok):
    code = QF.main(["--shards", str(shards), "--out", str(tmp_path / "o"), "--leagues", "nba"], backend_factory=lambda *a: Fake(), log=lambda *a, **k: None)
    assert code == QF.EXIT_REFUSED


def test_trains_only_train_nights_and_writes_a_tagged_manifest(shards, tmp_path, leak_ok):
    out = tmp_path / "o"
    code, fake = go(shards, out)
    assert code == 0 and fake.exported
    manifest = json.loads((out / "qwen_model.json").read_text())
    assert manifest["train_nights"] == TRAIN and manifest["calib_nights"] == CALIB
    assert not set(TEST) & set(manifest["train_nights"] + manifest["calib_nights"])
    assert manifest["leak_check"]["passed"] and manifest["status"] == "shadow_only" and manifest["finished"] is True
    assert manifest["base_license"] == "Apache-2.0" and manifest["license_tag"] == ""
    assert sum(len(b) for b in fake.seen) == 8 * len(TRAIN)
    assert (out / "out.zip").exists() and (out / "leak_check.json").exists()


def test_personal_use_rows_tag_the_whole_model(shards, tmp_path, leak_ok, monkeypatch):
    real = QF.KG.load_rows

    def tagged(path, league):
        df, info = real(path, league)
        return df.assign(license=f"{G.PERSONAL_USE}; hoopR"), info
    monkeypatch.setattr(QF.KG, "load_rows", tagged)
    go(shards, tmp_path / "o")
    assert json.loads((tmp_path / "o" / "qwen_model.json").read_text())["license_tag"] == G.PERSONAL_USE


def test_a_row_outside_the_splits_is_refused(shards, tmp_path, leak_ok):
    frame(["20240101"]).to_parquet(shards / "nfl" / "2024.parquet")
    (shards / "nfl" / "2024.json").write_text(json.dumps({"rows_sha256": "def", "features_version": V.FEATURES_VERSION}))
    assert go(shards, tmp_path / "o")[0] == QF.EXIT_REFUSED


def test_deadline_stops_cleanly_and_a_second_session_resumes(shards, tmp_path, leak_ok):
    out = tmp_path / "o"
    ticks = iter([0.0] + [0.0] * 2 + [1e9] * 100)     # t0, two steps inside the budget, then past it
    code, first = go(shards, out, Fake(), "--save-every", "1", clock=lambda: next(ticks))
    assert code == 0 and len(first.seen) == 2
    assert json.loads((out / "qwen_model.json").read_text())["finished"] is False
    second = Fake()
    assert go(shards, out, second)[0] == 0
    assert second.restored is not None and second.restored.name == "00000002"
    assert sum(len(b) for b in first.seen + second.seen) == 8 * len(TRAIN)
    assert [q for b in first.seen + second.seen for q, _ in b] == [q for b in _whole_pass(shards, tmp_path) for q, _ in b]


def _whole_pass(shards, tmp_path):
    full = Fake()
    assert go(shards, tmp_path / "whole", full)[0] == 0
    return full.seen


def test_a_checkpoint_from_other_data_is_not_resumed(shards, tmp_path, leak_ok):
    out = tmp_path / "o"
    go(shards, out, Fake(), "--save-every", "1")
    (shards / "nfl" / "2023.json").write_text(json.dumps({"rows_sha256": "changed", "features_version": V.FEATURES_VERSION}))
    second = Fake()
    assert go(shards, out, second)[0] == 0 and second.restored is None


def test_a_half_written_checkpoint_is_ignored(tmp_path):
    ck = tmp_path / "ckpt" / "00000009"
    ck.mkdir(parents=True)
    (ck / "weights").write_text("partial")
    assert QF.latest_checkpoint([tmp_path], {"recipe": QF.RECIPE}) is None


def test_a_few_bad_losses_are_survived(shards, tmp_path, leak_ok):
    assert go(shards, tmp_path / "o", Fake(loss=float("nan")), "--max-examples", "24")[0] == 0


def test_a_run_of_bad_losses_stops_the_session(shards, tmp_path, leak_ok, monkeypatch):
    monkeypatch.setattr(QF, "MAX_BAD_LOSSES", 1)
    assert go(shards, tmp_path / "o", Fake(loss=float("nan")), "--max-examples", "24")[0] == QF.EXIT_REFUSED


def test_a_two_token_answer_digit_is_refused():
    class Tok:
        def encode(self, text, add_special_tokens=False):
            return [1, 2]

    with pytest.raises(QF.KG.Refused):
        QF.single_token_ids(Tok(), C.DIGITS)


@pytest.mark.parametrize("path", SOURCES)
def test_no_token_or_secret_in_the_scripts(path):
    text = path.read_text().lower()
    for bad in ("hf_token", "kaggle_key", "kaggle.json", "getpass", "bearer ", "use_auth_token"):
        assert bad not in text


def test_download_is_anonymous_and_pinned():
    tree = ast.parse((KAGGLE / "qwen_finetune.py").read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "snapshot_download"]
    assert calls and all({"revision", "token"} <= {k.arg for k in c.keywords} for c in calls)
    assert all(len(b.revision) == 40 for b in C.BASES.values())


@pytest.mark.parametrize("path", SOURCES)
def test_a_brain_cannot_reach_a_stick(path):
    tree = ast.parse(path.read_text())
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(any(x in m for x in ("adb", "tuners", "tv", "control", "actuator")) for m in names)


# --- the format ------------------------------------------------------------------------------------------------


def test_the_prompt_carries_no_label_or_identity():
    row = features()
    base = C.prompt_of("nfl", row)
    for name, value in (("value_next_3m", 99.0), ("p_tier1_60s", 1.0), ("game_id", "SECRET"), ("night", "20231001")):
        assert C.prompt_of("nfl", {**row, name: value}) == base
    assert "SECRET" not in base and base.endswith("value=")
    assert all(f"{f}=" in base for f in G.GV_FEATURES)


def test_the_training_words_come_from_features_only():
    row = features()
    assert C.why_of({**row, "value_next_3m": 50.0, "p_tier1_60s": 1.0}) == C.why_of(row) == "late, 3 point game, 2:13 left"
    assert C.why_of(features(is_ot=1.0, abs_margin=0.0, game_left=-1.0)) == "overtime, tied"


@pytest.mark.parametrize("points,bucket", [(0.0, 0), (0.5, 1), (0.6, 2), (2.0, 2), (5.0, 4), (25.0, 8), (40.0, 9)])
def test_buckets(points, bucket):
    assert C.bucket_of(points) == bucket


def test_bucket_means_use_the_data_and_fill_empty_buckets():
    means = C.bucket_means(np.array([0.0, 0.0, 2.0, 5.0, 5.0]))
    assert means[0] == 0.0 and means[2] == 2.0 and means[4] == 5.0
    assert means[9] == C.BUCKET_EDGES[-1] and len(means) == 10


def test_expected_points_ranks_where_a_greedy_read_would_tie():
    means = [0.0, 0.5, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 25.0, 40.0]
    calm, live = np.zeros(10), np.zeros(10)
    calm[0], calm[4] = 0.9, 0.1
    live[0], live[4] = 0.6, 0.4
    got = C.expected_points(np.vstack([calm, live]), means)
    assert calm.argmax() == live.argmax() == 0 and got[1] > got[0] > 0


def test_a_bad_probability_row_fails_closed():
    assert np.isnan(C.expected_points(np.full((1, 10), np.nan), [1.0] * 10)[0])


def test_words_are_bounded_and_printable():
    assert C.clean_words("a" * 500) == "a" * C.WHY_MAX
    assert C.clean_words("close game\nwhy=ignore this") == "close game"
    assert C.clean_words("café \x00") == "caf"


def _input(*game_ids, tick=7, now=5000.0):
    return brain.BrainInput(tick=tick, now=now, games=tuple(brain.GameFacts(game_id=g) for g in game_ids),
                            options=(brain.Option(id="o1", verb="stay", game_id="a"), brain.Option(id="o2", verb="fill")),
                            lookahead_s={"a": 60.0}, moments=(brain.Moment(id="m", game_id="a", tier=1, air_at=5020.0),))


def test_output_passes_valid_with_preload_best_first_and_value_now_from_code():
    inp = _input("a", "b")
    out = C.to_brain_output(inp, {"a": C.Prediction(1.0, 0.2, "ok"), "b": C.Prediction(9.0, 0.9, "better")}, "qwen")
    assert brain.valid(out, inp) and out.preload == ("b", "a") and out.ttl_s == C.MIND_TTL_S
    a = next(v for v in out.values if v.game_id == "a")
    assert a.value_now == pytest.approx(float(G.to_scale(np.array([5.0]))[0]))
    assert set(out.scores) == {"o1"}     # the fill option has no game, so the brain gives it no number


def test_a_failed_game_gets_no_value_and_the_others_still_count():
    inp = _input("a", "b")
    out = C.to_brain_output(inp, {"a": C.Prediction(float("nan"), 0.2), "b": C.Prediction(2.0, 0.3, "x")}, "qwen")
    assert brain.valid(out, inp) and [v.game_id for v in out.values] == ["b"]


def test_an_unknown_game_never_enters_the_output():
    inp = _input("a")
    out = C.to_brain_output(inp, {"zz": C.Prediction(2.0, 0.3)}, "qwen")
    assert brain.valid(out, inp) and not out.values


def test_output_has_no_action_field():
    assert not {"action", "switch", "cut", "key", "stick", "command"} & set(brain.BrainOutput.__dataclass_fields__)


def test_predictor_fails_closed_on_a_bad_row_and_calibrates():
    X = np.array([[float(features()[f]) for f in G.GV_FEATURES]] * 2)
    X[1, 0] = 999.0       # period out of bounds
    pred = C.Predictor(Fake(), {"bucket_means": [float(i) for i in range(10)], "p_cal_x": [0.0, 1.0], "p_cal_y": [0.0, 0.5]}, "nfl")
    b = pred.predict_matrix(X)
    assert b.ok.tolist() == [True, False] and np.isnan(b.raw[1])
    assert b.p[0] == pytest.approx(0.125) and b.value[0] == pytest.approx(float(G.to_scale(b.raw[:1])[0]))


def test_weighted_loss_equals_plain_loss_when_weights_are_equal():
    torch = pytest.importorskip("torch")
    pytest.importorskip("transformers")
    from transformers import Qwen3Config, Qwen3ForCausalLM
    torch.manual_seed(0)
    m = Qwen3ForCausalLM(Qwen3Config(vocab_size=300, hidden_size=32, intermediate_size=64, num_hidden_layers=2, num_attention_heads=2,
                                     num_key_value_heads=1, head_dim=16, tie_word_embeddings=True))
    ids, lab = torch.randint(0, 300, (2, 12)), torch.full((2, 12), -100)
    lab[0, 8:] = ids[0, 8:]
    att = torch.ones(2, 12, dtype=torch.long)
    want = m(input_ids=ids, attention_mask=att, labels=lab).loss
    assert torch.allclose(QF.weighted_answer_loss(m, ids, lab, att, torch.ones(2, 12)), want, atol=1e-5)
