#!/bin/bash
# UNC Longleaf (or any SLURM GPU cluster): one GPU node, the portable trainer (scripts/nfm_train.py, EXP-106..108).
# Nick submits it under his own account: sbatch scripts/slurm/nfm_train.sbatch   (PRESET=300m sbatch ... to override)
# Partition and GPU names are Longleaf's public ones; check `sinfo` on the cluster and edit if they differ.
#SBATCH --job-name=rz-nfm
#SBATCH --partition=a100-gpu
#SBATCH --gres=gpu:1
#SBATCH --qos=gpu_access
#SBATCH --cpus-per-task=16
#SBATCH --mem=96G
#SBATCH --time=11:00:00
#SBATCH --output=rz-nfm-%j.log
set -euo pipefail
PRESET=${PRESET:-150m}
OUT=${OUT:-$HOME/rz-nfm/$PRESET}
cd "${RZ_REPO:-$HOME/redzone-everything}"
python -m pip install --user -q numpy pandas pyarrow torch
# the job resumes from $OUT/ckpt.pt if a previous job hit the time limit; resubmit to continue
python scripts/nfm_train.py --out "$OUT" --build-vocab 2 --leagues nfl,nba,mlb --preset "$PRESET" \
  --ctx 2048 --batch 32 --minutes 640 --exp EXP-107
