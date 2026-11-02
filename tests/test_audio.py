import random

from redzone.screen.audio import BLOCK_S, detect_surges, match_delay


def _room(seconds: float, start: float = 1000.0, base_db: float = -40.0, seed: int = 1):
    rng = random.Random(seed)
    return [(start + i * BLOCK_S, base_db + rng.uniform(-1.5, 1.5)) for i in range(int(seconds / BLOCK_S))]


def _roar(env, at: float, length: float, rise: float):
    return [(t, db + rise) if at <= t < at + length else (t, db) for t, db in env]


def test_finds_crowd_roar_and_measures_delay():
    env = _roar(_room(180), at=1100.0, length=4.0, rise=12)
    surges = detect_surges(env)
    assert len(surges) == 1 and abs(surges[0].t - 1100.0) < 0.5
    # The touchdown happened at 1058 (wallclock) -> screen ~42s behind.
    assert match_delay(1058.0, surges) == 42.0


def test_ignores_short_blips():
    env = _roar(_room(180), at=1100.0, length=0.5, rise=15)  # a cough / door slam
    assert detect_surges(env) == []


def test_ambiguous_evidence_is_rejected():
    env = _roar(_roar(_room(240), at=1100.0, length=3, rise=12), at=1150.0, length=3, rise=12)
    surges = detect_surges(env)
    assert len(surges) == 2
    assert match_delay(1058.0, surges) is None            # two candidates: don't guess
    assert match_delay(1058.0, surges, expected=40) == 42.0  # a prior estimate disambiguates


def test_quiet_room_gives_nothing():
    assert match_delay(1000.0, detect_surges(_room(200))) is None
