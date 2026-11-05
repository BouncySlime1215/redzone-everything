"""Cricsheet ball-by-ball JSON -> play table (one row per delivery).

Serves: North Star "% of hindsight-oracle value achieved" via world-model pretraining scale (~20k matches, T20 ~240,
ODI ~600, Test ~2000 deliveries each). Owner: Data Planner A.

Source: https://cricsheet.org/downloads/all_json.zip (or any per-competition zip), format v1.x. Licence ODC-BY 1.0,
credit Cricsheet. Cricket data has no home/away: home = info.teams[0], away = info.teams[1] by convention (NOT a
venue fact). period = innings number (super overs continue the numbering); side = possession = batting side;
home/away_score = running runs per side summed over all their innings (penalty runs not counted); outs = wickets
down in this innings before the ball ("retired hurt"/"retired not out" do not count). No clock, no wall time.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

try:
    from pbp import schema  # scripts/ on sys.path (tests, the Kaggle notebook)
except ImportError:
    try:
        from scripts.pbp import schema
    except ImportError:  # run as a script: scripts/pbp is on sys.path
        import schema  # type: ignore

LICENSE = "Open Data Commons Attribution (ODC-BY 1.0), credit Cricsheet"
CREDIT = "Data: Cricsheet (https://cricsheet.org), ODC-BY 1.0"
UA = "redzone-personal-research/1.0"
ALL_JSON_URL = "https://cricsheet.org/downloads/all_json.zip"
SOURCE = {"id": "cricsheet", "leagues": "many", "license": LICENSE, "tag": "",
          "est_events_per_game": {"T20": 240, "ODI": 600, "Test": 2000}}
EVENT_SLUGS = {"Indian Premier League": "ipl", "Big Bash League": "bbl", "Pakistan Super League": "psl",
               "Caribbean Premier League": "cpl", "Women's Big Bash League": "wbbl", "Bangladesh Premier League":
               "bpl", "Lanka Premier League": "lpl", "SA20": "sa20", "Major League Cricket": "mlc",
               "Women's Premier League": "wpl"}
INTL_FORMAT = {"T20": "t20i", "IT20": "t20i", "ODI": "odi", "ODM": "odi", "Test": "test", "MDM": "test"}
NOT_OUT = {"retired hurt", "retired not out"}


def slug(s) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s or "").lower()).strip("_")


def league_level(info: dict) -> tuple[str, str]:
    mt, gender = info.get("match_type", ""), info.get("gender", "male")
    intl = info.get("team_type") == "international" if info.get("team_type") else mt in ("Test", "ODI", "IT20")
    if intl:
        return f"{INTL_FORMAT.get(mt, slug(mt))}_{gender}", "intl"
    name = (info.get("event") or {}).get("name")
    if name:
        return EVENT_SLUGS.get(name, slug(name)), "pro"
    return f"{slug(mt)}_{gender}", "pro"


def _kind(d: dict) -> tuple[str, str]:
    ex, runs = d.get("extras") or {}, d.get("runs") or {}
    if d.get("wickets"):
        return "wicket", slug(d["wickets"][0].get("kind"))
    kind = next((k for k, src in (("wide", "wides"), ("noball", "noballs"), ("bye", "byes"),
                                  ("legbye", "legbyes")) if ex.get(src)), "delivery")
    b, t = runs.get("batter", 0), runs.get("total", 0)
    sub = "dot" if t == 0 else ("four" if b == 4 and not runs.get("non_boundary") else
                                "six" if b == 6 and not runs.get("non_boundary") else f"r{t}")
    return kind, sub


def convert_match(doc: dict, game_id: str) -> list[dict]:
    info = doc["info"]
    teams = list(info.get("teams") or ["", ""])[:2]
    league, level = league_level(info)
    first = str(info["dates"][0])
    base = dict(source="cricsheet", sport="cricket", league=league, level=level, season=int(first[:4]),
                game_id=game_id, night=first.replace("-", ""), home=slug(teams[0]), away=slug(teams[1]),
                license=LICENSE, tag="")
    score = {"home": 0, "away": 0}
    rows = []
    for inn_no, inn in enumerate(doc.get("innings") or [], 1):
        side = "home" if inn.get("team") == teams[0] else "away" if inn.get("team") == teams[1] else ""
        outs = 0
        dels = [d for ov in inn.get("overs") or [] for d in ov.get("deliveries") or []]
        for j, d in enumerate(dels):
            kind, sub = _kind(d)
            total = (d.get("runs") or {}).get("total", 0)
            if side:
                score[side] += total
            rows.append(schema.row(**base, seq=len(rows), period=inn_no, clock_s=None, elapsed_s=None,
                                   wall_ts=None, side=side, possession=side, kind=kind, sub_kind=sub,
                                   actor_id=d.get("batter"), actor2_id=d.get("bowler"), value=total,
                                   home_score=score["home"], away_score=score["away"], outs=outs,
                                   stop="end_period" if j == len(dels) - 1 else "none"))
            outs += sum(1 for w in d.get("wickets") or [] if w.get("kind") not in NOT_OUT)
    return rows


def download(url: str, dst: Path, session=None, chunk: int = 1 << 20) -> Path:
    """Resumable download (HTTP Range onto <dst>.part, renamed when complete); polite UA."""
    if dst.exists():
        return dst
    if session is None:
        import requests
        session = requests.Session()
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = Path(str(dst) + ".part")
    have = part.stat().st_size if part.exists() else 0
    r = session.get(url, headers={"User-Agent": UA, **({"Range": f"bytes={have}-"} if have else {})},
                    stream=True, timeout=120)
    if have and r.status_code != 206:
        have = 0
    r.raise_for_status()
    with open(part, "ab" if have else "wb") as f:
        for b in r.iter_content(chunk):
            f.write(b)
    part.rename(dst)
    return dst


def pull_all(out_dir, url: str = ALL_JSON_URL, leagues=None, session=None) -> list[dict]:
    """Download the zip, stream match files from it, one shard per league-season out_dir/cricsheet/<league>/<season>.
    Two passes over the zip (index by key, then convert per key) so memory holds one league-season at a time."""
    out = Path(out_dir) / "cricsheet"
    zpath = download(url, out / "_raw" / Path(url).name, session)
    keys: dict[tuple, list[str]] = {}
    with zipfile.ZipFile(zpath) as z:
        def load(n):
            return json.load(io.TextIOWrapper(z.open(n), "utf-8"))
        for n in z.namelist():
            if not n.endswith(".json"):
                continue
            info = load(n).get("info") or {}
            if not info.get("dates"):
                continue
            lg = league_level(info)[0]
            if leagues and lg not in leagues:
                continue
            keys.setdefault((lg, int(str(info["dates"][0])[:4])), []).append(n)
        metas = []
        for (lg, season), names in sorted(keys.items()):
            rows = []
            for n in names:
                g = convert_match(load(n), Path(n).stem)
                if g and not schema.validate(g):
                    rows += g
            if rows:
                metas.append(schema.write_shard(rows, out / lg / f"{season}.parquet", {
                    "source": "cricsheet", "league": lg, "season": season, "data_plan": "A/B",
                    "license": LICENSE, "tag": "", "credit": CREDIT, "url": url}))
    return metas


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--url", default=ALL_JSON_URL)
    ap.add_argument("--leagues", nargs="*")
    a = ap.parse_args()
    for meta in pull_all(a.out_dir, a.url, a.leagues):
        print(meta["league"], meta["season"], meta["events"], meta["games"])
