#!/usr/bin/env python3
"""Pack the code the Kaggle factory notebook needs into rz_factory_code.tar.gz (no data, no secrets).

    python scripts/pack_factory_code.py OUT_DIR      # -> OUT_DIR/rz_factory_code.tar.gz + kaggle_noggin_factory.ipynb
"""
from __future__ import annotations

import json
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ("noggin_factory.py", "noggin_selfplay.py", "train_noggin_pick.py", "kaggle_noggin_factory.py",
           "noggin_tokens.py", "nhl_apiweb.py", "world_model_pilot.py",
           "retrosheet_plays.py", "nba_cdn_plays.py", "exp022_tpp.py", "green_summaries.py", "noggin_tokens_v2.py")
SKIP = ("__pycache__", ".pyc", ".db", ".sqlite", ".env", ".log")


def _ok(p: Path) -> bool:
    return not any(s in str(p) for s in SKIP)


def notebook(src: Path) -> dict:
    """'# %%' cells -> a Jupyter notebook ('# %% [markdown]' cells become markdown)."""
    cells, cur, md = [], [], False

    def flush():
        if cur and any(x.strip() for x in cur):
            body = [x[2:] if md and x.startswith("# ") else ("" if md and x.strip() == "#" else x) for x in cur]
            text = "\n".join(body).strip("\n")
            lines = [ln + "\n" for ln in text.split("\n")]
            lines[-1] = lines[-1].rstrip("\n")
            cells.append({"cell_type": "markdown", "metadata": {}, "source": lines} if md else
                         {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
                          "source": lines})

    for line in src.read_text().split("\n"):
        if line.startswith("# %%"):
            flush()
            cur, md = [], "[markdown]" in line
        else:
            cur.append(line)
    flush()
    return {"cells": cells, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python",
                                                        "name": "python3"}}, "nbformat": 4, "nbformat_minor": 5}


def main(argv=None) -> int:
    out = Path((argv or sys.argv[1:])[0])
    out.mkdir(parents=True, exist_ok=True)
    pack = out / "rz_factory_code.tar.gz"
    with tarfile.open(pack, "w:gz") as tar:
        for p in sorted((ROOT / "redzone").rglob("*")):
            if p.is_file() and _ok(p):
                tar.add(p, f"rz/{p.relative_to(ROOT)}")
        for s in SCRIPTS:
            tar.add(ROOT / "scripts" / s, f"rz/scripts/{s}")
        tar.add(ROOT / "requirements.txt", "rz/requirements.txt")
    nb = out / "kaggle_noggin_factory.ipynb"
    nb.write_text(json.dumps(notebook(ROOT / "scripts" / "kaggle_noggin_factory.py"), indent=1))
    print(pack, pack.stat().st_size, nb)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
