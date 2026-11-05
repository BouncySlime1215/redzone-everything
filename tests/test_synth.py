"""Data division E: synthetic + teacher tokens (redzone/noggin/synth.py, scripts/synth_build.py)."""
import json

import numpy as np

from redzone.contracts import events as V
from redzone.noggin import synth as S
from scripts import synth_build as B

LIVE, TO = V.STOP0 + V.STOPS.index("none"), V.STOP0 + V.STOPS.index("timeout")


def game(n=40, seed=0):
    rng = np.random.default_rng(seed)
    out = [V.BOS0 + 2, V.TEAM0 + 5, V.TEAM0 + 9]
    for i in range(n):
        stop = TO if i % 7 == 6 else LIVE
        pts = V.PTS0 + (2 if i % 5 == 0 else 10 if i % 5 == 3 else 0)
        out += [V.KIND0 + int(rng.integers(0, 50)), V.DT0 + int(rng.integers(0, 20)), V.CLOCK0 + i % 60,
                V.MARGIN0 + V.MARGIN_CAP + int(rng.integers(-20, 21)), pts, stop]
    return np.asarray(out + [V.EOS], np.uint16)


def test_mirror_is_an_involution_and_flips_sides():
    g = game()
    m = S.mirror_game(g)
    assert np.array_equal(S.mirror_game(m), g)
    assert (m[1], m[2]) == (g[2], g[1])
    eg, em = S._events(g), S._events(m)
    c = V.MARGIN0 + V.MARGIN_CAP
    assert np.array_equal(em[:, 3].astype(int) - c, -(eg[:, 3].astype(int) - c))
    assert np.array_equal(em[:, [0, 1, 2, 5]], eg[:, [0, 1, 2, 5]])
    assert int(em[0, 4]) == V.PTS0 + 10 and int(em[3, 4]) == V.PTS0 + 2
    assert m.min() >= 0 and m.max() < V.VOCAB_SIZE


def test_retime_only_moves_live_gaps():
    g = game(200)
    r = S.retime_game(g, np.random.default_rng(1), jitter=0.10)
    eg, er = S._events(g), S._events(r)
    after_stop = np.concatenate([[False], eg[:-1, 5] != LIVE])
    assert np.array_equal(er[after_stop, 1], eg[after_stop, 1])          # break lengths exactly as recorded
    assert np.array_equal(er[:, [0, 2, 3, 4, 5]], eg[:, [0, 2, 3, 4, 5]])
    assert np.abs(er[:, 1].astype(int) - eg[:, 1].astype(int)).max() <= 1
    assert (er[:, 1] != eg[:, 1]).any()


def _write_shards(root, nights):
    games, toks, off = [], [], 0
    for i, night in enumerate(nights):
        g = game(30, i)
        games.append([f"g{i}", night, off, len(g)])
        toks.append(g)
        off += len(g)
    (root / "nba").mkdir(parents=True)
    np.save(root / "nba" / "2023.npy", np.concatenate(toks))
    (root / "nba" / "2023.json").write_text(json.dumps({"games": games, "tokens": off, "vocab": 1}))
    return off


def test_augment_skips_held_out_nights_and_respects_the_cap(tmp_path):
    real = _write_shards(tmp_path / "tok", ["20230101"] * 6 + ["20240301"] * 4)
    out = tmp_path / "syn"
    rc = B.main(["augment", "--shards", str(tmp_path / "tok"), "--out", str(out), "--train-before", "20240101",
                 "--mirror", "5", "--retime", "5", "--other-synthetic", f"nba={real}"])
    assert rc == 0
    man = json.loads((out / "manifest_augment.json").read_text())["per_league"]["nba"]
    assert man["synthetic_tokens"] <= S.CAP_X * real
    for aug in ("mirror", "retime"):
        meta = json.loads((out / "nba" / f"2023.{aug}.json").read_text())
        assert meta["synthetic"] and meta["license"] == S.LICENSE
        assert all(n < "20240101" and gid.endswith(f"~{aug}") for gid, n, _, _ in meta["games"])
    assert B.main(["check", str(out)]) == 0


def test_check_fails_over_cap(tmp_path):
    (tmp_path / "manifest_x.json").write_text(json.dumps({"per_league": {"nhl": {"real_tokens": 10,
                                                                                  "synthetic_tokens": 31}}}))
    assert B.main(["check", str(tmp_path)]) == 1


def test_teacher_answers_become_side_channel_tokens(tmp_path):
    ans = [{"id": "cbb:401:12|grounded", "task": "reasoner_grounded", "model": "Qwen3-32B-AWQ",
            "text": '<think>x</think> {"lock_in": "must_see", "safe_to_leave": "no", "confidence": 0.83, '
                    '"storyline": "tied late", "why": "w", "next": "within 1:00", "stay_s": 240}'},
           {"id": "nhl:77:3|blind", "task": "reasoner_blind", "text": '{"lock_in": "none", "confidence": "?"}'},
           {"id": "nhl:77:3|jump", "task": "jump_blind", "text": '{"jump": 1}'},
           {"id": "mlb:1:1|blind", "text": "no json here"}]
    p = tmp_path / "teacher_out.jsonl"
    p.write_text("\n".join(json.dumps(a) for a in ans) + "\n")
    assert B.main(["teacher", "--answers", str(p), "--out", str(tmp_path / "syn")]) == 0
    man = json.loads((tmp_path / "syn" / "manifest_teacher.json").read_text())
    assert man["unreadable"] == 1 and man["skipped_other_tasks"] == 1
    assert man["per_league"]["cbb"] == {"answers": 1, "hindsight": 1, "synthetic_tokens": 6}
    z = np.load(tmp_path / "syn" / "teacher" / "cbb.npz")
    t = [int(x) for x in z["tokens"][0]]
    assert t[1] == S.T_MODE0 and t[2] == S.T_LOCK0 + 2 and t[3] == S.T_SAFE0 + 1
    assert t[4] == S.T_CONF0 + 8 and t[5] == S.T_STAY0 + S.stay_bucket(240)
    assert max(t) < S.TEACHER_VOCAB and bool(z["hindsight"][0])
    n = np.load(tmp_path / "syn" / "teacher" / "nhl.npz")
    assert [int(x) for x in n["tokens"][0]][4] == S.T_CONF0 + S.CONF_BUCKETS         # missing confidence


def test_augment_index_has_reader_fields(tmp_path):
    _write_shards(tmp_path / "tok", ["20230101"] * 3)
    B.main(["augment", "--shards", str(tmp_path / "tok"), "--out", str(tmp_path / "syn"), "--train-before", "20240101"])
    meta = json.loads((tmp_path / "syn" / "nba" / "2023.mirror.json").read_text())
    assert meta["complete"] is True and "synthetic" in meta["tags"] and meta["license"]


def test_causal_commentary_queue_and_tokens(tmp_path):
    import gzip

    from agent.kaggle import commentary_queue as Q
    (tmp_path / "rows").mkdir()
    rows = [{"id": f"nba:9:{i}", "split": "train", "brief": f"NBA brief {i}", "crunch": i == 3, "mine": i == 5,
             "big_next": True, "next_text": "SECRET FUTURE", "t_big": 12.0} for i in range(8)]
    rows.append({"id": "nba:9:99", "split": "valid", "brief": "held out"})
    with gzip.open(tmp_path / "rows" / "nba_train.jsonl.gz", "wt") as f:
        f.write("\n".join(json.dumps(r) for r in rows))
    reqs = Q.build(tmp_path, ["nba", "nhl"])
    assert [r["id"] for r in reqs[:2]] == ["nba:9:5|commentary", "nba:9:3|commentary"]
    assert len(reqs) == 8 and "SECRET" not in json.dumps(reqs) and "held out" not in json.dumps(reqs)
    r = S.teacher_tokens({"id": "nba:9:5|commentary", "text": '{"importance": 7, "lock_in": "watch", "next": '
                                                              '"score", "confidence": 0.6, "commentary": "c"}'})
    assert r.tokens == (S.T_LEAGUE0 + V.BOS_LEAGUES.index("nba"), S.T_MODE0 + 2, S.T_LOCK0 + 1, S.T_IMP0 + 7,
                        S.T_NEXT0, S.T_CONF0 + 6)
    assert not r.hindsight and max(r.tokens) < S.TEACHER_VOCAB


def test_text_only_answers_are_not_counted_as_tokens(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"id": "nhl:5:1|grounded", "text": '{"storyline": "s", "why": "w", "next": "n"}'}) + "\n")
    assert B.main(["teacher", "--answers", str(p), "--out", str(tmp_path / "syn")]) == 0
    man = json.loads((tmp_path / "syn" / "manifest_teacher.json").read_text())
    assert man["per_league"]["nhl"] == {"answers": 0, "hindsight": 0, "synthetic_tokens": 0, "text_only": 1}
    assert not (tmp_path / "syn" / "teacher" / "nhl.npz").exists()
    assert "storyline" in (tmp_path / "syn" / "teacher" / "text.jsonl").read_text()


def v2_game(n=60, seed=3):
    """v1 event groups with v2-style (FIELD VALUE) pairs (ids above the v1 vocab) after each, plus a context block."""
    rng = np.random.default_rng(seed)
    hi = V.VOCAB_SIZE
    out = [V.BOS0 + 2, V.TEAM0 + 5, V.TEAM0 + 9, hi, hi + 7, hi + 900, hi + 1]
    for i in range(n):
        stop = TO if i % 6 == 5 else LIVE
        out += [V.KIND0 + int(rng.integers(0, 50)), V.DT0 + int(rng.integers(0, 20)), V.CLOCK0 + i % 60,
                V.MARGIN0 + V.MARGIN_CAP, V.PTS0, stop]
        out += [hi + 2 + int(rng.integers(0, 50)), hi + 1100 + int(rng.integers(0, 16))] * int(rng.integers(0, 4))
    return np.asarray(out + [V.EOS], np.uint16)


def test_retime_v2_only_moves_live_dt_ids(tmp_path):
    g = v2_game()
    r = S.retime_game(g, np.random.default_rng(2))
    st = S.event_starts(g)
    assert len(st) == 60
    changed = np.nonzero(r != g)[0]
    assert set(changed) <= set(st + 1)                              # only DT ids, never a field pair
    after_stop = st[1:][g[st[:-1] + 5] != LIVE]
    assert np.array_equal(r[after_stop + 1], g[after_stop + 1])     # break gaps untouched
    (tmp_path / "tok" / "nba").mkdir(parents=True)
    np.save(tmp_path / "tok" / "nba" / "2016.npy", g)
    (tmp_path / "tok" / "nba" / "2016.json").write_text(json.dumps({
        "vocab_version": 2, "vocab_size": 20001, "tokens": len(g), "games": 1, "real": True,
        "index": [["g1", "20160101", 0, len(g), 0]]}))
    B.main(["augment", "--shards", str(tmp_path / "tok"), "--out", str(tmp_path / "syn"), "--train-before", "20190901"])
    assert not (tmp_path / "syn" / "nba" / "2016.mirror.json").exists()
    meta = json.loads((tmp_path / "syn" / "nba" / "2016.retime.json").read_text())
    assert meta["vocab_version"] == 2 and meta["games"] == 1 and meta["index"][0][0] == "g1~retime"
