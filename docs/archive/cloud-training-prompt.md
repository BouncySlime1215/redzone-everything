Repo: github.com/BouncySlime1215/redzone-everything, branch `codex/resume-redzone`. Work on a new branch `agent-training` off it.

Goal: train the RedZone watchdog's local model so it picks the right action for errors it has never seen. Then hand back a small LoRA adapter that runs on my Mac with MLX (M2, 8 GB).

Read these first:
- `agent/policy.py`: the actions, the hard rules, `SYSTEM` (the prompt) and `parse_answer`.
- `agent/train/make_data.py`: the data generator, with a "teacher" for new kinds of errors.
- `agent/train/evaluate.py`: grading on held-out wordings.
- `agent/train/lora.yaml`: the training settings.

Base model: `mlx-community/Qwen3-1.7B-4bit`. Today's numbers: 22.8% off the shelf, 50.0% after a 150-step LoRA on 330 examples. It is worst on "person" errors (1 of 12, should be notify) and "stick_link" errors (1 of 12, should be reconnect_stick or notify).

Do this:
1. **Bigger and better data.** Extend `make_data.py`:
   - At least 25 wordings per kind (core_loop, stick_link, storage, person, upstream, cosmetic).
   - Add realistic tracebacks, mixed noise, and several unknown errors in one status.
   - Make 3,000 or more training examples.
   - Keep whole wordings held out for the test set: no test wording may appear in training, even paraphrased.
   - Keep the same labels: `agent.policy.rules()` first, then `teach_novel()`.
   - Add a "kind" for anything new, and document the judgment in `teach_novel`.
2. **Train with MLX if it runs on this machine.** Try `pip install "mlx[cuda]"` with a GPU, or `"mlx[cpu]"`, plus `mlx-lm`. Then run `mlx_lm lora -c agent/train/lora.yaml`. Tune iters, layers and learning rate on the valid set. This produces the adapter in the exact format my Mac loads.
   - Fallback only if MLX won't run: train a LoRA with Hugging Face PEFT on `Qwen/Qwen3-1.7B`.
   - Same chat format: `apply_chat_template` with `enable_thinking=False`, and train only on the assistant answer.
   - Write `agent/train/peft_to_mlx.py`, which converts the PEFT adapter into mlx_lm's `adapters.safetensors` + `adapter_config.json` (key names and transposes).
   - Prove the conversion by matching outputs on 20 prompts between the merged PEFT model and the converted adapter loaded with mlx_lm, if MLX CPU runs at all. Otherwise, say plainly that the conversion is unverified.
3. **Grade.** Run `agent/train/evaluate.py` on the held-out test.
   - Target: **90% or better overall, and 80% or better on every kind**, with 0 unusable answers.
   - Report a per-kind table before and after.
   - If you can't reach the target, say what limits it. Don't tune on the test set.
4. **Deliver.**
   - Commit the generator, any scripts, and `agent/model/adapter/adapters.safetensors` + `adapter_config.json` (force-add; it's gitignored, about 10–20 MB). Don't commit checkpoints or the base model.
   - Write `agent/train/RESULTS.md` covering: data sizes, settings, final scores per kind, time taken, and how to install. Installing should only be a pull, then removing `--no-model` from `agent/com.redzone.agent.plist`.
   - Push the branch and open a PR into `codex/resume-redzone`.

Rules:
- $0. Use only free compute. Ask before anything that costs money.
- Don't change the app's code outside `agent/`.
- Don't change the action set or what the hard rules decide.
- No secrets in the repo.
- End every commit message with: Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
