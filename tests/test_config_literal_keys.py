"""[F-01 CONFIG-TRUTH] Every config key the app's code reads by a literal name is a key in default_config.yaml.

The Config rejects every setting the YAML lacks (`Config.validate`: "Unknown setting"), so a key the code reads but the YAML
omits can never be set from data/config.yaml or the phone, and the YAML says less than the code does. PR 438 closed this for
`park.*` (tests/test_park_config_keys.py); this test closes it for every other literal read in redzone/: a call that passes a
dotted string literal to `get(...)` / `cfg.get(...)` / `config.default(...)` / `for_league(cfg, ...)` / `any_league(cfg, ...)`.
Reads built with an f-string (`get(f"slate.{name}")`, the dataclass families) are not literals and are not checked here.

F-01 also removed two blocks nothing read: the 15-key `flags:` block (redzone/contracts/flags.py: `enabled()` has no
production caller) and `eyes.enabled` (read by nothing yet). A key added in the PR that reads it is the rule from now on.

Why: PR 438 found 82 park keys the YAML lacked and F-01 measured 26 more across decider, delay, control, score.context and
others (24 got a key, 2 are allow-listed below); the CI test is what keeps the next unwired read from shipping (WORKPLAN F-01).
"""
from __future__ import annotations

import ast
import copy
import re
from pathlib import Path

import pytest
import yaml

from redzone.config import Config, ConfigError, validate

ROOT = Path(__file__).resolve().parents[1]
MISSING = object()

KEY = re.compile(r"^[a-z_][a-z0-9_]*(\.[A-Za-z0-9_]+)+$")
FIRST_ARG = {"get", "default"}                  # cfg.get("a.b"), the injected getter get("a.b"), config.default("a.b")
SECOND_ARG = {"for_league", "any_league"}       # for_league(cfg, "a.b", league), any_league(cfg, "a.b")
NOT_CONFIG_SUFFIX = (".json", ".jsonl", ".yaml", ".yml", ".txt", ".csv", ".log", ".png", ".bmp", ".mp4", ".npz", ".py", ".md")
NOT_CONFIG_RECEIVERS = {"sys.modules", "os.environ", "os.environ.copy()"}
OPEN_ENDED = ("teams", "mode.leagues", "replay.apps")   # groups Config.validate leaves open: any name under them is allowed


def _name(func: ast.expr) -> str:
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def literal_reads(root: Path = ROOT / "redzone") -> dict[str, list[tuple[str, int, ast.Call]]]:
    """key -> [(file, line, the call)] for every dotted string literal given to a config reader under `root`."""
    out: dict[str, list[tuple[str, int, ast.Call]]] = {}
    for p in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(p.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = _name(node.func)
            arg = node.args[0] if fn in FIRST_ARG and node.args else node.args[1] if fn in SECOND_ARG and len(node.args) > 1 else None
            if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str) and KEY.match(arg.value)):
                continue
            key = arg.value
            if key.endswith(NOT_CONFIG_SUFFIX) or key.startswith(OPEN_ENDED):
                continue
            if isinstance(node.func, ast.Attribute) and ast.unparse(node.func.value) in NOT_CONFIG_RECEIVERS:
                continue
            out.setdefault(key, []).append((str(p.relative_to(ROOT)), node.lineno, node))
    return out


def missing_keys(reads: dict, data: dict) -> list[str]:
    def has(key: str) -> bool:
        node = data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return False
            node = node[part]
        return True
    return sorted(f"{k} ({reads[k][0][0]}:{reads[k][0][1]})" for k in reads if not has(k))


@pytest.fixture(scope="module")
def cfg():
    return Config(user_path=ROOT / "tests" / "no-such-config.yaml")


@pytest.fixture(scope="module")
def reads():
    return literal_reads()


# Keys read with an inline fallback INSIDE A FROZEN GRADER FILE (docs/grader_freeze.json) and deliberately not in the YAML:
# a YAML default would only be read if the grader read it (the key falls back to another key, `start_bias_s` / `bias_sd_s`, so
# a number or a null in the YAML would change that fallback), and the grader is Nick's to change, not ours. Adding one of
# these to the YAML needs a grader change: Nick's call. Each entry names why; the test below keeps the list honest.
FROZEN_GRADER_FALLBACKS = {
    "sync.break_anchor.true_gap_s": "redzone/lab/backtest.py ad_breaks(): falls back to sync.break_anchor.start_bias_s",
    "sync.break_anchor.true_gap_sd_s": "redzone/lab/backtest.py ad_breaks(): falls back to sync.break_anchor.bias_sd_s",
}


def test_every_literal_config_key_the_code_reads_is_in_the_yaml(cfg, reads):
    assert len(reads) > 400, f"the scan found only {len(reads)} keys: it has stopped seeing the code's reads"
    missing = [m for m in missing_keys(reads, cfg.defaults) if m.split(" ")[0] not in FROZEN_GRADER_FALLBACKS]
    assert missing == []


def test_the_frozen_grader_allow_list_is_only_for_keys_read_in_frozen_files_and_absent_from_the_yaml(cfg, reads):
    import json
    frozen = set(json.loads((ROOT / "docs" / "grader_freeze.json").read_text())["files"])
    for key in FROZEN_GRADER_FALLBACKS:
        files = {f for f, _, _ in reads.get(key, ())}
        assert files and files <= frozen, f"{key} is read in {sorted(files)}: not (only) frozen grader files, so give it a YAML key"
        assert cfg.get(key, MISSING) is MISSING, f"{key} is in the YAML now: take it off the allow-list"


def test_the_scan_catches_a_key_the_yaml_lacks(cfg, reads):
    """A check that cannot fail proves nothing: drop one shipped key and one whole group, and both must be named."""
    data = copy.deepcopy(cfg.defaults)
    del data["control"]["max_switch_s"]
    del data["scoring"]
    got = missing_keys(reads, data)
    assert any(g.startswith("control.max_switch_s ") for g in got) and any(g.startswith("scoring.watch_value ") for g in got)


def test_the_shipped_config_rejects_nothing_and_unknown_settings_are_still_refused(tmp_path):
    assert Config(user_path=tmp_path / "none.yaml").rejected == []
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump({"flags": {"reel": False}, "eyes": {"enabled": True}}))
    got = Config(user_path=cfg_file, history_dir=tmp_path / "hist").rejected
    assert len(got) == 2 and all("Unknown setting" in g for g in got)       # flags.* and eyes.enabled are gone, not silently ignored


# What F-01 added, each at the value its reading code already used as the fallback: {key: value}. Flag-off is therefore
# decision-identical; a key absent from the YAML and a key present at this value read the same.
F01 = {
    "decider.fail_cooldown_s": 90, "decider.safe_auto_resume_s": 600, "decider.leave.high_leverage_recent_s": 30.0,
    "delay.boundary": True, "delay.scorebug": True, "delay.timing.app_sigma": None,
    "governor.footprint_log": True, "legal.leave_silent_s": 5.0, "queue.warn_within_s": 180.0,
    "replay.pause_poll_s": 0.4, "scoring.watch_value": False, "supervisor.ntfy_server": "https://ntfy.sh",
    "two_stick.break_p": 0.5, "timing.behind_live_s.shadow_stay_leave_every_s": 15.0,
    "control.max_switch_s": 150, "control.max_start_switch_s": 75,
    "score.context.lead_change_cap": 8, "score.context.lead_change_fade_s": 150, "score.context.fresh_flip": 0,
    "score.context.upset_min_spread": 3, "score.context.upset_full_spread": 10, "score.context.overtime": 4,
    "score.context.nba.late_within_6": 4, "score.context.nhl.late_one_goal": 10,
    # read through f"score.context.{league}.<name>" with a per-league fallback table in scoring/engine.py context()
    "score.context.nfl.wp_leverage": 0, "score.context.nba.wp_leverage": 0,
    "score.context.nhl.wp_leverage": 15, "score.context.mlb.wp_leverage": 6,
    "score.context.nfl.lead_change_each": 0, "score.context.nba.lead_change_each": 0,
    "score.context.nhl.lead_change_each": 2.0, "score.context.mlb.lead_change_each": 0,
    "score.context.nfl.upset": 0, "score.context.nba.upset": 8, "score.context.nhl.upset": 8, "score.context.mlb.upset": 8,
}


def _same(a, b) -> bool:
    return a == b and type(a) is type(b)


def test_each_added_key_is_at_the_value_the_code_already_fell_back_to(cfg):
    bad = [f"{k}: yaml {cfg.get(k, MISSING)!r} != {v!r}" for k, v in F01.items() if not _same(cfg.get(k, MISSING), v)]
    assert not bad, bad


def test_the_inline_fallback_at_every_literal_read_equals_the_yaml_value(cfg, reads):
    """For each added key, every call site that passes a literal fallback passes the very value the YAML now holds."""
    from redzone import supervisor
    assert F01["supervisor.ntfy_server"] == supervisor.DEFAULTS["ntfy_server"]     # read as `get(k, None) or DEFAULTS["ntfy_server"]`
    bad, checked = [], 0
    for key, want in F01.items():
        if key == "supervisor.ntfy_server":
            continue
        for f, line, call in reads.get(key, ()):
            if len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
                got = call.args[1].value
            elif len(call.args) == 1 and not call.keywords:
                got = None                                  # no fallback given: the read answers None
            else:
                continue                                    # a computed fallback
            checked += 1
            if not _same(got, want):
                bad.append(f"{f}:{line} {key}: code falls back to {got!r}, YAML holds {want!r}")
    assert checked >= 20
    assert not bad, bad


def test_every_added_key_is_a_legal_setting_at_its_own_value(tmp_path):
    """Setting a key to the value it ships with must not be refused (the generic score limit would have refused 150 s)."""
    over: dict = {}
    for key, v in F01.items():
        node = over
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = v
    validate(over, Config(user_path=tmp_path / "none.yaml").defaults)           # raises ConfigError on any refusal
    f = tmp_path / "config.yaml"
    f.write_text(yaml.safe_dump(over))
    assert Config(user_path=f, history_dir=tmp_path / "hist").rejected == []
    with pytest.raises(ConfigError):                                              # and the limit still bites above the new ceiling
        validate({"score": {"context": {"lead_change_fade_s": 1801}}}, Config(user_path=tmp_path / "none.yaml").defaults)


def test_score_context_is_identical_with_the_keys_present_and_absent(cfg):
    """The 20 score.context numbers sit in the YAML at the code's fallbacks: context() gives the same points, reasons and
    factors on a grid of games (every base league, regulation and overtime, spreads, win odds, lead-change counts) whether
    the config holds them or answers every read with the caller's fallback."""
    from redzone.models import GameState, League, Phase, Team
    from redzone.scoring.engine import History, closeness, context

    class Absent:                                           # every key missing: each read returns the caller's own fallback
        def get(self, k, default=None):
            return default

    now, n = 10_000.0, 0
    for lg in League:
        regulation = {League.NFL: 4, League.NBA: 4, League.NHL: 3, League.MLB: 9, League.CBB: 2, League.WCBB: 4, League.CFB: 4}[lg]
        for period in (1, regulation, regulation + 1):
            for clock in (None if lg == League.MLB else 90.0, None if lg == League.MLB else 290.0, None if lg == League.MLB else 600.0):
                for diff in (0, 1, -1, 3, -6, 14):
                    for spread in (None, 2.5, -4.0, 7.0, -12.5):
                        for wp in (None, 0.5, 0.92):
                            for changes, ago in ((0, None), (3, 20.0), (9, 200.0)):
                                g = GameState(lg, "g", Team("HOM", score=10 + max(diff, 0)), Team("AWY", score=10 + max(-diff, 0)),
                                              phase=Phase.LIVE, period=period, clock_s=clock, spread=spread, home_win_prob=wp)
                                h = History(lead_changes=changes, lead_change_at=None if ago is None else now - ago)
                                k = closeness(g)
                                want = context(g, copy.deepcopy(h), k, now, Absent())
                                got = context(g, copy.deepcopy(h), k, now, cfg)
                                assert got == want, (lg, period, clock, diff, spread, wp, changes, ago)
                                n += 1
    assert n > 5000
