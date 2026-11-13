"""[F-01b CONFIG-TRUTH 2] Config keys the code reads by a COMPUTED name are YAML keys, and every shipped default is a legal setting.

tests/test_config_literal_keys.py (F-01) checks the reads that pass a dotted string literal. This test checks the three other
kinds, all found by reading the code (AST), not by listing them here:
  1. a dataclass whose `from_config` reads `<prefix><field name>` for every field (fill, slate, arbiter, queue, ...);
  2. a module table of defaults that a reader indexes by its key argument (`supervisor.DEFAULTS`, `buffered.BJ_DEFAULTS`, ...);
  3. a helper that builds the key as an f-string from its parameters (`get(f"labels.{name}")`), at every call site that
     passes literal strings.
The Config refuses every setting the YAML lacks (`Config.validate`: "Unknown setting"), so a key read this way and missing from
the YAML can never be set from data/config.yaml or the phone. The `park.*` families have their own test
(tests/test_park_config_keys.py: aliases, the `max_park` rename) and are skipped here by class name.

The second half checks the other end of the same promise: every key the YAML ships passes the Config's own limits
(`Config.rejected == []` when the shipped file is loaded as a user file, and a full `validate(defaults, defaults)`).

Why: F-01 (PR 443) listed what it did not cover: about 59 keys read through f-strings (fill 9, slate 19, arbiter 16, queue 15)
and 32 of supervisor.DEFAULTS had no YAML key, and nine shipped defaults were refused by their own limits (decider
.wait_for_my_team_s 1800 against 1000); this test is what keeps the next unwired read and the next tight limit from shipping.
"""
from __future__ import annotations

import ast
import copy
import dataclasses as dc
import functools
import importlib
import math
import re
from pathlib import Path

import pytest
import yaml

from redzone.config import LIMITS, Config, ConfigError, validate

HERE = Path(__file__).resolve().parents[1]
GONE = object()
KEY = re.compile(r"^[a-z_][a-z0-9_]*(\.[A-Za-z0-9_]+)+$")   # a dotted config key (a plain dict's `get(f"{side}_names")` is not one)
READS_FIRST = {"get", "default"}                 # cfg.get("a.b"), the injected getter get("a.b"), config.default("a.b")
READS_SECOND = {"for_league", "any_league"}      # for_league(cfg, "a.b", league), any_league(cfg, "a.b")
PARK_TESTED_ELSEWHERE = {"ShowParams", "FactsParams", "TimingParams", "CatchupParams", "PackParams", "ParkParams"}
READ_ELSEWHERE = {("DirectorParams", "league_weight")}   # from_config skips it: the weights come from score.league_weight


# ---------------------------------------------------------------------------------------------- reading the code
@functools.lru_cache(maxsize=1)
def trees() -> dict[Path, ast.Module]:
    out = {}
    for p in sorted((HERE / "redzone").rglob("*.py")):
        try:
            out[p] = ast.parse(p.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
    return out


def module_of(p: Path) -> str:
    parts = list(p.relative_to(HERE).with_suffix("").parts)
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def key_arg(call: ast.Call):
    fn = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
    if fn in READS_FIRST and call.args:
        return call.args[0]
    return call.args[1] if fn in READS_SECOND and len(call.args) > 1 else None


def fstring_parts(js: ast.JoinedStr):
    """['text', ('slot', 'name'), 'text'] for an f-string of constants and bare names; None for anything else."""
    out = []
    for v in js.values:
        if isinstance(v, ast.Constant):
            out.append(v.value)
        elif isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name) and v.format_spec is None:
            out.append(("slot", v.value.id))
        else:
            return None
    return out


def functions(tree: ast.Module):
    """(name, args, node) for each def, and for each `name = lambda ...`."""
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield n.name, n.args, n
        elif isinstance(n, ast.Assign) and isinstance(n.value, ast.Lambda) and isinstance(n.targets[0], ast.Name):
            yield n.targets[0].id, n.value.args, n.value


@functools.lru_cache(maxsize=1)
def config_families() -> list[tuple[str, str, str]]:
    """(module, class, prefix) of each class whose `from_config` reads a key made of a prefix and a field's `.name`."""
    out = []
    for p, tree in trees().items():
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            for fn in (n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "from_config"):
                names = [a.arg for a in fn.args.args][len(fn.args.args) - len(fn.args.defaults):]
                kwdefault = {n: d.value for n, d in zip(names, fn.args.defaults) if isinstance(d, ast.Constant)}
                for call in (c for c in ast.walk(fn) if isinstance(c, ast.Call)):
                    arg = key_arg(call)
                    for n in ast.walk(arg) if arg is not None else ():
                        if isinstance(n, ast.JoinedStr) and len(n.values) == 2 and isinstance(n.values[0], ast.Constant) \
                                and isinstance(n.values[1], ast.FormattedValue) and is_field_name(n.values[1].value):
                            out.append((module_of(p), cls.name, n.values[0].value))
                        elif isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add) and isinstance(n.left, ast.Name) \
                                and n.left.id in kwdefault and is_field_name(n.right):
                            out.append((module_of(p), cls.name, kwdefault[n.left.id]))
    return sorted(set(out))


def is_field_name(e: ast.AST) -> bool:
    return isinstance(e, ast.Attribute) and e.attr == "name"


@functools.lru_cache(maxsize=1)
def defaults_tables() -> list[tuple[str, str, str]]:
    """(key pattern, module, table) for each module-level dict that a config reader indexes with its own key argument:
    `get(f"prefix.{key}")` beside `TABLE[key]` / `TABLE.get(key)`, or the same with the table (and the prefix) passed in by
    the caller. The pattern has one `{}` where the table's key goes."""
    out = []
    for p, tree in trees().items():
        dicts = {n.targets[0].id if isinstance(n, ast.Assign) else n.target.id for n in tree.body
                 if isinstance(n, (ast.Assign, ast.AnnAssign)) and isinstance(n.value, ast.Dict)
                 and isinstance(n.targets[0] if isinstance(n, ast.Assign) else n.target, ast.Name)}
        for name, args, node in functions(tree):
            params = [a.arg for a in args.args + args.kwonlyargs]
            for call in (c for c in ast.walk(node) if isinstance(c, ast.Call)):
                arg = key_arg(call)
                parts = fstring_parts(arg) if isinstance(arg, ast.JoinedStr) else None
                if not parts:
                    continue
                for slot in (x[1] for x in parts if isinstance(x, tuple) and x[1] in params):
                    for table in indexed_by(node, slot):
                        if table in dicts and [x for x in parts if isinstance(x, tuple)] == [("slot", slot)]:
                            out.append((pattern(parts, slot, {}), module_of(p), table))
                        elif table in params:                 # the caller passes the table and the prefix
                            out += bound_tables(p, tree, name, params, parts, slot, table, dicts)
    return sorted(set(out))


def indexed_by(fn_node: ast.AST, slot: str) -> set[str]:
    """Names X for which the function has `X[slot]` or `X.get(slot ...)`."""
    out = set()
    for n in ast.walk(fn_node):
        if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and isinstance(n.slice, ast.Name) and n.slice.id == slot:
            out.add(n.value.id)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get" \
                and isinstance(n.func.value, ast.Name) and n.args and isinstance(n.args[0], ast.Name) and n.args[0].id == slot:
            out.add(n.func.value.id)
    return out


def pattern(parts: list, key_slot: str, bound: dict) -> str:
    return "".join("{}" if x == ("slot", key_slot) else bound.get(x[1], "?") if isinstance(x, tuple) else x for x in parts)


def bound_tables(p, tree, fname, params, parts, key_slot, table_param, dicts) -> list[tuple[str, str, str]]:
    out = []
    for call in (c for c in ast.walk(tree) if isinstance(c, ast.Call) and getattr(c.func, "id", "") == fname):
        by = dict(zip(params, call.args))
        by.update({k.arg: k.value for k in call.keywords})
        t = by.get(table_param)
        others = [x[1] for x in parts if isinstance(x, tuple) and x[1] != key_slot]
        if isinstance(t, ast.Name) and t.id in dicts and all(isinstance(by.get(o), ast.Constant) for o in others):
            out.append((pattern(parts, key_slot, {o: by[o].value for o in others}), module_of(p), t.id))
    return out


@functools.lru_cache(maxsize=1)
def computed_reads() -> dict[str, str]:
    """key -> 'file:line' for every config read whose key is an f-string of parameters, at each call site that passes literals."""
    all_trees = trees()
    by_module = {module_of(p): p for p in all_trees}
    imports = {p: imports_of(p, t, by_module) for p, t in all_trees.items()}
    helpers = []
    for p, tree in all_trees.items():
        for name, args, node in functions(tree):
            params = [a.arg for a in args.args + args.kwonlyargs]
            for call in (c for c in ast.walk(node) if isinstance(c, ast.Call)):
                arg = key_arg(call)
                parts = fstring_parts(arg) if isinstance(arg, ast.JoinedStr) else None
                slots = [x[1] for x in parts or () if isinstance(x, tuple)]
                if slots and all(s in params for s in slots):
                    helpers.append((p, name, params, parts, slots))
    sites: dict[str, list[tuple[Path, ast.Call]]] = {}               # callee's short name -> every call of it
    for p, tree in all_trees.items():
        for c in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
            sites.setdefault(c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", ""), []).append((p, c))
    found: dict[str, str] = {}
    for hp, name, params, parts, slots in helpers:
        method = params[0] in ("self", "cls")
        for p, call in sites.get(name, ()):
            if not calls(call.func, name, p, hp, imports, by_module):
                continue
            values = {}
            for s in slots:
                i = params.index(s) - int(method)
                v = call.args[i] if 0 <= i < len(call.args) else None
                v = next((k.value for k in call.keywords if k.arg == s), v)
                if not (isinstance(v, ast.Constant) and isinstance(v.value, str)):
                    break
                values[s] = v.value
            else:
                key = "".join(values[x[1]] if isinstance(x, tuple) else x for x in parts)
                if KEY.match(key):
                    found.setdefault(key, f"{p.relative_to(HERE)}:{call.lineno}")
    return found


def imports_of(p: Path, tree: ast.Module, by_module: dict[str, Path]) -> dict[str, tuple[str, str | None]]:
    pkg = module_of(p).split(".")
    pkg = pkg if p.name == "__init__.py" else pkg[:-1]
    out: dict[str, tuple[str, str | None]] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            base = n.module or ""
            if n.level:
                base = ".".join(pkg[: len(pkg) - (n.level - 1)] + ([n.module] if n.module else []))
            for a in n.names:
                out[a.asname or a.name] = (f"{base}.{a.name}", None) if f"{base}.{a.name}" in by_module else (base, a.name)
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.asname:
                    out[a.asname] = (a.name, None)
    return out


def calls(func: ast.expr, name: str, in_file: Path, helper_file: Path, imports: dict, by_module: dict) -> bool:
    """True when `func` (the callee of a call in `in_file`) is the helper `name` defined in `helper_file`."""
    if isinstance(func, ast.Name) and func.id == name:
        src = imports[in_file].get(name)
        return in_file == helper_file or bool(src and by_module.get(src[0]) == helper_file and src[1] == name)
    if isinstance(func, ast.Attribute) and func.attr == name:
        if isinstance(func.value, ast.Name) and func.value.id not in ("self", "cls"):
            src = imports[in_file].get(func.value.id)
            if src and src[1] is None:
                return by_module.get(src[0]) == helper_file
        return in_file == helper_file
    return False


# ---------------------------------------------------------------------------------------------- reading the YAML
def yaml_get(data: dict, dotted: str):
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return GONE
        node = node[part]
    return node


def default_of(f: dc.Field):
    return f.default_factory() if f.default is dc.MISSING else f.default


@functools.lru_cache(maxsize=1)
def needed_keys() -> dict[str, str]:
    """key -> where the code reads it, for the three kinds above."""
    need: dict[str, str] = {}
    for module, cls_name, prefix in config_families():
        if cls_name in PARK_TESTED_ELSEWHERE:
            continue
        cls = getattr(importlib.import_module(module), cls_name)
        for f in dc.fields(cls):
            if (cls_name, f.name) not in READ_ELSEWHERE:
                need.setdefault(f"{prefix}{f.name}", f"{module}.{cls_name}.{f.name}")
    for pat, module, table in defaults_tables():
        for k in getattr(importlib.import_module(module), table):
            need.setdefault(pat.format(k), f"{module}.{table}[{k!r}]")
    for key, where in computed_reads().items():
        need.setdefault(key, where)
    return need


def missing_from(data: dict) -> list[str]:
    return sorted(f"{k} ({where})" for k, where in needed_keys().items() if yaml_get(data, k) is GONE)


@pytest.fixture(scope="module")
def cfg():
    return Config(user_path=HERE / "tests" / "no-such-config.yaml")


# ---------------------------------------------------------------------------------------------- 1. every computed read has a key
def test_the_scan_sees_the_families_the_tables_and_the_helpers():
    assert {p for _, c, p in config_families() if c not in PARK_TESTED_ELSEWHERE} >= {
        "fill.", "slate.", "arbiter.", "queue.", "threads.", "threads.arrival.", "park.director."}
    assert {(m.rsplit(".", 1)[-1], t) for _, m, t in defaults_tables()} >= {
        ("supervisor", "DEFAULTS"), ("buffered", "BJ_DEFAULTS"), ("canary", "CANARY_DEFAULTS"), ("join_watchdog", "WATCHDOG_DEFAULTS")}
    assert len(computed_reads()) > 150, "the helper scan has stopped seeing the code's reads"
    assert "labels.stale_s" in computed_reads() and "timing.behind_live_s.label_settle_s" in computed_reads()


def test_every_key_the_code_reads_by_a_computed_name_is_in_the_yaml(cfg):
    assert missing_from(cfg.defaults) == []


def test_the_scan_catches_a_key_the_yaml_lacks(cfg):
    """A check that cannot fail proves nothing: drop one key of each kind and each must be named."""
    data = copy.deepcopy(cfg.defaults)
    for dotted in ("slate.hold_s", "supervisor.every_s", "join.watchdog.max_s", "labels.stale_s", "queue.tune_s"):
        *path, leaf = dotted.split(".")
        node = data
        for part in path:
            node = node[part]
        del node[leaf]
    got = " ".join(missing_from(data))
    for dotted in ("slate.hold_s", "supervisor.every_s", "join.watchdog.max_s", "labels.stale_s", "queue.tune_s"):
        assert f"{dotted} (" in got, dotted


# ---------------------------------------------------------------------------------------------- 2. the YAML says what the code says
# A YAML value that is NOT the code's own default, and why. Every other computed-name key ships at the value the code falls back to,
# so a key present and a key absent read the same: that is what makes adding one decision-identical.
SHIPPED_DIFFERENT = {
    "arbiter.plan": "ships OFF (MUST-LIVE: Nick decides); the dataclass default is True. Set when the key was added, before F-01b",
    "supervisor.budget": "a group the code defaults to {}: the objectives live in redzone/observability.BUDGET_DEFAULTS",
    "supervisor.mac_health": "a group the code defaults to {}: redzone/platform/mac_health.py reads its own defaults",
    "supervisor.night_ops": "a group the code defaults to {}: redzone/platform/night_ops.py reads its own defaults",
}


def same(a, b) -> bool:
    """Equal as the config sees it: a tuple is a list, an infinite cap is a null, 95 is 95.0, True is not 1."""
    a, b = (list(x) if isinstance(x, tuple) else None if isinstance(x, float) and math.isinf(x) else x for x in (a, b))
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def test_every_family_field_and_table_entry_ships_at_the_code_default(cfg):
    bad, checked = [], 0
    for module, cls_name, prefix in config_families():
        if cls_name in PARK_TESTED_ELSEWHERE:
            continue
        for f in dc.fields(getattr(importlib.import_module(module), cls_name)):
            key = f"{prefix}{f.name}"
            if (cls_name, f.name) in READ_ELSEWHERE or key in SHIPPED_DIFFERENT:
                continue
            checked += 1
            if not same(yaml_get(cfg.defaults, key), default_of(f)):
                bad.append(f"{key}: yaml {yaml_get(cfg.defaults, key)!r} != code {default_of(f)!r}")
    for pat, module, table in defaults_tables():
        for k, v in getattr(importlib.import_module(module), table).items():
            key = pat.format(k)
            checked += 1
            if key not in SHIPPED_DIFFERENT and not same(yaml_get(cfg.defaults, key), v):
                bad.append(f"{key}: yaml {yaml_get(cfg.defaults, key)!r} != code {v!r}")
    assert checked > 250
    assert not bad, bad


def test_the_values_that_differ_are_still_in_the_yaml_and_still_different(cfg):
    """The allow-list stays honest: a name on it that now matches the code, or that left the YAML, comes off."""
    from redzone.noggin.arbiter import Params
    from redzone.supervisor import DEFAULTS
    assert cfg.get("arbiter.plan") is False and Params().plan is True
    for k in ("budget", "mac_health", "night_ops"):
        assert DEFAULTS[k] == {} and cfg.get(f"supervisor.{k}") != {}


# ---------------------------------------------------------------------------------------------- 3. every shipped default is a legal setting
def leaves(d: dict, path: str = ""):
    for k, v in d.items():
        p = f"{path}.{k}" if path else str(k)
        if isinstance(v, dict) and v and not any("." in str(x) for x in v):    # a flat map of dotted keys is one setting
            yield from leaves(v, p)
        else:
            yield p, v


def nest(dotted: str, value) -> dict:
    out = value
    for part in reversed(dotted.split(".")):
        out = {part: out}
    return out


def test_every_shipped_default_passes_the_configs_own_limits(cfg, tmp_path):
    shipped = {k: v for k, v in cfg.defaults.items() if k != "league_overrides"}   # its keys are dotted paths: the whole pass below
    refused = []
    for key, v in leaves(shipped):
        try:
            validate(nest(key, v), cfg.defaults)
        except ConfigError as e:
            refused.append(str(e))
    assert refused == []
    validate(cfg.defaults, cfg.defaults)                                         # the whole tree in one pass, league_overrides too
    assert Config(user_path=tmp_path / "none.yaml", history_dir=tmp_path / "hist").rejected == []
    f = tmp_path / "config.yaml"                                                 # and as a user file: setting every key to its own value
    f.write_text(yaml.safe_dump(cfg.defaults))
    assert Config(user_path=f, history_dir=tmp_path / "hist").rejected == []


def test_the_scan_of_the_limits_catches_a_default_outside_them(cfg):
    for dotted, v in (("decider.wait_for_my_team_s", 7201), ("timing.nerds_shots_s", [11.0, 500.0])):
        data = copy.deepcopy(cfg.defaults)
        node = data
        *path, leaf = dotted.split(".")
        for part in path:
            node = node[part]
        node[leaf] = v
        with pytest.raises(ConfigError, match=leaf):
            validate(data, cfg.defaults)


# The nine defaults F-01 found outside their own limits: (key, the value it has always shipped, the new top of its limit).
# Fixed by widening the limit (redzone/config.py LIMITS), never by changing the value.
NINE = [
    ("score.nfl.wp_swing.fade_s", 120, 1800), ("score.nba.run_fade_s", 120, 1800), ("decider.wait_for_my_team_s", 1800, 7200),
    ("tuners.unauthorized_retry_s", 300, 3600), ("tuners.launch_timeout_s", 30, 120), ("governor.swap_critical_pct", 90, 100),
    ("replay.montage_age_s", 14400, 86400), ("timing.nerds_shots_s", [11.0, 17.0, 22.0], 120),
]


@pytest.mark.parametrize("key,shipped,top", NINE, ids=[n[0] for n in NINE])
def test_each_limit_admits_the_value_the_key_always_shipped_with_and_still_bites(cfg, key, shipped, top):
    assert yaml_get(cfg.defaults, key) == shipped                                # the VALUE did not move
    validate(nest(key, shipped), cfg.defaults)
    over = [top + 1] if isinstance(shipped, list) else top + 1
    with pytest.raises(ConfigError):
        validate(nest(key, over), cfg.defaults)


def test_a_list_of_numbers_is_a_setting_and_a_list_of_names_is_still_names(cfg):
    """`timing.nerds_shots_s` is the one numeric list: it was refused as 'not a list of names' even at its own value."""
    validate({"timing": {"nerds_shots_s": [5, 9.5]}}, cfg.defaults)
    for bad in ({"timing": {"nerds_shots_s": ["a"]}}, {"timing": {"nerds_shots_s": 12}}, {"timing": {"nerds_shots_s": [True]}},
                {"timing": {"nerds_shots_s": [-1]}}, {"slate": {"leagues": [1, 2]}}):
        with pytest.raises(ConfigError):
            validate(bad, cfg.defaults)
    validate({"slate": {"leagues": ["nhl"]}}, cfg.defaults)


def test_every_limit_names_a_yaml_key_or_group_and_is_a_real_range(cfg):
    """A limit on a misspelt key limits nothing."""
    assert [k for k in LIMITS if yaml_get(cfg.defaults, k) is GONE] == []
    assert [k for k, (lo, hi) in LIMITS.items() if lo > hi] == []
