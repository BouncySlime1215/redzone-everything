"""scripts/results_ledger.py: the chain catches every edit, delete and rewrite; the public view leaks no locked value."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ci_report  # noqa: E402
import results_ledger as L  # noqa: E402


@pytest.fixture
def d(tmp_path):
    base = tmp_path / "results"
    L.init(base, "test")
    return base


def row(kind="result", suite="s", **kw):
    return {"kind": kind, "suite": suite, "commit": "abc", **kw}


def lines(d):
    return (d / L.LEDGER_NAME).read_bytes().splitlines(keepends=True)


def test_rows_chain_and_verify(d):
    a = L.append(d, row(metrics={"entries_mid_play": 0}))
    b = L.append(d, row(verdict="PASS"))
    assert b["prev"] == a["hash"] and b["seq"] == 2
    assert len(L.verify(d)) == 3
    assert (d / L.HEAD_NAME).read_text() == f"2 {b['hash']}\n"


def test_edited_number_is_caught(d):
    L.append(d, row(metrics={"entries_mid_play": 3}))
    p = d / L.LEDGER_NAME
    p.write_bytes(p.read_bytes().replace(b'"entries_mid_play":3', b'"entries_mid_play":0'))
    with pytest.raises(L.LedgerError, match="edited"):
        L.verify(d)


def test_whitespace_edit_is_caught(d):
    L.append(d, row())
    p = d / L.LEDGER_NAME
    all_lines = lines(d)
    all_lines[1] = all_lines[1].replace(b'{"commit"', b'{ "commit"')
    p.write_bytes(b"".join(all_lines))
    with pytest.raises(L.LedgerError, match="canonical"):
        L.verify(d)


def test_deleted_middle_row_is_caught(d):
    L.append(d, row())
    L.append(d, row())
    p = d / L.LEDGER_NAME
    all_lines = lines(d)
    p.write_bytes(all_lines[0] + all_lines[2])
    with pytest.raises(L.LedgerError, match="seq"):
        L.verify(d)


def test_dropped_last_row_is_caught_by_head(d):
    L.append(d, row())
    p = d / L.LEDGER_NAME
    p.write_bytes(lines(d)[0])
    with pytest.raises(L.LedgerError, match="HEAD"):
        L.verify(d)


def test_partial_write_is_caught(d):
    p = d / L.LEDGER_NAME
    p.write_bytes(p.read_bytes().rstrip(b"\n"))
    with pytest.raises(L.LedgerError, match="newline"):
        L.verify(d)


def test_edited_summary_is_caught(d):
    L.append(d, row(verdict="FAIL"))
    s = d / L.SUMMARY_NAME
    s.write_text(s.read_text().replace("FAIL", "PASS"))
    with pytest.raises(L.LedgerError, match="view was edited"):
        L.verify(d)


def test_missing_ledger_fails_and_append_will_not_recreate_it(d):
    (d / L.LEDGER_NAME).unlink()
    with pytest.raises(L.LedgerError, match="missing"):
        L.verify(d)
    with pytest.raises(L.LedgerError, match="missing"):
        L.append(d, row())


def test_append_refuses_a_broken_chain(d):
    L.append(d, row(metrics={"x": 1}))
    p = d / L.LEDGER_NAME
    p.write_bytes(p.read_bytes().replace(b'"x":1', b'"x":2'))
    with pytest.raises(L.LedgerError):
        L.append(d, row())


def test_init_only_once(d):
    with pytest.raises(L.LedgerError, match="once"):
        L.init(d, "again")


def test_locked_eval_is_a_verdict_only(d):
    with pytest.raises(L.LedgerError, match="no metrics"):
        L.append(d, row("locked_eval", verdict="PASS", release="rc1", metrics={"dead_air_pct": 1.0}))
    with pytest.raises(L.LedgerError, match="per-night"):
        L.append(d, row("locked_eval", verdict="PASS", release="rc1", note="bad on 20261101"))
    with pytest.raises(L.LedgerError, match="verdict"):
        L.append(d, row("locked_eval", release="rc1"))


def test_one_locked_run_per_release_until_retracted(d):
    first = L.append(d, row("locked_eval", verdict="FAIL", release="rc1"))
    with pytest.raises(L.LedgerError, match="already used"):
        L.append(d, row("locked_eval", verdict="PASS", release="rc1"))
    L.append(d, row("retract", retracts=first["seq"], note="runner died mid-run"))
    L.append(d, row("locked_eval", verdict="PASS", release="rc1"))
    assert len(L.verify(d)) == 4


def test_retract_rules(d):
    r = L.append(d, row())
    with pytest.raises(L.LedgerError, match="say why"):
        L.append(d, row("retract", retracts=r["seq"]))
    with pytest.raises(L.LedgerError, match="not an earlier"):
        L.append(d, row("retract", retracts=99, note="x"))
    with pytest.raises(L.LedgerError, match="not an earlier"):
        L.append(d, row("retract", retracts=0, note="x"))
    L.append(d, row("retract", retracts=r["seq"], note="wrong build"))
    with pytest.raises(L.LedgerError, match="already retracted"):
        L.append(d, row("retract", retracts=r["seq"], note="again"))


def test_prereg_must_come_first(d):
    with pytest.raises(L.LedgerError, match="no earlier prereg"):
        L.append(d, row(prereg="exp-1"))
    L.append(d, row("prereg", prereg="exp-1"))
    L.append(d, row(prereg="exp-1"))
    with pytest.raises(L.LedgerError, match="already exists"):
        L.append(d, row("prereg", prereg="exp-1"))


def test_a_hand_built_chain_still_meets_the_rules():
    """Correct hashes do not excuse a broken rule: verify re-runs the rules on every row."""
    rows = []
    prev = L.GENESIS_PREV
    for i, fields in enumerate([row("genesis", suite=""), row("locked_eval", verdict="PASS", release="rc1"),
                                row("locked_eval", verdict="PASS", release="rc1")]):
        rec = {"seq": i, "ts": "t", "prev": prev, **fields}
        rec["hash"] = prev = L.digest(rec)
        rows.append(L.canon(rec) + b"\n")
    with pytest.raises(L.LedgerError, match="already used"):
        L.parse_chain(b"".join(rows))


def test_summary_counts_runs_and_skips_retracted(d):
    a = L.append(d, row(verdict="PASS", metrics={"m": 1}))
    b = L.append(d, row(verdict="PASS", metrics={"m": 2}))
    L.append(d, row("retract", retracts=b["seq"], note="bad"))
    s = L.build_summary(L.verify(d))
    assert s["runs"]["result/s"] == 2                  # best-of-N stays visible
    assert s["latest"]["result/s"]["seq"] == a["seq"]
    assert s["retracted"] == [b["seq"]]


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True,
                   capture_output=True)


def test_base_check_needs_an_extension(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "x").write_text("x")
    _git(repo, "add", "x")
    _git(repo, "commit", "-qm", "before the ledger")
    _git(repo, "tag", "before")
    monkeypatch.setattr(L, "ROOT", repo)
    d = repo / "results"
    L.init(d, "t")
    L.append(d, row())
    _git(repo, "add", "results")
    _git(repo, "commit", "-qm", "ledger")
    _git(repo, "tag", "base")

    L.verify(d, "before")                               # the ref predates the ledger
    L.append(d, row())
    L.verify(d, "base")                                 # a plain append extends it

    # A whole new chain: every hash is valid and HEAD matches, only the base ref can tell.
    (d / L.LEDGER_NAME).unlink()
    L.init(d, "rewritten")
    L.append(d, row(metrics={"better": 1}))
    L.verify(d)
    with pytest.raises(L.LedgerError, match="rewritten"):
        L.verify(d, "base")
    with pytest.raises(L.LedgerError, match="not found"):
        L.verify(d, "no-such-ref")


def card(night="20261101", value=7):
    return {"metrics": {"eval_data": "eval-data-v1", "mid_play_cuts": 0, "entries": 40, "entries_mid_play": 1,
                        "cut_timing": {"entries": 40, "entries_mid_play": 1}, "big_seen_pct": 91.5, "dead_air_pct": 2.5,
                        "per_night": [{"night": night, "entries": 40, "entries_mid_play": 1, "big": value, "big_seen": value}]}}


def test_public_gate_keeps_verdicts_and_withholds_values(tmp_path):
    src = tmp_path / "locked.json"
    src.write_text(json.dumps({"cards": {"nfl": card()}}))
    md = tmp_path / "gate.md"
    assert L.main(["public-gate", str(src), "--md", str(md)]) in (0, 1)
    text = md.read_text()
    assert "mid-play cuts = 0" in text and "PASS" in text
    for leaked in ("20261101", "91.5", "(1/40)", "2.5%"):
        assert leaked not in text


def test_public_gate_exit_code_matches_the_release_gate(tmp_path):
    bad = card()
    bad["metrics"]["mid_play_cuts"] = 2
    src = tmp_path / "locked.json"
    src.write_text(json.dumps({"cards": {"nfl": bad}}))
    assert L.main(["public-gate", str(src), "--md", str(tmp_path / "g.md")]) == 1
    assert "FAIL" in (tmp_path / "g.md").read_text()


def test_public_gate_bad_input_is_a_tool_error_not_a_verdict(tmp_path):
    src = tmp_path / "locked.json"
    src.write_text("not json")
    assert L.main(["public-gate", str(src), "--md", str(tmp_path / "g.md")]) == 2


def test_scrub_removes_night_rows_at_any_depth():
    got = L.scrub({"slice": {"nights": 3}, "cards": {"nfl": card()},
                   "by_day": {"20261101": {"x": 1}, "total": 2}, "rows": [{"night": "20261101"}, 5]})
    text = json.dumps(got)
    assert "20261101" not in text and "per_night" not in text
    assert got["slice"]["nights"] == 3                  # a scalar count stays
    assert got["cards"]["nfl"]["metrics"]["mid_play_cuts"] == 0
    assert got["by_day"] == {"total": 2} and got["rows"] == [5]


def test_scrubbed_card_gives_the_same_ci_report_numbers():
    """The PR comment and the hard rule read aggregates only, so scrubbing cannot change them."""
    m = card()["metrics"]
    s = L.scrub(m)
    for lg in ci_report.HEADLINE:
        for path, _label, _dir in ci_report.HEADLINE[lg]:
            assert ci_report.pick(m, path) == ci_report.pick(s, path)


def test_scrub_missing_file_is_skipped_and_bad_json_fails(tmp_path):
    assert L.main(["scrub", str(tmp_path / "none.json")]) == 0
    bad = tmp_path / "bad.json"
    bad.write_text("{")
    assert L.main(["scrub", str(bad)]) == 2


def test_cli_round_trip(tmp_path, capsys):
    base = ["--dir", str(tmp_path / "r")]
    assert L.main([*base, "init"]) == 0
    raw = tmp_path / "raw.json"
    raw.write_text("{}")
    assert L.main([*base, "append", "--kind", "live", "--suite", "hdmi1", "--verdict", "PASS", "--metric",
                   "entries_mid_play=0", "--raw", str(raw), "--commit", "abc"]) == 0
    assert L.main([*base, "verify"]) == 0
    capsys.readouterr()
    assert L.main([*base, "summary"]) == 0
    view = json.loads(capsys.readouterr().out)
    assert view["latest"]["live/hdmi1"]["metrics"] == {"entries_mid_play": 0}
    assert L.main([*base, "append", "--kind", "result", "--metric", "oops"]) == 1
