# %% [markdown]
# # RedZone feed-lag probe: which live feed shows each score first (EXP-802)
#
# Serves EXP-802 ("the fastest feed wins", sync #1). Owner: Data Planner A; numbers go to lane 2.
# Runs on free Kaggle CPU with Internet On during a slate. It never runs on the Mac.
#
# **What it measures.** Every POLL_S seconds it reads each live scoreboard feed once. That is one request per feed for
# all games, which keeps it polite. It records the first wall time each feed shows each new score (game, home, away).
# After a game ends, it fetches the game's official action log (NBA.com cdn timeActual, the scorer's table clock) and
# ESPN's play wallclocks. Lag = first seen − the scoring action's official wall time, per feed and per league.
#
# **Feeds.**
# - NBA: ESPN site scoreboard and NBA.com cdn todaysScoreboard.
# - NFL: ESPN site scoreboard. NFL has no official public log, so its truth is ESPN's play wallclock (lag vs ESPN's own
#   clock; a second NFL feed can be added once the scout clears one).
#
# **Guardrails.**
# - Public endpoints only. No keys, no logins.
# - On the first 403 or 429 from a host, that host is dropped for the rest of the run, with no retry and no workaround
#   (scout rule, #276).
# - It stores only timestamps and scores, never play text.
#
# **Output.** /kaggle/working/feed_lag/<date>/lag.json (summary) and events.jsonl (first-seen rows).
#
# **Settings.** Accelerator None, Internet On. Start it before tip-off and let it run. It stops when every game is
# final or RZ_HOURS has passed.

# %%
import json
import os
import statistics
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

POLL_S = float(os.environ.get("RZ_POLL_S", "3"))
HOURS = float(os.environ.get("RZ_HOURS", "7"))
LEAGUES = os.environ.get("RZ_LEAGUES", "nba,nfl").split(",")
OUT = Path(os.environ.get("RZ_KAGGLE_WORKING", "/kaggle/working")) / "feed_lag" / datetime.now(timezone.utc).strftime("%Y%m%d")
OUT.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "redzone-personal-research/1.0"}
S = requests.Session()
S.headers.update(UA)
BLOCKED: set[str] = set()
ESPN_SB = {"nba": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
           "nfl": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"}
ESPN_SUM = {"nba": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={eid}",
            "nfl": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={eid}"}
CDN_SB = "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"
CDN_PBP = "https://cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{gid}.json"


def get(url):
    host = url.split("/")[2]
    if host in BLOCKED:
        return None
    try:
        r = S.get(url, timeout=10)
    except requests.RequestException:
        return None
    if r.status_code in (403, 429):
        BLOCKED.add(host)
        print(f"STOP {host}: HTTP {r.status_code}; dropped for this run (no retry)")
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def iso(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# %%
def espn_scores(league):
    js = get(ESPN_SB[league]) or {}
    out = {}
    for ev in js.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        teams = {c.get("homeAway"): c for c in comp.get("competitors", [])}
        if "home" not in teams:
            continue
        key = f"{league}:{teams['away']['team']['abbreviation']}@{teams['home']['team']['abbreviation']}"
        st = (ev.get("status") or {}).get("type", {}).get("state", "")
        out[key] = (int(teams["home"].get("score") or 0), int(teams["away"].get("score") or 0), st, ev.get("id"))
    return out


TRI = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS", "PHX": "PHX"}


def cdn_scores():
    js = get(CDN_SB) or {}
    out = {}
    for g in (js.get("scoreboard") or {}).get("games", []):
        h, a = g["homeTeam"], g["awayTeam"]
        st = {1: "pre", 2: "in", 3: "post"}.get(g.get("gameStatus"), "")
        out[f"nba:{a['teamTricode']}@{h['teamTricode']}"] = (int(h.get("score") or 0), int(a.get("score") or 0), st,
                                                           g.get("gameId"))
    return out


def norm(key):
    lg, m = key.split(":")
    a, h = m.split("@")
    return f"{lg}:{TRI.get(a, a)}@{TRI.get(h, h)}"


# %%
first = defaultdict(dict)          # (game, home, away) -> {feed: first seen}
ids = defaultdict(dict)            # game -> {feed: id}
state = {}
T0 = time.time()
log = (OUT / "events.jsonl").open("a")
while time.time() - T0 < HOURS * 3600:
    t = time.time()
    feeds = {}
    for lg in LEAGUES:
        feeds[f"espn_scoreboard:{lg}"] = espn_scores(lg)
    if "nba" in LEAGUES:
        feeds["nba_cdn_scoreboard:nba"] = cdn_scores()
    live = 0
    for feed, games in feeds.items():
        for key, (h, a, st, gid) in games.items():
            g = norm(key)
            ids[g][feed.split(":")[0]] = gid
            state[g] = st if feed.startswith("espn") else state.get(g, st)
            live += st == "in"
            k = (g, h, a)
            if (h or a) and feed not in first[k]:
                first[k][feed] = t
                log.write(json.dumps({"game": g, "home": h, "away": a, "feed": feed, "seen": t}) + "\n")
    log.flush()
    if state and all(s == "post" for s in state.values()) and time.time() - T0 > 600:
        break
    time.sleep(max(0.0, POLL_S - (time.time() - t)))
log.close()

# %%
# truth: the official wall time of each scoring action (NBA cdn timeActual; ESPN wallclock as the fallback / NFL)
truth = {}
for g, fid in ids.items():
    lg = g.split(":")[0]
    if lg == "nba" and fid.get("nba_cdn_scoreboard"):
        js = get(CDN_PBP.format(gid=fid["nba_cdn_scoreboard"])) or {}
        for a in (js.get("game") or {}).get("actions", []):
            h, w = int(a.get("scoreHome") or 0), int(a.get("scoreAway") or 0)
            ts = iso(a.get("timeActual"))
            if ts and (g, h, w) not in truth:
                truth[(g, h, w)] = ("nba_cdn_timeActual", ts)
    if fid.get("espn_scoreboard"):
        js = get(ESPN_SUM[lg].format(eid=fid["espn_scoreboard"])) or {}
        for p in js.get("plays", []):
            h, w = int(p.get("homeScore") or 0), int(p.get("awayScore") or 0)
            ts = iso(p.get("wallclock"))
            if ts and (g, h, w) not in truth:
                truth[(g, h, w)] = ("espn_wallclock", ts)

lags = defaultdict(list)
for k, seen in first.items():
    if k not in truth:
        continue
    src, ts = truth[k]
    for feed, t in seen.items():
        if -30 < t - ts < 600:
            lags[(feed, src)].append(t - ts)


def q(v, p):
    v = sorted(v)
    return round(v[min(len(v) - 1, int(p * len(v)))], 1)


summary = {"poll_s": POLL_S, "blocked_hosts": sorted(BLOCKED), "games": len(ids),
           "note": f"lag includes up to {POLL_S:.0f} s of polling granularity (subtract ~{POLL_S / 2:.1f} s for the mean)",
           "rows": [{"feed": f, "truth": s, "n": len(v), "p10": q(v, .1), "p50": q(v, .5), "p90": q(v, .9),
                     "mean": round(statistics.fmean(v), 1)} for (f, s), v in sorted(lags.items()) if v]}
(OUT / "lag.json").write_text(json.dumps(summary, indent=1))
print(json.dumps(summary, indent=1))
