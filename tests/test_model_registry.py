"""redzone/models_rt/registry.py (F-02): register, promote and load a toy model; the chain catches edits; the
runtime refuses unpromoted, failed, demoted, retracted, tampered and incomplete records, never falling back."""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from redzone.models_rt import registry as R
from redzone.models_rt.forest import Nodes

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import model_registry as CLI  # noqa: E402

FEATURES = ["x"]


def toy_forest(path: Path, value_left: float = -1.0) -> None:
    """One stump: x <= 0.5 -> value_left, else +1."""
    meta = json.dumps({"features": FEATURES, "depth": 1}).encode()
    np.savez(path, feat=np.array([0, -1, -1]), left=np.array([1, -1, -1]), right=np.array([2, -1, -1]),
             thr=np.array([0.5, 0, 0]), dleft=np.array([1, 0, 0]), miss=np.array([0, 0, 0]),
             value=np.array([0.0, value_left, 1.0]), roots=np.array([0]), meta=np.frombuffer(meta, dtype=np.uint8))


@pytest.fixture
def env(tmp_path):
    base = tmp_path / "results"
    R.L.init(base, "test")
    root = tmp_path / "repo"
    (root / "models").mkdir(parents=True)
    toy_forest(root / "models" / "toy.npz")
    return base, root


def h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def record(root: Path, run_id: str = "toy-1", **kw) -> dict:
    rec = {"run_id": run_id, "git_sha": "a" * 40, "code_hash": h("code"), "config_hash": h("cfg"),
           "data_manifest_hash": h("data"), "prereg_hash": h("prereg"), "seed": 7,
           "metrics": {"auc": 0.61, "entries_mid_play": 0}, "verdict": "PASS",
           "artifact_path": "models/toy.npz", "artifact_sha256": R.sha256_file(root / "models" / "toy.npz"),
           "promoted": False, "gpu_type": "cpu", "hours": 0.01, "model": "toy", "license": "personal-use"}
    rec.update(kw)
    return rec


def test_register_promote_load(env):
    base, root = env
    R.register(record(root), base=base)
    with pytest.raises(R.UnpromotedModelError, match="not promoted"):
        R.load_forest("toy-1", FEATURES, base=base, root=root)
    R.promote("toy-1", base=base, root=root, note="test")
    arrays, meta = R.load_forest("toy-1", FEATURES, base=base, root=root)
    nodes = Nodes(arrays, meta["depth"])
    assert [float(nodes.value[nodes.leaves(np.array([x]))][0]) for x in (0.0, 1.0)] == [-1.0, 1.0]
    assert R.resolve_promoted("toy-1", base=base, root=root) == (root / "models" / "toy.npz").resolve()
    st = R.state(base)["toy-1"]
    assert st["promoted"] and st["seqs"] == [1, 2]
    R.L.verify(base)                                    # the ledger's own verify still passes with registry rows


def test_every_registry_field_lands_in_the_chain(env):
    base, root = env
    row = R.register(record(root), base=base)
    assert set(R.REQUIRED) <= set(row["registry"])
    assert row["kind"] == "result" and row["suite"] == R.SUITE and row["commit"] == "a" * 40


def test_editing_an_old_record_is_detected(env):
    base, root = env
    R.register(record(root), base=base)
    R.promote("toy-1", base=base, root=root)
    R.register(record(root, run_id="toy-2"), base=base)
    path = base / R.L.LEDGER_NAME
    lines = path.read_bytes().splitlines(keepends=True)
    old = json.loads(lines[1])
    old["registry"]["verdict"] = "FAIL"                 # an edit to an old row, everything else kept
    lines[1] = R.L.canon(old) + b"\n"
    path.write_bytes(b"".join(lines))
    with pytest.raises(R.RegistryError, match="row 1: hash"):
        R.state(base)
    with pytest.raises(R.UnpromotedModelError, match="does not verify"):
        R.resolve_promoted("toy-1", base=base, root=root)
    old["hash"] = R.L.digest(old)                       # re-hashing the row breaks the next row's link instead
    lines[1] = R.L.canon(old) + b"\n"
    path.write_bytes(b"".join(lines))
    with pytest.raises(R.UnpromotedModelError, match="chain break"):
        R.load_forest("toy-1", FEATURES, base=base, root=root)


def test_unregistered_model_refused(env):
    base, root = env
    with pytest.raises(R.UnpromotedModelError, match="not in the registry"):
        R.resolve_promoted("nope", base=base, root=root)


@pytest.mark.parametrize("field", R.REQUIRED)
def test_missing_required_field_refused(env, field):
    base, root = env
    rec = record(root)
    del rec[field]
    with pytest.raises(R.RegistryError, match=f"missing required field.*{field}"):
        R.register(rec, base=base)
    assert R.state(base) == {}                          # nothing was written


@pytest.mark.parametrize("bad", [
    {"verdict": "MAYBE"}, {"promoted": True}, {"seed": "7"}, {"seed": True}, {"hours": -1}, {"hours": float("nan")},
    {"git_sha": "abc"}, {"code_hash": "x" * 64}, {"metrics": {}}, {"metrics": {"a": [1]}}, {"gpu_type": " "},
    {"artifact_path": "/abs/toy.npz"}, {"artifact_path": "../toy.npz"}, {"artifact_path": "~/toy.npz"},
    {"run_id": "bad id"}, {"surprise": 1},
])
def test_bad_record_refused(env, bad):
    base, root = env
    with pytest.raises(R.RegistryError):
        R.register(record(root, **bad), base=base)


def test_duplicate_run_id_refused(env):
    base, root = env
    R.register(record(root), base=base)
    with pytest.raises(R.RegistryError, match="already registered"):
        R.register(record(root), base=base)


@pytest.mark.parametrize("verdict", ["FAIL", "INCONCLUSIVE"])
def test_only_pass_is_promoted(env, verdict):
    base, root = env
    R.register(record(root, verdict=verdict), base=base)
    with pytest.raises(R.RegistryError, match="only PASS"):
        R.promote("toy-1", base=base, root=root)
    with pytest.raises(R.UnpromotedModelError, match=f"not promoted \\(verdict {verdict}\\)"):
        R.resolve_promoted("toy-1", base=base, root=root)


def test_hand_written_promote_of_failed_run_fails_closed(env):
    """A promote row appended straight to the ledger (around the registry API) is caught when the chain is folded."""
    base, root = env
    rec = R.register(record(root, verdict="FAIL"), base=base)
    R.L.append(base, {"kind": "result", "suite": R.SUITE, "commit": "a" * 40,
                      "registry": {"event": "promote", "run_id": "toy-1",
                                   "artifact_sha256": rec["registry"]["artifact_sha256"]}})
    with pytest.raises(R.UnpromotedModelError, match="only PASS"):
        R.resolve_promoted("toy-1", base=base, root=root)


def test_artifact_changed_after_promotion_refused(env):
    base, root = env
    R.register(record(root), base=base)
    R.promote("toy-1", base=base, root=root)
    toy_forest(root / "models" / "toy.npz", value_left=-2.0)
    with pytest.raises(R.UnpromotedModelError, match="changed since promotion"):
        R.load_forest("toy-1", FEATURES, base=base, root=root)
    (root / "models" / "toy.npz").unlink()
    with pytest.raises(R.UnpromotedModelError, match="missing"):
        R.resolve_promoted("toy-1", base=base, root=root)


def test_promote_checks_the_artifact(env):
    base, root = env
    R.register(record(root, artifact_sha256=h("other")), base=base)
    with pytest.raises(R.RegistryError, match="no longer matches"):
        R.promote("toy-1", base=base, root=root)


def test_demote_and_retract_refuse(env):
    base, root = env
    R.register(record(root), base=base)
    R.promote("toy-1", base=base, root=root)
    with pytest.raises(R.RegistryError, match="say why"):
        R.demote("toy-1", "", base=base)
    R.demote("toy-1", "regressed", base=base)
    with pytest.raises(R.UnpromotedModelError, match="not promoted"):
        R.resolve_promoted("toy-1", base=base, root=root)
    reg = R.register(record(root, run_id="toy-2"), base=base)
    R.promote("toy-2", base=base, root=root)
    R.L.append(base, {"kind": "retract", "suite": "", "commit": "a" * 40, "retracts": reg["seq"], "note": "leak"})
    with pytest.raises(R.UnpromotedModelError, match="retracted"):
        R.resolve_promoted("toy-2", base=base, root=root)
    with pytest.raises(R.RegistryError, match="retracted"):
        R.promote("toy-2", base=base, root=root)


def test_no_fallback_to_another_run(env):
    """Asking for an unpromoted run never returns a promoted sibling with the same model name or artifact."""
    base, root = env
    R.register(record(root, run_id="toy-1"), base=base)
    R.promote("toy-1", base=base, root=root)
    R.register(record(root, run_id="toy-2"), base=base)
    with pytest.raises(R.UnpromotedModelError, match="toy-2"):
        R.load_forest("toy-2", FEATURES, base=base, root=root)


def test_artifact_symlink_out_of_root_refused(env, tmp_path):
    base, root = env
    outside = tmp_path / "outside.npz"
    toy_forest(outside)
    (root / "models" / "link.npz").symlink_to(outside)
    R.register(record(root, artifact_path="models/link.npz"), base=base)
    with pytest.raises(R.RegistryError, match="escapes the root"):
        R.promote("toy-1", base=base, root=root)


def test_malformed_registry_row_fails_closed(env):
    base, root = env
    R.L.append(base, {"kind": "result", "suite": R.SUITE, "commit": "a" * 40,
                      "registry": {"event": "register", "run_id": "toy-1"}})
    with pytest.raises(R.UnpromotedModelError, match="missing required field"):
        R.resolve_promoted("toy-1", base=base, root=root)


def test_other_ledger_rows_are_ignored(env):
    base, root = env
    R.L.append(base, {"kind": "live", "suite": "hdmi1-nfl", "commit": "abc", "verdict": "PASS"})
    R.register(record(root), base=base)
    assert list(R.state(base)) == ["toy-1"]


def test_cli_round_trip(env, tmp_path, capsys):
    base, root = env
    src = tmp_path / "run.json"
    src.write_text(json.dumps(record(root)))
    args = ["--dir", str(base), "--root", str(root)]
    assert CLI.main([*args, "register", "--from", str(src)]) == 0
    assert CLI.main([*args, "resolve", "toy-1"]) == 1
    assert "not promoted" in capsys.readouterr().err
    assert CLI.main([*args, "promote", "toy-1", "--note", "t"]) == 0
    assert CLI.main([*args, "resolve", "toy-1"]) == 0
    assert CLI.main([*args, "list"]) == 0
    assert "toy-1\tPASS\tpromoted" in capsys.readouterr().out
    assert CLI.main([*args, "demote", "toy-1", "--reason", "r"]) == 0
    assert CLI.main([*args, "resolve", "toy-1"]) == 1
