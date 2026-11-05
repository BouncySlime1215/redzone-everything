"""NBA.com stats pbp for the SportVU games (real trimmed fixture: 0021500001, DET 106 at ATL 94)."""
import sys
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pbp import nba_stats_events as N, schema  # noqa: E402

FX = Path(__file__).parent / "fixtures/pbp/nba_stats_events_min.csv"


def test_convert_real_fixture():
    rows = N.convert_game(pd.read_csv(FX), "0021500001", "20151027", "ATL", "DET")
    assert len(rows) == 65 and schema.validate(rows) == []
    assert rows[0]["kind"] == "start_period" and rows[1]["kind"] == "jump_ball"
    assert (rows[-1]["home_score"], rows[-1]["away_score"]) == (94, 106) and rows[-1]["kind"] == "end_period"
    assert {r["side"] for r in rows} <= {"home", "away", ""} and rows[0]["clock_s"] == 720
    made = [r for r in rows if r["kind"] == "made_shot"]
    assert made and all(r["value"] in (2.0, 3.0) for r in made)
    assert all(r["tag"] == schema.PERSONAL and r["wall_ts"] is None for r in rows)


def _txz(name: str, df) -> bytes:
    import io
    import lzma
    import tarfile
    raw = df.to_csv(index=False).encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tar:
        info = tarfile.TarInfo(name)
        info.size = len(raw)
        tar.addfile(info, io.BytesIO(raw))
    return lzma.compress(buf.getvalue())


def test_pull_season_dates_home_and_shot_xy(tmp_path):
    pytest.importorskip("pyarrow")
    pbp = pd.read_csv(FX)
    pbp["GAME_ID"] = 21500001
    made = pbp[pbp.EVENTMSGTYPE == 1].EVENTNUM.iloc[0]
    sd = pd.DataFrame({"GAME_ID": [21500001], "GAME_EVENT_ID": [made], "LOC_X": [0], "LOC_Y": [-50],
                       "GAME_DATE": [20151027], "HTM": ["ATL"], "VTM": ["DET"]})
    blobs = {"nbastats": _txz("nbastats_2015.csv", pbp), "shotdetail": _txz("shotdetail_2015.csv", sd)}

    class R:
        def __init__(self, b):
            self.status_code, self.content = (200, b) if b else (404, b"")

    class S:
        headers: dict = {}

        def get(self, url, timeout=None):
            return R(next((b for k, b in blobs.items() if f"/{k}_2015" in url), None))

    m = N.pull_season(2015, tmp_path, session=S())
    assert m["games"] == 1 and m["season"] == 2016 and m["tag"] == schema.PERSONAL and m["undated_games"] == 0
    df = pd.read_parquet(tmp_path / "nba_stats_pbp/2016.parquet")
    assert df.game_id.iloc[0] == "0021500001" and df.home.iloc[0] == "ATL" and df.night.iloc[0] == "20151027"
    shot = df[df.kind == "made_shot"].iloc[0]
    assert shot.x == pytest.approx(0.5) and shot.y == pytest.approx(0.0)
    assert N.pull_season(2015, tmp_path, playoffs=True, session=S()) is None     # no playoff file -> nothing
