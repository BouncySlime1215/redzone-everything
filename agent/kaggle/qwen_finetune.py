#!/usr/bin/env python3
"""Kaggle GPU: fine-tune a 1.7 B Qwen3 on the leak-checked GameValue shards to give the brain contract's numbers and words.

    python agent/kaggle/qwen_finetune.py --shards /kaggle/input/value-shards/value_shards --out /kaggle/working/qwen --leagues nfl

Why: Nick wants a 1-2 B parameter brain (the-mind sections 3 and 8), built by fine-tuning an open model. A checkpoint must
survive Kaggle's 12 h cap and no leaked row may ever reach it, or qwen_eval.py's capture number means nothing.

Accelerator GPU T4, Internet On. One T4 trains; the Mac never does (exit 3). No token: both bases are public and ungated,
and the download passes token=False.

Order: fetch the raw seasons, run scripts/value_leak_check.py with the raw audit on these exact shards (any failure exits 2
before a row is read), read the train + calib nights only (the test nights are dropped here and never trained on), then one
pass of LoRA over at most --max-examples rows. Every --save-every steps a checkpoint lands atomically under <out>/ckpt; a new
session resumes from it, or from a prior version's output in /kaggle/input, only when data fingerprint and recipe match.
At the deadline it stops cleanly and still calibrates and exports, with "finished": false in the manifest.

Writes <out>/model/peft, <out>/model/mlx, <out>/qwen_model.json, <out>/leak_check.json, <out>/out.zip.
Not done here: forward-chaining CV. One fit costs a session, five do not fit the weekly quota, so the manifest says so and
this model cannot pass the release gate on its own. status stays shadow_only.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import os
import random
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from agent.kaggle import qwen_common as C  # noqa: E402
from redzone.noggin import gamevalue as G  # noqa: E402
from redzone.noggin import value_shards as V  # noqa: E402
from redzone.noggin.predict_fit import isotonic  # noqa: E402
from scripts import kaggle_gamevalue as KG  # noqa: E402
from scripts import value_leak_check as LC  # noqa: E402

EXIT_REFUSED, EXIT_MAC = 2, 3
RECIPE = "q1"                # bump when the prompt, buckets or loss change: an older checkpoint is never resumed
SEED = 7
RANK, LR = 32, 2e-4          # run_students recipe D19, the game-reasoner's rank
TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
BATCH = 8
SCORE_BATCH = 64
MAX_EXAMPLES = 50_000
CALIB_ROWS = 20_000
WHY_WEIGHT = 0.1             # mean loss over ~20 easy word tokens would otherwise drown the two number tokens
WORDS_MAX_NEW = 28
MAX_BAD_LOSSES = 20
KEEP_CKPTS = 2
SESSION_H, RESERVE_H = 11.5, 1.0   # Kaggle's cap is 12 h; the reserve pays for calibration and the export
MIN_TRANSFORMERS = (4, 51)   # Qwen3 raises KeyError: 'qwen3' below this (model card)


def single_token_ids(tok, words) -> list[int]:
    """Numbers are read off one logit position, so each option must be exactly one token. Refuses rather than misreads."""
    out = []
    for w in words:
        ids = tok.encode(w, add_special_tokens=False)
        if len(ids) != 1:
            raise KG.Refused(f"{w!r} is {len(ids)} tokens in this tokenizer; the answer format needs one")
        out.append(ids[0])
    return out


def weighted_answer_loss(model, ids, lab, att, wt):
    """run_students.answer_loss with a weight per answer token. Logits only where a label is, to spare T4 memory."""
    import torch
    lm = model.get_base_model() if hasattr(model, "get_base_model") else model
    hidden = lm.model(input_ids=ids, attention_mask=att).last_hidden_state
    tgt = lab[:, 1:]
    keep = tgt != -100
    logits = lm.lm_head(hidden[:, :-1][keep]).float()
    losses = torch.nn.functional.cross_entropy(logits, tgt[keep], reduction="none")
    weights = wt[:, 1:][keep].to(losses.dtype)
    return (losses * weights).sum() / weights.sum()


def prepare_base(name: str, work: Path) -> Path:
    """The pinned checkpoint as a plain Hugging Face directory. The 4-bit base is rebuilt into the weights the Mac runs."""
    from huggingface_hub import snapshot_download
    base = C.BASES[name]
    src = Path(snapshot_download(base.repo, revision=base.revision, token=False))
    if not base.dequantize:
        return src
    out = work / "base-deq"
    if not (out / "model.safetensors").exists():
        from agent.kaggle.dequant import convert
        print("dequantize:", convert(src, out), flush=True)
    return out


class TorchLora:
    """The real model, on one GPU. Imported only when a session runs, so tests and the Mac never load torch."""

    def __init__(self, base_dir: Path, total_steps: int = 1, adapter: Path | None = None):
        import torch
        import transformers
        from peft import LoraConfig, PeftModel, get_peft_model
        from transformers import AutoModelForCausalLM, AutoTokenizer
        if tuple(int(x) for x in transformers.__version__.split(".")[:2]) < MIN_TRANSFORMERS:
            raise KG.Refused(f"transformers {transformers.__version__} cannot load Qwen3; {MIN_TRANSFORMERS} or newer")
        random.seed(SEED)
        np.random.seed(SEED)
        torch.manual_seed(SEED)
        self.torch = torch
        self.dev = "cuda" if torch.cuda.is_available() else "cpu"
        self.amp = {"device_type": self.dev, "dtype": torch.float16, "enabled": self.dev == "cuda"}
        self.tok = AutoTokenizer.from_pretrained(base_dir)
        self.tok.padding_side = "left"
        if self.tok.pad_token_id is None:
            self.tok.pad_token = self.tok.eos_token
        self.digit_ids = single_token_ids(self.tok, C.DIGITS)
        self.yes_no_ids = single_token_ids(self.tok, (C.YES, C.NO))
        base = AutoModelForCausalLM.from_pretrained(base_dir, torch_dtype=torch.float16, attn_implementation="sdpa").to(self.dev)
        self.n_layers = base.config.num_hidden_layers
        if adapter is not None:
            self.model = PeftModel.from_pretrained(base, adapter).eval()
            return
        base.config.use_cache = False
        base.gradient_checkpointing_enable()
        base.enable_input_require_grads()
        self.model = get_peft_model(base, LoraConfig(r=RANK, lora_alpha=2 * RANK, lora_dropout=0.05, target_modules=TARGETS,
                                                     task_type="CAUSAL_LM"))
        params = [p for p in self.model.parameters() if p.requires_grad]
        for p in params:
            p.data = p.data.float()      # float32 LoRA on a float16 base: pure float16 can overflow in Qwen3
        self.params = params
        self.opt = torch.optim.AdamW(params, lr=LR, weight_decay=0.0)
        self.scaler = torch.amp.GradScaler(self.dev, enabled=self.dev == "cuda")
        self.sched = torch.optim.lr_scheduler.LambdaLR(
            self.opt, lambda s: min(1.0, (s + 1) / 30) * max(0.05, 1 - s / max(1, total_steps)))

    def _encode(self, prompt: str, parts: tuple[str, str, str]):
        digit, yes_no, why = parts
        enc = lambda s: self.tok.encode(s, add_special_tokens=False)  # noqa: E731
        p = enc(prompt)
        middle, tail = enc(C.TIER1_PREFIX), enc(C.WHY_PREFIX + why) + [self.tok.eos_token_id]
        answer = single_token_ids(self.tok, (digit,)) + middle + single_token_ids(self.tok, (yes_no,)) + tail
        weights = [1.0] + [WHY_WEIGHT] * len(middle) + [1.0] + [WHY_WEIGHT] * len(tail)
        return p + answer, [-100] * len(p) + answer, [0.0] * len(p) + weights

    def train_step(self, batch: list[tuple[str, tuple[str, str, str]]]) -> float:
        torch = self.torch
        rows = [self._encode(q, parts) for q, parts in batch]
        n = max(len(r[0]) for r in rows)
        ids = torch.full((len(rows), n), self.tok.pad_token_id)
        lab, att = torch.full((len(rows), n), -100), torch.zeros((len(rows), n), dtype=torch.long)
        wt = torch.zeros((len(rows), n))
        for k, (a, b, w) in enumerate(rows):
            ids[k, :len(a)], lab[k, :len(b)], att[k, :len(a)], wt[k, :len(w)] = (torch.tensor(a), torch.tensor(b), 1, torch.tensor(w))
        self.model.train()
        with torch.autocast(**self.amp):
            loss = weighted_answer_loss(self.model, ids.to(self.dev), lab.to(self.dev), att.to(self.dev), wt.to(self.dev))
        if not torch.isfinite(loss):
            self.opt.zero_grad(set_to_none=True)
            return float("nan")
        self.scaler.scale(loss).backward()
        self.scaler.unscale_(self.opt)
        torch.nn.utils.clip_grad_norm_(self.params, 1.0)
        self.scaler.step(self.opt)
        self.scaler.update()
        self.opt.zero_grad(set_to_none=True)
        self.sched.step()
        return float(loss.item())

    def _last_logits(self, prompts: list[str], ids: list[int]):
        torch = self.torch
        self.model.eval()
        out = []
        for i in range(0, len(prompts), SCORE_BATCH):
            enc = self.tok(prompts[i:i + SCORE_BATCH], return_tensors="pt", padding=True, add_special_tokens=False).to(self.dev)
            with torch.no_grad(), torch.autocast(**self.amp):
                logits = self.model(**enc).logits[:, -1, ids].float()
            out.append(torch.softmax(logits, dim=1).cpu().numpy())
        return np.concatenate(out) if out else np.zeros((0, len(ids)))

    def digit_probs(self, prompts: list[str]) -> np.ndarray:
        return self._last_logits(prompts, self.digit_ids)

    def yes_probs(self, prompts: list[str]) -> np.ndarray:
        return self._last_logits(prompts, self.yes_no_ids)[:, 0]

    def words(self, prompts: list[str]) -> list[str]:
        torch = self.torch
        self.model.eval()
        out = []
        for i in range(0, len(prompts), SCORE_BATCH):
            enc = self.tok(prompts[i:i + SCORE_BATCH], return_tensors="pt", padding=True, add_special_tokens=False).to(self.dev)
            with torch.no_grad(), torch.autocast(**self.amp):
                got = self.model.generate(**enc, do_sample=False, max_new_tokens=WORDS_MAX_NEW, pad_token_id=self.tok.pad_token_id)
            out += [C.clean_words(t) for t in self.tok.batch_decode(got[:, enc["input_ids"].shape[1]:], skip_special_tokens=True)]
        return out

    def save(self, path: Path) -> None:
        self.model.save_pretrained(path)
        self.torch.save({"opt": self.opt.state_dict(), "sched": self.sched.state_dict(), "scaler": self.scaler.state_dict()},
                        path / "trainer.pt")

    def restore(self, path: Path) -> None:
        from peft import set_peft_model_state_dict
        from safetensors.torch import load_file
        set_peft_model_state_dict(self.model, load_file(str(path / "adapter_model.safetensors"), device=self.dev))
        state = self.torch.load(path / "trainer.pt", map_location=self.dev)
        self.opt.load_state_dict(state["opt"])
        self.sched.load_state_dict(state["sched"])
        self.scaler.load_state_dict(state["scaler"])

    def export(self, path: Path) -> None:
        from agent.train.peft_to_mlx import convert
        self.model.save_pretrained(path / "peft")
        convert(path / "peft", path / "mlx", self.n_layers)


def fingerprint(shards: Path) -> str:
    """Names plus recorded row hashes of every shard, so a checkpoint or report is tied to the bytes the leak check passed."""
    h = hashlib.sha256()
    for side in sorted(Path(shards).glob("*/*.json")):
        info = json.loads(side.read_text())
        h.update(f"{side.parent.name}/{side.stem}:{info.get('rows_sha256')};".encode())
    return h.hexdigest()


def leak_gate(shards: Path, out: Path, leagues: list[str], archive: Path | None, personal_nba: bool, raw: Path, log=print) -> dict:
    """Runs the check on these shards and writes leak_check.json. Refused on any failure, before a single row is read."""
    KG.fetch_raw(shards, raw, leagues, personal_nba)
    rep = LC.check_dir(shards, archive, personal_nba, raw)
    if not rep.fails and not rep.checked.get("raw_rows"):
        rep.fail("raw_audit", "no row was checked against the raw source")
    if rep.fails:
        for line in rep.fails[:50]:
            log("LEAK CHECK FAIL", line, flush=True)
        raise KG.Refused(f"the leak check found {len(rep.fails)} problems; nothing was trained")
    result = {"passed": True, "fingerprint": fingerprint(shards), **rep.checked}
    (out / "leak_check.json").write_text(json.dumps(result, indent=1, sort_keys=True))
    log("leak check passed", json.dumps(result), flush=True)
    return result


def load_splits(shards: Path, leagues: list[str]) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Train and calib rows of every league, nothing from a test night. Refused when splits.json does not cover the rows."""
    splits = json.loads((Path(shards) / "splits.json").read_text())
    train, calib, test_nights = [], [], []
    for league in leagues:
        df, _info = KG.load_rows(shards, league)
        s = splits.get(league) or {}
        tr, ca, te = (list(s.get(k) or []) for k in ("train", "calib", "test"))
        if not tr or not ca or not te or not set(df.night) <= set(tr + ca + te):
            raise KG.Refused(f"{league}: splits.json does not cover the rows")
        train.append(df[df.night.isin(set(tr))].assign(league=league))
        calib.append(df[df.night.isin(set(ca))].assign(league=league))
        test_nights += te
    return pd.concat(train, ignore_index=True), pd.concat(calib, ignore_index=True), sorted(set(test_nights))


def examples(df: pd.DataFrame, max_examples: int) -> pd.DataFrame:
    """At most max_examples rows, the same ones every session, so a resumed run continues the same pass."""
    order = np.random.default_rng(SEED).permutation(len(df))[:max_examples]
    return df.iloc[order].reset_index(drop=True)


def latest_checkpoint(roots: list[Path], stamp: dict) -> Path | None:
    """The newest finished checkpoint from an earlier run of the same data and recipe. `state.json` is written last."""
    found = []
    for root in roots:
        for state in glob.glob(str(root / "**" / "ckpt" / "*" / "state.json"), recursive=True):
            try:
                info = json.loads(Path(state).read_text())
            except (OSError, ValueError):
                continue
            if all(info.get(k) == v for k, v in stamp.items()):
                found.append((int(info["step"]), Path(state).parent))
    return max(found, key=lambda x: x[0])[1] if found else None


def save_checkpoint(backend, out: Path, step: int, stamp: dict) -> None:
    root = out / "ckpt"
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f".tmp-{step}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    backend.save(tmp)
    (tmp / "state.json").write_text(json.dumps({**stamp, "step": step}))
    final = root / f"{step:08d}"
    shutil.rmtree(final, ignore_errors=True)
    os.replace(tmp, final)
    for old in sorted(p for p in root.iterdir() if p.name[0] != ".")[:-KEEP_CKPTS]:
        shutil.rmtree(old, ignore_errors=True)


def _pairs(rows: pd.DataFrame, start: int, stop: int):
    out = []
    for r in rows.iloc[start:stop].to_dict("records"):
        out.append((C.prompt_of(r["league"], r), C.answer_parts(r["value_next_3m"], r["p_tier1_60s"], C.why_of(r))))
    return out


def train_loop(backend, rows: pd.DataFrame, out: Path, stamp: dict, deadline: float, save_every: int,
               resume: Path | None, clock=time.time, log=print) -> tuple[int, int, bool]:
    """(steps done, steps planned, finished). A non-finite loss streak stops the run; the last good checkpoint stays."""
    total = math.ceil(len(rows) / BATCH)
    step = 0
    if resume is not None:
        backend.restore(resume)
        step = int(json.loads((resume / "state.json").read_text())["step"])
        log(f"resumed from {resume} at step {step}/{total}", flush=True)
    bad = 0
    while step < total:
        if clock() >= deadline:
            log(f"time cap at step {step}/{total}", flush=True)
            break
        loss = backend.train_step(_pairs(rows, step * BATCH, (step + 1) * BATCH))
        if not math.isfinite(loss):
            bad += 1
            if bad > MAX_BAD_LOSSES:
                raise KG.Refused(f"{bad} non-finite losses by step {step}; the last good checkpoint is kept")
            step += 1
            continue
        step += 1
        if step % 50 == 0:
            log(f"step {step}/{total} loss {loss:.4f}", flush=True)
        if step % save_every == 0:
            save_checkpoint(backend, out, step, stamp)
    if step:
        save_checkpoint(backend, out, step, stamp)
    return step, total, step >= total


def fit_calibration(backend, calib: pd.DataFrame) -> tuple[list[float], list[float]]:
    """Isotonic on the calib nights, which no gradient step saw. Same digit-then-yes read the grader uses."""
    rows = calib.iloc[np.random.default_rng(SEED).permutation(len(calib))[:CALIB_ROWS]].to_dict("records")
    prompts = [C.prompt_of(r["league"], r) for r in rows]
    digits = [C.DIGITS[int(i)] for i in np.asarray(backend.digit_probs(prompts)).argmax(axis=1)]
    yes = np.asarray(backend.yes_probs([C.after_digit(q, d) for q, d in zip(prompts, digits)]), float)
    x, y = isotonic(yes, np.array([r["p_tier1_60s"] for r in rows], float))
    return [float(v) for v in x], [float(v) for v in y]


def run(a, backend_factory, clock=time.time, log=print) -> dict:
    t0 = clock()
    leagues = [x for x in a.leagues.split(",") if x]
    unknown = [x for x in leagues if x not in G.GV_LEAGUES]
    if not leagues or unknown:
        raise KG.Refused(f"only {G.GV_LEAGUES} have a GameValue model, got {a.leagues}")
    for lg in leagues:
        status, why = V.public_status(lg, a.personal_nba)
        if status != "ok":
            raise KG.Refused(f"{lg}: {why}")
    a.out.mkdir(parents=True, exist_ok=True)
    leak = leak_gate(a.shards, a.out, leagues, a.archive, a.personal_nba, a.raw, log)
    train, calib, test_nights = load_splits(a.shards, leagues)
    if train.empty or calib.empty:
        raise KG.Refused("no train or calib rows")
    rows = examples(train, a.max_examples)
    base = C.BASES[a.base]
    stamp = {"fingerprint": leak["fingerprint"], "recipe": RECIPE, "base": a.base, "max_examples": a.max_examples}
    total_steps = math.ceil(len(rows) / BATCH)
    backend = backend_factory(a.base, a.out, total_steps)
    resume = latest_checkpoint([a.out, *a.resume_from], stamp)
    deadline = t0 + (a.session_h - RESERVE_H) * 3600.0
    done, total, finished = train_loop(backend, rows, a.out, stamp, deadline, a.save_every, resume, clock, log)
    x, y = fit_calibration(backend, calib)
    model_dir = a.out / "model"
    model_dir.mkdir(parents=True, exist_ok=True)
    backend.export(model_dir)
    manifest = {"schema": C.SCHEMA, "status": C.STATUS, "recipe": RECIPE, "base": a.base, "base_repo": base.repo, "base_revision": base.revision,
                "base_license": base.license, "mac_4bit_available": base.has_mlx_4bit, "leak_check": leak, "fingerprint": leak["fingerprint"],
                "leagues": leagues, "bucket_edges": list(C.BUCKET_EDGES), "bucket_means": C.bucket_means(train.value_next_3m.to_numpy(float)),
                "p_cal_x": x, "p_cal_y": y, "steps_done": done, "steps_planned": total, "finished": finished, "rows_trained": min(done * BATCH, len(rows)),
                "train_nights": sorted(set(train.night.astype(str))), "calib_nights": sorted(set(calib.night.astype(str))), "test_nights_held_out": test_nights,
                "cv": "not run: one fit per 12 h session; this model cannot pass the release gate alone", **KG.license_fields(pd.concat([train, calib]))}
    (a.out / "qwen_model.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))
    keep = a.out / "bundle"
    shutil.rmtree(keep, ignore_errors=True)
    keep.mkdir()
    shutil.copytree(model_dir, keep / "model")
    for f in ("qwen_model.json", "leak_check.json"):
        shutil.copy(a.out / f, keep / f)
    shutil.make_archive(str(a.out / "out"), "zip", keep)
    shutil.rmtree(keep, ignore_errors=True)
    shutil.rmtree(a.out / "base-deq", ignore_errors=True)     # 3.4 GB, rebuilt next session
    return manifest


def _real_backend(base: str, out: Path, total_steps: int) -> TorchLora:
    return TorchLora(prepare_base(base, out), total_steps)


def parse(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--shards", type=Path, required=True, help="the value_shards folder (manifest.json, splits.json, <league>/)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--leagues", default="nfl")
    ap.add_argument("--personal-nba", action="store_true", help="accept hoopR NBA rows; tags the whole model personal-use (off by default)")
    ap.add_argument("--archive", type=Path, default=None, help="data/archive, needed only if the shards hold archive rows")
    ap.add_argument("--raw", type=Path, default=Path(os.environ.get("RZ_RAW", "/kaggle/working/raw")))
    ap.add_argument("--base", choices=sorted(C.BASES), default=C.DEFAULT_BASE)
    ap.add_argument("--max-examples", type=int, default=MAX_EXAMPLES)
    ap.add_argument("--session-h", type=float, default=SESSION_H)
    ap.add_argument("--save-every", type=int, default=500)
    ap.add_argument("--resume-from", type=Path, nargs="*", default=[Path("/kaggle/input")], help="earlier versions' outputs to look in")
    return ap.parse_args(argv)


def main(argv=None, backend_factory=None, clock=time.time, log=print) -> int:
    a = parse(argv)
    if backend_factory is None and sys.platform == "darwin":
        print("REFUSED: training never runs on the Mac. Run this on a Kaggle GPU.", flush=True)
        return EXIT_MAC
    try:
        manifest = run(a, backend_factory or _real_backend, clock, log)
    except (KG.Refused, G.ModelRejected, V.SourceNotCleared) as e:
        print(f"REFUSED: {e}", flush=True)
        return EXIT_REFUSED
    print(json.dumps({k: manifest[k] for k in ("steps_done", "steps_planned", "finished", "rows_trained", "license_tag", "base_repo")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
