# Compute exchange: many free sources acting as one machine

Owner: lane 1. Order: Nick 1:15 AM Nov 5. Pre-registered: EXP-111 (DiLoCo vs single site). Everything shared through
the exchange is **personal-use, not for commercial release**. Secrets such as HF_TOKEN and the Kaggle credentials
are added by Nick in each site's secret store. No agent creates accounts or repos under his name; the code creates
a repo only when it runs with his token, and always as private.

| item | what | where |
|---|---|---|
| (a) one code pack | `factory/rz_factory_code.tar.gz` on kaggle-inputs: nfm_train (TPU via torch_xla, GPU, CPU), tokenizers, exchange | pinned SHA per notebook |
| (b) shards | `scripts/exchange/shards.py push/pull`: `<set>/<league>/<season>.npy+.json` + MANIFEST (sha256, license; an unlicensed shard is refused) | private HF dataset `hf:<owner>/rz-shards`; Kaggle mirror via `kaggle_push.py dataset` |
| (c) checkpoints | `nfm_train.py --exchange hf:<owner>/rz-ckpts --run NAME`: pushed on every 30-min save; a site without a newer local one pulls the newest; a site that is behind never overwrites | private HF model repo |
| (d) launchers | SkyPilot `sky/nfm_train.yaml` (clouds, Kubernetes, SLURM: Longleaf / ACCESS / NAIRR once granted; spot is fine because it resumes); Kaggle `kaggle_push.py kernel`; Colab self-contained notebooks | Nick runs them |
| (e) ledger + router | `docs/compute_ledger.json` (per source: status, A100-equivalents, hours/CU left, reserve, expiry, runs with grades); `router.py route/record`: pretrains go to the biggest active free pool, sweeps and evals to free quota first (Kaggle, then Colab CU above the 100 CU reserve), and anything that costs money is never picked | repo |
| (f) DiLoCo | `scripts/exchange/diloco.py worker`: H local steps, the pseudo-gradient goes to the store, worker 0 applies outer Nesterov and publishes the next global, and stragglers time out; `simulate` = EXP-111 | store |

## Status

- Tested here on local stores, CPU only:
  - shard push and pull with a sha check;
  - license refusal;
  - checkpoint no-backwards;
  - router rules;
  - the DiLoCo outer step;
  - a 2-worker DiLoCo run over a shared directory, 2 rounds.
- Not tested here: HF Hub. This container cannot reach huggingface.co, and Nick's token is needed anyway. The first
  real run is a Colab or Kaggle notebook with HF_TOKEN set as a secret.
- Sources: Kaggle TPU, Kaggle GPU and Colab Pro+ are active. Longleaf, TRC, ACCESS/NAIRR and Azure are "applied"
  in the ledger until Nick says they are granted. Azure is marked costs_money, so the router never picks it.
- Model: the trainer is PyTorch on TPU via torch_xla, not JAX. One code path for every site was the faster way to
  ship. A JAX port is the fallback if torch_xla does not work on Kaggle's TPU image.

## What Nick does (once)

1. Create two private HF repos (one dataset, one model), or let the first run create them with his token. Then
   add HF_TOKEN as a Colab secret, a Kaggle secret, and an env var on the clusters.
2. `pip install skypilot` on his Mac, then `sky check` to show which pools his credentials open.
3. Tell lane 1 when a grant lands, so its ledger line flips to "active".
