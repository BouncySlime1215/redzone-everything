"""Read-only lab recording: explicit devices, timed probes, and bounded human labels."""
import json
import sqlite3

import pytest

from scripts import screen_lab as lab


class Clock:
    def __init__(self):
        self.t = 0.0

    def wall(self):
        return 1_800_000_000 + self.t

    def mono(self):
        return self.t

    async def sleep(self, seconds):
        self.t += seconds


class ObservedStick:
    host = "192.168.1.22:5555"

    def __init__(self, clock, connected=True):
        self.clock, self.connected = clock, connected
        self.calls = []

    async def _read(self, name, value):
        self.calls.append(name)
        await self.clock.sleep(0.2)
        return value

    async def connect(self):
        return await self._read("connect", self.connected)

    async def playback(self):
        return await self._read("playback", {"state": "playing", "pkg": "player", "position": self.clock.t * 1000})

    async def foreground(self):
        return await self._read("foreground", "player/.WatchActivity")

    async def screen_text(self):
        return await self._read("ui_text", "Game title | Live")

    async def close(self):
        return await self._read("close", None)


def sample(at, pos=1000, state="playing", length=0.2, pkg="player"):
    begin = {"unix": at, "monotonic": at - 100}
    end = {"unix": at + length, "monotonic": at - 100 + length}
    return {"kind": "sample", "session_id": "test", "begin": begin, "end": end,
            "probes": {"playback": {"begin": begin, "end": end,
                                    "value": {"state": state, "pkg": pkg, "position": pos}}}}


def session(samples, start=100, end=200):
    return {"start": {"kind": "session_start", "session_id": "test", "at": {"unix": start}},
            "samples": samples,
            "end": {"kind": "session_end", "session_id": "test", "at": {"unix": end}}}


def test_explicit_host_never_defaults_to_first_saved_device():
    devices = {"sticks": [{"host": "192.168.1.10:5555", "name": "Xbox-room"},
                          {"host": "192.168.1.22:5555", "name": "Monitor"}]}
    selected = lab.select_stick("192.168.1.22", devices)
    assert selected.host == "192.168.1.22:5555" and selected.name == "Monitor"
    assert lab.select_stick("192.168.1.99:5555", devices).host == "192.168.1.99:5555"
    with pytest.raises(ValueError, match="explicitly"):
        lab.select_stick("", devices)


async def test_capture_bounds_every_probe_and_closes_session(tmp_path):
    clock = Clock()
    stick = ObservedStick(clock)
    out = tmp_path / "capture.jsonl"
    await lab._record(stick, out, session_id="unique-session", duration=5,
                      clock=clock.wall, monotonic=clock.mono, sleep=clock.sleep)
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert all(row["session_id"] == "unique-session" for row in rows)
    assert rows[0]["kind"] == "session_start" and rows[-1]["kind"] == "session_end"
    assert rows[-1]["status"] == "complete"
    assert rows[0]["at"]["utc"].endswith("+00:00")
    samples = [row for row in rows if row["kind"] == "sample"]
    assert len(samples) == 3
    for row in samples:
        assert "label" not in row and "live_play" not in row
        for read in row["probes"].values():
            assert read["end"]["monotonic"] > read["begin"]["monotonic"]
            assert read["end"]["unix"] > read["begin"]["unix"]
    assert stick.calls[0] == "connect" and stick.calls[-1] == "close"
    assert set(stick.calls) == {"connect", "playback", "foreground", "ui_text", "close"}


async def test_failed_connection_still_closes_and_records_session_end(tmp_path):
    clock, out = Clock(), tmp_path / "capture.jsonl"
    stick = ObservedStick(clock, connected=False)
    with pytest.raises(RuntimeError, match="selected stick"):
        await lab._record(stick, out, session_id="failed", duration=5,
                          clock=clock.wall, monotonic=clock.mono, sleep=clock.sleep)
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert rows[-1]["kind"] == "session_end" and rows[-1]["status"] == "interrupted"
    assert stick.calls == ["connect", "close"]


async def test_failed_probe_is_error_evidence_not_a_playback_state():
    clock = Clock()
    async def fail():
        await clock.sleep(0.4)
        raise OSError("read timed out")
    result = await lab.probe(fail, clock.wall, clock.mono)
    assert "value" not in result and "OSError" in result["error"]
    assert result["end"]["monotonic"] - result["begin"]["monotonic"] == 0.4


def test_prior_session_and_old_labels_never_carry_forward():
    current = session([sample(110), sample(150), sample(180)])
    result = lab.summarize(current, [(90, "game"), (115, "commercial")], label_max_age=30)
    assert result["labeled"] == 0 and result["counts"] == {}


def test_media_playing_occurs_under_different_human_labels_without_accuracy_claim():
    current = session([sample(110), sample(120), sample(130)])
    result = lab.summarize(current, [(109, "game"), (119, "commercial"), (129, "replay")])
    assert result["counts"] == {("game", "playing"): 1, ("commercial", "playing"): 1, ("replay", "playing"): 1}
    assert "accuracy" not in result and result["speeds"] == {}


def test_label_change_during_probe_is_ambiguous():
    result = lab.summarize(session([sample(110, length=2)]), [(109, "game"), (111, "commercial")])
    assert not result["counts"] and result["labeled"] == 0


def test_position_speed_never_crosses_label_boundary_seek_or_app_change():
    current = session([sample(110, 1000), sample(112, 3000), sample(114, 2000),
                       sample(116, 4000, pkg="other"), sample(118, 6000, pkg="other")])
    result = lab.summarize(current, [(109, "game"), (117, "commercial")])
    assert result["speeds"] == {"game": [1.0]}


def test_report_ignores_legacy_unbounded_rows(tmp_path, capsys):
    path = tmp_path / "old.jsonl"
    path.write_text(json.dumps({"t": 100, "state": "playing", "pos": 1000}) + "\n")
    lab.report(path, tmp_path / "missing.db")
    assert "No session-bound recording" in capsys.readouterr().out
    assert not (tmp_path / "missing.db").exists()


def test_report_is_bound_to_recording_and_does_not_create_or_mutate_database(tmp_path, capsys):
    path, db_path = tmp_path / "screen.jsonl", tmp_path / "labels.db"
    current = session([sample(110), sample(160)])
    path.write_text("\n".join(json.dumps(row) for row in [current["start"], *current["samples"], current["end"]]))
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE labels (ts REAL, label TEXT)")
        db.executemany("INSERT INTO labels VALUES (?,?)", [(90, "old"), (109, "replay")])
    before = db_path.read_bytes()
    lab.report(path, db_path)
    output = capsys.readouterr().out
    assert "1/2 samples" in output and "not classifier accuracy" in output
    assert "you labeled old" not in output and "you labeled replay" in output
    assert db_path.read_bytes() == before


@pytest.mark.parametrize("args", [[], ["--report"], ["--host", "192.168.1.22", "--duration", "nan"]])
def test_cli_requires_specific_target_or_recording_before_work(monkeypatch, args):
    monkeypatch.setattr("sys.argv", ["screen_lab.py", *args])
    with pytest.raises(SystemExit) as error:
        lab.main()
    assert error.value.code == 2
