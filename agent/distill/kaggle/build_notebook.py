"""Turn a ``# %%`` percent-format script into a Kaggle/Jupyter notebook. Stdlib only.

    python agent/distill/kaggle/build_notebook.py   # teacher_kaggle.py -> teacher_kaggle.ipynb
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def cells_of(src: str) -> list[dict]:
    cells, cur, kind = [], [], None

    def flush():
        if kind is None:
            return
        lines = cur[:]
        while lines and not lines[-1].strip():
            lines.pop()
        if kind == "markdown":
            lines = [ln[2:] if ln.startswith("# ") else ln.lstrip("#") for ln in lines]
        text = [ln + "\n" for ln in lines]
        if text:
            text[-1] = text[-1].rstrip("\n")
        cell = {"cell_type": kind, "metadata": {}, "source": text}
        if kind == "code":
            cell.update(outputs=[], execution_count=None)
        cells.append(cell)

    for line in src.splitlines():
        if line.startswith("# %%"):
            flush()
            kind, cur = ("markdown" if "[markdown]" in line else "code"), []
        elif kind is not None:
            cur.append(line)
    flush()
    return cells


def build(src: Path, dst: Path) -> None:
    nb = {"cells": cells_of(src.read_text()), "nbformat": 4, "nbformat_minor": 5,
          "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
                       "language_info": {"name": "python"},
                       "kaggle": {"accelerator": "nvidiaTeslaT4", "isInternetEnabled": True, "isGpuEnabled": True}}}
    dst.write_text(json.dumps(nb, indent=1) + "\n")


if __name__ == "__main__":
    names = sys.argv[1:] or ["teacher_kaggle"]
    for n in names:
        build(HERE / f"{n}.py", HERE / f"{n}.ipynb")
        print("wrote", HERE / f"{n}.ipynb")
