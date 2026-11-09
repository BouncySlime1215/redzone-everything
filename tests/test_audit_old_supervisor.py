"""AUDIT-OLD, supervisor: a rule's "held for this long" clock must see every tick it depends on.

Why: rule_stuck_switch returned early during a switch, so its other clock never saw the break in the condition.
"""
from types import SimpleNamespace

from test_supervisor import world  # noqa: F401  (the fixture)


class _Running:
    def done(self):
        return False


async def test_a_fresh_controller_command_is_not_cancelled_on_an_old_clock(world):  # noqa: F811
    """Repro: the ctl_task clock started on a preload, then a switch ran for 200 s. The rule returned early during the
    switch without clearing that clock, so the next command to be running was "stuck" the moment the switch ended."""
    w = world
    w.control._task = _Running()
    await w.s.rule_stuck_switch(w.clock())                # a preload: the clock starts
    w.control._task = None
    w.e._pending_decision, w.e._pending_since = SimpleNamespace(target="youtubetv:espn"), w.clock()
    w.clock.t += 200.0
    w.e._pending_since = w.clock()                        # a healthy switch, not a stuck one
    await w.s.rule_stuck_switch(w.clock())
    w.e._pending_decision = None
    w.control._task = _Running()                          # a brand-new command
    await w.s.rule_stuck_switch(w.clock())
    assert w.control.cancelled == 0


async def test_a_command_that_really_is_stuck_is_still_cancelled(world):  # noqa: F811
    w = world
    w.control._task = _Running()
    await w.s.rule_stuck_switch(w.clock())
    w.clock.t += 200.0
    await w.s.rule_stuck_switch(w.clock())
    assert w.control.cancelled == 1
