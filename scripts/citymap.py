"""City map: one generated graph of every RedZone module (role, inputs -> outputs, owner, serves).

    python scripts/citymap.py            # rewrite docs/citymap.md and docs/citymap.json
    python scripts/citymap.py --check    # exit 1 if they are stale
    python scripts/citymap.py --focus redzone/decider/cuts.py [--tokens 1500]   # ranked slice
    python scripts/citymap.py --impact redzone/fsm.py                          # PR risk list

Stdlib only and it never imports redzone, so it is safe on the live Mac.
- role: first sentence of the module docstring.
Covers redzone/**, agent/** (the LOCAL AI lane) and scripts/*.py.
- inputs: redzone/agent/scripts modules it imports (relative imports resolved) + contract names it uses.
- outputs: modules that import it + contract Protocols it implements (class X(BreakModel)).
- owner: the 'File ownership' table in docs/architecture.md section 4, first match wins.
- serves: playbook ids in its source (checked against docs/playbooks.md) + North Star metric
  lines from docs/citymap_tags.json (checked against docs/northstar.md).
- calls: 'path:symbol' this module calls in another repo module (resolved through its imports).
- data: data files (.jsonl/.json/.db/.yaml/.csv) named in string literals, split into reads, writes
  and uses (best effort from the surrounding call: open mode, write_text/read_text, write_*/read_*).
- machines: fsm Spec names a module declares, and the ones it drives (Spec.owner or SPEC.new()).
--focus prints a token-budgeted, in-degree-ranked 1-2 hop slice (signatures only); --impact lists
everything a change to one file can reach (importers, tests, contracts, machines, data, tags).
Exit 0 = fine, 1 = stale (--check), 2 = a tag in docs/citymap_tags.json is unknown.
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JSON_OUT = "docs/citymap.json"
MD_OUT = "docs/citymap.md"
TAGS = "docs/citymap_tags.json"
LANES = ("DECISION", "MOMENT/BREAK", "LOCAL AI", "SWITCHING", "SYNC", "EXPERIENCE", "PLATFORM", "INTEGRATION")
PACKAGES = ("redzone", "agent")
PLAYBOOK_ID = re.compile(r"\b(?:APP|BTN|FEED|GAME|MAC|NET|SAFE|SLATE|STK|SYNC|TASTE|TV)-\d{2}\b")
_TICKS = re.compile(r"`([^`]+)`")
_BRACE = re.compile(r"\{([^{}]*)\}")
_SENTENCE = re.compile(r"^(.+?[.!?])(?:\s|$)")
ROLE_MAX = 160
SHOW_EDGES = 6
TOP_CALLED = 25


class TagError(ValueError):
    """A tag in docs/citymap_tags.json names no North Star metric or playbook id."""


@dataclass
class Info:
    path: str
    kind: str
    role: str
    owner: str = "unowned"
    serves: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    contracts: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    data: dict[str, list[str]] = field(default_factory=lambda: {"reads": [], "writes": [], "uses": []})
    declares: list[str] = field(default_factory=list)
    drives: list[str] = field(default_factory=list)
    spec_owners: dict[str, str] = field(default_factory=dict)


# ---- reading the repo -------------------------------------------------------------------------

def module_files(root: Path) -> dict[str, str]:
    """path -> kind for redzone/**/*.py, agent/**/*.py and scripts/*.py."""
    out = {}
    for pkg in PACKAGES:
        for p in sorted((root / pkg).rglob("*.py")):
            rel = p.relative_to(root).as_posix()
            out[rel] = "package" if p.name == "__init__.py" else "module"
    for p in sorted((root / "scripts").glob("*.py")):
        out[p.relative_to(root).as_posix()] = "entry"
    return out


def dotted(path: str) -> str:
    parts = path[:-3].split("/")
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def role_of(tree: ast.Module | None) -> str:
    doc = ast.get_docstring(tree) if tree is not None else None
    if not doc:
        return "no docstring"
    para = " ".join(doc.strip().split("\n\n")[0].split())
    m = _SENTENCE.match(para)
    text = m.group(1) if m else para
    return text if len(text) <= ROLE_MAX else text[: ROLE_MAX - 3].rstrip() + "..."


def _resolve_from(node: ast.ImportFrom, me: str, is_pkg: bool) -> str:
    if not node.level:
        return node.module or ""
    base = me.split(".") if is_pkg else me.split(".")[:-1]
    base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
    return ".".join([*base, node.module] if node.module else base)


def imports_of(tree: ast.Module, me: str, is_pkg: bool, names: dict[str, str]) -> tuple[set[str], set[str]]:
    """(repo module paths imported, names imported from redzone.contracts)."""
    mods, used = set(), set()

    def add(name: str) -> bool:
        if name in names:
            mods.add(names[name])
            return True
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                parts = a.name.split(".")
                for i in range(len(parts), 0, -1):  # longest prefix that is a module
                    if add(".".join(parts[:i])):
                        break
        elif isinstance(node, ast.ImportFrom):
            src = _resolve_from(node, me, is_pkg)
            if src.split(".")[0] not in (*PACKAGES, "scripts"):
                continue
            for a in node.names:
                if not add(f"{src}.{a.name}"):
                    add(src)
                if src == "redzone.contracts" or src.startswith("redzone.contracts."):
                    if a.name != "*" and f"{src}.{a.name}" not in names:
                        used.add(a.name)
    return mods, used


def implements_of(tree: ast.Module, protocols: set[str]) -> set[str]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for b in node.bases:
                name = b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", "")
                if name in protocols:
                    out.add(name)
    return out


def contract_protocols(trees: dict[str, ast.Module | None]) -> set[str]:
    out = set()
    for path, tree in trees.items():
        if not path.startswith("redzone/contracts/") or tree is None:
            continue
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and any(
                getattr(b, "id", getattr(b, "attr", "")) == "Protocol" for b in node.bases
            ):
                out.add(node.name)
    return out


# ---- call, data and machine edges --------------------------------------------------------------

DATA_FILE = re.compile(r"^(?:\./)?[\w-][\w.-]*(?:/[\w.-]+)*\.(?:jsonl|json|db|yaml|yml|csv)$")
_PATH_CTORS = {"Path", "PurePath", "PosixPath", "joinpath", "with_name", "with_suffix", "resolve",
               "expanduser", "absolute"}
_WRITE_METHODS = {"write_text", "write_bytes", "touch", "unlink", "rename", "replace"}
_READ_METHODS = {"read_text", "read_bytes"}
_WRITE_WORDS = ("write", "append", "save", "dump", "persist", "store", "merge", "log_")
_READ_WORDS = ("read", "load", "parse", "tail", "iter", "rows")


def _chain(node: ast.AST) -> list[str] | None:
    """a.b.c -> ['a', 'b', 'c'] for a Name/Attribute chain, else None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    return [node.id, *reversed(parts)]


def alias_map(tree: ast.Module, me: str, is_pkg: bool) -> dict[str, str]:
    """Local name -> dotted target for every repo import ('rx' -> 'redzone.x', 'f' -> 'redzone.x.f')."""
    out: dict[str, str] = {}
    roots = (*PACKAGES, "scripts")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in roots:
                    continue
                if a.asname:
                    out[a.asname] = a.name
                else:
                    out[a.name.split(".")[0]] = a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            src = _resolve_from(node, me, is_pkg)
            if src.split(".")[0] not in roots:
                continue
            for a in node.names:
                if a.name != "*":
                    out[a.asname or a.name] = f"{src}.{a.name}"
    return out


def _target(chain: list[str], aliases: dict[str, str], names: dict[str, str]) -> tuple[str, list[str]] | None:
    """Resolve an alias chain to (module path, remaining parts) by the longest module prefix."""
    if chain[0] not in aliases:
        return None
    full = aliases[chain[0]].split(".") + chain[1:]
    for i in range(len(full), 0, -1):
        mod = ".".join(full[:i])
        if mod in names:
            return names[mod], full[i:]
    return None


def calls_of(tree: ast.Module, me_path: str, aliases: dict[str, str],
             names: dict[str, str]) -> tuple[set[str], set[tuple[str, str]]]:
    """('path:symbol' this module calls in other repo modules, {(path, symbol)} that get .new() called)."""
    calls, news = set(), set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        chain = _chain(node.func)
        hit = _target(chain, aliases, names) if chain else None
        if not hit or not hit[1] or hit[0] == me_path:
            continue
        path, rest = hit
        calls.add(f"{path}:{rest[0]}")
        if rest[1:] == ["new"]:
            news.add((path, rest[0]))
    return calls, news


def _data_key(text: str, ctx_mentions_data: bool) -> str:
    text = text.removeprefix("./")
    if "/" not in text and ctx_mentions_data:
        return f"data/{text}"
    return text


def _mentions_data(node: ast.AST) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and n.value == "data":
            return True
        if isinstance(n, ast.Name) and "DATA" in n.id:
            return True
    return False


def _path_refs(node: ast.AST, by_name: dict[str, set[str]], by_attr: dict[str, set[str]],
               ctx: bool = False) -> set[str]:
    """Files a path expression names: literals (DATA / 'x.json' -> data/x.json) plus names and
    attributes bound to one earlier in the module."""
    if isinstance(node, ast.Call):
        fn = _chain(node.func) or [getattr(node.func, "attr", "")]
        if fn[-1] not in _PATH_CTORS:
            return set()
        ctx = ctx or _mentions_data(node)
        parts = [*node.args, *(k.value for k in node.keywords)]
        if isinstance(node.func, ast.Attribute):
            parts.append(node.func.value)
        return set().union(*(_path_refs(p, by_name, by_attr, ctx) for p in parts))
    if isinstance(node, ast.Constant):
        ok = isinstance(node.value, str) and DATA_FILE.match(node.value)
        return {_data_key(node.value, ctx)} if ok else set()
    if isinstance(node, ast.BinOp):
        ctx = ctx or _mentions_data(node)
        return _path_refs(node.left, by_name, by_attr, ctx) | _path_refs(node.right, by_name, by_attr, ctx)
    if isinstance(node, ast.Name):
        return set(by_name.get(node.id, ()))
    if isinstance(node, ast.Attribute):
        if node.attr in _PATH_CTORS:
            return _path_refs(node.value, by_name, by_attr, ctx)
        return set(by_attr.get(node.attr, ()))
    return set()


def _mode(call: ast.Call, pos: int) -> str | None:
    node = call.args[pos] if len(call.args) > pos else next(
        (k.value for k in call.keywords if k.arg == "mode"), ast.Constant("r"))
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _verb(name: str, call: ast.Call, method: bool) -> str:
    """'write', 'read' or 'uses' for a call that takes or acts on a path."""
    if name == "open":
        mode = _mode(call, 0 if method else 1)
        if mode is None:
            return "uses"
        return "write" if set(mode) & set("wax+") else "read"
    if method and name in _WRITE_METHODS:
        return "write"
    if method and name in _READ_METHODS:
        return "read"
    low = name.lower()
    if any(w in low for w in _WRITE_WORDS):
        return "write"
    if any(w in low for w in _READ_WORDS):
        return "read"
    return "uses"


def _file_literals(tree: ast.Module) -> list[str]:
    """Whole string literals that name a data file (f-string fragments are not whole names)."""
    parts = {id(v) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for v in n.values}
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and id(n) not in parts
            and isinstance(n.value, str) and DATA_FILE.match(n.value)]


def data_of(tree: ast.Module) -> dict[str, list[str]]:
    """{'reads': [...], 'writes': [...], 'uses': [...]} for data files named in string literals."""
    literals = _file_literals(tree)
    if not literals:
        return {"reads": [], "writes": [], "uses": []}
    by_name: dict[str, set[str]] = {}
    by_attr: dict[str, set[str]] = {}
    seen: set[str] = set()

    def bind(target: ast.AST, files: set[str], in_class: bool) -> None:
        if not files:
            return
        if isinstance(target, ast.Name):
            (by_attr if in_class else by_name).setdefault(target.id, set()).update(files)
        elif isinstance(target, ast.Attribute):
            by_attr.setdefault(target.attr, set()).update(files)

    def visit(node: ast.AST, in_class: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Assign, ast.AnnAssign)) and child.value is not None:
                files = _path_refs(child.value, by_name, by_attr)
                seen.update(files)
                for t in (child.targets if isinstance(child, ast.Assign) else [child.target]):
                    bind(t, files, in_class)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = child.args
                pos = [*a.posonlyargs, *a.args]
                for arg, default in [*zip(pos[len(pos) - len(a.defaults):], a.defaults),
                                     *zip(a.kwonlyargs, a.kw_defaults)]:
                    if default is not None:
                        files = _path_refs(default, by_name, by_attr)
                        seen.update(files)
                        bind(ast.Name(arg.arg), files, False)
            visit(child, isinstance(child, ast.ClassDef) or (in_class and not isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef))))

    visit(tree, False)
    verbs: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = _chain(node.func)
        name = fn[-1] if fn else getattr(node.func, "attr", "")
        if name in _PATH_CTORS:
            continue
        if isinstance(node.func, ast.Attribute):
            files = _path_refs(node.func.value, by_name, by_attr)
            for f in files:
                verbs.setdefault(f, set()).add(_verb(name, node, True))
            seen.update(files)
        for arg in [*node.args, *(k.value for k in node.keywords)]:
            files = _path_refs(arg, by_name, by_attr)
            for f in files:
                verbs.setdefault(f, set()).add(_verb(name, node, False))
            seen.update(files)
    for text in literals:  # literals no path expression reached (dict values, tuples, ...)
        key = _data_key(text, False)
        if not any(s == key or s.endswith("/" + key) for s in seen):
            seen.add(key)
    reads = sorted(f for f in seen if "read" in verbs.get(f, ()))
    writes = sorted(f for f in seen if "write" in verbs.get(f, ()))
    uses = sorted(f for f in seen if f not in reads and f not in writes)
    return {"reads": reads, "writes": writes, "uses": uses}


def specs_of(tree: ast.Module) -> list[tuple[str, str, str]]:
    """(variable, spec name, owner) for each `X = Spec("name", owner="mod.Class.attr", ...)`."""
    out = []
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        fn = _chain(node.value.func)
        if not fn or fn[-1] != "Spec":
            continue
        kw = {k.arg: k.value for k in node.value.keywords}
        name = node.value.args[0] if node.value.args else kw.get("name")
        owner = kw.get("owner")
        if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
            continue
        var = node.targets[0].id if isinstance(node.targets[0], ast.Name) else ""
        out.append((var, name.value, owner.value if isinstance(owner, ast.Constant) else ""))
    return out


def owner_module(owner: str, names: dict[str, str], trees: dict[str, ast.Module | None]) -> str | None:
    """'control.Controller.standby_m' -> 'redzone/control.py' (a package defers to the submodule
    that defines the next name, e.g. decider.Decider -> redzone/decider/decider.py)."""
    parts = owner.split(".")
    for i in range(len(parts), 0, -1):
        for pre in ("redzone.", ""):
            mod = pre + ".".join(parts[:i])
            if mod not in names:
                continue
            path = names[mod]
            if path.endswith("__init__.py") and i < len(parts):
                pkg = path[: -len("__init__.py")]
                for sub in sorted(p for p in trees if p.startswith(pkg) and p.count("/") == pkg.count("/")):
                    t = trees[sub]
                    if t is not None and any(getattr(n, "name", None) == parts[i] for n in t.body):
                        return sub
            return path
    return None


def expand(glob: str) -> list[str]:
    """Expand {a,b} brace sets: 'x/{a,b}.py' -> ['x/a.py', 'x/b.py']."""
    m = _BRACE.search(glob)
    if not m:
        return [glob]
    head, tail = glob[: m.start()], glob[m.end():]
    return [g for opt in m.group(1).split(",") for g in expand(head + opt.strip() + tail)]


def ownership(root: Path) -> list[tuple[str, str]]:
    """(glob, owner) in table order from the 'File ownership' table in architecture.md."""
    p = root / "docs/architecture.md"
    if not p.exists():
        return []
    rules, inside = [], False
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            inside = "file ownership" in line.lower()
            continue
        if not inside or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or cells[0] not in LANES:
            continue
        for tick in _TICKS.findall(cells[1]):
            rules.extend((g, cells[0]) for g in expand(tick.strip()))
    return rules


def owner_of(path: str, rules: list[tuple[str, str]]) -> str:
    for glob, owner in rules:
        if fnmatch.fnmatchcase(path, glob):
            return owner
    return "unowned"


def playbook_ids(root: Path) -> set[str]:
    p = root / "docs/playbooks.md"
    return set(PLAYBOOK_ID.findall(p.read_text(encoding="utf-8"))) if p.exists() else set()


def northstar_metrics(root: Path) -> set[str]:
    """First-column text of every table in docs/northstar.md whose header starts with 'metric'."""
    p = root / "docs/northstar.md"
    if not p.exists():
        return set()
    out, inside = set(), False
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            inside = False
            continue
        first = line.strip().strip("|").split("|")[0].strip()
        if first.lower() == "metric":
            inside = True
        elif inside and not set(first) <= set("-: "):
            out.add(first)
    return out


def load_tags(root: Path, metrics: set[str], ids: set[str], paths: list[str]) -> list[tuple[str, list[str]]]:
    p = root / TAGS
    if not p.exists():
        return []
    raw = json.loads(p.read_text(encoding="utf-8"))
    bad = []
    for key, tags in raw.items():
        if not any(fnmatch.fnmatchcase(path, key) for path in paths):
            bad.append(f"{key}: matches no module")
        bad.extend(f"{key}: unknown tag {t!r}" for t in tags if t not in metrics and t not in ids)
    if bad:
        raise TagError("; ".join(bad))
    return [(k, list(v)) for k, v in raw.items()]


# ---- the graph --------------------------------------------------------------------------------

def scan(root: Path) -> dict[str, Info]:
    """Every module path -> Info (raises TagError on an unknown tag)."""
    root = Path(root)
    kinds = module_files(root)
    trees: dict[str, ast.Module | None] = {}
    sources: dict[str, str] = {}
    for path in kinds:
        src = (root / path).read_text(encoding="utf-8", errors="replace")
        sources[path] = src
        try:
            trees[path] = ast.parse(src)
        except SyntaxError:
            trees[path] = None
    names = {dotted(p): p for p in kinds}
    protocols = contract_protocols(trees)
    rules = ownership(root)
    ids = playbook_ids(root)
    tags = load_tags(root, northstar_metrics(root), ids, list(kinds))

    infos: dict[str, Info] = {}
    implements: dict[str, set[str]] = {}
    new_calls: dict[str, set[tuple[str, str]]] = {}
    spec_vars: dict[tuple[str, str], str] = {}
    for path, kind in kinds.items():
        tree = trees[path]
        info = Info(path=path, kind=kind, role=role_of(tree) if tree else "unparsed")
        info.owner = owner_of(path, rules)
        serves = set(PLAYBOOK_ID.findall(sources[path])) & ids
        for key, vals in tags:
            if fnmatch.fnmatchcase(path, key):
                serves.update(vals)
        info.serves = sorted(serves) or ["untagged"]
        if tree is not None:
            mods, used = imports_of(tree, dotted(path), kind == "package", names)
            mods.discard(path)
            info.inputs = sorted(mods)
            info.contracts = sorted(used)
            implements[path] = implements_of(tree, protocols)
            aliases = alias_map(tree, dotted(path), kind == "package")
            calls, news = calls_of(tree, path, aliases, names)
            info.calls = sorted(calls)
            new_calls[path] = news
            info.data = data_of(tree)
            for var, spec, owner in specs_of(tree):
                info.declares.append(spec)
                info.spec_owners[spec] = owner
                spec_vars[(path, var)] = spec
        infos[path] = info
    for path, info in infos.items():
        for spec, owner in info.spec_owners.items():
            mod = owner_module(owner, names, trees)
            if mod in infos:
                infos[mod].drives.append(spec)
    for path, news in new_calls.items():
        infos[path].drives.extend(spec_vars[n] for n in news if n in spec_vars)
    for info in infos.values():
        info.declares = sorted(set(info.declares))
        info.drives = sorted(set(info.drives))
    importers: dict[str, set[str]] = {p: set() for p in infos}
    for path, info in infos.items():
        for dep in info.inputs:
            importers[dep].add(path)
    for path, info in infos.items():
        info.outputs = sorted(importers[path]) + [f"implements:{n}" for n in sorted(implements.get(path, ()))]
        info.inputs = info.inputs + [f"uses:{n}" for n in info.contracts]
    return infos


def is_orphan(path: str, mod: dict) -> bool:
    """A plain module nothing imports (entries, packages and __main__ are run, not imported)."""
    return (mod["kind"] == "module" and not path.endswith("/__main__.py")
            and not any(not o.startswith("implements:") for o in mod["outputs"]))


def build(root: Path) -> dict:
    infos = scan(root)
    mods = {p: {"kind": i.kind, "role": i.role, "owner": i.owner, "serves": i.serves,
                "inputs": i.inputs, "outputs": i.outputs, "contracts": i.contracts,
                "calls": i.calls, "data": i.data,
                "machines": {"declares": i.declares, "drives": i.drives}}
            for p, i in sorted(infos.items())}
    files: dict[str, dict[str, list[str]]] = {}
    for p, m in mods.items():
        for verb, role in (("reads", "readers"), ("writes", "writers"), ("uses", "users")):
            for f in m["data"][verb]:
                files.setdefault(f, {"readers": [], "writers": [], "users": []})[role].append(p)
    machines = {}
    for p, i in sorted(infos.items()):
        for spec in i.declares:
            machines[spec] = {"declared_in": p, "owner": i.spec_owners[spec],
                              "driven_by": sorted(q for q, j in infos.items() if spec in j.drives)}
    summary = {
        "modules": len(mods),
        "calls": sum(len(m["calls"]) for m in mods.values()),
        "data_files": len(files),
        "machines": len(machines),
        "untagged": sum(m["serves"] == ["untagged"] for m in mods.values()),
        "unowned": sum(m["owner"] == "unowned" for m in mods.values()),
        "orphans": sum(is_orphan(p, m) for p, m in mods.items()),
    }
    # hand-written lane pages (docs/lanes/<lane>.md: endpoints, flags, the contract fields each view reads),
    # linked from that lane's section; the generated tables never hold prose
    pages = {lane: f"lanes/{lane.lower().replace('/', '-').replace(' ', '-')}.md" for lane in LANES}
    pages = {lane: p for lane, p in pages.items() if (Path(root) / "docs" / p).exists()}
    return {"version": 2, "modules": mods, "summary": summary, "data_files": dict(sorted(files.items())),
            "machines": dict(sorted(machines.items())), **({"pages": pages} if pages else {})}


# ---- rendering --------------------------------------------------------------------------------

def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _short(path: str) -> str:
    return dotted(path).removeprefix("redzone.") if path.endswith(".py") else path


def _edges(items: list[str]) -> str:
    shown = [_short(x) for x in items[:SHOW_EDGES]]
    more = len(items) - SHOW_EDGES
    return ", ".join(shown) + (f" +{more}" if more > 0 else "") if items else "-"


def render_json(graph: dict) -> str:
    return json.dumps(graph, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def render_md(graph: dict) -> str:
    mods, s = graph["modules"], graph["summary"]
    out = [
        "# City map",
        "",
        "Generated by `python scripts/citymap.py` from the source, docs/architecture.md (owners),",
        "docs/playbooks.md, docs/northstar.md and docs/citymap_tags.json (serves tags).",
        "Do not edit by hand: rerun the script (`--check` exits 1 when this file is stale).",
        "Machine-readable twin: [citymap.json](citymap.json).",
        "",
        f"**{s['modules']} modules**, {s['untagged']} untagged, {s['unowned']} unowned, "
        f"{s['orphans']} orphans (no importer); {s['calls']} call edges, {s['data_files']} data files, "
        f"{s['machines']} machines.",
        "",
        "Slice for an agent: `python scripts/citymap.py --focus <file>`; PR risk list: "
        "`python scripts/citymap.py --impact <file>`.",
        "",
        "inputs = repo modules it imports + `uses:` contract names; outputs = modules that import it "
        "+ `implements:` contract Protocols. Long edge lists show the first "
        f"{SHOW_EDGES} (full lists in the JSON).",
    ]
    for lane in (*LANES, "unowned"):
        rows = [(p, m) for p, m in mods.items() if m["owner"] == lane]
        if not rows:
            continue
        out += ["", f"## {lane} ({len(rows)})", ""]
        if lane in graph.get("pages", {}):
            out += [f"Lane page: [{graph['pages'][lane]}]({graph['pages'][lane]})", ""]
        out += ["| module | role | inputs -> outputs | serves |", "|---|---|---|---|"]
        for p, m in rows:
            kind = "" if m["kind"] == "module" else f" ({m['kind']})"
            out.append(f"| `{p}`{kind} | {_cell(m['role'])} | {_cell(_edges(m['inputs']))} -> "
                       f"{_cell(_edges(m['outputs']))} | {_cell(', '.join(m['serves']))} |")
    untagged = [p for p, m in mods.items() if m["serves"] == ["untagged"]]
    orphan = [p for p, m in mods.items() if is_orphan(p, m)]
    out += ["", f"## Untagged ({len(untagged)})", ""] + [f"- `{p}`" for p in untagged]
    out += ["", f"## Orphans (no importer) ({len(orphan)})", ""] + [f"- `{p}`" for p in orphan]
    out += render_edges_md(graph)
    return "\n".join(out) + "\n"


def render_edges_md(graph: dict) -> list[str]:
    """Compact call, data and machine sections (full lists in the JSON)."""
    calls = callers(graph)
    top = sorted(calls.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:TOP_CALLED]
    out = ["", f"## Most-called symbols (top {len(top)} of {len(calls)})", "",
           "| symbol | callers |", "|---|---:|"]
    out += [f"| `{c}` | {len(s)} |" for c, s in top]
    files = {f: w for f, w in graph["data_files"].items() if len({*w["readers"], *w["writers"], *w["users"]}) > 1}
    out += ["", f"## Shared data files ({len(files)} of {len(graph['data_files'])})", "",
            "Files two or more modules name (the rest are in the JSON). Best effort from string literals: "
            "w = writes, r = reads, u = named but unclassified.", "",
            "| file | writers (w) | readers (r) | other (u) |", "|---|---|---|---|"]
    for f, who in files.items():
        out.append(f"| `{_cell(f)}` | {_cell(_edges(who['writers']))} | {_cell(_edges(who['readers']))} | "
                   f"{_cell(_edges(who['users']))} |")
    mach = graph["machines"]
    out += ["", f"## Machines ({len(mach)})", "", "| machine | declared in | owner | driven by |", "|---|---|---|---|"]
    for name, spec in mach.items():
        out.append(f"| `{name}` | {_short(spec['declared_in'])} | `{spec['owner']}` | "
                   f"{_cell(_edges(spec['driven_by']))} |")
    return out
# ---- --focus and --impact ---------------------------------------------------------------------

CHARS_PER_TOKEN = 4
DOC_MAX = 90
FOCUS_METHODS = 6  # public methods shown per class outside the focus file
IMPACT_SHOW = 12  # deep importer levels and indirect tests list this many, then "+N more"


def _rel(root: Path, path: str) -> str:
    p = Path(path)
    if p.is_absolute():
        try:
            return p.resolve().relative_to(Path(root).resolve()).as_posix()
        except ValueError:
            return p.as_posix()
    return p.as_posix().removeprefix("./")


def _imports(m: dict) -> list[str]:
    return [x for x in m["inputs"] if not x.startswith("uses:")]


def _importers(m: dict) -> list[str]:
    return [x for x in m["outputs"] if not x.startswith("implements:")]


def callers(graph: dict) -> dict[str, set[str]]:
    """'path:symbol' -> modules that call it."""
    out: dict[str, set[str]] = {}
    for p, m in graph["modules"].items():
        for c in m["calls"]:
            out.setdefault(c, set()).add(p)
    return out


def in_degree(graph: dict) -> dict[str, int]:
    """Distinct modules that import or call each module (the Aider-style rank)."""
    refs: dict[str, set[str]] = {p: set(_importers(m)) for p, m in graph["modules"].items()}
    for p, m in graph["modules"].items():
        for c in m["calls"]:
            refs.setdefault(c.split(":")[0], set()).add(p)
    return {p: len(s) for p, s in refs.items()}


def adjacency(graph: dict) -> dict[str, set[str]]:
    """Undirected neighbours over imports, calls, shared data files and machines."""
    mods = graph["modules"]
    adj: dict[str, set[str]] = {p: set() for p in mods}

    def link(a: str, b: str) -> None:
        if a != b and a in adj and b in adj:
            adj[a].add(b)
            adj[b].add(a)

    for p, m in mods.items():
        for dep in _imports(m):
            link(p, dep)
        for c in m["calls"]:
            link(p, c.split(":")[0])
    for f in graph["data_files"].values():
        group = sorted({*f["readers"], *f["writers"], *f["users"]})
        for a in group:
            for b in group:
                link(a, b)
    for spec in graph["machines"].values():
        for d in spec["driven_by"]:
            link(spec["declared_in"], d)
    return adj


def _doc1(node: ast.AST) -> str:
    doc = ast.get_docstring(node) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else None
    line = doc.strip().splitlines()[0].strip() if doc else ""
    return line if len(line) <= DOC_MAX else line[: DOC_MAX - 3].rstrip() + "..."


def _is_setter(node: ast.AST) -> bool:
    return any(isinstance(d, ast.Attribute) and d.attr in ("setter", "deleter")
               for d in getattr(node, "decorator_list", []))


def _sig(node: ast.AST) -> str:
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(ast.unparse(b) for b in node.bases)
        return f"class {node.name}" + (f"({bases})" if bases else "")
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    ret = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    return f"{prefix} {node.name}({ast.unparse(node.args)}){ret}"


def signatures(root: Path, path: str, refs: dict[str, int], methods: int | None = None) -> list[str]:
    """Public top-level defs/classes (and public methods), most-called first, with doc line 1."""
    try:
        tree = ast.parse((Path(root) / path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return []
    defs = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    tops = [n for n in tree.body if isinstance(n, defs) and not n.name.startswith("_")]
    tops.sort(key=lambda n: (-refs.get(n.name, 0), n.lineno))
    out = []
    for n in tops:
        doc = _doc1(n)
        used = refs.get(n.name, 0)
        out.append(f"  {_sig(n)}" + (f"  # {doc}" if doc else "") + (f"  [called by {used}]" if used else ""))
        if isinstance(n, ast.ClassDef):
            meths = [c for c in n.body if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and not c.name.startswith("_") and not _is_setter(c)]
            for c in meths[:methods]:
                doc = _doc1(c)
                out.append(f"    {_sig(c)}" + (f"  # {doc}" if doc else ""))
            if methods is not None and len(meths) > methods:
                out.append(f"    ... +{len(meths) - methods} more methods")
    return out


def focus(graph: dict, root: Path, path: str, tokens: int = 1500) -> str:
    """Ranked 1-2 hop neighbourhood of `path`, signatures only, cut at ~tokens."""
    mods = graph["modules"]
    adj, deg, calls = adjacency(graph), in_degree(graph), callers(graph)
    hop = {path: 0}
    frontier = [path]
    for d in (1, 2):
        nxt = sorted({n for f in frontier for n in adj[f]} - set(hop))
        hop.update((n, d) for n in nxt)
        frontier = nxt
    ranked = sorted(hop, key=lambda p: (hop[p], -deg.get(p, 0), p))
    budget = tokens * CHARS_PER_TOKEN
    head = (f"# focus {path}: {len(hop) - 1} neighbours within 2 hops (imports, calls, data, machines), "
            f"nearest first, then by in-degree; ~{tokens} tokens")
    out, used, shown = [head], len(head) + 1, 0
    for p in ranked:
        m = mods[p]
        refs = {c.split(":", 1)[1]: len(s) for c, s in calls.items() if c.split(":")[0] == p}
        block = [f"{p}  [hop {hop[p]}, in {deg.get(p, 0)}, {m['owner']}]  {m['role']}"]
        extra = [*(f"writes {f}" for f in m["data"]["writes"]), *(f"reads {f}" for f in m["data"]["reads"]),
                 *(f"declares {s}" for s in m["machines"]["declares"]),
                 *(f"drives {s}" for s in m["machines"]["drives"])]
        if extra:
            block.append("  ~ " + "; ".join(extra))
        block += signatures(root, p, refs, None if hop[p] == 0 else FOCUS_METHODS)
        for i, line in enumerate(block):
            if used + len(line) + 1 > budget:
                if i:
                    out.append("  ...")
                    shown += 1
                left = len(ranked) - shown
                out.append(f"... cut at ~{tokens} tokens; {left} more module(s) not shown (raise --tokens)")
                return "\n".join(out) + "\n"
            out.append(line)
            used += len(line) + 1
        shown += 1
    return "\n".join(out) + "\n"


def test_importers(root: Path, names: dict[str, str]) -> dict[str, list[str]]:
    """repo module path -> tests/**/*.py files that import it directly."""
    out: dict[str, list[str]] = {}
    for t in sorted((Path(root) / "tests").rglob("*.py")):
        rel = t.relative_to(root).as_posix()
        if "fixtures" in t.relative_to(root).parts:  # fixture repos, not tests
            continue
        try:
            tree = ast.parse(t.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        mods, _ = imports_of(tree, dotted(rel), t.name == "__init__.py", names)
        for m in mods:
            out.setdefault(m, []).append(rel)
    return out


def _wrap(items: list[str], indent: str = "  ") -> list[str]:
    lines, cur = [], indent
    for i, item in enumerate(items):
        piece = item + (", " if i < len(items) - 1 else "")
        if len(cur) + len(piece) > 100 and cur.strip():
            lines.append(cur.rstrip())
            cur = indent
        cur += piece
    if cur.strip():
        lines.append(cur.rstrip())
    return lines or [indent + "-"]


def impact(graph: dict, root: Path, path: str) -> str:
    """Everything a change to `path` can reach, grouped for a PR's risk section."""
    mods = graph["modules"]
    m = mods[path]
    depth = {path: 0}
    frontier = [path]
    while frontier:
        nxt = sorted({i for f in frontier for i in _importers(mods[f])} - set(depth))
        depth.update((n, depth[frontier[0]] + 1) for n in nxt)
        frontier = nxt
    names = {dotted(p): p for p in mods}
    tests = test_importers(root, names)
    direct = sorted(tests.get(path, []))
    via: dict[str, int] = {}  # test -> depth of the nearest importer it imports
    for p, d in depth.items():
        for t in tests.get(p, []) if p != path else []:
            via[t] = min(via.get(t, d), d)
    for t in direct:
        via.pop(t, None)
    via_sorted = sorted(via, key=lambda t: (via[t], t))
    out = [f"# impact {path}", f"owner: {m['owner']}  role: {m['role']}", ""]
    max_d = max(depth.values())
    out.append(f"importers (transitive): {len(depth) - 1}")
    for d in range(1, max_d + 1):
        at = sorted(p for p, v in depth.items() if v == d)
        shown = at if d <= 2 else at[:IMPACT_SHOW]
        out.append(f" depth {d} ({len(at)}):")
        out += _wrap(shown + ([f"+{len(at) - len(shown)} more"] if len(at) > len(shown) else []), "   ")
    calls = callers(graph)
    used = sorted(((c.split(":", 1)[1], len(s)) for c, s in calls.items() if c.split(":")[0] == path),
                  key=lambda x: (-x[1], x[0]))
    out += ["", "symbols called from other modules:"] + _wrap([f"{s} ({n})" for s, n in used])
    impl = [o.removeprefix("implements:") for o in m["outputs"] if o.startswith("implements:")]
    out += ["", "contracts implemented:"] + _wrap(impl) + ["contracts used:"] + _wrap(m["contracts"])
    out += ["", f"tests importing it directly ({len(direct)}):"] + _wrap(direct)
    near = via_sorted[:IMPACT_SHOW]
    out += [f"tests importing an importer ({len(via)}, nearest first):"] + _wrap(
        near + ([f"+{len(via) - len(near)} more"] if len(via) > len(near) else []))
    mach = m["machines"]
    out += ["", "machines declared:"] + _wrap(mach["declares"]) + ["machines driven:"] + _wrap(mach["drives"])
    out += ["", "data files:"]
    rows = [(f, verb) for verb in ("writes", "reads", "uses") for f in m["data"][verb]]
    if not rows:
        out.append("  -")
    for f, verb in sorted(rows):
        info = graph["data_files"].get(f, {})
        others = [f"{o} ({v[:-1]})" for v in ("writers", "readers", "users")
                  for o in info.get(v, []) if o != path]
        out.append(f"  {verb} {f}" + (f"; also: {', '.join(others)}" if others else ""))
    out += ["", "north star / serves:"] + _wrap(m["serves"])
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--check", action="store_true", help="exit 1 if the docs are stale")
    ap.add_argument("--focus", metavar="FILE", help="print a ranked 1-2 hop slice of FILE (signatures only)")
    ap.add_argument("--tokens", type=int, default=1500, help="budget for --focus (about 4 chars per token)")
    ap.add_argument("--impact", metavar="FILE", help="print what a change to FILE can reach")
    args = ap.parse_args(argv)
    try:
        graph = build(args.root)
    except TagError as e:
        print(f"citymap: bad tags in {TAGS}: {e}", file=sys.stderr)
        return 2
    for flag, fn in (("focus", focus), ("impact", impact)):
        target = getattr(args, flag)
        if target is None:
            continue
        rel = _rel(args.root, target)
        if rel not in graph["modules"]:
            print(f"citymap: {target} is not a mapped module (redzone/**, agent/**, scripts/*.py)",
                  file=sys.stderr)
            return 2
        text = fn(graph, args.root, rel, args.tokens) if flag == "focus" else fn(graph, args.root, rel)
        sys.stdout.write(text)
        return 0
    want = {MD_OUT: render_md(graph), JSON_OUT: render_json(graph)}
    if args.check:
        stale = [p for p, text in want.items()
                 if not (args.root / p).exists() or (args.root / p).read_text(encoding="utf-8") != text]
        if stale:
            print(f"citymap stale: {', '.join(stale)} (run python scripts/citymap.py)")
            return 1
        print("citymap up to date")
        return 0
    for p, text in want.items():
        (args.root / p).parent.mkdir(parents=True, exist_ok=True)
        (args.root / p).write_text(text, encoding="utf-8")
    s = graph["summary"]
    print(f"citymap: {s['modules']} modules, {s['untagged']} untagged, {s['unowned']} unowned, "
          f"{s['orphans']} orphans, {s['calls']} call edges, {s['data_files']} data files, {s['machines']} machines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
