"""One-brain step 5: ONE measured break table + ONE BreakBook (behind breaks.one_model, default off)."""
import json

from redzone.config import Config
from redzone.contracts.breaks import BREAK_TABLE_PATH, BreakTable, load_table
from redzone.scoring import breaks


def test_committed_table_has_provenance_and_never_read_locked_nights():
    t = load_table()
    assert isinstance(t, BreakTable) and t.version == "v1"
    assert "locked test nights never read" in t.provenance["split"]
    for lg in ("nfl", "nba", "nhl"):
        assert t.provenance["leagues"][lg]["locked_skipped"] > 0 and t.provenance["leagues"][lg]["games"] > 0
    for k, m in t.rules.items():
        assert k in breaks.LEN or k == "review" or k == "injury", k
        assert 0 < m.q10_s <= m.q50_s <= m.q90_s


def test_measured_medians_replace_only_well_measured_priors():
    t = load_table()
    lens = breaks.lengths_from(t)
    assert breaks.lengths_from(None) is breaks.LEN
    for k, v in breaks.LEN.items():
        m = t.rule(k)
        assert lens[k] == (m.q50_s if m is not None and m.n >= breaks.MIN_N else v), k
    assert lens["cbb_media"] == breaks.LEN["cbb_media"]       # no CBB archive: the prior stays
    assert lens["nhl_tv"] == t.rule("nhl_tv").q50_s


def test_the_table_reproduces_the_priors_where_the_definitions_match():
    """NFL and NHL priors were measured the same way: the table must agree within a few seconds."""
    t = load_table()
    for k in ("nfl_2min", "nfl_quarter", "nfl_half", "nfl_team_to", "nhl_tv", "nhl_goal", "nhl_intermission"):
        assert abs(t.rule(k).q50_s - breaks.LEN[k]) <= 10, (k, t.rule(k).q50_s, breaks.LEN[k])


def test_flag_off_keeps_two_books_and_the_old_lengths(tmp_path):
    from redzone.scoring.drive import DriveScorer
    cfg = Config(user_path=tmp_path / "c.yaml")
    d = DriveScorer(cfg)
    assert d.breaks.table is None and d.breaks.lens is breaks.LEN


def test_flag_on_shares_one_book(tmp_path):
    from redzone.scoring.drive import DriveScorer
    (tmp_path / "c.yaml").write_text("breaks:\n  one_model: true\n")
    cfg = Config(user_path=tmp_path / "c.yaml")
    d = DriveScorer(cfg)
    assert d.breaks.table is not None and d.breaks.lens["nba_long"] == load_table().rule("nba_long").q50_s
    book = breaks.BreakBook(table=d.breaks.table)
    d.use_breaks(book)
    assert d.breaks is book


def test_table_file_is_what_the_lab_script_writes():
    d = json.loads(BREAK_TABLE_PATH.read_text())
    assert d["provenance"]["script"] == "scripts/measure_break_table.py"
    assert set(d["stoppage"]) == {"nfl", "nba", "nhl"}


def test_engine_owns_the_one_book(tmp_path):
    from redzone.engine import Engine
    from redzone.store import Store
    for on in (False, True):
        d = tmp_path / str(on)
        d.mkdir()
        (d / "config.yaml").write_text(f"breaks:\n  one_model: {str(on).lower()}\n")
        e = Engine(Config(user_path=d / "config.yaml", history_dir=d / "hist"), Store(d / "rz.db"))
        assert (e.scorer.breaks is e.breaks) is on
        assert (e.breaks.table is not None) is on
