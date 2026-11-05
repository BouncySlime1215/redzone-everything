"""[EXP-040] Print the "worth it >= live" evaluation line from data/worth.jsonl.

    python scripts/worth_line.py --live 2026-11-05 2026-11-06 --candidate 2026-11-08

Evaluation only: Nick's end-of-night ratings grade a release and never feed training (redzone/lab/worth.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from redzone.lab.worth import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
