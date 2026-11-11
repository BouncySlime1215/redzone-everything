"""[WP-15] Small mutation engine: one small code change at a time, then ask whether a named check notices.

`mutants(path, functions, rule_tuples)` returns one Mutant per single-point change inside the named functions: `not x`
to `x`, and/or swapped or one operand dropped, a comparison flipped, a number or bool or kind literal changed, an `if`
negated, a rule's answer inverted, an entry dropped from a rule table. `load` builds the mutated module in memory, so
the real one is never touched. `killed_by` says which check fails first.

Why: a test that still passes with the rule broken proves nothing, and Rule #1 rests on these rules (build plan WP-15).
"""
from __future__ import annotations

import ast
import copy
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_FLIP = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
         ast.In: ast.NotIn, ast.NotIn: ast.In, ast.Is: ast.IsNot, ast.IsNot: ast.Is}


@dataclass(frozen=True)
class Mutant:
    id: str          # stable: function:change:code[:n], so a line shift elsewhere in the file can't rename it
    source: str      # the changed module


def _parents(root: ast.AST) -> dict:
    return {c: p for p in ast.walk(root) for c in ast.iter_child_nodes(p)}


def _replace(root: ast.AST, old: ast.AST, new: ast.AST) -> None:
    parent = _parents(root)[old]
    for f, v in ast.iter_fields(parent):
        if v is old:
            setattr(parent, f, new)
        elif isinstance(v, list) and any(x is old for x in v):
            setattr(parent, f, [new if x is old else x for x in v])


def _code(node: ast.AST) -> str:
    """The source a change touches, on one line. Names a site by what it is, so only editing that code renames it."""
    return " ".join(ast.unparse(node.test if isinstance(node, ast.If) else node).split())


def _sites(node: ast.AST, parent: ast.AST | None, rule: bool) -> list[str]:
    """The single-point changes this node offers (each names one edit `_apply` makes)."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return ["drop_not"]
    if isinstance(node, ast.BoolOp):
        return ["swap_bool", *[f"drop_operand_{i}" for i in range(len(node.values))]]
    if isinstance(node, ast.Compare):
        return [f"flip_cmp_{i}" for i, o in enumerate(node.ops) if type(o) in _FLIP]
    if isinstance(node, ast.IfExp):
        return ["swap_ifexp"]
    if isinstance(node, ast.If):
        return ["negate_if"]
    if isinstance(node, ast.Return) and rule and node.value is not None:
        return ["invert_return"]
    if isinstance(node, ast.Constant):
        v = node.value
        if isinstance(v, bool):
            return ["flip_bool"]
        if isinstance(v, (int, float)):
            return ["bump_number"]
        in_compare = isinstance(parent, (ast.Compare, ast.Tuple, ast.List))
        if isinstance(v, str) and (v == "" or in_compare):
            return ["change_str"]
    return []


def _apply(root: ast.AST, node: ast.AST, change: str) -> None:
    if change == "drop_not":
        _replace(root, node, node.operand)
    elif change == "swap_bool":
        node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
    elif change.startswith("drop_operand_"):
        rest = [v for i, v in enumerate(node.values) if i != int(change.rsplit("_", 1)[1])]
        _replace(root, node, rest[0] if len(rest) == 1 else ast.BoolOp(node.op, rest))
    elif change.startswith("flip_cmp_"):
        i = int(change.rsplit("_", 1)[1])
        node.ops[i] = _FLIP[type(node.ops[i])]()
    elif change == "swap_ifexp":
        node.body, node.orelse = node.orelse, node.body
    elif change == "negate_if":
        node.test = ast.UnaryOp(ast.Not(), node.test)
    elif change == "invert_return":
        node.value = ast.UnaryOp(ast.Not(), node.value)
    elif change == "flip_bool":
        node.value = not node.value
    elif change == "bump_number":
        node.value = node.value + 1
    elif change == "change_str":
        node.value = node.value + "_mutated"
    else:
        raise ValueError(change)


def _named(tree: ast.Module, want: set[str]) -> list[tuple[str, ast.AST]]:
    """(qualified name, node) for each wanted function or method, and each wanted module constant."""
    out = []
    for top in tree.body:
        if isinstance(top, ast.FunctionDef) and top.name in want:
            out.append((top.name, top))
        elif isinstance(top, ast.ClassDef) and top.name in want:
            out += [(f"{top.name}.{f.name}", f) for f in top.body if isinstance(f, ast.FunctionDef)]
        elif isinstance(top, ast.Assign) and {getattr(t, "id", None) for t in top.targets} & want:
            out.append((top.targets[0].id, top))
        elif isinstance(top, ast.Assign) and any(isinstance(t, ast.Tuple) and {getattr(e, "id", None) for e in t.elts} & want
                                                 for t in top.targets):
            out.append(("constants", top))
    return out


def mutants(path: Path, want: set[str], *, rules: set[str] = frozenset(), rule_prefix: str = "_",
            tables: tuple[str, ...] = ()) -> list[Mutant]:
    """Every single-point mutant of the named functions. A function in `rules` also gets its answer inverted."""
    src = Path(path).read_text()
    tree = ast.parse(src)
    out: list[Mutant] = []
    seen: dict[str, int] = {}

    def add(base: str, make: Callable[[ast.Module], None]) -> None:
        seen[base] = seen.get(base, 0) + 1
        t = copy.deepcopy(tree)
        make(t)
        out.append(Mutant(f"{base}:{seen[base]}" if seen[base] > 1 else base, ast.unparse(t)))

    for name, fn in _named(tree, want):
        nodes = list(ast.walk(fn))
        parents = _parents(fn)
        is_rule = name in rules
        for i, node in enumerate(nodes):
            for change in _sites(node, parents.get(node), is_rule):
                def make(t, i=i, change=change, name=name):
                    fn2 = dict(_named(t, want))[name]
                    target = list(ast.walk(fn2))[i]
                    _apply(fn2, target, change)
                add(f"{name}:{change}:{_code(node)}", make)
    for table in tables:
        top = next(n for n in tree.body if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == table)
        for j, elt in enumerate(top.value.elts):
            label = elt.elts[0].value if isinstance(elt, ast.Tuple) else "star"
            def drop(t, j=j, table=table):
                n = next(x for x in t.body if isinstance(x, ast.AnnAssign) and getattr(x.target, "id", "") == table)
                del n.value.elts[j]
            add(f"{table}:drop:{label}", drop)
    return out


def load(mutant: Mutant, restore: dict | None = None) -> types.ModuleType:
    """The mutated module, in memory. `restore` puts real names back (classes other code builds, isinstance checks)."""
    m = types.ModuleType("rz_mutant")
    sys.modules["rz_mutant"] = m   # dataclasses look their module up while the class body runs
    try:
        exec(compile(mutant.source, "<mutant>", "exec"), m.__dict__)
    finally:
        del sys.modules["rz_mutant"]
    m.__dict__.update(restore or {})
    return m


def killed_by(m: types.ModuleType, checks) -> str | None:
    """The first check that fails on this mutant (a crash counts as noticing); None when it survives every one."""
    for c in checks:
        try:
            c(m)
        except Exception as e:  # noqa: BLE001  (any failure is the mutant being noticed)
            return f"{c.__name__}: {type(e).__name__}"
    return None
