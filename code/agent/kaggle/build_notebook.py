"""Turn a ``# %%`` percent-format script into a Kaggle/Jupyter notebook. Stdlib only.

    python agent/kaggle/build_notebook.py   # teacher_distill.py -> teacher_distill.ipynb
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
    if src.stem.startswith("colab"):        # [Colab V8] a Colab notebook asks for a T4 by itself
        nb["metadata"] = {"kernelspec": nb["metadata"]["kernelspec"], "language_info": {"name": "python"},
                          "accelerator": "GPU", "colab": {"provenance": [], "gpuType": "T4"}}
    dst.write_text(json.dumps(nb, indent=1) + "\n")


# [Colab, Nick Nov 4 7:30 PM] self-contained Colab notebooks from one template (colab_students.py); the PIN is
# filled in when the notebook is copied to kaggle-inputs (the pack's own commit).
COLAB = {
    "v8_watchdog_l4": dict(TITLE="V8 watchdog, uncapped (EXP-085): the V8 run stopped it at step ~450 of 1436.",
                           GPU="L4", JOBS="watchdog", ANSWERS="", SESSION_H="2.5", STUDENT_H="2.2", EPOCHS="", WATCHDOG_TRAIN=""),
    "v81_watchdog_l4": dict(TITLE="V8.1 watchdog (EXP-101): V7's rows + counterfactual rows on the conditional kinds.",
                            GPU="L4", JOBS="watchdog", ANSWERS="", SESSION_H="2.5", STUDENT_H="2.2", EPOCHS="",
                            WATCHDOG_TRAIN="v81"),
    "v82_students_l4": dict(TITLE="V8.2 students (EXP-087): the full recipe on the new teacher labels.",
                            GPU="L4", JOBS="game-reasoner,watchdog", ANSWERS="__ANSWERS__", SESSION_H="4.5",
                            STUDENT_H="4.0", EPOCHS="", WATCHDOG_TRAIN=""),
}


def build_colab(name: str, out_dir: Path) -> Path:
    src = (HERE / "colab_students.py").read_text()
    for k, v in COLAB[name].items():
        src = src.replace(f"__{k}__", v)
    tmp = out_dir / f"{name}.py"
    tmp.write_text(src)
    build(tmp, out_dir / f"colab_{name}.ipynb")
    tmp.unlink()
    return out_dir / f"colab_{name}.ipynb"


if __name__ == "__main__":
    if sys.argv[1:2] == ["--colab"]:
        out = Path(sys.argv[2])
        out.mkdir(parents=True, exist_ok=True)
        for n in COLAB:
            print("wrote", build_colab(n, out))
        raise SystemExit(0)
    names = sys.argv[1:] or ["teacher_distill"]
    for n in names:
        build(HERE / f"{n}.py", HERE / f"{n}.ipynb")
        print("wrote", HERE / f"{n}.ipynb")
