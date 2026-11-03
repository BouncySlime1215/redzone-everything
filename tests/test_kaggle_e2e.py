"""The whole Kaggle notebook (teacher_distill.py), end to end on CPU, on a tiny sample:

  code pack -> multi-season build (ESPN fixtures as the archive) -> teacher (echo backend) ->
  student data -> blind teacher pass -> LoRA students on a tiny random Qwen3 -> mlx convert ->
  grade -> out.zip

so a packaging or path bug fails here, not after hours of Kaggle GPU time. Needs torch,
transformers, peft and lightgbm (the CI "kaggle-smoke" job installs CPU builds); skipped otherwise.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
for mod in ("torch", "transformers", "peft", "lightgbm", "tokenizers", "safetensors"):
    pytest.importorskip(mod)

CHAT = ("{% for m in messages %}<|im_start|>{{ m.role }}\n{{ m.content }}<|im_end|>\n{% endfor %}"
        "{% if add_generation_prompt %}<|im_start|>assistant\n{% endif %}")


def tiny_base(d: Path) -> Path:
    """A random 8-layer Qwen3 with a byte-level tokenizer trained on a few lines (no download)."""
    from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers
    from transformers import PreTrainedTokenizerFast, Qwen3Config, Qwen3ForCausalLM
    tok = Tokenizer(models.BPE())
    tok.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tok.decoder = decoders.ByteLevel()
    special = ["<|endoftext|>", "<|im_start|>", "<|im_end|>"]
    tr = trainers.BpeTrainer(vocab_size=600, special_tokens=special, initial_alphabet=pre_tokenizers.ByteLevel.alphabet())
    tok.train_from_iterator(['{"lock_in": "watch", "safe_to_leave": "no"} NFL NBA NHL live drive'] * 50, tr)
    fast = PreTrainedTokenizerFast(tokenizer_object=tok, eos_token="<|im_end|>", pad_token="<|endoftext|>")
    fast.chat_template = CHAT
    cfg = Qwen3Config(vocab_size=len(fast), hidden_size=32, intermediate_size=64, num_hidden_layers=8,
                      num_attention_heads=2, num_key_value_heads=1, head_dim=16, tie_word_embeddings=True,
                      max_position_embeddings=2048)
    Qwen3ForCausalLM(cfg).save_pretrained(d)
    fast.save_pretrained(d)
    return d


def _day(start: str, plus: int) -> str:
    import datetime as dt
    return (dt.datetime.strptime(start, "%Y%m%d") + dt.timedelta(days=plus)).strftime("%Y%m%d")


def archive(d: Path) -> Path:
    from agent.distill.split import BIG_CUTS
    """Fixture summaries copied over 12 nights per league, so the time split has train/valid/test."""
    for j, lg in enumerate(("nfl", "nba", "nhl")):
        src = sorted(FIX.glob(f"espn_{lg}_summary_*.json"))[0]
        (d / lg).mkdir(parents=True)
        v, t, lk = BIG_CUTS[lg]
        nights = [_day(v, -40 + 3 * i) for i in range(5)] + [_day(v, 2 + i) for i in range(3)] + \
            [_day(t, 2 + i) for i in range(3)] + [_day(lk, 1)]          # train, valid, test, locked
        for k, night in enumerate(nights):
            gid = str(9_000_000 + 100 * j + k)
            shutil.copy(src, d / lg / f"summary_{gid}.json")
            (d / lg / f"scoreboard_{night}.json").write_text(json.dumps({"events": [{"id": gid}]}))
    return d


def _wgz(p: Path, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(p, "wt") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def data_pack(d: Path) -> Path:
    """The smallest round-1 data pack the notebook accepts."""
    (d / "data/reasoner").mkdir(parents=True)
    (d / "data/reasoner/system.txt").write_text("You are RedZone's game reasoner. JSON only.")
    for s in ("train", "valid", "test"):
        _wgz(d / f"data/reasoner/rows_{s}.jsonl.gz", [])
    _wgz(d / "data/jump_rows.jsonl.gz", [])
    (d / "data/gold").mkdir()
    (d / "data/gold/lock_gold.jsonl").write_text("")
    wd = d / "data/watchdog"
    wd.mkdir()
    chat = {"messages": [{"role": "system", "content": "watchdog"}, {"role": "user", "content": "stream froze"},
                         {"role": "assistant", "content": '{"action": "none"}'}]}
    for f in ("train", "valid", "test"):
        (wd / f"{f}.jsonl").write_text((json.dumps(chat) + "\n") * 4)
    (wd / "test.labels.jsonl").write_text((json.dumps({"label": "none", "kind": "paused"}) + "\n") * 4)
    (wd / "novel.json").write_text("{}")
    _wgz(d / "teacher_requests.jsonl.gz", [
        {"id": "wd:paused:0", "task": "wd_paraphrase", "kind": "paused", "max_tokens": 50,
         "messages": [{"role": "user", "content": "x"}],
         "followup": {"field": "lines", "messages": [{"role": "user", "content": "classify {item}"}]}}])
    (d / "manifest.json").write_text(json.dumps({"counts": {}}))
    return d


def test_notebook_end_to_end_on_cpu(tmp_path):
    from agent.kaggle.pack_data import code_pack
    inp, work = tmp_path / "input", tmp_path / "working"
    data_pack(inp / "redzone-distill")
    code_pack(ROOT, inp / "rz-code" / "rz_code.tar.gz")
    work.mkdir()
    env = {**os.environ, "RZ_KAGGLE_INPUT": str(inp), "RZ_KAGGLE_WORKING": str(work), "RZ_SMOKE": "1",
           "RZ_FAKE_TEACHER": "1", "RZ_BASE_DIR": str(tiny_base(tmp_path / "base")),
           "RZ_BUILD_ARCHIVE": str(archive(tmp_path / "archive")), "CUDA_VISIBLE_DEVICES": ""}
    p = subprocess.run([sys.executable, str(ROOT / "agent/kaggle/teacher_distill.py")], env=env, cwd=tmp_path,
                       capture_output=True, text=True, timeout=1700)
    log = p.stdout + p.stderr
    assert p.returncode == 0, log[-4000:]
    z = zipfile.ZipFile(work / "out.zip")
    names = set(z.namelist())
    for f in ("report.md", "teacher_out.jsonl", "build_report.json", "split_plan.json",
              "game-reasoner/mlx/adapters.safetensors", "watchdog/mlx/adapters.safetensors"):
        blog = (work / "build.log").read_text()[-3000:] if (work / "build.log").exists() else ""
        assert f in names, (f, sorted(names), log[-2000:], blog)
    plan = json.loads(z.read("split_plan.json"))
    assert all("locked" in m.values() and "train" in m.values() for m in plan.values() if m)
    build = json.loads(z.read("build_report.json"))
    assert build["total_rows"] > 0
