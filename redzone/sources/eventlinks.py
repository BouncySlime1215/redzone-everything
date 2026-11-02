"""Per-game streaming deep links (Prime Video GTIs, ESPN+/ESPN watch ids) with no login.

Two anonymous, undocumented web feeds are combined (same idea as the open-source
FruitDeepLinks project):

1. Apple TV "UTS" sports API (what tv.apple.com uses), with its public anonymous key:
       GET uts-api.itunes.apple.com/uts/v3/shelves/uts.col.search.SE?searchTerm=nba
           -> up to 28 upcoming events per term (teams, kickoff)
       GET uts-api.itunes.apple.com/uts/v3/sporting-events/<umc.cse.id>
           -> every "playable" for the event: Prime Video broadcast GTIs (NBA League
              Pass feeds and Prime's own games), Peacock, DAZN, NBA app, NFL+ ...
   Apple's punchout `aiv://aiv/detail?gti=<content>&broadcast=<GTI>` becomes
   https://app.primevideo.com/detail?gti=<broadcast GTI>.  `geo_ok` is Apple's
   isGeoRestricted flag for the caller's IP.  It is only a weak hint for choosing among
   the ~7 League Pass feeds of a game, and can disagree with Amazon's own page (see
   prime_page_info), so verify the one you are about to tune.
2. ESPN Watch Graph (what espn.com/watch uses), public web apiKey, one POST per day:
   every ESPN+ / ESPN-network airing with its watch id, feed (home/away/national) and
   ESPN event id.  URL: https://www.espn.com/watch/player/_/id/<id>, also openable as
   sportscenter://x-callback-url/showWatchStream?playID=<id>.

Caveats: unofficial endpoints, may change or be rate-limited at any time, and using them
may violate the providers' terms; keep volume low (results are cached for 5 minutes).
Apple search caps at 28 events per term (about two days of NBA), so far-future dates are
only covered via `extra_terms` (e.g. team names).  YouTube TV watch ids are NOT available
from either feed.  Every function swallows errors and returns [] / {} instead of raising.

    .venv/bin/python -m redzone.sources.eventlinks [YYYY-MM-DD]
"""
from __future__ import annotations

import asyncio
import copy
import datetime as dt
import logging
import re
import shutil
import sys
import time
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
CACHE_TTL_S = 300.0
DEFAULT_TERMS = ("nba", "nhl", "nfl", "mlb")
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

APPLE_BASE = "https://uts-api.itunes.apple.com/uts/v3"
APPLE_PARAMS = {  # public, anonymous web/iPad key; no account involved
    "caller": "web", "sf": "143441", "v": "90", "pfm": "web", "locale": "en-US",
    "utsk": "6e3013c6d6fae3c2::::::235656c069bb0efb",
}
ESPN_GRAPH = "https://watch.graph.api.espn.com/api"
ESPN_GRAPH_PARAMS = {"apiKey": "0dbf88e8-cc6d-41da-aa83-18b5c630bc5c", "features": "pbov7"}
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
_ESPN_QUERY = (
    "query Airings($countryCode: String!, $deviceType: DeviceType!, $tz: String!, "
    "$day: String!, $limit: Int) { airings(countryCode: $countryCode, deviceType: "
    "$deviceType, tz: $tz, day: $day, limit: $limit) { id name startDateTime feedName "
    "feedType language isReAir network { id name } league { name } "
    "competition { id } program { id } } }"
)

_LEAGUES = {"nba": "nba", "nba_cup": "nba", "nhl": "nhl", "nfl": "nfl", "mlb": "mlb"}
_ESPN_LEAGUES = {"NBA": "nba", "NHL": "nhl", "NFL": "nfl", "MLB": "mlb"}
_FEEDS = {"NATIONAL_FEED": "national", "HOME": "home", "AWAY": "away", "ALTERNATE": "alt"}
_FEED_RANK = {"national": 0, "home": 1, "away": 2, "alt": 3, "mobile": 6}
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

_cache: dict[str, tuple[float, list[dict]]] = {}


# --- tiny helpers --------------------------------------------------------------

def _get(o, *path, default=None):
    for p in path:
        o = o.get(p) if isinstance(o, dict) else None
        if o is None:
            return default
    return o


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


_SYN: dict[str, set[str]] = {}
for _a, _b in [("sj", "sjs"), ("tb", "tbl"), ("nj", "njd"), ("la", "lak"), ("utah", "uta"),
               ("wsh", "was"), ("gs", "gsw"), ("ny", "nyk"), ("no", "nop"), ("sa", "sas"),
               ("phx", "pho"), ("bkn", "brk"), ("cha", "cho"), ("jax", "jac"), ("kc", "kan"),
               ("gb", "gnb"), ("ne", "nwe"), ("sf", "sfo"), ("lv", "lvr"), ("ari", "az"),
               ("cgy", "cal"), ("mtl", "mon")]:
    _SYN.setdefault(_a, set()).add(_b)
    _SYN.setdefault(_b, set()).add(_a)


def _norm_set(names) -> set[str]:
    out: set[str] = set()
    for n in names or ():
        k = _norm(n)
        if k:
            out.add(k)
            out |= _SYN.get(k, set())
    return out


def _parse_day(date: str) -> dt.date | None:
    try:
        s = str(date).strip()
        return dt.date.fromisoformat(s) if "-" in s else dt.datetime.strptime(s, "%Y%m%d").date()
    except (ValueError, TypeError):
        return None


def _window(day: dt.date) -> tuple[float, float]:
    """Sports day in US/Eastern: 4h before midnight (live carry-over) to 05:00 next day."""
    start = dt.datetime.combine(day, dt.time(0), ET) - dt.timedelta(hours=4)
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(5), ET)
    return start.timestamp(), end.timestamp()


def _iso_epoch(s) -> float | None:
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _provider_for(service: str, punch: str) -> str:
    s = (service or "").lower()
    if "amazon" in s or "prime video" in s:
        return "prime"
    if s.startswith("espn+"):
        return "espnplus"
    if s.startswith("espn"):
        return "espn"
    if "peacock" in s:
        return "peacock"
    if "dazn" in s:
        return "dazn"
    if s == "nba":
        return "nba"
    if punch.startswith("nflctv:"):
        return "nflplus"
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_") or "other"


# --- Apple TV UTS ----------------------------------------------------------------

async def _apple(client: httpx.AsyncClient, path: str, **params) -> dict | None:
    try:
        r = await client.get(f"{APPLE_BASE}/{path}", params={**APPLE_PARAMS, **params},
                             headers={"User-Agent": _UA, "Accept": "application/json"},
                             timeout=_TIMEOUT)
        if r.status_code != 200:
            log.debug("apple %s -> HTTP %s", path, r.status_code)
            return None
        data = r.json()
        return data.get("data") if isinstance(data, dict) else None
    except Exception as e:  # network, JSON, anything: callers treat None as "no data"
        log.debug("apple %s failed: %s", path, e)
        return None


def _team_aliases(c: dict) -> list[str]:
    out: list[str] = []
    for k in ("name", "nickname", "shortName", "abbreviation"):
        v = c.get(k)
        if isinstance(v, str) and v and v not in out:
            out.append(v)
    return out


def _apple_meta(ev: dict) -> dict | None:
    league = _LEAGUES.get(str(ev.get("leagueAbbreviation") or "").lower())
    comps = [c for c in (ev.get("competitors") or []) if isinstance(c, dict)]
    if not league or len(comps) != 2:
        return None
    home = next((c for c in comps if c.get("isHome") is True), comps[1])
    away = next((c for c in comps if c is not home), comps[0])
    ms = _get(ev, "eventTime", "gameKickOffStartTime")
    return {
        "league": league, "event_id": ev.get("id"),
        "away": away.get("name") or away.get("shortName") or "",
        "home": home.get("name") or home.get("shortName") or "",
        "away_names": _team_aliases(away), "home_names": _team_aliases(home),
        "start": int(ms / 1000) if isinstance(ms, (int, float)) else None,
    }


def _apple_item(meta: dict, p: dict) -> dict | None:
    punch = _get(p, "punchoutUrls", "play") or _get(p, "punchoutUrls", "open") or ""
    q = {k: v[0] for k, v in parse_qs(urlsplit(punch).query).items()} if punch else {}
    ext = str(p.get("externalId") or "")
    provider = _provider_for(p.get("serviceName") or "", punch)
    gti = url = play_id = None
    if provider == "prime":
        gti = q.get("broadcast") or (ext if ext.startswith("amzn1.dv.gti.") else None) \
            or q.get("gti")
        url = f"https://app.primevideo.com/detail?gti={gti}" if gti else None
    elif provider in ("espn", "espnplus"):
        play_id = q.get("playID") or (ext if _UUID.fullmatch(ext) else None)
        url = f"https://www.espn.com/watch/player/_/id/{play_id}" if play_id else None
    elif punch.startswith("open.dazn.com://"):
        url = "https://open.dazn.com/" + punch[len("open.dazn.com://"):]
    elif punch:
        url = punch  # https deep link (Peacock) or app scheme (gametime://, nflctv://)
    if not (gti or url):
        return None
    geo = p.get("isGeoRestricted")
    return {
        **meta, "provider": provider, "gti": gti, "url": url,
        "title": p.get("title") or "", "source": "apple",
        "geo_ok": (not geo) if isinstance(geo, bool) else None,
        "feed": None, "feed_name": None, "network": p.get("serviceName"),
        "lang": None, "play_id": play_id, "program_id": ext or None,
        "espn_event_id": None, "raw": punch or None,
    }


async def _apple_links(client, lo: float, hi: float, terms) -> tuple[list[dict], dict]:
    """Return (items, alias_index).  alias_index maps normalized team name -> aliases."""
    shelves = await asyncio.gather(*[
        _apple(client, "shelves/uts.col.search.SE", searchTerm=t) for t in terms])
    events: dict[str, dict] = {}
    for data in shelves:
        for ev in _get(data, "shelf", "items", default=[]) or []:
            meta = _apple_meta(ev) if isinstance(ev, dict) else None
            if meta and meta["start"] is not None and lo <= meta["start"] < hi:
                events.setdefault(meta["event_id"], {"meta": meta, "shelf": ev})
    sem = asyncio.Semaphore(6)

    async def detail(eid: str):
        async with sem:
            return await _apple(client, f"sporting-events/{eid}")

    details = await asyncio.gather(*[detail(e) for e in events])
    items: list[dict] = []
    aliases: dict[str, set[str]] = {}
    for (eid, rec), d in zip(events.items(), details):
        meta = rec["meta"]
        for side in ("away", "home"):
            aliases.setdefault(_norm(meta[side]), set()).update(meta[f"{side}_names"])
        pls = list((_get(d, "playables", default={}) or {}).values())
        if not pls:  # detail failed: fall back to the (partial) playables on the shelf row
            pls = [p for p in (rec["shelf"].get("playables") or []) if isinstance(p, dict)]
        for p in pls:
            it = _apple_item(meta, p) if isinstance(p, dict) else None
            if it:
                items.append(it)
    return items, aliases


# --- ESPN Watch Graph ------------------------------------------------------------

_VS = re.compile(r"\s+(?:vs\.?|at|@)\s+", re.I)


def _espn_teams(name: str) -> tuple[str, str] | None:
    parts = _VS.split(re.sub(r"\s*\([^)]*\)\s*$", "", name or ""))
    if len(parts) != 2:
        return None
    away, home = (p.rsplit("-", 1)[-1].strip() for p in parts)  # drop "En Español-" etc.
    return (away, home) if away and home else None


async def _espn_links(client, day: dt.date, aliases: dict[str, set[str]]) -> list[dict]:
    body = {"operationName": "Airings", "query": _ESPN_QUERY, "variables": {
        "countryCode": "US", "deviceType": "DESKTOP", "tz": "America/New_York",
        "day": day.isoformat(), "limit": 2000}}
    try:
        r = await client.post(
            ESPN_GRAPH, params=ESPN_GRAPH_PARAMS, json=body, timeout=_TIMEOUT,
            headers={"User-Agent": _UA, "Origin": "https://www.espn.com",
                     "Referer": "https://www.espn.com/", "Accept": "application/json"})
        airings = _get(r.json(), "data", "airings") if r.status_code == 200 else None
    except Exception as e:
        log.debug("espn graph failed: %s", e)
        return []
    out: list[dict] = []
    for a in airings if isinstance(airings, list) else []:
        try:
            league = _ESPN_LEAGUES.get(str(_get(a, "league", "name") or ""))
            teams = _espn_teams(a.get("name"))
            if not league or not teams or a.get("isReAir") or not a.get("id"):
                continue
            away, home = teams
            feed = _FEEDS.get(str(a.get("feedType") or ""))
            start = _iso_epoch(a.get("startDateTime"))
            net = a.get("network") or {}
            plus = net.get("id") == "bam_dtc"
            pid = a["id"]
            out.append({
                "league": league, "away": away, "home": home,
                "away_names": sorted({away} | aliases.get(_norm(away), set())),
                "home_names": sorted({home} | aliases.get(_norm(home), set())),
                "start": int(start) if start is not None else None,
                "provider": "espnplus" if plus else "espn", "gti": None,
                "url": f"https://www.espn.com/watch/player/_/id/{pid}",
                "title": a.get("name") or "", "source": "espn", "event_id": None,
                "geo_ok": None, "feed": feed, "feed_name": a.get("feedName"),
                "network": net.get("name"), "lang": a.get("language"), "play_id": pid,
                "program_id": _get(a, "program", "id"),
                "espn_event_id": _get(a, "competition", "id"),
                "raw": f"sportscenter://x-callback-url/showWatchStream?playID={pid}",
            })
        except Exception as e:  # one malformed airing must not lose the rest
            log.debug("skip airing: %s", e)
    return out


# --- public API ------------------------------------------------------------------

async def fetch_event_links(client: httpx.AsyncClient, date: str, *,
                            extra_terms: tuple[str, ...] = ()) -> list[dict]:
    """All known deep-link targets for NBA/NHL/NFL/MLB games on `date` (US/Eastern day).

    `date` is "YYYY-MM-DD" (or "YYYYMMDD").  Each item has at least: league, away, home,
    start (epoch s), provider ("prime" | "espnplus" | "espn" | "peacock" | "dazn" | "nba" |
    "nflplus" ...), gti (Prime broadcast GTI), url, title.  Extras: away_names/home_names
    (aliases incl. abbreviations), geo_ok (True = playable from this IP, per Apple),
    feed ("national"|"home"|"away"|"alt"), feed_name, espn_event_id, play_id, raw, source.
    Never raises; returns [] when nothing could be fetched.
    """
    try:
        day = _parse_day(date)
        if day is None:
            return []
        terms = tuple(dict.fromkeys((*DEFAULT_TERMS, *extra_terms)))
        key = f"{day.isoformat()}|{','.join(terms)}"
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL_S:
            return copy.deepcopy(hit[1])
        lo, hi = _window(day)
        a_items: list[dict] = []
        aliases: dict[str, set[str]] = {}
        try:
            a_items, aliases = await _apple_links(client, lo, hi, terms)
        except Exception as e:  # Apple down: ESPN rows alone are still useful
            log.warning("apple links failed: %s", e)
        e_items = await _espn_links(client, day, aliases)
        seen_ids = {i["play_id"] for i in e_items} | {i["program_id"] for i in e_items}
        links = list(e_items)
        for it in a_items:  # Apple's own ESPN rows only when ESPN's graph lacks them
            if it["provider"] in ("espn", "espnplus") and (
                    it["play_id"] in seen_ids or it["program_id"] in seen_ids):
                continue
            links.append(it)
        # carry Apple's per-IP geo flag onto the matching ESPN rows (same playID)
        geo = {i["play_id"]: i["geo_ok"] for i in a_items if i.get("play_id")}
        for it in e_items:
            it["geo_ok"] = geo.get(it["play_id"])
        # Apple dedupe: identical (provider, gti/url) rows
        uniq: dict[tuple, dict] = {}
        for it in links:
            uniq.setdefault((it["provider"], it["gti"] or it["url"], it["event_id"]), it)
        links = sorted(uniq.values(), key=lambda i: (i["start"] or 0, i["league"], i["home"]))
        if links:
            _cache[key] = (time.time(), copy.deepcopy(links))
        return links
    except Exception as e:
        log.warning("fetch_event_links failed: %s", e)
        return []


def _side_hit(it: dict, side: str, names: set[str]) -> bool:
    cand = _norm_set(it.get(f"{side}_names") or [it.get(side)])
    for a in cand:
        for n in names:
            if a == n:
                return True
            short, long_ = sorted((a, n), key=len)
            if len(short) >= 3 and long_.endswith(" " + short):
                return True
    return False


def _rank(it: dict) -> tuple:
    geo = it.get("geo_ok")
    return (0 if geo is True else 1 if geo is None else 2,
            _FEED_RANK.get(it.get("feed"), 4),
            0 if it.get("lang") in (None, "en") else 1, it.get("start") or 0)


def match(links, league, away_names: set[str], home_names: set[str], *,
          espn_event_id: str | int | None = None) -> dict[str, dict]:
    """provider -> best item for one game (case-insensitive; abbr, nickname or full name).

    Best = playable from this IP first (geo_ok), then national > home > away feed, then
    English.  The chosen dict is a copy with `alts` = the other candidates for that
    provider (e.g. the other League Pass / ESPN+ feeds).  Never raises.
    """
    try:
        lg = str(getattr(league, "value", league) or "").lower()
        an, hn = _norm_set(away_names), _norm_set(home_names)
        by_provider: dict[str, list[dict]] = {}
        for it in links or []:
            if not isinstance(it, dict) or it.get("league") != lg:
                continue
            by_id = espn_event_id is not None and it.get("espn_event_id") == str(espn_event_id)
            if by_id or (_side_hit(it, "away", an) and _side_hit(it, "home", hn)):
                by_provider.setdefault(it.get("provider") or "other", []).append(it)
        out: dict[str, dict] = {}
        for prov, items in by_provider.items():
            items = sorted(items, key=_rank)
            best = dict(items[0])
            best["alts"] = [dict(i) for i in items[1:]]
            out[prov] = best
        return out
    except Exception as e:
        log.warning("match failed: %s", e)
        return {}


# --- optional: inspect a Prime detail page (needs a browser-like TLS stack) -------

_CARD = re.compile(
    r'data-card-title="([^"]+)"\s+data-card-entity-type="LIVE_EVENT_ITEM"', re.I)
_GTI = re.compile(r"amzn1\.dv\.gti\.[0-9a-f-]{36}")
_BENEFIT = re.compile(r"benefitId=([A-Za-z0-9_.\-]+)")
_BENEFITS = {"amzn1.dv.channel.7a36cb2b-40e6-40c7-809f-a6cf9b9f0859": "nba_league_pass",
             "prime_included": "prime"}


def _text_of(html: str, attr: str) -> str:
    m = re.search(rf'data-(?:testid|automation-id)="{attr}"[^>]*>(.*?)</', html, re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip() if m else ""


def _parse_prime_page(html: str) -> dict:
    labels: dict[str, str] = {}
    for m in _CARD.finditer(html):
        g = _GTI.search(html, m.end(), m.end() + 6000)
        if g:
            labels.setdefault(g.group(0), m.group(1).replace("&#x27;", "'"))
    ben = _BENEFIT.search(html)
    msg = _text_of(html, "buy-box-msg")
    return {"labels": labels, "message": msg, "benefit": _BENEFITS.get(
                ben.group(1), ben.group(1)) if ben else None,
            "entitlement": _text_of(html, "entitlement-message"),
            "unavailable": "unavailable" in msg.lower()}


async def _curl_html(url: str) -> str:
    exe = shutil.which("curl")
    if not exe:
        return ""
    proc = await asyncio.create_subprocess_exec(
        exe, "-sL", "-m", "25", "-A", "Mozilla/5.0", "-H", "Accept-Language: en-US", url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await asyncio.wait_for(proc.communicate(), 30)
    return out.decode("utf-8", "replace")


async def prime_page_info(client: httpx.AsyncClient, gti: str) -> dict:
    """What Amazon's public detail page says about one GTI: {"labels": {gti: 'Wizards
    Broadcast', ...} (the game's feed picker), "benefit" ('nba_league_pass' | 'prime' |
    raw id), "entitlement" ('Free trial of NBA League Pass'), "message", "unavailable"
    (True when the buy box says 'unavailable ... regional restrictions' for this IP)}.

    Amazon serves a bot wall to httpx's TLS fingerprint, so this falls back to the system
    `curl`.  It scrapes a consumer page: call it only for the game you are about to tune.
    Returns {} on any failure.
    """
    try:
        url = f"https://www.amazon.com/gp/video/detail/{gti}"
        html = ""
        try:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0",
                                               "Accept-Language": "en-US"},
                                 timeout=_TIMEOUT, follow_redirects=True)
            html = r.text if r.status_code == 200 else ""
        except Exception:
            pass
        if len(html) < 50_000:  # the bot-wall stub is ~4 KB; a real page is ~1 MB
            html = await _curl_html(url)
        return _parse_prime_page(html) if len(html) >= 50_000 else {}
    except Exception as e:
        log.debug("prime_page_info failed: %s", e)
        return {}


async def prime_feed_labels(client: httpx.AsyncClient, gti: str) -> dict[str, str]:
    """{gti: 'Wizards Broadcast' | 'Magic Broadcast' | 'Mobile Broadcast'} for the game."""
    return (await prime_page_info(client, gti)).get("labels") or {}


def _feed_kind(label: str | None, it: dict) -> str:
    if not label:
        return "national"
    name = _norm(re.sub(r"(?i)\s*broadcast\s*$", "", label))
    if "mobile" in name:
        return "mobile"
    if name in _norm_set(it.get("home_names")):
        return "home"
    return "away" if name in _norm_set(it.get("away_names")) else "alt"


async def label_prime(client: httpx.AsyncClient, item: dict) -> dict:
    """Name the feeds of a `match(...)["prime"]` item: sets feed_name ("Wizards Broadcast",
    "Mobile Broadcast", None = generic League Pass feed) and feed (national/home/away/
    mobile) on it and its `alts`, then re-picks the best (geo_ok, national, home, away,
    mobile).  Returns the item unchanged if labels can't be fetched.  Never raises."""
    try:
        items = [{k: v for k, v in item.items() if k != "alts"}]
        items += [dict(a) for a in item.get("alts") or []]
        labels = await prime_feed_labels(client, items[0]["gti"])
        if not labels:
            return item
        for it in items:
            it["feed_name"] = labels.get(it["gti"])
            it["feed"] = _feed_kind(it["feed_name"], it)
        items.sort(key=_rank)
        return {**items[0], "alts": items[1:]}
    except Exception as e:
        log.debug("label_prime failed: %s", e)
        return item


# --- CLI ------------------------------------------------------------------------

def _fmt(it: dict) -> str:
    when = (dt.datetime.fromtimestamp(it["start"], ET).strftime("%H:%M ET")
            if it.get("start") else "  ?  ")
    geo = {True: "ok", False: "geo-blocked", None: "?"}[it.get("geo_ok")]
    feed = it.get("feed_name") or it.get("feed") or ""
    return (f"{it['league']:3} {when} {it['away']} @ {it['home']}  [{it['provider']}"
            f"{'/' + it['network'] if it.get('network') and it['provider'] != 'prime' else ''}]"
            f" {geo} {feed} -> {it.get('url') or it.get('raw')}")


async def _main(date: str) -> None:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        links = await fetch_event_links(client, date)
        print(f"{len(links)} links for {date}")
        games: dict[tuple, list[dict]] = {}
        for it in links:
            games.setdefault((it["league"], it["away"], it["home"]), []).append(it)
        for (lg, away, home), items in games.items():
            best = match(items, lg, set(items[0]["away_names"]), set(items[0]["home_names"]))
            print(f"\n{lg.upper()} {away} @ {home}  ({len(items)} links)")
            for prov, it in best.items():
                print(f"   {prov:9} {it.get('geo_ok')!s:5} {it.get('feed_name') or it.get('feed') or '-':22}"
                      f" +{len(it['alts'])} alt  {it.get('url') or it.get('raw')}")
        print("\n--- lookups ---")
        for lg, away, home in [("nba", {"WSH", "Wizards"}, {"ORL", "Magic"}),
                               ("nhl", {"DET", "Red Wings"}, {"FLA", "Panthers"}),
                               ("nhl", {"COL", "Avalanche"}, {"CHI", "Blackhawks"}),
                               ("nhl", {"CGY", "Flames"}, {"SJ", "Sharks"})]:
            found = match(links, lg, away, home)
            print(f"{lg} {sorted(away)} @ {sorted(home)}: {sorted(found)}")
            for prov in ("prime", "espnplus"):
                if prov in found:
                    it = found[prov]
                    print(f"   {prov}: {it['url']}  geo_ok={it['geo_ok']} feed={it['feed_name'] or it['feed']}")
        wiz = match(links, "nba", {"WSH"}, {"ORL"}).get("prime")
        if wiz:
            info = await prime_page_info(client, wiz["gti"])
            print("Prime page for", wiz["gti"], "->", info or "(unavailable)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    _d = sys.argv[1] if len(sys.argv) > 1 else dt.datetime.now(ET).date().isoformat()
    asyncio.run(_main(_d))
