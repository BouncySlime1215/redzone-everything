"""[EXP-005] Off-policy logging: each decision carries its option set and propensities. North Star:
% of oracle (off-policy grading). Owner: lane 2 (noggin logic). Logging only: no behavior change."""
from tests.test_decider import cands, dec, start_on  # noqa: F401


def _on(dec):
    dec.cfg.update({"decider": {"log_options": True}}, record=False)


def test_every_decision_lists_its_options_with_one_taken(dec, clock):
    _on(dec)
    start_on(dec, clock, "a", {"b": 40, "c": 20})
    d = dec.decide(cands(a=60, b=40, c=20))
    opts = {(o["a"], o["g"]): o for o in d.options}
    assert ("stay", "a") in opts and ("switch", "b") in opts and ("switch", "c") in opts
    assert sum(o["p"] for o in d.options) == 1.0 and d.propensity == 1.0
    taken = [o for o in d.options if o["p"] == 1.0][0]
    assert (taken["a"], taken["g"]) == (("stay", "a") if d.action == "stay" else (d.action, d.target))
    assert opts[("switch", "b")]["score"] == 40.0


def test_off_by_default_and_choices_unchanged(cfg, clock):
    from redzone.decider.decider import Decider
    from redzone.decider.screen import DelayBook
    from tests.test_decider import with_fresh_timing

    def run(on):
        d = Decider(cfg, DelayBook({"youtubetv": 40.0}, sigma_default=15.0, clock=clock), clock=clock)
        with_fresh_timing(d, clock)
        if on:
            _on(d)
        t0, out = clock.t, []
        for i, sc in enumerate([(90, 40), (30, 80), (30, 95), (99, 10)] * 3):
            clock.t = t0 + 40 * i
            x = d.decide(cands(a=sc[0], b=sc[1]))
            out.append((x.action, x.target, x.kind, bool(x.options)))
        clock.t = t0
        return out
    off, on = run(False), run(True)
    assert [o[:3] for o in off] == [o[:3] for o in on]
    assert not any(o[3] for o in off) and all(o[3] for o in on)
