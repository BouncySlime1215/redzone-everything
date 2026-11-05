"""Night lab on an NBA-only slate (test helper for tests/test_buffer_shadow_contract.py; run as a script).

The stock night has one NBA game, so the buffer editor makes ~1 NBA entry. This runs the same night lab on three
overlapping NBA games from one eval-data-v1 FIT night (20261021: never locked, so the leak rule holds), so the editor
switches between NBA games and EXP-744's clock truth grades its entries. Args after the archive dir go to the night.

    python tests/night_nba_slate.py data/archive --start 20:55 --hours 1.5 --seed 1 --work W --json J [--set k=v ...]
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# (gid, target, kick ET): fit-split games of 20261021 (scripts/entry_autopsy.py nba --split fit lists them)
SLATE = [("401909835", "youtubetv:espn", "21:00", "ESPN"),      # MIL @ WSH
         ("401909839", "youtubetv:abc", "21:04", "ABC"),        # IND @ NO
         ("401909092", "prime:amzn1.dv.gti.night-gs-lal", "21:08", "")]   # GS @ LAL


def main(argv: list[str]) -> int:
    archive, rest = Path(argv[0]), argv[1:]
    src = [archive / "nba" / f"summary_{g}.json" for g, *_ in SLATE]
    if not all(p.exists() for p in src):
        print("SKIP: eval-data-v1 NBA archive not present (python scripts/fetch_eval_data.py)")
        return 3
    fix = Path(tempfile.mkdtemp(prefix="rz-nba-slate-"))
    for p in src:
        shutil.copy(p, fix / f"espn_nba_summary_{p.stem.split('_')[1]}.json")
    from redzone.lab.night import run as night_run
    night_run.FIX = fix
    night_run.GAMES[:] = [night_run.GameSpec("nba", f"espn_nba_summary_{g}", t, k, ch) for g, t, k, ch in SLATE]
    from redzone.lab.night.__main__ import main as night_main
    return night_main(rest)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
