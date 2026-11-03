---
name: mac-load-limits
description: "Nick's M2 (8 GB RAM, ~9 GB free disk) gets crushed by heavy jobs; run one at a time, lightly"
metadata:
  node_type: memory
  type: feedback
  originSessionId: db4845a1-e6cf-4448-8874-21dc8f52883f
  modified: 2026-11-03T15:05:33.128Z
---

Heavy work bogs down Nick's Mac ("This is killing my Mac, please mind that", Nov 3 2026). The machine is an M2 with 8 GB RAM and a 98%-full disk, and it also runs the live RedZone service.

**Why:** A LoRA fine-tune (mlx_lm, Qwen3 1.7B) running alongside parallel scorecards and pytest pushed free memory down to 16%.

**How to apply:**
- Never run model training, the 6-way parallel `cmp.sh` scorecards, the demo, and the full test suite at the same time. Run one heavy job at a time.
- Use `nice -n 15` for long jobs.
- Model training only overnight or when he says so, with small settings (batch 1, fewer layers, shorter sequences).
- Prefer single-league scorecards over running all of them.
- Related: [[no-build-until-explicit-go]]
