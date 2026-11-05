# EXP-109 speedrun recipe A/B (EXP-103 size, 18.6M params): results

Thu Nov 5, 2026, finished 5:33 AM. Colab A100, pack bd1f807, EXP-103 data (nflverse NFL, NBA.com cdn NBA,
Retrosheet MLB; vocab v1) and split, ONE seed, 15 min of training per arm. Session 2.27 h, ~29.5 CU.

| Arm | Recipe | Best valid | NBA | NFL | MLB | tok/s | Hours to 0.334 | vs base |
|---|---|---|---|---|---|---|---|---|
| A_base | AdamW, cosine | 0.3263 | 0.5727 | 0.5588 | 0.2579 | 393k | 0.095 | 1.0x |
| B_muon | +Muon | 0.3302 | 0.5844 | 0.5719 | 0.2598 | 318k | 0.103 | 0.92x |
| C_muon_wsd | +Muon +WSD | 0.3285 | 0.5797 | 0.5658 | 0.2588 | 360k | 0.129 | 0.74x |
| D_bundle | C + QK-norm, ReLU², untied, softcap, value-emb | 0.3262 | 0.5718 | 0.5565 | 0.2580 | 223k | 0.083 | 1.15x |
| **E_compile** | **D + torch.compile** | **0.3234** | **0.5670** | **0.5396** | **0.2565** | 560k | **0.046** | **2.07x** |
| F_docmask | E + doc masking | — | — | — | — | — | — | did not report |
| G_bundle_only | base + bundle (no Muon) | 0.3284 | 0.5775 | 0.5602 | 0.2593 | 231k | 0.121 | 0.79x |
| H_compile_only | base + torch.compile | 0.3256 | 0.5732 | 0.5606 | 0.2572 | 679k | 0.063 | 1.50x |

## Read
- **Winner: E (Muon + WSD + modded-nanoGPT bundle + torch.compile).** Best loss overall and per league (NFL
  0.5396 vs 0.5588 base), and **2.07x faster** to EXP-103's 0.334.
- **torch.compile is the single biggest free win:** 1.5x by itself (H), with the highest throughput (679k tok/s).
- **Muon alone hurt at 18.6M (B), and so did the bundle alone (G).** Only together, plus compile, do they beat
  base. That matches the independent 2025-26 benchmarks (~1.1-1.4x claims, sensitive to tuning).
- On an A100 the base hits EXP-103's L4 result (0.334) in ~6 min instead of 45.
- **Caveats:** one seed; loss gaps of 0.002-0.003 may sit inside seed noise; 15 min arms (short). F (doc
  masking) did not report. EXP-110 (FIM-PP zero-shot) did not run: `pip install git+OpenFIM` failed.

## Next
1. Ladder (60m/150m/300m) uses recipe E. 2 seeds on A vs E to confirm the gap is beyond noise.
2. Fix F (doc-mask / FlexAttention) and the EXP-110 install (pin a wheel or vendor OpenFIM).
