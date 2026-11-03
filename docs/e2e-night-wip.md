# End-to-end night test: work in progress (stopped 2026-11-03 for the usage limit)

Code: `redzone/lab/night/` (vclock.py virtual time, house.py fake sticks + TV, faults.py fault
hook, run.py the night + grading, `python -m redzone.lab.night`). Not wired into pytest yet.

Where it stands: everything is written, but the first run spins with virtual time stuck at 0.
Debugging showed the loop has 4 timers scheduled, nothing ready, no threads out, yet the clock
never advances. Likely cause: the loop's selector wrapper. `asyncio.SelectorEventLoop.__init__`
sets `self._selector = selector`, but the wrapper's `select()` advances `self._clock`, and
`_run_once` computes `timeout` from `self.time()`; check that `select(timeout)` actually gets a
positive timeout (it may be called with 0 because `_scheduled[0]._when` is behind
`self.time()` by the loop's clock resolution, or `CLOCK.t0` was reset in Night.prepare after the
timers were created). First step next session: print `timeout` inside `_Selector.select`.

Then: run a 30-min slice, fix the fakes until the real Controller tunes/flips cleanly, run the
whole night with the default faults, add `tests/test_e2e_night.py` (CI runs `tests/test_e2e*.py`
as its own step; must be Linux-only fakes, uses committed fixtures, no data/ archive needed),
self-review, draft PR into integration/next (or codex/resume-redzone if it doesn't exist yet).
The platform thread's redzone/platform/faults.py plugs in through `--faults module:function`.
