"""City map: one generated graph of every RedZone module (role, inputs -> outputs, owner, serves).

    python scripts/citymap.py            # rewrite docs/citymap.md and docs/citymap.json
    python scripts/citymap.py --check    # exit 1 if they are stale

Stdlib only and it never imports redzone, so it is safe on the live Mac.
- role: first sentence of the module docstring.
Covers redzone/**, agent/** (the LOCAL AI lane) and scripts/*.py.
- inputs: redzone/agent/scripts modules it imports (relative imports resolved) + contract names it uses.
- outputs: modules that import it + contract Protocols it implements (class X(BreakModel)).
- owner: the 'File ownership' table in docs/architecture.md section 4, first match wins.
- serves: playbook ids in its source (checked against docs/playbooks.md) + North Star metric
  lines from docs/citymap_tags.json (checked against docs/northstar.md).
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
LANES = ("DECISION", "MOMENT/BREAK", "LOCAL AI", "SWITCHING", "SYNC", "PLATFORM", "INTEGRATION")
PACKAGES = ("redzone", "agent")
PLAYBOOK_ID = re.compile(r"\b(?:APP|BTN|FEED|GAME|MAC|NET|SAFE|SLATE|STK|SYNC|TASTE|TV)-\d{2}\b")
_TICKS = re.compile(r"`([^`]+)`")
_BRACE = re.compile(r"\{([^{}]*)\}")
_SENTENCE = re.compile(r"^(.+?[.!?])(?:\s|$)")
ROLE_MAX = 160
SHOW_EDGES = 6


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
        infos[path] = info
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
                "inputs": i.inputs, "outputs": i.outputs, "contracts": i.contracts}
            for p, i in sorted(infos.items())}
    summary = {
        "modules": len(mods),
        "untagged": sum(m["serves"] == ["untagged"] for m in mods.values()),
        "unowned": sum(m["owner"] == "unowned" for m in mods.values()),
        "orphans": sum(is_orphan(p, m) for p, m in mods.items()),
    }
    return {"version": 1, "modules": mods, "summary": summary}


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
        f"{s['orphans']} orphans (no importer).",
        "",
        "inputs = repo modules it imports + `uses:` contract names; outputs = modules that import it "
        "+ `implements:` contract Protocols. Long edge lists show the first "
        f"{SHOW_EDGES} (full lists in the JSON).",
    ]
    for lane in (*LANES, "unowned"):
        rows = [(p, m) for p, m in mods.items() if m["owner"] == lane]
        if not rows:
            continue
        out += ["", f"## {lane} ({len(rows)})", "", "| module | role | inputs -> outputs | serves |",
                "|---|---|---|---|"]
        for p, m in rows:
            kind = "" if m["kind"] == "module" else f" ({m['kind']})"
            out.append(f"| `{p}`{kind} | {_cell(m['role'])} | {_cell(_edges(m['inputs']))} -> "
                       f"{_cell(_edges(m['outputs']))} | {_cell(', '.join(m['serves']))} |")
    untagged = [p for p, m in mods.items() if m["serves"] == ["untagged"]]
    orphan = [p for p, m in mods.items() if is_orphan(p, m)]
    out += ["", f"## Untagged ({len(untagged)})", ""] + [f"- `{p}`" for p in untagged]
    out += ["", f"## Orphans (no importer) ({len(orphan)})", ""] + [f"- `{p}`" for p in orphan]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--check", action="store_true", help="exit 1 if the docs are stale")
    args = ap.parse_args(argv)
    try:
        graph = build(args.root)
    except TagError as e:
        print(f"citymap: bad tags in {TAGS}: {e}", file=sys.stderr)
        return 2
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
          f"{s['orphans']} orphans")
    return 0


if __name__ == "__main__":
    sys.exit(main())
