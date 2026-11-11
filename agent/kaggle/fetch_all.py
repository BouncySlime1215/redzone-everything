"""Launch one fetch by name: `python agent/kaggle/fetch_all.py <source> [options]` (or `list`).

Why: data job 10: Nick launches every fetch with the same one-liner, only the source name changes.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.kaggle import data_fetch as df  # noqa: E402

SOURCES = ("kuairand27k", "kuairec", "zvuk", "gdelt", "wikimedia", "backblaze", "lichess", "criteo", "azure")


def main(argv: list[str]) -> int:
    if not argv or argv[0] == "list" or argv[0] not in SOURCES:
        print("sources:", " ".join(SOURCES))
        return 0 if argv and argv[0] == "list" else 2
    spec = importlib.import_module(f"agent.kaggle.fetch_{argv[0]}").SPEC
    return df.cli(spec, argv[1:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
