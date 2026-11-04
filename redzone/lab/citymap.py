"""The city map: every module of redzone/ and agent/, what it reads (imports, config sections, data
files), who uses it, and who owns it; plus the contract checks (redzone/contracts/map.py) run over it.

    from redzone.lab.citymap import build, violations
    m = build(ROOT)                  # {"modules": {...}, "edges": [...]}
    v = violations(m, ROOT)          # [{"rule", "module", "detail", "owner", "lane", "key"}]

Static only (the AST and the source text): nothing is imported or run. scripts/citymap.py writes
docs/citymap.md + docs/citymap.json from it; scripts/contract_check.py fails CI on a new violation.
"""
from __future__ import annotations

import ast
import fnmatch
import re
from pathlib import Path

from redzone.contracts import map as M

PACKAGES = ("redzone", "agent")
CONFIG_KEY = re.compile(r"""\.get\(\s*['"]([a-z_][a-z0-9_]*)\.[a-z0-9_.]+['"]""")
DATA_FILE = re.compile(r"""['"]([A-Za-z0-9_\-]+\.(?:jsonl|db|sqlite))['"]""")


def _expand(glob: str) -> list[str]:
    m = re.search(r"\{([^{}]*)\}", glob)
    if not m:
        return [glob]
    return [g for part in m.group(1).split(",") for g in _expand(glob[:m.start()] + part + glob[m.end():])]


def _match(path: str, globs) -> bool:
    return any(fnmatch.fnmatch(path, g) for pat in globs for g in _expand(pat))


def owner(path: str) -> str:
    for glob, who in M.OWNERS:
        if _match(path, [glob]):
            return who
    return "?"


def modname(path: str) -> str:
    p = path[:-3].replace("/", ".")
    return p[:-len(".__init__")] if p.endswith(".__init__") else p


def _files(root: Path) -> list[str]:
    out = []
    for pkg in PACKAGES:
        out += [str(p.relative_to(root)) for p in sorted((root / pkg).rglob("*.py"))
                if "__pycache__" not in p.parts]
    return out


def _imports(tree: ast.AST, mod: str, is_pkg: bool) -> set[str]:
    got: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            got |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            if n.level:
                base = mod.split(".")
                base = base[:len(base) - n.level + (1 if is_pkg else 0)]
                src = ".".join(base + ([n.module] if n.module else []))
            else:
                src = n.module or ""
            got.add(src)
            got |= {f"{src}.{a.name}" for a in n.names}     # `from pkg import module`
    return got


def _resolve(name: str, known: set[str]) -> str | None:
    """The module of ours an import names (a name inside a module resolves to that module)."""
    while name:
        if name in known:
            return name
        name = name.rpartition(".")[0]
    return None


def _timing_constants(tree: ast.Module) -> dict[str, float]:
    out = {}
    for n in tree.body:
        targets = n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(n, ast.AnnAssign) else []
        val = getattr(n, "value", None)
        if not isinstance(val, ast.Constant) or isinstance(val.value, bool) or not isinstance(val.value, (int, float)):
            continue
        for t in targets:
            if isinstance(t, ast.Name) and t.id.isupper() and t.id.endswith(M.SECONDS_SUFFIXES):
                out[t.id] = float(val.value)
    return out


def _class_timing(tree: ast.Module) -> dict[str, float]:
    """Class-level seconds constants (standby's FLIP_S = 3.0 lives in a class body)."""
    out = {}
    for c in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
        for n in c.body:
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) \
                    and isinstance(n.value.value, (int, float)) and not isinstance(n.value.value, bool):
                for t in n.targets:
                    if isinstance(t, ast.Name) and t.id.isupper() and t.id.endswith(M.SECONDS_SUFFIXES):
                        out[f"{c.name}.{t.id}"] = float(n.value.value)
    return out


def build(root: Path) -> dict:
    files = _files(root)
    known = {modname(f) for f in files}
    mods: dict[str, dict] = {}
    for f in files:
        src = (root / f).read_text(encoding="utf-8", errors="replace")
        mod = modname(f)
        try:
            tree = ast.parse(src)
        except SyntaxError as ex:
            mods[mod] = {"path": f, "owner": owner(f), "error": str(ex), "imports": [], "lines": 0}
            continue
        imps = sorted({r for r in (_resolve(i, known) for i in _imports(tree, mod, f.endswith("__init__.py")))
                       if r and r != mod})
        doc = (ast.get_docstring(tree) or "").strip().split("\n")[0][:140]
        mods[mod] = {"path": f, "owner": owner(f), "lines": src.count("\n") + 1, "doc": doc, "imports": imps,
                     "config": sorted(set(CONFIG_KEY.findall(src))), "data": sorted(set(DATA_FILE.findall(src))),
                     "timing": {**_timing_constants(tree), **_class_timing(tree)},
                     "json_loads": src.count("json.loads("), "main": "__name__ == \"__main__\"" in src
                     or "__name__ == '__main__'" in src, "_src": src}
    for m in mods.values():
        m["used_by"] = []
    for name, m in mods.items():
        for i in m["imports"]:
            mods[i]["used_by"].append(name)
    users = _outside_users(root, known)
    for name, m in mods.items():
        m["used_by"] = sorted(set(m["used_by"]))
        m["used_by_scripts"] = sorted(users["scripts"].get(name, ()))
        m["used_by_tests"] = len(users["tests"].get(name, ()))
    return {"modules": mods, "edges": [(a, b) for a, m in mods.items() for b in m["imports"]]}


def _outside_users(root: Path, known: set[str]) -> dict[str, dict[str, set[str]]]:
    """Which scripts and tests import each module (scripts are entry points, tests are not)."""
    out: dict[str, dict[str, set[str]]] = {"scripts": {}, "tests": {}}
    for kind in ("scripts", "tests"):
        for p in sorted((root / kind).rglob("*.py")):
            try:
                tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for i in _imports(tree, kind + "." + p.stem, False):
                r = _resolve(i, known)
                if r:
                    out[kind].setdefault(r, set()).add(str(p.relative_to(root)))
            # `python -m redzone.x` / runpy strings in scripts and tests
            for s in re.findall(r"""['"]((?:redzone|agent)(?:\.[a-z_0-9]+)+)['"]""", p.read_text(errors="replace")):
                r = _resolve(s, known)
                if r:
                    out[kind].setdefault(r, set()).add(str(p.relative_to(root)))
    return out


def reachable(m: dict) -> set[str]:
    mods = m["modules"]
    seeds = [n for n in mods if n in M.ENTRY_POINTS or mods[n]["used_by_scripts"] or mods[n].get("main")]
    seen: set[str] = set()
    stack = list(seeds)
    while stack:
        n = stack.pop()
        if n in seen or n not in mods:
            continue
        seen.add(n)
        stack += mods[n]["imports"]
        # a package's __init__ is loaded with any of its modules
        parts = n.split(".")
        stack += [".".join(parts[:k]) for k in range(1, len(parts))]
    return seen


def _v(rule: str, mod: str, m: dict, detail: str, key: str, line: int | None = None) -> dict:
    x = m["modules"].get(mod, {})
    own = x.get("owner", "?")
    where = x.get("path", mod) + (f":{line}" if line else "")
    return {"rule": rule, "where": where, "module": mod, "detail": detail, "owner": own, "lane": M.LANE.get(own),
            "key": f"{rule}|{key}"}


def _line(x: dict, pat: str) -> int | None:
    rx = re.compile(pat)
    return next((i for i, ln in enumerate(x.get("_src", "").splitlines(), 1) if rx.search(ln)), None)


def violations(m: dict) -> list[dict]:
    mods = m["modules"]
    out: list[dict] = []
    # 1. measured costs: a timing cost as a literal in code; a seconds constant defined in two modules
    cost = re.compile(M.COST_NAME)
    seen: dict[str, list[str]] = {}
    for name, x in mods.items():
        for const, val in x.get("timing", {}).items():
            bare = const.split(".")[-1]
            seen.setdefault(bare, []).append(name)
            if cost.match(bare):
                out.append(_v("timing-literal", name, m, f"{const} = {val:g}: a timing cost belongs in measured "
                              "config (timing.* / calib.*)", f"{name}.{const}", _line(x, rf"^\s*{bare}\s*[:=]")))
    for const, where in sorted(seen.items()):
        if len(set(where)) > 1 and const not in M.ALLOW_TWICE:
            for w in sorted(set(where)):
                out.append(_v("defined-twice", w, m, f"{const} is also defined in "
                              + ", ".join(x for x in sorted(set(where)) if x != w), f"{const}@{w}",
                              _line(mods[w], rf"^\s*{const}\s*[:=]")))
    # 2. boundaries
    for rule, pat, allowed in M.BOUNDARIES:
        rx = re.compile(pat)
        for name, x in mods.items():
            if "_src" in x and rx.search(x["_src"]) and not _match(x["path"], allowed + M.RULE_FILES):
                line = next((i for i, ln in enumerate(x["_src"].splitlines(), 1) if rx.search(ln)), None)
                out.append(_v("boundary", name, m, rule, f"{rule}@{name}", line))
    # 3. one brain: a runtime import of a scorer from outside the one scoring path
    for a, b in m["edges"]:
        if b in M.SCORERS and a not in M.SCORING_PATH and not any(a == o or a.startswith(o + ".") for o in M.OFFLINE):
            out.append(_v("second-scoring-path", a, m, f"{a} imports the scorer {b} (route it through Watch Value)",
                          f"{a}->{b}"))
    # 4. dead and orphan modules
    live = reachable(m)
    for name, x in sorted(mods.items()):
        if name in live or name.split(".")[-1] == "__init__" or x["path"].endswith("__init__.py"):
            continue
        if x["used_by_tests"]:
            out.append(_v("dead", name, m, f"reached only from tests ({x['used_by_tests']} files), no entry point uses it",
                          name))
        else:
            out.append(_v("orphan", name, m, "nothing imports it and it is no entry point", name))
    return out


def public(m: dict) -> dict:
    """The map without the sources (what docs/citymap.json holds)."""
    return {"modules": {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")} for k, v in m["modules"].items()},
            "edges": m["edges"]}
