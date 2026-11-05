"""FTN charting (via nflverse) -> a per-play NFL side table: no-huddle, motion, play action, box count and the rest.

Serves: NFL entries landing in breaks (is_no_huddle marks plays where the next snap comes fast, a break that never
arrives) and tokenizer v2 rich NFL fields (EXP-1000). Owner: Data Planner A. Scout #276 (terms id ftn_charting).

    python scripts/pbp/ftn_charting.py OUT_DIR [--seasons 2022-2024]

Join key: nflverse game_id + play_id (the same ids as nflverse pbp in the factory). Flags only, no text. The 2025
season is never written: it overlaps the NFL locked window (EVAL_FIRST_NIGHT nfl 20250905).
"""
from __future__ import annotations

import json
from pathlib import Path

try:
    from pbp import schema
except ImportError:
    try:
        from scripts.pbp import schema
    except ImportError:  # run as a script
        import schema  # type: ignore

URL = "https://github.com/nflverse/nflverse-data/releases/download/ftn_charting/ftn_charting_{y}.parquet"
LICENSE = "FTN Data via nflverse (CC BY-SA 4.0, share-alike); plan B personal use; credit FTN Data via nflverse"
SOURCE = "ftn_charting"
UA = "redzone-personal-research/1.0"
LAST_SEASON = 2024                # 2025 overlaps the locked NFL window
FLAGS = ["is_no_huddle", "is_motion", "is_play_action", "is_screen_pass", "is_rpo", "is_trick_play",
         "is_qb_out_of_pocket", "is_interception_worthy", "is_throw_away", "is_catchable_ball", "is_contested_ball",
         "is_created_reception", "is_drop", "is_qb_sneak", "is_qb_fault_sack"]
COUNTS = ["n_offense_backfield", "n_defense_box", "n_blitzers", "n_pass_rushers"]
CATS = ["starting_hash", "qb_location", "read_thrown"]


def convert(df):
    """Raw FTN frame -> side table (game_id, play_id, season, week, int8 flags, int8 counts, short codes)."""
    import pandas as pd
    out = pd.DataFrame({"game_id": df.nflverse_game_id.astype(str), "play_id": df.nflverse_play_id.astype("int32"),
                        "season": df.season.astype("int16"), "week": df.week.astype("int8")})
    for c in FLAGS:
        out[c] = df[c].fillna(False).astype("int8")
    for c in COUNTS:
        out[c] = pd.to_numeric(df[c], errors="coerce").fillna(-1).clip(-1, 11).astype("int8")
    for c in CATS:
        out[c] = df[c].fillna("0").astype(str).str[:4]
    return out.drop_duplicates(["game_id", "play_id"]).sort_values(["game_id", "play_id"], kind="stable")


def pull_season(year: int, out_dir, session=None) -> dict | None:
    if year > LAST_SEASON:
        return None
    import io

    import pandas as pd
    import requests
    shard = Path(out_dir) / SOURCE / f"{year}.parquet"
    side = Path(str(shard) + ".json")
    if shard.exists() and side.exists():
        return json.loads(side.read_text())
    s = session or requests.Session()
    s.headers.update({"User-Agent": UA})
    r = s.get(URL.format(y=year), timeout=300)
    if r.status_code != 200:
        return None
    t = convert(pd.read_parquet(io.BytesIO(r.content)))
    shard.parent.mkdir(parents=True, exist_ok=True)
    t.to_parquet(shard, compression="zstd", index=False)
    meta = {"source": SOURCE, "league": "nfl", "season": year, "playoffs": False, "license": LICENSE,
            "tag": schema.PERSONAL, "data_plan": "B", "games": int(t.game_id.nunique()), "events": int(len(t)),
            "v1_tokens": int(len(t)) * 6, "bytes": shard.stat().st_size,
            "rows_are": "per-play charting flags (side table; join nflverse game_id + play_id)",
            "terms_id": "ftn_charting", "credit": "FTN Data via nflverse"}
    side.write_text(json.dumps(meta, indent=1))
    return meta


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--seasons", default="2022-2024")
    a = ap.parse_args()
    lo, _, hi = a.seasons.partition("-")
    for y in range(int(lo), int(hi or lo) + 1):
        m = pull_season(y, a.out_dir)
        print(y, m and {k: m[k] for k in ("games", "events", "v1_tokens")})
