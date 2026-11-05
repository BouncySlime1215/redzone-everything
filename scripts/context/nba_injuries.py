#!/usr/bin/env python3
"""NBA official injury report + referee assignments -> context facts (Data Crew B, worker B4). Runs on GitHub Actions
(.github/workflows/context-nba.yml), never on the Mac and never in the Claude container (no route to nba.com).

    python scripts/context/nba_injuries.py injuries --from 2024-10-22 --to 2024-11-01 --out OUT [--player-box P]
    python scripts/context/nba_injuries.py injuries --live --out OUT          # today's reports (ET)
    python scripts/context/nba_injuries.py refs --out OUT                     # today's assignments (live only)

Injury report PDFs (public, Dec 2018 on):
    https://ak-static.cms.nba.com/referee/injury/Injury-Report_YYYY-MM-DD_HH_MMPM.pdf   (2023-24 on, every 30-60 min)
    https://ak-static.cms.nba.com/referee/injury/Injury-Report_YYYY-MM-DD_HHPM.pdf      (older: a few per day)
Each run first reads robots.txt of every host it touches and ABORTS if the path is disallowed (robots_ok).
Requests are paced (1 per 1.5 s) with a named User-Agent. Only counts, statuses and official ids are written.

Facts (team level, kind "new", source nba_injury_archive, asof = the report's own timestamp, ET -> UTC, and only
reports at least 30 min before tip are used; per game the LATEST such report wins):
    inj_out_n, inj_doubtful_n, inj_questionable_n, inj_probable_n, inj_report_age_min (tip - report, minutes),
    inj_star_out (0-2: how many of the team's top-2 players by season minutes in PRIOR games are Out/Doubtful;
    needs --player-box = hoopR player_box parquet, else not emitted), inj_not_submitted (1 if "NOT YET SUBMITTED").
Referee facts (game level, kind "new", source nba_ref_assignments, asof = the run time, before tip):
    ref_crew_chief_id, ref_2_id, ref_3_id (value_str "ref_<NBA official id>" via the L2M name->id table when
    known, else "refname_<slug>"), ref_crew_chief_no (jersey number).
Parsers are tested offline on a text fixture written from the known layout: NEEDS LIVE VERIFICATION.
"""
from __future__ import annotations

import argparse
import gzip
import io
import re
import sys
import time
import urllib.error
import urllib.request
import urllib.robotparser
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from redzone.context.nba import NBA_ESPN_TO_TRI  # noqa: E402
from redzone.context.tokens import PERSONAL_USE, Fact  # noqa: E402

ET = ZoneInfo("America/New_York")
UA = "redzone-context/1.0 (personal, non-commercial research; github.com/BouncySlime1215/redzone-everything)"
INJ_URL = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_{d}_{t}.pdf"
REFS_URL = "https://official.nba.com/referee-assignments/"
NBA_INJ_LICENSE = f"NBA official injury report, public; {PERSONAL_USE}"
NBA_REF_LICENSE = f"NBA official referee assignments (official.nba.com), public; {PERSONAL_USE}"
NBA_SRC_INJ = "nba_injury_archive"     # terms.json id
NBA_SRC_REFS = "nba_ref_assignments"
NBA_INJ_LEAD = timedelta(minutes=30)
NBA_STATUSES = ("Out", "Doubtful", "Questionable", "Probable", "Available")
SCHEDULE_GZ = ROOT / "redzone" / "context" / "schedules" / "nba.csv.gz"

# full name (as the report prints it) -> ESPN abbr (the play table's)
NBA_FULL_NAMES = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN", "Charlotte Hornets": "CHA",
    "Charlotte Bobcats": "CHA", "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE", "Dallas Mavericks": "DAL",
    "Denver Nuggets": "DEN", "Detroit Pistons": "DET", "Golden State Warriors": "GS", "Houston Rockets": "HOU",
    "Indiana Pacers": "IND", "LA Clippers": "LAC", "Los Angeles Clippers": "LAC", "Los Angeles Lakers": "LAL",
    "Memphis Grizzlies": "MEM", "Miami Heat": "MIA", "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans": "NO", "New York Knicks": "NY", "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI", "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SA", "Toronto Raptors": "TOR", "Utah Jazz": "UTAH", "Washington Wizards": "WSH",
}
NBA_TRI_TO_ESPN = {v: k for k, v in NBA_ESPN_TO_TRI.items()}
_STATUS_RE = re.compile(r"^(?P<player>[A-Z][A-Za-z.'\- ]*?(?: (?:Jr\.|Sr\.|II|III|IV))?, [A-Z][A-Za-z.'\- ]*?) "
                        r"(?P<status>" + "|".join(NBA_STATUSES) + r")\b ?(?P<reason>.*)$")
_HEAD_RE = re.compile(r"^(?:(?P<date>\d\d/\d\d/\d{4}) )?(?:(?P<time>\d\d:\d\d) \(ET\) )?"
                      r"(?:(?P<away>[A-Z]{2,3})@(?P<home>[A-Z]{2,3}) )?(?P<rest>.*)$")
_TEAM_RE = re.compile(r"^(?P<team>" + "|".join(sorted(map(re.escape, NBA_FULL_NAMES), key=len, reverse=True))
                      + r")(?: (?P<rest>.*))?$")


# ------------------------------------------------------------------------------------------------ fetch

class Polite:
    def __init__(self, rps: float = 1 / 1.5):
        self.gap, self.t, self.robots = 1.0 / rps, 0.0, {}

    def robots_ok(self, url: str) -> bool:
        host = "/".join(url.split("/")[:3])
        if host not in self.robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                req = urllib.request.Request(host + "/robots.txt", headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=20) as r:
                    rp.parse(r.read().decode("utf-8", "replace").splitlines())
            except urllib.error.HTTPError as e:   # 404 = no rules (allow); 401/403 = treat as disallow-all
                rp.parse(["User-agent: *", "Disallow: /"] if e.code in (401, 403) else [])
            except Exception:  # noqa: BLE001  (unreachable robots.txt: be conservative)
                rp.parse(["User-agent: *", "Disallow: /"])
            self.robots[host] = rp
        return self.robots[host].can_fetch(UA, url)

    def get(self, url: str) -> bytes | None:
        if not self.robots_ok(url):
            raise SystemExit(f"robots.txt disallows {url}: aborting (docs/datasources/context-nba.md)")
        wait = self.t + self.gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.t = time.monotonic()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return None
            raise


def report_slots(d: date) -> list[datetime]:
    """Candidate report times (ET) for one day, newest first: every 15 min 11:00 AM .. 11:45 PM, plus the older
    on-the-hour names. URL spelling per slot: `slot_names`."""
    out = []
    for h in range(23, 10, -1):
        for m in (45, 30, 15, 0):
            out.append(datetime(d.year, d.month, d.day, h, m, tzinfo=ET))
    return out


def slot_names(t: datetime) -> list[str]:
    h12 = t.strftime("%I")
    ap = t.strftime("%p")
    names = [f"{h12}_{t.strftime('%M')}{ap}"]
    if t.minute == 0:
        names.append(f"{h12}{ap}")
    return names


def pdf_text(blob: bytes) -> str:
    import pdfplumber
    with pdfplumber.open(io.BytesIO(blob)) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


# ------------------------------------------------------------------------------------------------ parse

def parse_report(text: str) -> list[dict]:
    """Report text (pdfplumber lines) -> rows {date, time_et, away, home, team, player, status, reason}. Game
    date/time/matchup/team carry down until the next one appears (the PDF leaves them blank on follow-on rows).
    Team = ESPN abbr. A team line with 'NOT YET SUBMITTED' gives a row with status 'NotSubmitted'."""
    rows, cur = [], {"date": None, "time_et": None, "away": None, "home": None, "team": None}
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line or line.startswith(("Injury Report:", "Page ", "Game Date", "Game Time")):
            continue
        m = _HEAD_RE.match(line)
        if m.group("date"):
            cur["date"] = datetime.strptime(m.group("date"), "%m/%d/%Y").date().isoformat()
        if m.group("time"):
            cur["time_et"] = m.group("time")
        if m.group("home"):
            cur["away"] = NBA_TRI_TO_ESPN.get(m.group("away"), m.group("away"))
            cur["home"] = NBA_TRI_TO_ESPN.get(m.group("home"), m.group("home"))
            cur["team"] = None
        rest = m.group("rest") or ""
        t = _TEAM_RE.match(rest)
        if t:
            cur["team"] = NBA_FULL_NAMES[t.group("team")]
            rest = t.group("rest") or ""
        if not cur["team"] or not cur["home"]:
            continue
        if rest.upper().startswith("NOT YET SUBMITTED"):
            rows.append({**cur, "player": None, "status": "NotSubmitted", "reason": ""})
            continue
        s = _STATUS_RE.match(rest)
        if s:
            rows.append({**cur, "player": s.group("player").strip(), "status": s.group("status"),
                         "reason": s.group("reason").strip()})
    return rows


def norm_player(name: str) -> str:
    """'Jackson Jr., Jaren' / 'Jaren Jackson Jr.' -> 'jaren jackson'."""
    s = str(name or "")
    if "," in s:
        last, first = s.split(",", 1)
        s = f"{first} {last}"
    s = re.sub(r"[^a-z ]", "", s.lower().replace("-", " "))
    return " ".join(w for w in s.split() if w not in ("jr", "sr", "ii", "iii", "iv"))


def top2_minutes(player_box, team: str, before_utc) -> set[str]:
    """The team's top-2 players by total minutes in games that tipped BEFORE `before_utc` (same season)."""
    pb = player_box[(player_box["team"] == team) & (player_box["kick"] < before_utc)]
    if pb.empty:
        return set()
    return set(pb.groupby("player")["minutes"].sum().nlargest(2).index)


def injury_facts(reports: list[tuple[datetime, list[dict]]], games, player_box=None) -> list[Fact]:
    """reports: (report time ET, parsed rows). games: DataFrame game_id, home, away, kickoff_utc (tz-aware).
    Per (game, team) the latest report with time <= tip - 30 min."""
    import pandas as pd
    key = {(r.home, r.away, r.kickoff_utc.tz_convert(ET).date().isoformat()): r for r in games.itertuples()}
    best: dict[tuple[str, str], tuple[datetime, list[dict]]] = {}
    for rt, rows in reports:
        by: dict[tuple, list[dict]] = {}
        for r in rows:
            g = key.get((r["home"], r["away"], r["date"]))
            if g is None or pd.Timestamp(rt) > g.kickoff_utc - NBA_INJ_LEAD:
                continue
            by.setdefault((g.game_id, r["team"]), []).append(r)
        for k, v in by.items():
            if k not in best or best[k][0] < rt:
                best[k] = (rt, v)
    kick = {r.game_id: r.kickoff_utc for r in games.itertuples()}
    facts = []
    for (gid, team), (rt, rows) in sorted(best.items()):
        asof = pd.Timestamp(rt).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")

        def F(fld, val, gid=gid, team=team, asof=asof):
            facts.append(Fact(gid, team, asof, fld, val, "new", NBA_SRC_INJ, NBA_INJ_LICENSE))
        st = [r["status"] for r in rows]
        if "NotSubmitted" in st:
            F("inj_not_submitted", 1)
        for s in ("Out", "Doubtful", "Questionable", "Probable"):
            F(f"inj_{s.lower()}_n", st.count(s))
        F("inj_report_age_min", round((kick[gid] - pd.Timestamp(rt)).total_seconds() / 60))
        if player_box is not None:
            stars = top2_minutes(player_box, team, kick[gid])
            if stars:
                out = {norm_player(r["player"]) for r in rows if r["status"] in ("Out", "Doubtful")}
                F("inj_star_out", len(stars & out))
    return facts


def load_player_box(path):
    """hoopR player_box parquet(s) -> team (ESPN abbr), player (normalized), minutes, kick (UTC)."""
    import pandas as pd
    d = pd.concat([pd.read_parquet(p, columns=["athlete_display_name", "team_abbreviation", "minutes",
                                               "game_date_time"]) for p in path], ignore_index=True)
    return pd.DataFrame({"team": d["team_abbreviation"], "player": d["athlete_display_name"].map(norm_player),
                         "minutes": pd.to_numeric(d["minutes"], errors="coerce").fillna(0),
                         "kick": pd.to_datetime(d["game_date_time"], utc=True)})


# ------------------------------------------------------------------------------------------- referees

class _Table(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell, self.in_cell = [], None, [], False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.in_cell, self.cell = True, []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.row is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.in_cell = False
        elif tag == "tr" and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.in_cell:
            self.cell.append(data)


NBA_CITY = {"Atlanta": "ATL", "Boston": "BOS", "Brooklyn": "BKN", "Charlotte": "CHA", "Chicago": "CHI",
            "Cleveland": "CLE", "Dallas": "DAL", "Denver": "DEN", "Detroit": "DET", "Golden State": "GS",
            "Houston": "HOU", "Indiana": "IND", "LA Clippers": "LAC", "L.A. Clippers": "LAC", "LA Lakers": "LAL",
            "L.A. Lakers": "LAL", "Memphis": "MEM", "Miami": "MIA", "Milwaukee": "MIL", "Minnesota": "MIN",
            "New Orleans": "NO", "New York": "NY", "Oklahoma City": "OKC", "Orlando": "ORL", "Philadelphia": "PHI",
            "Phoenix": "PHX", "Portland": "POR", "Sacramento": "SAC", "San Antonio": "SA", "Toronto": "TOR",
            "Utah": "UTAH", "Washington": "WSH"}


def parse_refs(html: str) -> list[dict]:
    """official.nba.com/referee-assignments table rows: Game ('Boston @ New York') | Crew Chief | Referee |
    Umpire | Alternate, names like 'Scott Foster (#48)' -> {away, home, officials: [(name, number), ...]}."""
    p = _Table()
    p.feed(html)
    out = []
    for row in p.rows:
        if len(row) < 4 or "@" not in row[0]:
            continue
        a, h = (x.strip() for x in row[0].split("@", 1))
        if a not in NBA_CITY or h not in NBA_CITY:
            continue
        offs = []
        for c in row[1:4]:
            m = re.match(r"^(.*?)\s*\(#?(\d+)\)\s*$", c)
            offs.append((m.group(1).strip(), int(m.group(2))) if m else (c.strip(), None))
        out.append({"away": NBA_CITY[a], "home": NBA_CITY[h], "officials": offs})
    return out


def ref_slug(name: str, name_to_id: dict[str, str]) -> str:
    k = norm_player(name)
    return f"ref_{name_to_id[k]}" if k in name_to_id else "refname_" + k.replace(" ", "_")


def ref_facts(assign: list[dict], games, asof_utc: str, name_to_id: dict[str, str]) -> list[Fact]:
    import pandas as pd
    key = {(r.home, r.away): r for r in games.itertuples()}
    facts = []
    for a in assign:
        g = key.get((a["home"], a["away"]))
        if g is None or pd.Timestamp(asof_utc) >= g.kickoff_utc:
            continue
        for k, (name, no) in enumerate(a["officials"]):
            fld = "ref_crew_chief_id" if k == 0 else f"ref_{k + 1}_id"
            facts.append(Fact(g.game_id, None, asof_utc, fld, ref_slug(name, name_to_id), "new", NBA_SRC_REFS,
                              NBA_REF_LICENSE))
            if k == 0 and no is not None:
                facts.append(Fact(g.game_id, None, asof_utc, "ref_crew_chief_no", no, "new", NBA_SRC_REFS,
                                  NBA_REF_LICENSE))
    return facts


def l2m_name_ids(l2m_csv) -> dict[str, str]:
    import pandas as pd
    d = pd.read_csv(l2m_csv, usecols=lambda c: c.startswith("OFFICIAL_"), dtype=str)
    out = {}
    for k in (1, 2, 3, 4):
        if f"OFFICIAL_{k}" in d and f"OFFICIAL_ID_{k}" in d:
            for n, i in zip(d[f"OFFICIAL_{k}"], d[f"OFFICIAL_ID_{k}"]):
                if isinstance(n, str) and isinstance(i, str) and i not in ("NA", ""):
                    out[norm_player(n)] = i.split(".")[0]
    return out


# ------------------------------------------------------------------------------------------------- main

def load_games():
    import pandas as pd
    with gzip.open(SCHEDULE_GZ, "rt") as f:
        g = pd.read_csv(f, dtype={"game_id": str, "nba_game_id": str})
    g["kickoff_utc"] = pd.to_datetime(g["start_utc"], utc=True)
    return g


def write(facts, games, out: Path, name: str) -> None:
    from redzone.context.tokens import to_frame
    if not facts:
        print(f"{name}: no facts")
        return
    df = to_frame(facts)
    s_of = games.set_index("game_id")["season"]
    out.mkdir(parents=True, exist_ok=True)
    for s, part in df.groupby(df["game_id"].map(s_of)):
        p = out / f"{name}_{int(s)}.parquet"
        if p.exists():
            import pandas as pd
            part = pd.concat([pd.read_parquet(p), part]).drop_duplicates(["game_id", "team", "asof_utc", "field"],
                                                                         keep="last")
        part.to_parquet(p, index=False)
        print(f"{p}: {len(part)} facts")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["injuries", "refs"])
    ap.add_argument("--from", dest="t0")
    ap.add_argument("--to", dest="t1")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--out", required=True)
    ap.add_argument("--player-box", nargs="*")
    ap.add_argument("--l2m")
    ap.add_argument("--max-minutes", type=float, default=100)
    a = ap.parse_args(argv)
    games = load_games()
    http = Polite()
    out = Path(a.out)
    now = datetime.now(ET)
    if a.what == "refs":
        html = http.get(REFS_URL)
        names = l2m_name_ids(a.l2m) if a.l2m else {}
        today = games[games["kickoff_utc"].dt.tz_convert(ET).dt.date == now.date()]
        asof = now.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
        write(ref_facts(parse_refs((html or b"").decode("utf-8", "replace")), today, asof, names), games, out,
              "nba_refs")
        return
    d0 = now.date() if a.live else date.fromisoformat(a.t0)
    d1 = now.date() + timedelta(days=1) if a.live else date.fromisoformat(a.t1)
    pb = load_player_box(a.player_box) if a.player_box else None
    t_end = time.monotonic() + a.max_minutes * 60
    reports = []
    d = d0
    while d < d1 and time.monotonic() < t_end:
        day = games[games["kickoff_utc"].dt.tz_convert(ET).dt.date == d]
        if len(day):
            # newest-first; a slot is worth a request only while some game still lacks a report before its tip
            need = sorted(day["kickoff_utc"] - NBA_INJ_LEAD)
            found = []
            for slot in report_slots(d):
                if slot > now or not any(slot <= t and not any(f <= t for f in found) for t in need):
                    continue
                for nm in slot_names(slot):
                    blob = http.get(INJ_URL.format(d=d.isoformat(), t=nm))
                    if blob:
                        reports.append((slot, parse_report(pdf_text(blob))))
                        found.append(slot)
                        break
                if a.live and found:
                    break
        d += timedelta(days=1)
    write(injury_facts(reports, games, pb), games, out, "nba_injuries")


if __name__ == "__main__":
    main()
