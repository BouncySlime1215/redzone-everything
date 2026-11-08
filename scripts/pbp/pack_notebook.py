#!/usr/bin/env python3
"""Build the self-contained Kaggle notebook for the play-by-play crew (code embedded, no data, no secrets).

Serves: EXP-1000. Owner: Data Planner A.

    python scripts/pbp/pack_notebook.py OUT_DIR [--jobs files|baseball|euro]   # -> OUT_DIR/rz_pbp_crew_<jobs>.ipynb
Why: Builds the self-contained Kaggle notebook for the play-by-play crew (docstring, Data A commit).
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULES = ("__init__.py", "schema.py", "mlb_statsapi.py", "wehoop.py", "statsbomb.py", "wyscout.py", "cricsheet.py",
           "tennis.py", "sportvu.py", "euroleague.py")


def code_b64() -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for m in MODULES:
            tar.add(ROOT / "scripts/pbp" / m, arcname=f"pbp/{m}")
    return base64.b64encode(buf.getvalue()).decode()


def notebook(src: str) -> dict:
    """'# %%' cells -> a Jupyter notebook ('# %% [markdown]' cells become markdown)."""
    cells, cur, md = [], [], False

    def flush():
        if cur and any(x.strip() for x in cur):
            body = [x[2:] if md and x.startswith("# ") else ("" if md and x.strip() == "#" else x) for x in cur]
            lines = [ln + "\n" for ln in "\n".join(body).strip("\n").split("\n")]
            lines[-1] = lines[-1].rstrip("\n")
            cells.append({"cell_type": "markdown", "metadata": {}, "source": lines} if md else
                         {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": lines})

    for line in src.split("\n"):
        if line.startswith("# %%"):
            flush()
            cur, md = [], "[markdown]" in line
        else:
            cur.append(line)
    flush()
    return {"cells": cells, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python",
                                                        "name": "python3"}}, "nbformat": 4, "nbformat_minor": 5}


def build(jobs: str) -> dict:
    src = (ROOT / "scripts/kaggle_pbp_crew.py").read_text()
    src = src.replace('CODE_B64 = ""', f'CODE_B64 = "{code_b64()}"', 1)
    src = src.replace('os.environ.get("RZ_PBP_JOBS", "files")', f'os.environ.get("RZ_PBP_JOBS", "{jobs}")', 1)
    return notebook(src)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--jobs", default="files")
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    p = a.out / f"rz_pbp_crew_{a.jobs.replace(',', '_')}.ipynb"
    p.write_text(json.dumps(build(a.jobs), indent=1))
    print(p, p.stat().st_size, "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
