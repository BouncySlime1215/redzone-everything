"""Route every game on a public ESPN schedule through the repo's ChannelMap and print the reach table. Read only.

    python scripts/coverage_audit.py --archive data/archive [--raw DIR] [--channels data/channels.json] [--out audit.json]

--archive  the RedZone archive (data/archive/<league>/scoreboard_*.json). It also names the time split.
--raw      optional folder of public scoreboards named <league>_<YYYYMMDD>.json (the planner's year of schedule).
           Without it the archive's own scoreboards are the schedule.

Dev-only: a night is read only when it is on the fit side of the split (redzone/lab/splits.py, active pin):
older than the newest-25% locked block and not after the pin's window end (fresh). Locked and fresh nights are
refused BY FILE NAME, before any file is opened; a league with no archive folder or no archived nights (no known split) is refused whole.
`--nights` asks for named nights and exits 2 if any is locked. `DevOnly` is shared with reach_grid.py and holdset.py.

Why: the planner's reach table (major considerations, section 3, "33% reachable today") lived in a one-off script
that read every night including the locked ones; this is the same table as a repo tool that cannot touch them.
The base is therefore the dev nights only, so its counts differ from the planner's 12,870-game year.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, ROOT.as_posix())

from redzone.lab import evaldata, splits  # noqa: E402

LEAGUES = ("nfl", "cfb", "nba", "cbb", "nhl", "mlb")
ET = ZoneInfo("America/New_York")
LOCAL = {"cbs", "fox", "nbc", "abc", "nbcsn"}
MINE = {"nfl": {"WSH"}, "nba": {"WSH"}, "nhl": {"WSH"}, "mlb": {"WSH"}, "cbb": {"UNC"}, "cfb": {"UNC"}}
CLASSES = ["yttv_code", "sunday_ticket", "yttv_local_search", "prime", "espn_event", "yttv_no_code",
           "peacock_only", "other_app", "no_route"]
FAST = ("yttv_code", "sunday_ticket")
WORKS = ("yttv_code", "sunday_ticket", "yttv_local_search", "prime")
OTHER_APPS = ("netflix", "apple", "max", "flosports", "b1g", "bigten")
# Certain local blackouts: Hurricanes on ESPN+ (NHL espn_event) and Hornets on League Pass (NBA prime).
BLACKOUT = {"nhl": ("CAR", "espn_event"), "nba": ("CHA", "prime")}


class LockedNightError(ValueError):
    """A locked or fresh night was asked for. Nothing may read or grade these."""


class DevOnly:
    """The dev-only filter for one league: which nights (YYYYMMDD) are safe to read."""

    def __init__(self, archive: Path | str, league: str):
        self.league = league
        league_dir = Path(archive) / league
        if not league_dir.is_dir():
            raise LockedNightError(f"{league}: no archive folder, so no known split; refusing every night")
        lock = evaldata.load_lock(evaldata.LOCK) or {}
        end = ((lock.get("window") or {}).get(league) or {}).get("end")
        nights = splits.archive_nights(league_dir, end)
        if not nights:
            raise LockedNightError(f"{league}: no archived nights, so no known split; refusing every night")
        self.nights = nights                       # night -> game ids, from the archive scoreboards
        self.split = splits.league_split(league, nights)
        self.test = set(self.split.test)
        self.cutoff = self.split.cutoff            # last night anything may learn from
        self.first_locked = min(self.test) if self.test else None

    def allows(self, night: str) -> bool:
        night = str(night)
        return bool(self.cutoff) and night <= self.cutoff and night not in self.test

    def game_ids(self) -> dict[str, str]:
        """game id -> night, for dev nights only. Per-game files (summary_<id>.json) are opened only for these ids,
        so a locked game's file is never read, not even to learn its date."""
        return {gid: n for n, ids in self.nights.items() if self.allows(n) for gid in ids}

    def require(self, nights) -> None:
        bad = sorted(str(n) for n in nights if not self.allows(str(n)))
        if bad:
            raise LockedNightError(f"{self.league}: locked or fresh nights refused: {bad[:5]}")


def scoreboard_files(archive: Path, raw: Path | None, league: str, dev: DevOnly) -> list[tuple[str, Path]]:
    """(night, path) of the scoreboards to read, dev nights only. Filtering is by name; refused files stay closed."""
    if raw is not None:
        cands = [(p.stem.split("_")[-1], p) for p in sorted(raw.glob(f"{league}_*.json"))]
    else:
        cands = [(p.stem.split("_")[-1], p) for p in sorted((archive / league).glob("scoreboard_*.json"))]
    return [(n, p) for n, p in cands if dev.allows(n)]


def classify(w, cm) -> str:
    t = w.target
    if not t:
        label = w.label.lower()
        if "peacock" in label:
            return "peacock_only"
        return "other_app" if any(x in label for x in OTHER_APPS) else "no_route"
    app, _, rest = t.partition(":")
    if app == "prime":
        return "prime"
    if app == "espn":
        return "espn_event"
    if app == "youtubetv":
        if rest.startswith("st"):
            return "sunday_ticket"
        if rest == "espnplus" or rest.startswith("ev:"):
            return "espn_event"
        if rest in LOCAL:
            return "yttv_local_search"
        return "yttv_code" if rest in cm.codes else "yttv_no_code"
    return "no_route"


def audit(archive: Path, raw: Path | None, channels: Path, leagues=LEAGUES, only_nights=None) -> dict:
    from redzone.channels import NETWORKS, ChannelMap
    from redzone.models import League
    from redzone.sources.espn import parse_scoreboard

    lg = {"nfl": League.NFL, "cfb": League.CFB, "nba": League.NBA, "cbb": League.CBB, "nhl": League.NHL, "mlb": League.MLB}
    cm = ChannelMap(channels)
    cm.local_search = True
    cm.save = lambda: None
    rows, seen, unmapped, refused = [], set(), collections.Counter(), {}
    for league in leagues:
        try:
            dev = DevOnly(archive, league)
            if only_nights:
                dev.require(only_nights)
        except LockedNightError as e:
            if only_nights:
                raise
            refused[league] = str(e)
            continue
        files = scoreboard_files(archive, raw, league, dev)
        if only_nights:
            files = [(n, p) for n, p in files if n in set(only_nights)]
        for _night, f in files:
            d = json.loads(f.read_text())
            evs = d.get("events", [])
            bad = {e["id"] for e in evs if (e.get("status", {}).get("type", {}).get("name")) in ("STATUS_CANCELED", "STATUS_POSTPONED")}
            allstar = {e["id"] for e in evs if "all-star" in (e.get("name", "") + e.get("shortName", "")).lower()}
            for g in parse_scoreboard(lg[league], d):
                if g.game_id in seen or g.game_id in bad or g.game_id in allstar or not g.start_time:
                    continue
                seen.add(g.game_id)
                c = classify(cm.where(g), cm)
                for b in g.broadcasts:
                    if not NETWORKS.get(b.lower().strip()):
                        unmapped[(league, b)] += 1
                teams = {g.home.abbr, g.away.abbr}
                rows.append(dict(l=league, id=g.game_id, c=c, night=dt.datetime.fromtimestamp(g.start_time, ET).date().isoformat(),
                                 mine=bool(MINE[league] & teams),
                                 blackout=bool(BLACKOUT.get(league) and BLACKOUT[league][0] in teams and BLACKOUT[league][1] == c)))
    return {"rows": rows, "unmapped": unmapped, "refused": refused}


def reach_table(rows: list[dict]) -> dict:
    """Per league and 'all': games, class counts, and the four reach shares (blackouts leave the numerator, not the base)."""
    def share(r):
        n = len(r)
        cnt = collections.Counter(x["c"] for x in r)
        ok = sum(1 for x in r if x["c"] in WORKS and not x["blackout"])
        espn = sum(1 for x in r if x["c"] == "espn_event" and not x["blackout"])
        return dict(games=n, classes={k: cnt[k] for k in CLASSES}, blackouts=sum(1 for x in r if x["blackout"]),
                    fast=sum(cnt[k] for k in FAST), reachable=ok, plus_espn_events=ok + espn,
                    pct=_pct(n, sum(cnt[k] for k in FAST), ok, ok + espn))
    out = {lg: share([x for x in rows if x["l"] == lg]) for lg in LEAGUES}
    out["all"] = share(rows)
    out["without_cbb"] = share([x for x in rows if x["l"] != "cbb"])
    out["mine"] = share([x for x in rows if x["mine"]])
    return out


def _pct(n, fast, ok, plus):
    d = n or 1
    return {"fast": round(100 * fast / d, 1), "reachable_today": round(100 * ok / d, 1), "plus_espn_events": round(100 * plus / d, 1)}


def render(table: dict, refused: dict) -> str:
    out = ["scope            games   fast%  reachable%  +espn-events%  blackouts"]
    for k in (*LEAGUES, "all", "without_cbb", "mine"):
        t = table[k]
        p = t["pct"]
        out.append(f"{k:<14}{t['games']:>8}{p['fast']:>8}{p['reachable_today']:>12}{p['plus_espn_events']:>15}{t['blackouts']:>11}")
    for lg, why in refused.items():
        out.append(f"REFUSED {lg}: {why}")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--archive", required=True, type=Path)
    ap.add_argument("--raw", type=Path, default=None)
    ap.add_argument("--channels", type=Path, default=ROOT / "data" / "channels.json")
    ap.add_argument("--leagues", default=",".join(LEAGUES))
    ap.add_argument("--nights", default="", help="comma list; exit 2 if any is locked or fresh")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    only = [n for n in a.nights.split(",") if n] or None
    try:
        res = audit(a.archive, a.raw, a.channels, tuple(x for x in a.leagues.split(",") if x in LEAGUES), only)
    except LockedNightError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    table = reach_table(res["rows"])
    print(render(table, res["refused"]))
    if a.out:
        a.out.write_text(json.dumps({"table": table, "refused": res["refused"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
