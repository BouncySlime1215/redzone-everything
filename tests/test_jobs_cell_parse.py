"""F-03 job runner: the Colab cell compiles and round-trips the spec; parse accepts only a matching result; Kaggle stub.

No network: the fetch cell is compiled, never run; the Kaggle backend runs with a fake runner only.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import job as jobcli
from tools.jobs import cell, kaggle_backend, parse, worker
from tools.jobs.spec import canonical

REPO = Path(__file__).resolve().parents[1]
WORKER_BYTES = (REPO / cell.WORKER_PATH).read_bytes()
SHA = "0123456789abcdef0123456789abcdef01234567"


def toy(commit=SHA, **kw):
    return jobcli.toy_spec(commit, **kw).validate()


# --- the cell -----------------------------------------------------------------------------------------------------
def test_the_fetch_cell_compiles_fits_and_round_trips_the_spec():
    spec = toy()
    text = cell.build_cell(spec, WORKER_BYTES)
    compile(text, "<colab cell>", "exec")
    assert len(text) < cell.CELL_LIMIT
    assert cell.typing_problems(text) == []
    got = cell.decode_cell(text)
    assert got["spec_json"] == spec.to_json() and json.loads(got["spec_json"]) == spec.to_dict()
    assert got["worker_sha256"] == hashlib.sha256(WORKER_BYTES).hexdigest() and got["worker"] is None
    assert got["url"] == ("https://raw.githubusercontent.com/BouncySlime1215/redzone-everything/"
                          f"{SHA}/tools/jobs/worker.py")
    assert spec.spec_hash()[:12] in text.splitlines()[0]


def test_the_embedded_cell_carries_the_exact_worker():
    spec = toy()
    text = cell.build_cell(spec, WORKER_BYTES, embed=True)
    compile(text, "<colab cell>", "exec")
    assert cell.typing_problems(text) == []
    got = cell.decode_cell(text)
    assert got["worker"] == WORKER_BYTES and got["url"] is None
    assert worker.spec_hash(json.loads(got["spec_json"])) == spec.spec_hash()


def test_the_typing_check_catches_editor_traps():
    assert cell.typing_problems("for x in y:\n print(x)\n") == ["line 1 ends with ':'", "line 2 is indented"]
    assert "contains a triple quote" in cell.typing_problems("x='''a'''\n")
    assert "contains an f-string" in cell.typing_problems("x=f'{y}'\n")
    assert cell.typing_problems("x=b64decode('abcf')\n") == []


def test_the_fetch_cell_needs_a_github_repo():
    spec = toy()
    spec.repo_url = "https://example.org/a/b.git"
    with pytest.raises(ValueError, match="github"):
        cell.build_cell(spec, WORKER_BYTES)
    assert "example.org" not in cell.build_cell(spec, WORKER_BYTES, embed=True).split("B=")[0]


# --- parse --------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def finished(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("jobs-parse")
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    spec = toy(head, steps=(0, 20))
    p = tmp / "spec.json"
    p.write_text(spec.to_json())
    text, res = jobcli.run_worker(REPO / cell.WORKER_PATH, p, tmp / "work")
    assert res["status"] == "ok", text
    return spec, "Colab chrome text\nRZ-PROGRESS {\"step\": 10}\n" + text + "RZ-EXIT 0\n", tmp / "work" / "out"


def _relined(res: dict) -> str:
    body = {k: v for k, v in res.items() if k != "line"}
    return "RZ-RESULT " + canonical({**body, "line": hashlib.sha256(canonical(body).encode()).hexdigest()})


def test_a_matching_result_is_accepted_with_files(finished):
    spec, page, out = finished
    rep = parse.check_text(page, spec, files_dir=out)
    assert rep["accepted"] and rep["status"] == "ok" and rep["problems"] == []
    assert rep["progress"]["step"] == 20 and rep["git"] == spec.commit


def test_another_specs_result_is_refused(finished):
    spec, page, _out = finished
    other = toy(spec.commit, steps=(0, 20))
    other.seed = 8
    rep = parse.check_text(page, other)
    assert not rep["accepted"] and "spec hash does not match this spec" in rep["problems"]


def test_a_changed_output_hash_is_refused(finished):
    spec, page, out = finished
    _line, res = parse.last_result(page)
    res["outputs"]["model.bin"][0] = "0" * 64
    rep = parse.check_text(_relined(res), spec)
    assert not rep["accepted"] and "outputs hash does not re-compute from the per-output hashes" in rep["problems"]
    res["outputs_sha256"] = hashlib.sha256(canonical(res["outputs"]).encode()).hexdigest()
    rep = parse.check_text(_relined(res), spec, files_dir=out)
    assert rep["problems"] == ["output model.bin does not hash to the result"]


def test_a_changed_file_is_refused(finished, tmp_path):
    spec, page, out = finished
    copy = tmp_path / "out"
    shutil.copytree(out, copy)
    (copy / "state.json").write_text("{}")
    rep = parse.check_text(page, spec, files_dir=copy)
    assert rep["problems"] == ["output state.json does not hash to the result"]


def test_a_cut_or_edited_line_is_refused(finished):
    spec, page, _out = finished
    line, _res = parse.last_result(page)
    with pytest.raises(parse.ResultError):
        parse.check_text(line[:-40], spec)                   # cut: not JSON any more
    edited = line.replace('"status":"ok"', '"status":"partial"')
    assert "line hash does not match" in parse.check_text(edited, spec)["problems"][0]


def test_the_last_result_wins_and_the_chain_is_checked(finished):
    spec, page, _out = finished
    line, res = parse.last_result(page)
    nxt = {**res, "prev": hashlib.sha256(line.encode()).hexdigest()}
    two = page + _relined(nxt) + "\n"
    assert parse.check_text(two, spec, prev_line=line)["accepted"]
    assert "chain" in parse.check_text(two, spec, prev_line="RZ-RESULT {}")["problems"][0]
    with pytest.raises(parse.ResultError, match="no RZ-RESULT"):
        parse.check_text("still running\nRZ-PROGRESS {\"step\": 3}\n", spec)


def test_the_parse_cli_exit_codes(finished, tmp_path):
    spec, page, out = finished
    (tmp_path / "spec.json").write_text(json.dumps(spec.to_dict(), indent=1))
    (tmp_path / "page.txt").write_text(page)
    assert jobcli.main(["parse", str(tmp_path / "spec.json"), str(tmp_path / "page.txt"), "--files", str(out)]) == 0
    (tmp_path / "bad.txt").write_text("nothing yet")
    assert jobcli.main(["parse", str(tmp_path / "spec.json"), str(tmp_path / "bad.txt")]) == 1


# --- Kaggle stub (fake runner only) ------------------------------------------------------------------------------
def test_the_kaggle_bundle_is_a_private_script_with_internet(tmp_path):
    spec = toy()
    md_path, run_py = kaggle_backend.build_bundle(spec, WORKER_BYTES, owner="someone", out_dir=tmp_path)
    md = json.loads(md_path.read_text())
    assert md["id"] == "someone/rz-toy-smoke" and md["code_file"] == "run.py" and md["is_private"] is True
    assert md["enable_internet"] is True and md["kernel_type"] == "script" and md["enable_gpu"] is False
    text = run_py.read_text()
    compile(text, "run.py", "exec")
    assert "/kaggle/working/rz_job/toy-smoke" in text and cell.decode_cell(text)["worker"] == WORKER_BYTES
    with pytest.raises(ValueError):
        kaggle_backend.kernel_metadata(spec, "Not A User!")


def test_the_kaggle_commands_go_only_to_the_given_runner(tmp_path):
    calls = []

    def fake(cmd):
        calls.append(cmd)
        return 0, "KernelWorkerStatus.COMPLETE"

    code, out = kaggle_backend.run(kaggle_backend.push_cmd(tmp_path), fake)
    kaggle_backend.run(kaggle_backend.status_cmd("someone/rz-toy"), fake)
    kaggle_backend.run(kaggle_backend.output_cmd("someone/rz-toy", tmp_path / "o"), fake)
    assert [c[:3] for c in calls] == [["kaggle", "kernels", "push"], ["kaggle", "kernels", "status"],
                                      ["kaggle", "kernels", "output"]]
    assert kaggle_backend.parse_status(out) == "complete"
    assert kaggle_backend.parse_status('has status "running"') == "running"
    assert kaggle_backend.parse_status("status: error\nFailure message: x") == "error"
    with pytest.raises(RuntimeError, match="unused until Nick"):
        kaggle_backend.run(["kaggle"], None)
