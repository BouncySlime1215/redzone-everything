"""run_students' memory savers keep the training maths the same (V4 ran out of memory on a T4)."""
from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("transformers")
peft = pytest.importorskip("peft")

from agent.kaggle import run_students as rs  # noqa: E402


def _tiny():
    from transformers import Qwen3Config, Qwen3ForCausalLM
    torch.manual_seed(0)
    cfg = Qwen3Config(vocab_size=300, hidden_size=32, intermediate_size=64, num_hidden_layers=2,
                      num_attention_heads=2, num_key_value_heads=1, head_dim=16, tie_word_embeddings=True)
    return Qwen3ForCausalLM(cfg)


def _batch():
    ids = torch.randint(0, 300, (2, 12))
    lab = torch.full((2, 12), -100)
    lab[0, 8:] = ids[0, 8:]                  # answer tokens only
    lab[1, 5:9] = ids[1, 5:9]
    att = torch.ones(2, 12, dtype=torch.long)
    att[1, 9:] = 0
    return ids, lab, att


def test_answer_loss_matches_the_full_logits_loss():
    m = _tiny()
    ids, lab, att = _batch()
    want = m(input_ids=ids, attention_mask=att, labels=lab).loss
    assert torch.allclose(rs.answer_loss(m, ids, lab, att), want, atol=1e-5)
    p = peft.get_peft_model(m, peft.LoraConfig(r=4, lora_alpha=8, target_modules=["q_proj", "v_proj"],
                                               task_type="CAUSAL_LM"))
    want = p(input_ids=ids, attention_mask=att, labels=lab).loss
    got = rs.answer_loss(p, ids, lab, att)
    assert torch.allclose(got, want, atol=1e-5)
    got.backward()
    assert any(q.grad is not None for n, q in p.named_parameters() if "lora_" in n)


def test_effective_batch_is_eight():
    assert rs.BATCH * rs.ACCUM == 8 and rs.BATCH <= 2


def test_recipe_d19_defaults(monkeypatch):
    import importlib
    for k in ("RZ_LORA_RANK", "RZ_LORA_LAYERS", "RZ_LR", "RZ_BASE_DTYPE"):
        monkeypatch.delenv(k, raising=False)
    m = importlib.reload(rs)
    assert (m.RANK, m.LAST_LAYERS, m.LR, m.BASE_DTYPE) == (None, None, 2e-4, "float16")
    assert (m.rank_for("game-reasoner"), m.rank_for("watchdog")) == (32, 16)      # [V7] D19 r16-32
    assert m.CONSTRAINED and m.MAX_NEW >= 112
