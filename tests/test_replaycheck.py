"""Replay diagnostics must not turn archive gaps or an always-hold policy into success."""
from types import SimpleNamespace

from redzone.decider.screen import DelayBook
from redzone.lab import backtest
from redzone.lab.replaycheck import ArchiveProxy, ExitAudit, ReplayClock, load_date, timestamp_quality, utc
from redzone.models import League


def replay(wall=(100.0, 200.0), raw=None):
    raw = raw or wall
    plays = [{'id': str(i), 'wallclock': utc(t), 'type': {'text': 'Rush'}, 'text': 'run'}
             for i, t in enumerate(raw)]
    return backtest.Replay(League.NFL, 'g', {}, plays, list(wall), matchup='A @ B')


def test_clock_maps_historical_time_without_current_wallclock():
    clock = ReplayClock(1793563200, speed=2)
    assert clock.historical_at(30) == 1793563260
    assert clock.elapsed_at(1793563260) == 30
    assert clock.historical_at(clock.elapsed_at(1793563277)) == 1793563277


def test_exact_date_never_substitutes_an_available_older_slate(tmp_path):
    folder = tmp_path / 'nfl'
    folder.mkdir()
    (folder / 'scoreboard_20261031.json').write_text('{"events": []}')
    games, manifest = load_date(tmp_path, League.NFL, '20261101')
    assert not games and manifest['status'] == 'unavailable'
    assert manifest['requested_date'] == '20261101' and manifest['missing'] == ['scoreboard']


def test_archive_window_catches_action_between_clear_endpoints():
    proxy = ArchiveProxy(replay(wall=(50.0, 100.0, 200.0)))
    assert proxy.at(90)[0] and proxy.at(105)[0]
    assert proxy.window(90, 15) == 'unsafe_proxy'
    assert proxy.window(110, 8) == 'clear_proxy'


def test_timestamp_normalization_is_unknown_not_safe_evidence():
    r = replay(wall=(100.0, 100.0, 200.0), raw=(100.0, 90.0, 200.0))
    quality = timestamp_quality(r)
    assert quality['backward_transitions'] == 1 and quality['normalized_timestamps'] == 1
    assert ArchiveProxy(r).window(110, 8) == 'unknown'


def test_always_hold_reports_missed_clear_opportunities(cfg):
    r = replay(wall=(50.0, 100.0, 200.0))
    audit = ExitAudit([r], true_delay=40, step=5, horizon=8, cfg=cfg, clock=ReplayClock(0))
    dec = SimpleNamespace(timelines={}, delays=DelayBook({'youtubetv': 40}, 3))
    audit.observe(150, dec)  # source screen time110, clear until118
    result = audit.summary()
    assert result['counts']['allowed'] == 0
    assert result['counts']['held_on_clear_proxy'] == 1
    assert result['counts']['model_unknown'] == 1
    assert result['visual_detection_accuracy'] is None


def test_lab_observer_receives_historical_clock_before_selection(cfg):
    class EmptyReplay(backtest.Replay):
        def state_at(self, idx):
            return None
    r = replay(wall=(1793563200.0, 1793563300.0))
    r = EmptyReplay(r.league, r.gid, r.data, r.plays, r.wall)
    seen = []
    backtest.run([r], cfg, set(), start=1793563200.0, end=1793563210.0, step=5,
                 on_tick=lambda now, dec: seen.append((now, dec.current)))
    assert seen == [(1793563200.0, None), (1793563205.0, None), (1793563210.0, None)]
