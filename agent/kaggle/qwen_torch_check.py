"""The real-torch check for QF.weighted_answer_loss: run it on Kaggle (or any box with torch + transformers), not in pytest.

    python agent/kaggle/qwen_torch_check.py        # exit 0 = equal weights give the plain loss; exit 2 = no torch here

Why: tests/test_qwen_finetune.py covers the same arithmetic with a numpy stand-in for torch (the Mac has no torch and must not
get it, 8 GB). This is the one check against the real thing, so it must not be a skipped test.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    try:
        import torch
        from transformers import Qwen3Config, Qwen3ForCausalLM
    except ImportError as e:
        print(f"no torch/transformers here: {e}")
        return 2
    from agent.kaggle import qwen_finetune as QF
    torch.manual_seed(0)
    m = Qwen3ForCausalLM(Qwen3Config(vocab_size=300, hidden_size=32, intermediate_size=64, num_hidden_layers=2, num_attention_heads=2,
                                     num_key_value_heads=1, head_dim=16, tie_word_embeddings=True))
    ids, lab = torch.randint(0, 300, (2, 12)), torch.full((2, 12), -100)
    lab[0, 8:] = ids[0, 8:]
    att = torch.ones(2, 12, dtype=torch.long)
    want = m(input_ids=ids, attention_mask=att, labels=lab).loss
    got = QF.weighted_answer_loss(m, ids, lab, att, torch.ones(2, 12))
    ok = bool(torch.allclose(got, want, atol=1e-5))
    print(f"weighted_answer_loss {float(got):.6f} vs plain {float(want):.6f}: {'ok' if ok else 'MISMATCH'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
