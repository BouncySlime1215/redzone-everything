"""Sunday Ticket scan: resumable, finished by T-20; app-version alerts. Fakes only (no stick, no TV, no adb).

Why: today's scan starts at 12:45, already past T-20 for a 1:00 kickoff, and a stop in the middle starts over.
Both behaviours are behind autonomy.st_scan_early / autonomy.app_version_alerts, read with code default OFF."""
from datetime import datetime
from types import SimpleNamespace

from redzone.autonomy import ET, SUN, Autonomy, AppCheck, Hooks, Paths
from redzone.models import League, Phase

KICK = datetime(2026, 11, 8, 13, 0, tzinfo=ET).timestamp()    # a Sunday 1:00 PM kickoff
T75, T20 = KICK - 75 * 60, KICK - 20 * 60
CODES = [f"c{i}" for i in range(16)]
BATCH_S = 35.0


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class FakeController:
    two_stick = True
    hidden = object()
    on_target = None
    live_control = True
    usable = []

    def __init__(self, clock):
        self.clock, self.batches = clock, []

    async def scan_sunday_ticket(self, codes, games, on_found):
        self.batches.append(list(codes))
        self.clock.t += BATCH_S
        return {codes[0]: games[0].game_id} if len(self.batches) == 1 else {}


def build(tmp_path, clock, options, stop_after_batch=None):
    ctl = FakeController(clock)
    toasts = []
    game = SimpleNamespace(league=League.NFL, phase=Phase.PRE, start_time=KICK, game_id="g1")
    hooks = Hooks(ctl, SimpleNamespace(st_codes=[], st_slots={}, save=lambda: None), cfg=None,
                  games=lambda: [game], toast=lambda t, level="info": toasts.append((level, t)))
    a = Autonomy(hooks, paths=Paths.under(tmp_path), clock=clock, options=options)
    a._st_candidates = lambda: list(CODES)
    a._st_games = lambda now: [game]

    async def safe():   # one big moment after batch `stop_after_batch`, then clear
        if stop_after_batch and len(ctl.batches) == stop_after_batch and not getattr(safe, "hit", False):
            safe.hit = True
            return "a big moment"
        return None
    a._st_safe = safe
    return a, ctl, toasts


async def test_all_batches_finish_before_t20_with_a_stop_in_the_middle(tmp_path):
    clock = Clock(T75)
    a, ctl, _ = build(tmp_path, clock, {"st_scan_early": True}, stop_after_batch=2)
    first = await a._st_scan()
    assert first.ok is False and "stopped early" in first.message and len(ctl.batches) == 2
    clock.t += 120                                  # the scheduler's retry_s
    second = await a._st_scan()
    assert second.ok is not False or "after T-20" not in second.message
    scanned = [c for b in ctl.batches for c in b]
    assert scanned == CODES                         # every code once, none repeated after the stop
    assert len(ctl.batches) == 4 and clock.t < T20
    assert "after T-20" not in second.message and "all channels read" in second.message


async def test_resume_survives_a_restart(tmp_path):
    clock = Clock(T75)
    a, ctl, _ = build(tmp_path, clock, {"st_scan_early": True}, stop_after_batch=1)
    await a._st_scan()
    b, ctl2, _ = build(tmp_path, clock, {"st_scan_early": True})   # new object, same state file
    await b._st_scan()
    assert [c for x in ctl2.batches for c in x] == CODES[4:]


async def test_flag_off_is_todays_scan_and_todays_times(tmp_path):
    clock = Clock(T75)
    a, ctl, _ = build(tmp_path, clock, {}, stop_after_batch=2)
    r = await a._st_scan()
    assert r.ok is True and len(ctl.batches) == 2 and "st_scan" not in a.memory   # stop = done, as today
    job = a.scheduler.jobs["st_scan"]
    assert (job.when[0].hour, job.when[0].minute, job.attempts) == (12, 45, 2)
    assert job.when[0].days == (SUN,)


async def test_early_job_starts_at_t75_for_both_windows(tmp_path):
    a, _, _ = build(tmp_path, Clock(T75), {"st_scan_early": True})
    job = a.scheduler.jobs["st_scan"]
    assert [(w.hour, w.minute) for w in job.when] == [(11, 45), (15, 10)] and job.attempts == 4


async def test_version_change_alerts_only_when_flag_on(tmp_path):
    stick = SimpleNamespace(host="1.2.3.4", name="Left", model="", packages={})
    for flag, expect in ((True, 1), (False, 0)):
        a, _, toasts = build(tmp_path, Clock(T75), {"app_version_alerts": flag})
        a._record(stick, "youtubetv", "full", AppCheck(True, "ok", "1.0"))
        a._record(stick, "youtubetv", "full", AppCheck(True, "ok", "1.0"))   # same version: quiet
        assert not toasts
        a._record(stick, "youtubetv", "full", AppCheck(True, "ok", "2.0"))
        assert len(toasts) == expect
        if expect:
            assert "1.0 -> 2.0" in toasts[0][1]
