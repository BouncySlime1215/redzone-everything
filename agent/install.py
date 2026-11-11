"""Render (and optionally write) the watchdog's launchd plist from the repo template. Stdlib only.

    python -m agent.install              # print the plist for this checkout and this user
    python -m agent.install --check      # compare it with ~/Library/LaunchAgents/com.redzone.agent.plist
    python -m agent.install --write      # write that file (never loads it)

Why: nothing installed the watchdog's plist. The one on the Mac (Nov 3) had a WorkingDirectory under ~/Documents and a
log under data/, which macOS privacy (TCC) closes to launchd (EX_CONFIG 78): the watchdog ran no check from Nov 3 to Nov 8
and nothing noticed (P-02). The rendered plist has neither.

This module only writes the file. It never runs launchctl and never changes what is loaded: after --write, load it
yourself, as the printed commands say.
"""
from __future__ import annotations

import argparse
import os
import plistlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = Path(__file__).with_name("com.redzone.agent.plist")
LABEL = "com.redzone.agent"
# The template was written on this checkout; its paths are rewritten for wherever it is rendered.
TEMPLATE_ROOT = "/Users/nick_matta/Documents/Codex/2026-11-02/task-2/redzone"
TEMPLATE_HOME = "/Users/nick_matta"


def installed_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def render(root: Path = ROOT, home: Path | None = None, template: Path = TEMPLATE) -> str:
    """The plist text for `root`, owned by `home`'s user. Raises ValueError if the result would be one macOS
    privacy blocks (a WorkingDirectory, or a log under ~/Documents)."""
    home = home or Path.home()
    text = template.read_text()
    # longest first: the root is under the home, and must be rewritten before it
    text = text.replace(TEMPLATE_ROOT, str(root)).replace(TEMPLATE_HOME, str(home))
    problems = problems_in(text, home)
    if problems:
        raise ValueError("; ".join(problems))
    return text


def problems_in(text: str, home: Path | None = None) -> list[str]:
    """Why launchd would fail this plist with EX_CONFIG (78)."""
    home = home or Path.home()
    try:
        d = plistlib.loads(text.encode())
    except Exception as e:   # not a plist at all
        return [f"not a valid plist: {e}"]
    out = []
    if d.get("Label") != LABEL:
        out.append(f"Label is {d.get('Label')!r}, not {LABEL!r}")
    if "WorkingDirectory" in d:
        out.append("has a WorkingDirectory (macOS privacy closes ~/Documents to launchd: EX_CONFIG 78)")
    docs = str(home / "Documents")
    for key in ("StandardOutPath", "StandardErrorPath"):
        if str(d.get(key, "")).startswith(docs):
            out.append(f"{key} is under ~/Documents (EX_CONFIG 78)")
    return out


def check(home: Path | None = None, root: Path = ROOT) -> list[str]:
    """What is wrong with the installed plist, in words ([] = it matches this checkout's template)."""
    path = installed_path(home)
    if not path.exists():
        return [f"{path} is not installed"]
    got = path.read_text()
    out = problems_in(got, home)
    if not out and got != render(root, home):
        out.append("differs from the repo template (agent/com.redzone.agent.plist); --write to refresh it")
    return out


def write(root: Path = ROOT, home: Path | None = None) -> Path:
    """Write the rendered plist (atomically) and make the log folder it needs. Does not load it."""
    home = home or Path.home()
    text = render(root, home)
    path = installed_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    (home / "Library" / "Logs" / "redzone").mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--write", action="store_true", help="write ~/Library/LaunchAgents/com.redzone.agent.plist")
    ap.add_argument("--check", action="store_true", help="compare the installed plist with this checkout's")
    a = ap.parse_args(argv)
    if a.check:
        bad = check()
        print("\n".join(bad) if bad else "installed plist matches the template")
        return 1 if bad else 0
    if a.write:
        path = write()
        uid = os.getuid()
        print(f"wrote {path}\nto load it (this module does not):\n"
              f"  launchctl bootstrap gui/{uid} {path}\n  launchctl kickstart -k gui/{uid}/{LABEL}")
        return 0
    print(render(), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
