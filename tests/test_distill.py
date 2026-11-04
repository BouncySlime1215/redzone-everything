"""Big teacher -> small student pipeline: prompts, leakage, parsing, datasets, eval, notebook."""
from __future__ import annotations

import json
import pytest

from agent.distill import evaluate, student, teacher
from agent.kaggle import build_notebook


SYSTEM = teacher.STUDENT_SYSTEM


def row(i, split="train", lock="must_see", t_big=60.0, safe="no", league="nfl", key=80, head=0.8, y=None):
    brief = (f"NFL BUF 21 @ KC 24 | Q4 2:{i % 60:02d} | live\nball BUF 2&6 at KC 31\n"
             f"brain: key soon {key}%, break soon 20%, flow 70/100\nlock-in odds {int(head * 100)}%")
    tgt = {"lock_in": lock, "safe_to_leave": safe, "confidence": head, "storyline": "rule story line",
           "why": "rule why line", "next": "touchdown likely within 1:00", "stay_s": 120}
    big = t_big is not None and t_big <= 180
    return {"id": f"{league}:g{i}:{i}", "league": league, "game_id": f"g{i}", "night": f"202510{10 + i % 9:02d}",
            "split": split, "brief": brief, "target": tgt, "y": int(big if y is None else y), "big_next": int(big),
            "crunch_next": 0, "t_big": t_big, "t_break": 40.0, "next_text": f"SECRET FUTURE Passing Touchdown {i}",
            "garbage": False, "crunch": True, "late": True, "mine": False, "head_p": head}


def chat_of(r):
    return {"messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": r["brief"]},
                         {"role": "assistant", "content": json.dumps(r["target"])}]}


GOOD = ('{"lock_in":"must_see","safe_to_leave":"no","confidence":0.7,"storyline":"BUF driving to retake the lead",'
        '"why":"2:00 left, down 3, in range","next":"TD or FG try within 1:30","stay_s":150}')
WORDS = '{"storyline": "BUF two-minute drive, down 3", "why": "inside the 35 with the clock running", "next": "TD try within 1:00"}'


# ---------------------------------------------------------------------------------------- parsing

def test_extract_json_tolerates_think_fences_and_prose():
    assert teacher.extract_json(f"<think>hmm {{no}}</think>\n```json\n{GOOD}\n```")["lock_in"] == "must_see"
    assert teacher.extract_json(f"partial thought...</think>Sure: {GOOD} done")["stay_s"] == 150
    assert teacher.extract_json('{"a": "brace } in string", "b": 1}')["b"] == 1
    assert teacher.extract_json("no json here") is None
    assert teacher.extract_json('{"broken": ') is None


def test_clean_reasoner_bounds_and_rejects():
    d = json.loads(GOOD)
    c = teacher.clean_reasoner({**d, "confidence": 3, "stay_s": 99999, "lock_in": "Must See", "safe_to_leave": True})
    assert c["confidence"] == 1.0 and c["stay_s"] == 900 and c["lock_in"] == "must_see" and c["safe_to_leave"] == "yes"
    assert list(c) == list(teacher.ORDER)
    assert teacher.clean_reasoner({**d, "lock_in": "maybe"}) is None
    assert teacher.clean_reasoner({**d, "safe_to_leave": "perhaps"}) is None
    assert teacher.clean_reasoner({**d, "confidence": "nan"}) is None
    assert teacher.clean_reasoner(None) is None
    assert teacher.clean_reasoner(d, {"lock_in": "none"})["lock_in"] == "none"


def test_eta_from_next_line():
    assert teacher.eta_s("rushing TD likely within 1:15") == 75
    assert teacher.eta_s("shot in 45 s") == 45
    assert teacher.eta_s("goal in 2 min") == 120
    assert teacher.eta_s("routine play next") is None


# ------------------------------------------------------------------------------- held-out leakage

def test_test_nights_get_blind_prompts_only_and_never_see_the_future():
    rows = [row(1, "train"), row(2, "valid"), row(3, "test")]
    reqs = teacher.reasoner_requests(rows, SYSTEM)
    tasks = {(r["id"].split("|")[0], r["task"]) for r in reqs}
    assert (rows[2]["id"], "reasoner_blind") in tasks
    assert (rows[2]["id"], "reasoner_grounded") not in tasks
    assert (rows[0]["id"], "reasoner_grounded") in tasks and (rows[1]["id"], "reasoner_grounded") in tasks
    for r in reqs:
        text = " ".join(m["content"] for m in r["messages"])
        if r["task"] == "reasoner_blind":
            assert "SECRET FUTURE" not in text and "ACTUALLY HAPPENED" not in text
            assert r["messages"][0]["content"].startswith(SYSTEM)       # same question the student gets
            assert r["messages"][1]["content"] == rows[2]["brief"]
    grounded = next(r for r in reqs if r["task"] == "reasoner_grounded")
    assert "SECRET FUTURE Passing Touchdown 1" in grounded["messages"][1]["content"]
    assert '"lock_in": "must_see"' in grounded["messages"][1]["content"]


def test_blind_train_sample_is_optional():
    rows = [row(i, "train") for i in range(10)]
    assert not [r for r in teacher.reasoner_requests(rows) if r["task"] == "reasoner_blind"]
    assert len([r for r in teacher.reasoner_requests(rows, blind_train_every=5) if r["task"] == "reasoner_blind"]) == 2


def test_watchdog_prompts_never_show_held_out_wordings():
    novel = {"stick_link": {"test": ["HELD TEST adb refused"], "valid": ["HELD VALID adb reset"],
                            "train": ["ERROR redzone.tuners.adb: closed", "ERROR redzone.control: protocol fault"]},
             "cosmetic": {"test": ["HELD TEST favicon"], "valid": [], "train": ["WARNING redzone.web: GET /x 404"]}}
    reqs = teacher.watchdog_requests(novel, per_kind=3)
    assert len(reqs) == 6
    blob = json.dumps(reqs)
    assert "HELD" not in blob
    assert all(r["followup"]["task"] == "wd_classify" and "{item}" in r["followup"]["messages"][1]["content"]
               for r in reqs)
    # older flat shape: first two wordings were test-only
    old = teacher.watchdog_requests({"storage": ["T1", "T2", "ERROR redzone.store: disk I/O error"]}, per_kind=1)
    assert "T1" not in json.dumps(old) and "disk I/O" in json.dumps(old)


def test_watchdog_wordings_keep_only_blind_agreements():
    ans = [{"id": "wd|stick_link|0", "task": "wd_paraphrase", "kind": "stick_link", "followups": [
        {"item": "ERROR redzone.tuners.adb: refused by 10.0.0.9:5555", "text": '{"kind": "stick_link"}'},
        {"item": "ERROR redzone.tuners.adb: refused by 10.0.0.9:5555", "text": '{"kind": "stick_link"}'},   # dupe
        {"item": "WARNING redzone.sources.http: espn 503", "text": '{"kind": "upstream"}'},             # disagrees
        {"item": "held one", "text": '{"kind": "stick_link"}'},                                          # held out
    ]}, {"id": "x|blind", "task": "reasoner_blind", "text": GOOD}]
    w = teacher.watchdog_wordings(ans, held_out=["HELD ONE"])
    assert w == {"stick_link": ["ERROR redzone.tuners.adb: refused by 10.0.0.9:5555"]}


# ------------------------------------------------------------------------------------- datasets

def test_student_swaps_only_the_words_and_keeps_graded_fields_and_order():
    rows = {"train": [row(1, lock="watch", safe="yes"), row(2, lock="none", t_big=None, safe="yes")],
            "valid": [row(3, "valid")]}
    chats = {k: [chat_of(r) for r in v] for k, v in rows.items()}
    # teacher tries to change lock_in / safe: ignored; its words are used
    ans = [{"id": f"{rows['train'][0]['id']}|grounded",
            "text": WORDS[:-1] + ', "lock_in": "must_see", "safe_to_leave": "no", "stay_s": 5}'},
           {"id": f"{rows['train'][1]['id']}|grounded", "text": "sorry, no json"},
           {"id": f"{rows['valid'][0]['id']}|grounded",
            "text": '{"storyline": "what actually happened next was a TD", "why": "in hindsight, a TD", "next": "TD"}'}]
    out = student.build(rows, chats, ans)
    t0 = json.loads(out["train"][0]["messages"][2]["content"])
    assert t0["lock_in"] == "watch" and t0["safe_to_leave"] == "yes" and t0["stay_s"] == 120 and t0["confidence"] == 0.8
    assert t0["storyline"] == "BUF two-minute drive, down 3" and t0["next"] == "TD try within 1:00"
    assert list(t0) == list(rows["train"][0]["target"])                      # lock_in first, export order
    assert out["train"][1] == chats["train"][1]                               # unusable answer: rule words kept
    assert out["valid"][0] == chats["valid"][0]                               # leaked hindsight: rejected
    assert [m["src"] for m in out["meta"]] == ["teacher", "rule", "rule"]
    assert len(student.build(rows, chats, ans, teacher_only=True)["train"]) == 1


def test_student_refuses_misaligned_or_test_rows():
    import pytest
    rows = {"train": [row(1), row(2)]}
    with pytest.raises(ValueError):
        student.build(rows, {"train": [chat_of(row(1))]}, [])
    with pytest.raises(AssertionError):
        student.build(rows, {"train": [chat_of(row(2)), chat_of(row(1))]}, [])
    with pytest.raises(ValueError):
        student.build({"train": [row(1, "test")]}, {"train": [chat_of(row(1, "test"))]}, [])


# ------------------------------------------------------------------------------------------ eval

def test_score_counts_unsafe_leaves_and_lift():
    rows = [row(1, "test", t_big=30.0), row(2, "test", lock="none", t_big=None, safe="yes"),
            row(3, "test", lock="none", t_big=None, safe="yes"), row(4, "test", lock="watch", t_big=900.0, safe="yes")]
    preds = {rows[0]["id"]: {"lock_in": "must_see", "safe_to_leave": "no", "next": "TD within 1:00"},
             rows[1]["id"]: {"lock_in": "must_see", "safe_to_leave": "yes", "next": ""},
             rows[2]["id"]: {"lock_in": "none", "safe_to_leave": "yes", "next": ""},
             rows[3]["id"]: None}
    s = evaluate.score(rows, preds)
    assert s["usable_json_pct"] == 75.0 and s["big_base_rate_pct"] == 25.0
    assert s["must_see_precision"] == 50.0 and s["must_see_recall"] == 100.0 and s["must_see_lift"] == 2.0
    assert s["unsafe_leave_pct"] == 0.0 and s["eta_mae_s"] == 30.0


def test_compare_fits_baselines_on_valid_and_scores_test_only():
    rows = ([row(i, "valid", key=90, head=0.9) for i in range(5)] +
            [row(i + 5, "valid", lock="none", t_big=None, safe="yes", key=10, head=0.1) for i in range(5)] +
            [row(20, "test", key=85, head=0.85), row(21, "test", lock="none", t_big=None, safe="yes", key=15, head=0.2)])
    teach = {f"{rows[-2]['id']}|blind": {"id": "x", "text": GOOD}}
    res = evaluate.compare(rows, {"today": None, "head": None, "teacher": teach})
    assert res["today"]["all"]["n"] == 2 and res["today"]["all"]["lock_acc"] == 100.0
    assert res["head"]["all"]["must_see_precision"] == 100.0
    assert res["teacher"]["all"]["answered_pct"] == 50.0 and res["teacher"]["all"]["usable_json_pct"] == 100.0
    assert set(res["thresholds"]["today"]) == {"nfl"}
    assert "| must_see_lift |" in evaluate.table(res)


# -------------------------------------------------------------------------------------- notebook

def test_notebook_cells_compile_and_match_the_script():
    src = (build_notebook.HERE / "teacher_distill.py").read_text()
    cells = build_notebook.cells_of(src)
    code = [c for c in cells if c["cell_type"] == "code"]
    assert cells[0]["cell_type"] == "markdown" and len(code) >= 4
    for c in code:
        compile("".join(c["source"]), "cell", "exec")
    nb = json.loads((build_notebook.HERE / "teacher_distill.ipynb").read_text())
    assert [c["source"] for c in nb["cells"]] == [c["source"] for c in cells], \
        "teacher_distill.ipynb is stale: run python agent/kaggle/build_notebook.py"


def test_watchdog_novel_is_read_without_importing_make_data(tmp_path):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("distill_cli", Path(__file__).resolve().parents[1] / "scripts" / "distill.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    f = tmp_path / "make_data.py"
    f.write_text("from agent.policy import DOES_NOT_EXIST\nNOVEL: dict = {'paused': {'test': ['t'], 'valid': [], 'train': ['x']}}\n")
    assert cli.novel(str(f)) == {"paused": {"test": ["t"], "valid": [], "train": ["x"]}}
    assert cli.novel(None)          # this checkout's make_data.py


def test_ship_gate_needs_perfect_json_and_near_zero_unsafe_leaves():
    assert evaluate.gate({"usable_json_pct": 100.0, "lock_acc": 95.0, "unsafe_leave_pct": 0.3}) == []
    assert len(evaluate.gate({"usable_json_pct": 99.9, "lock_acc": 94.9, "unsafe_leave_pct": 2.0})) == 3
    assert evaluate.gate({"usable_json_pct": 100.0, "lock_acc": 99.0, "unsafe_leave_pct": None})     # no leaves said: can't pass blind


def test_time_split_keeps_held_out_nights_locked_and_games_whole():
    from agent.distill import split
    rows = []
    for lg, nights in {"nba": [f"202610{d:02d}" for d in range(20, 32)] + ["20261101"],
                       "nfl": ["20250907", "20250914", "20250921", "20250928", "20251005", "20251012",
                               "20251019", "20260104", "20261101"]}.items():
        for k, n in enumerate(nights):
            for i in range(3):
                rows.append({"id": f"{lg}:{k}:{i}", "league": lg, "night": n})
    split.assign(rows)
    by = {(r["league"], r["night"]): r["split"] for r in rows}
    for n in ("20261029", "20261030", "20261031", "20261101"):
        assert by[("nba", n)] == "locked"
    assert by[("nfl", "20261101")] == by[("nfl", "20260104")] == "locked"
    assert by[("nba", "20261020")] == by[("nfl", "20250907")] == "train"
    for lg in ("nba", "nfl"):
        ns = {n for (l2, n) in by if l2 == lg}
        assert sum(by[(lg, n)] == "locked" for n in ns) >= 0.25 * len(ns)
    rows[0]["split"] = "test"           # one play of a train game moved: the leakage check catches it
    with pytest.raises(AssertionError):
        split.check(rows)


def test_pack_parts_reassemble_exactly(tmp_path):
    import subprocess
    from agent.kaggle.pack_data import split_parts
    pack = tmp_path / "kin"
    (pack / "code").mkdir(parents=True)
    (pack / "manifest.json").write_text("{}")
    (pack / "big.bin").write_bytes(__import__("os").urandom(120_000))
    names = split_parts(pack, tmp_path / "parts", part_bytes=50_000)
    assert names[0] == "rz_pack.partaa.bin" and len(names) >= 2
    assert all((tmp_path / "parts" / n).stat().st_size <= 50_000 for n in names)
    joined = tmp_path / "j.tar.gz"
    joined.write_bytes(b"".join((tmp_path / "parts" / n).read_bytes() for n in sorted(names)))
    (tmp_path / "out").mkdir()
    subprocess.run(["tar", "-xzf", str(joined), "-C", str(tmp_path / "out"), "--strip-components=1"], check=True)
    assert (tmp_path / "out" / "big.bin").read_bytes() == (pack / "big.bin").read_bytes()
    assert (tmp_path / "out" / "manifest.json").exists()
