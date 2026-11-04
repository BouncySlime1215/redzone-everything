"""Proof kernel: check the whole redzone/ graph against the contract rules on every change.

    python scripts/contract_check.py                    # report; exit 1 iff a NEW violation
    python scripts/contract_check.py --json             # machine output
    python scripts/contract_check.py --write-baseline   # accept today's violations (ratchet)

Stdlib only, pure AST + text scan, never imports redzone (it runs in the lint job, which installs
only ruff). A violation is {rule, path, key, line, detail}. The baseline (docs/contract_baseline.json)
keys on (rule, path, key) with no line number, so unrelated edits never look new. There are no
inline ignores: a false positive goes in the baseline.

Rules (ids are stable):
  K1 duplicate-def   an UPPER_CASE module constant, or a top-level function body, defined in 2+ modules
  K2 timing-literal  a *_S / *_MS / *_SECS constant or a sleep() with a bare number, outside config.py
  K3 device-io       stick/TV I/O (adb, .shell(, keyevent, dumpsys, SamsungTVWS) outside the tuners
  K4 second-decider  a decide/score/rank/pick/... function, or max/min/sorted over candidates,
                     outside the decision path
  K5 orphan          a module nothing in redzone/ or scripts/ imports
  K6 lab-import      production code importing redzone.lab
  K7 untagged        (warn only) a module docs/citymap.json tags as serving nothing
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from functools import cached_property
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = "docs/contract_baseline.json"
CITYMAP = "docs/citymap.json"
CONFIG_FILE = "redzone/config.py"
# A trailing "/" means the whole folder.
ALLOWED_DEVICE_IO = ("redzone/tuners/", "redzone/tv.py", "redzone/lab/night/house.py",
                     "redzone/lab/night/smartthings.py", "redzone/lab/prime_mv_fake.py",
                     "redzone/platform/faults.py")
ALLOWED_DECIDERS = ("redzone/decider/core.py", "redzone/decider/decider.py", "redzone/noggin/")
LAB_DIRS = ("redzone/lab/",)

_UPPER = re.compile(r"^[A-Z][A-Z0-9_]*[A-Z0-9]$")
_TIMING = re.compile(r"_S$|_S_|_MS$|_SECS$")
_DECIDER_NAMES = {"decide", "score", "rank", "pick", "choose", "best", "order"}
_CANDIDATES = re.compile(r"cand|games|options|scored", re.I)
_DEVICE_STRINGS = ("exec-out", "keyevent", "dumpsys")
_SUBPROCESS = {"run", "Popen", "check_output", "check_call", "call", "create_subprocess_exec"}
_MODULE_REF = re.compile(r"^redzone(\.[A-Za-z_]\w*)+$")
_LAB_IN_TEXT = re.compile(r"(?:from|import)\s+(redzone\.lab(?:\.\w+)*)")


@dataclass(frozen=True)
class Violation:
    rule: str
    path: str
    key: str
    line: int
    detail: str
    warn: bool = False

    @property
    def ident(self) -> tuple[str, str, str]:
        return (self.rule, self.path, self.key)


@dataclass
class Mod:
    path: str          # posix, relative to the root
    name: str          # dotted module name
    tree: ast.Module
    is_pkg: bool

    @cached_property
    def nodes(self) -> list[tuple[ast.AST, str]]:
        """Every node with its scope, walked once and shared by all rules."""
        return list(scoped(self.tree))


# ---- loading -------------------------------------------------------------------------------

def load(root: Path, top: str) -> list[Mod]:
    """Parse every .py under root/top (tests/ and __pycache__ skipped), sorted by path."""
    mods = []
    base = root / top
    if not base.is_dir():
        return mods
    for p in sorted(base.rglob("*.py")):
        rel = p.relative_to(root)
        if "tests" in rel.parts or "__pycache__" in rel.parts:
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(rel))
        except (SyntaxError, UnicodeDecodeError):
            continue   # ruff reports syntax errors
        parts = list(rel.with_suffix("").parts)
        is_pkg = parts[-1] == "__init__"
        if is_pkg:
            parts = parts[:-1]
        mods.append(Mod(rel.as_posix(), ".".join(parts), tree, is_pkg))
    return mods


def _allowed(path: str, allow: tuple[str, ...]) -> bool:
    return any(path == a or (a.endswith("/") and path.startswith(a)) for a in allow)


_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
# Load/Store/Add/Eq...: leaves no rule looks at; skipping them halves the walk.
_LEAVES = (ast.expr_context, ast.boolop, ast.operator, ast.unaryop, ast.cmpop)


def scoped(tree: ast.AST):
    """Yield (node, scope) for every node; scope is the enclosing def/class qualname ('' = module)."""
    stack = [(tree, "")]
    while stack:
        node, scope = stack.pop()
        for field in node._fields:
            value = getattr(node, field, None)
            for child in value if isinstance(value, list) else (value,):
                if not isinstance(child, ast.AST) or isinstance(child, _LEAVES):
                    continue
                yield child, scope
                if isinstance(child, _SCOPES):
                    stack.append((child, f"{scope}.{child.name}" if scope else child.name))
                else:
                    stack.append((child, scope))


def _assigns(node: ast.stmt):
    """(name, value) pairs for a plain `NAME = value` / `NAME: T = value` statement."""
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name):
                yield t.id, node.value
    elif isinstance(node, ast.AnnAssign) and node.value is not None and isinstance(node.target, ast.Name):
        yield node.target.id, node.value


def _docstrings(mod: Mod) -> set[int]:
    out = set()
    for node in [mod.tree, *(n for n, _ in mod.nodes)]:
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


def _name_of(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _numeric(v: ast.AST) -> bool:
    if isinstance(v, ast.Constant):
        return isinstance(v.value, (int, float)) and not isinstance(v.value, bool)
    if isinstance(v, ast.UnaryOp) and isinstance(v.op, (ast.USub, ast.UAdd)):
        return _numeric(v.operand)
    if isinstance(v, (ast.Tuple, ast.List, ast.Set)):
        return bool(v.elts) and all(_numeric(e) for e in v.elts)
    if isinstance(v, ast.Dict):
        return bool(v.values) and all(_numeric(e) for e in v.values)
    return False


def _where(scope: str) -> str:
    return scope or "<module>"


def _resolve(mod: Mod, module: str | None, level: int) -> str:
    """Absolute dotted name for `from <level dots><module> import ...` inside mod."""
    if not level:
        return module or ""
    pkg = mod.name.split(".") if mod.is_pkg else mod.name.split(".")[:-1]
    if level > 1:
        pkg = pkg[: len(pkg) - (level - 1)]
    return ".".join([*pkg, module] if module else pkg)


def imports(mod: Mod, known: set[str]) -> list[tuple[str, int]]:
    """(module, line) for each import in mod; `from a import b` gives a.b when that is a module."""
    out = []
    for node, _ in mod.nodes:
        if isinstance(node, ast.Import):
            out += [(a.name, node.lineno) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = _resolve(mod, node.module, node.level)
            subs = [f"{base}.{a.name}" for a in node.names if f"{base}.{a.name}" in known]
            out += [(s, node.lineno) for s in subs] or [(base, node.lineno)]
    return out


# ---- rules ---------------------------------------------------------------------------------

def k1_duplicate_def(ctx) -> list[Violation]:
    """Same UPPER_CASE module constant, or same top-level function body, in 2+ modules."""
    consts = defaultdict(list)
    bodies = defaultdict(list)
    for m in ctx["redzone"]:
        seen = set()
        for node in m.tree.body:
            for name, value in _assigns(node):
                # `X = other.X` / `X = Y` is reuse, not a second definition.
                if _UPPER.match(name) and name not in seen and not isinstance(value, (ast.Name, ast.Attribute)):
                    seen.add(name)
                    consts[name].append((m.path, node.lineno))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body = node.body
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    body = body[1:]
                if len(body) < 2 and not any(isinstance(s, (ast.If, ast.For, ast.While, ast.Try, ast.With))
                                             for s in body):
                    continue   # one-liners are too generic to call duplicates
                sig = ast.dump(node.args) + "|" + "|".join(ast.dump(s) for s in body)
                bodies[sig].append((m.path, node.lineno, node.name))
    out = []
    for name, where in consts.items():
        paths = sorted({p for p, _ in where})
        if len(paths) < 2:
            continue
        for p, line in where:
            others = ", ".join(x for x in paths if x != p)
            out.append(Violation("K1", p, name, line, f"constant {name} also defined in {others}"))
    for where in bodies.values():
        paths = sorted({p for p, _, _ in where})
        if len(paths) < 2:
            continue
        for p, line, fn in where:
            others = ", ".join(f"{x}:{n}" for x, _, n in sorted(where) if x != p)
            out.append(Violation("K1", p, fn, line, f"def {fn} has the same body as {others}"))
    return out


def k2_timing_literal(ctx) -> list[Violation]:
    """Timing constants and sleeps written as bare numbers instead of measured config."""
    out = []
    for m in ctx["redzone"]:
        if m.path == CONFIG_FILE:
            continue
        holders = [("", m.tree)]
        holders += [(f"{s}.{n.name}" if s else n.name, n) for n, s in m.nodes if isinstance(n, ast.ClassDef)]
        for prefix, holder in holders:
            for node in holder.body:
                for name, value in _assigns(node):
                    if _TIMING.search(name) and _numeric(value):
                        key = f"{prefix}.{name}" if prefix else name
                        out.append(Violation("K2", m.path, key, node.lineno,
                                             f"{key} = {ast.unparse(value)[:40]}"))
        for node, scope in m.nodes:
            if isinstance(node, ast.Call) and node.args and _numeric(node.args[0]):
                f = node.func
                is_sleep = (isinstance(f, ast.Attribute) and f.attr == "sleep"
                            and isinstance(f.value, ast.Name) and f.value.id in ("time", "asyncio")) \
                    or (isinstance(f, ast.Name) and f.id == "sleep")
                if is_sleep:
                    out.append(Violation("K2", m.path, f"sleep@{_where(scope)}", node.lineno,
                                         f"{ast.unparse(node)[:50]}"))
    return out


def _adb_argv(call: ast.Call) -> bool:
    if _name_of(call.func) not in _SUBPROCESS:
        return False
    for a in call.args[:1]:
        first = a.elts[0] if isinstance(a, (ast.List, ast.Tuple)) and a.elts else a
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            word = first.value.split(" ", 1)[0]
            if word == "adb" or word.endswith("/adb"):
                return True
    return False


def k3_device_io(ctx) -> list[Violation]:
    """Stick/TV I/O outside the tuners (and the named fakes)."""
    out = []
    for m in ctx["redzone"]:
        if _allowed(m.path, ALLOWED_DEVICE_IO):
            continue
        docs = _docstrings(m)
        for node, scope in m.nodes:
            what = ""
            if isinstance(node, ast.Call):
                fn = _name_of(node.func)
                if isinstance(node.func, ast.Attribute) and fn == "shell":
                    what = ".shell("
                elif fn == "_adb":
                    what = "_adb("
                elif _adb_argv(node):
                    what = "adb argv"
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
                what = next((s for s in _DEVICE_STRINGS if s in node.value), "")
            elif isinstance(node, (ast.Name, ast.Attribute)) and _name_of(node) == "SamsungTVWS":
                what = "SamsungTVWS"
            elif isinstance(node, ast.ImportFrom) and any(a.name == "SamsungTVWS" for a in node.names):
                what = "SamsungTVWS"
            if what:
                out.append(Violation("K3", m.path, _where(scope), node.lineno, what))
    return out


def k4_second_decider(ctx) -> list[Violation]:
    """A second place that picks between games, outside the one decision path."""
    out = []
    for m in ctx["redzone"]:
        if _allowed(m.path, ALLOWED_DECIDERS):
            continue
        for node, scope in m.nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.lstrip("_") in _DECIDER_NAMES:
                q = f"{scope}.{node.name}" if scope else node.name
                out.append(Violation("K4", m.path, q, node.lineno, f"def {node.name}"))
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id in ("max", "min", "sorted") and node.args:
                it = node.args[0]
                if isinstance(it, (ast.GeneratorExp, ast.ListComp)):
                    it = it.generators[0].iter
                if isinstance(it, ast.Call) and isinstance(it.func, ast.Attribute):
                    it = it.func.value   # games.values(), cands.items()
                name = _name_of(it)
                if name and _CANDIDATES.search(name):
                    out.append(Violation("K4", m.path, _where(scope), node.lineno, f"{node.func.id}({name})"))
    return out


def k5_orphan(ctx) -> list[Violation]:
    """A redzone module that no other module in redzone/ or scripts/ imports."""
    known = {m.name for m in ctx["redzone"]}
    refs = defaultdict(set)   # module -> importers
    for m in ctx["redzone"] + ctx["scripts"]:
        names = [n for n, _ in imports(m, known)]
        for node, _ in m.nodes:   # importlib.import_module("redzone.x"), `-m redzone.x`
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and _MODULE_REF.match(node.value):
                names.append(node.value)
        for n in names:
            refs[n].add(m.name)
    out = []
    for m in ctx["redzone"]:
        if m.is_pkg or m.name.endswith(".__main__") or m.name == "redzone":
            continue
        if not (refs.get(m.name, set()) - {m.name}):
            out.append(Violation("K5", m.path, m.name, 1, f"{m.name} is imported by nothing"))
    return out


def k6_lab_import(ctx) -> list[Violation]:
    """Production code importing the lab (backtests, fitters, fakes)."""
    known = {m.name for m in ctx["redzone"]}
    out = []
    for m in ctx["redzone"]:
        if _allowed(m.path, LAB_DIRS):
            continue
        for name, line in imports(m, known):
            if name == "redzone.lab" or name.startswith("redzone.lab."):
                out.append(Violation("K6", m.path, name, line, f"imports {name}"))
        for node, _ in m.nodes:   # code run in a subprocess: "from redzone.lab.x import y"
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for name in _LAB_IN_TEXT.findall(node.value):
                    out.append(Violation("K6", m.path, name, node.lineno, f"runs code importing {name}"))
    return out


def _untagged(data, parent: str = ""):
    if isinstance(data, dict):
        if data.get("serves") == ["untagged"]:
            yield str(data.get("path") or data.get("module") or data.get("name") or parent)
        for k, v in data.items():
            yield from _untagged(v, str(k))
    elif isinstance(data, list):
        for v in data:
            yield from _untagged(v, parent)


def k7_untagged(ctx) -> list[Violation]:
    """Warn for each module docs/citymap.json tags as serving no spec item. Never fails."""
    path = ctx["root"] / CITYMAP
    if not path.exists():
        ctx["notes"].append(f"untagged: skipped ({CITYMAP} not found)")
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        ctx["notes"].append(f"untagged: skipped ({CITYMAP} unreadable: {e})")
        return []
    return [Violation("K7", who, who, 0, "serves nothing (untagged)", warn=True)
            for who in sorted(set(_untagged(data)))]


RULES = (
    ("K1", "duplicate-def", k1_duplicate_def),
    ("K2", "timing-literal", k2_timing_literal),
    ("K3", "device-io", k3_device_io),
    ("K4", "second-decider", k4_second_decider),
    ("K5", "orphan", k5_orphan),
    ("K6", "lab-import", k6_lab_import),
    ("K7", "untagged", k7_untagged),
)
RULE_NAMES = {rid: name for rid, name, _ in RULES}


# ---- run, compare, report ------------------------------------------------------------------

def check(root: Path = ROOT) -> tuple[list[Violation], list[str]]:
    """All violations (one per identity, earliest line, details merged), sorted; plus notes."""
    ctx = {"root": root, "redzone": load(root, "redzone"), "scripts": load(root, "scripts"), "notes": []}
    merged: dict[tuple[str, str, str], Violation] = {}
    for _, _, fn in RULES:
        for v in fn(ctx):
            old = merged.get(v.ident)
            if old is None:
                merged[v.ident] = v
            else:
                details = sorted(set(old.detail.split("; ")) | {v.detail})
                merged[v.ident] = Violation(v.rule, v.path, v.key, min(old.line, v.line),
                                            "; ".join(details), v.warn)
    found = sorted(merged.values(), key=lambda v: (v.rule, v.path, v.key))
    return found, ctx["notes"]


def read_baseline(path: Path) -> set[tuple[str, str, str]]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {(v["rule"], v["path"], v["key"]) for v in data.get("violations", [])}


def write_baseline(path: Path, found: list[Violation]) -> int:
    rows = sorted({v.ident for v in found if not v.warn})
    # One violation per line, so a ratchet diff reads as a list of fixed items.
    items = ",\n".join("  " + json.dumps({"rule": r, "path": p, "key": k}) for r, p, k in rows)
    text = ('{\n "version": 1,\n "generated_from": "integration/next",\n "violations": [\n'
            + items + ("\n" if items else "") + " ]\n}\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return len(rows)


def compare(found: list[Violation], base: set) -> dict:
    hard = [v for v in found if not v.warn]
    current = {v.ident for v in hard}
    return {
        "violations": hard,
        "warnings": [v for v in found if v.warn],
        "new": [v for v in hard if v.ident not in base],
        "fixed": sorted(base - current),
        "total": len(hard),
        "baseline": len(base),
    }


def report(res: dict, notes: list[str]) -> list[str]:
    lines = ["contract kernel"]
    for rid, name, _ in RULES:
        n = sum(v.rule == rid for v in res["violations"])
        w = sum(v.rule == rid for v in res["warnings"])
        new = sum(v.rule == rid for v in res["new"])
        fixed = sum(f[0] == rid for f in res["fixed"])
        if rid == "K7":
            lines.append(f"  {rid} {name}: {w} warn")
        else:
            lines.append(f"  {rid} {name}: {n} (new {new}, fixed {fixed})")
    lines += [f"  {n}" for n in notes]
    for v in res["warnings"]:
        lines.append(f"WARN {v.path} {v.rule} {RULE_NAMES[v.rule]} {v.detail}")
    if res["new"]:
        lines.append(f"NEW ({len(res['new'])}): fix these, or if truly a false positive, "
                     "re-run with --write-baseline and say why in the PR")
        lines += [f"  {v.path}:{v.line} {v.rule} {RULE_NAMES[v.rule]} [{v.key}] {v.detail}" for v in res["new"]]
    if res["fixed"]:
        lines.append(f"FIXED: {len(res['fixed'])} (in the baseline, gone now; ratchet with --write-baseline)")
        lines += [f"  {p} {r} [{k}]" for r, p, k in res["fixed"]]
    lines.append(f"contract violations: {res['total']} (baseline {res['baseline']})")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--root", type=Path, default=ROOT, help="repo root to check")
    ap.add_argument("--baseline", type=Path, help=f"baseline file (default <root>/{BASELINE})")
    ap.add_argument("--write-baseline", action="store_true", help="accept the current violations")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args(argv)
    root = a.root.resolve()
    base_path = a.baseline or root / BASELINE
    found, notes = check(root)
    if a.write_baseline:
        n = write_baseline(base_path, found)
        print(f"wrote {n} violations to {base_path}")
        return 0
    res = compare(found, read_baseline(base_path))
    if a.json:
        out = {k: ([asdict(v) for v in res[k]] if k in ("violations", "warnings", "new") else res[k])
               for k in ("violations", "warnings", "new", "total", "baseline")}
        out["fixed"] = [{"rule": r, "path": p, "key": k} for r, p, k in res["fixed"]]
        out["notes"] = notes
        print(json.dumps(out, indent=1, sort_keys=True))
    else:
        print("\n".join(report(res, notes)))
    return 1 if res["new"] else 0


if __name__ == "__main__":
    sys.exit(main())
