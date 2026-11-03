# Watchdog v2: where training stopped (2026-11-03 17:10 UTC)

Done on this branch: policy v2 (6 guarded fix actions), data v2 (13 kinds, 6,548 / 275 / 686,
held-out wordings with a leak check), watchdog fixes + tests with fakes, agent/llm_server.py,
agent/train/train_peft.py (fast CPU path), agent/train/peft_to_mlx.py, evaluate.py --backend torch.

Next:
1. Re-run training (the run-1 checkpoints lived in a cloud container and are gone):
   base = /mnt/project-files/models/qwen3-1.7b-4bit (dequantize: `python -m mlx_lm convert
   --hf-path <it> --mlx-path deq -d`, drop "quantization" from deq/config.json), then
   `python agent/train/train_peft.py --base deq --cache /tmp/rzcache --out /tmp/peft_run1 --layers 8
   --rank 8 --alpha 16 --lr 2e-4 --epochs 1.2` (~35 min cache + ~8-11 s/step on 4 CPU cores).
2. Grade on valid with `evaluate.py --backend torch --split valid --model deq --adapter <ckpt>`, pick a
   checkpoint, and only then grade on test. Also grade the bare base for the before table.
3. `peft_to_mlx.py <ckpt> agent/model/watchdog --verify 20 --torch-base deq --mlx-base <4bit>`.
4. The llm_server functional test on MLX CPU (adapter swap, /score, unload) still needs to finish.
5. RESULTS.md, self-review, draft PR into integration/next. A Kaggle GPU run from the distill thread
   (#13) may supply the adapter instead.
POST /api/agent/fix is in (approved by Nick 2026-11-03): refresh feeds, restart feeds/control, re-tune a stick.
