"""End-to-end smoke of the Kaggle scripts on tiny CPU samples, so packaging bugs fail in CI and not
on Kaggle GPU time (run 1 died at 255 s on a .gz opened as text)."""
from __future__ import annotations

import gzip
import json

import pytest

from agent.kaggle import run_teacher


class FakeBackend:
    def generate(self, batch):
        out = []
        for r in batch:
            t = r.get("task")
            if t == "wd_paraphrase":
                text = '{"lines": ["stream froze", "picture stuck"]}'
            elif t == "jump_blind":
                text = '{"decision": "stay", "reel_s": 0, "why": "x"}'
            else:
                text = '{"lock_in": "watch", "safe_to_leave": "no", "confidence": 0.6}'
            out.append((text, 10, 5))
        return out


def _reqs(n):
    rs = [{"id": f"nfl:1:{i}|blind", "task": "reasoner_blind", "max_tokens": 50,
           "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": f"b{i}"}]} for i in range(n)]
    rs.append({"id": "wd:paused:0", "task": "wd_paraphrase", "kind": "paused", "max_tokens": 50,
               "messages": [{"role": "user", "content": "x"}],
               "followup": {"field": "lines", "messages": [{"role": "user", "content": "classify {item}"}]}})
    return rs


def _wgz(path, rows):
    with gzip.open(path, "wt") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_teacher_entry_point_reads_gz_queue_and_gz_resume(tmp_path):
    q = tmp_path / "teacher_requests.jsonl.gz"
    _wgz(q, _reqs(5))
    out = tmp_path / "out"
    run_teacher.main(["--requests", str(q), "--out", str(out), "--budget-h", "1"], backend_factory=lambda: (
        FakeBackend(), "fake", []))
    ans = [json.loads(x) for x in open(out / "teacher_out.jsonl")]
    assert len(ans) == 6 and any(a.get("followups") for a in ans)
    rep = json.loads((out / "teacher_report.json").read_text())
    assert rep["complete"] and rep["answered"] == 6
    # resume from a gzipped earlier output with a cut-off tail: nothing is asked twice
    prev = tmp_path / "prev.jsonl.gz"
    data = gzip.compress("".join(json.dumps(a) + "\n" for a in ans[:4]).encode())
    prev.write_bytes(data[:-6])                    # a stopped session's truncated gzip
    out2 = tmp_path / "out2"
    run_teacher.main(["--requests", str(q), "--out", str(out2), "--resume-from", str(prev)],
                     backend_factory=lambda: (FakeBackend(), "fake", []))
    rep2 = json.loads((out2 / "teacher_report.json").read_text())
    assert rep2["already_done"] >= 3 and rep2["complete"]
    assert len({json.loads(x)["id"] for x in open(out2 / "teacher_out.jsonl")}) == 6


def test_teacher_reads_plain_jsonl_too(tmp_path):
    q = tmp_path / "teacher_requests.jsonl"         # Nick's notebook patch gunzips the queue first
    q.write_text("".join(json.dumps(r) + "\n" for r in _reqs(2)))
    run_teacher.main(["--requests", str(q), "--out", str(tmp_path / "o")],
                     backend_factory=lambda: (FakeBackend(), "fake", []))
    assert sum(1 for _ in open(tmp_path / "o" / "teacher_out.jsonl")) == 3


def test_notebook_cells_compile():
    from agent.kaggle import build_notebook
    nb = json.loads((build_notebook.HERE / "teacher_distill.ipynb").read_text())
    for c in nb["cells"]:
        if c["cell_type"] == "code":
            compile("".join(c["source"]), "cell", "exec")


@pytest.mark.parametrize("script", ["run_teacher", "run_students", "grade", "pack_data", "student_data",
                                    "build_moments_kaggle", "dequant"])
def test_kaggle_scripts_import(script):
    __import__(f"agent.kaggle.{script}")
