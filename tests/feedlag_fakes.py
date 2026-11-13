"""Fake ESPN / NBA liveData payloads, a fake clock and a fake fetcher for the feed-arrival logger tests.

Nothing here touches the network or sleeps for real: FakeTime advances only when the code under test sleeps or
a fake request "takes" its latency. Shared by tests/test_feedlag*.py.
"""
from __future__ import annotations

from datetime import datetime, timezone

from redzone.lab.feedlag import EASTERN
from scripts.feedlag_logger import Resp

NIGHT_START = datetime(2026, 11, 12, 23, 0, tzinfo=EASTERN).timestamp()     # slate 2026-11-12


def iso(t: float) -> str:
    return datetime.fromtimestamp(int(t), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_ms(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int((t % 1) * 1000):03d}Z"


class FakeTime:
    def __init__(self, wall: float = NIGHT_START):
        self.wall, self.start = wall, wall

    def clock(self) -> float:
        return self.wall

    def mono(self) -> float:
        return self.wall - self.start + 1000.0

    def sleep(self, dt: float) -> None:
        self.wall += max(0.0, dt)

    def elapsed(self) -> float:
        return self.wall - self.start


class FakeFetcher:
    """routes: url -> payload dict, Resp, or a callable returning either. Unknown urls are 404."""

    def __init__(self, ft: FakeTime, routes: dict, latency: float = 0.2):
        self.ft, self.routes, self.latency = ft, routes, latency
        self.calls: list[tuple[float, str]] = []

    def get(self, url: str) -> Resp:
        self.calls.append((self.ft.mono(), url))
        self.ft.sleep(self.latency)
        r = self.routes.get(url)
        r = r() if callable(r) else r
        if r is None:
            return Resp(404)
        return r if isinstance(r, Resp) else Resp(200, r, {"date": "Fri, 13 Nov 2026 04:00:00 GMT"})

    def urls(self, needle: str) -> list[float]:
        return [t for t, u in self.calls if needle in u]


# --- payloads -----------------------------------------------------------------------------------------------

def espn_play(pid: str, type_text: str, stamp: float, period: int = 1, clock: str = "10:00", scoring: bool = False,
              text: str = "Some Player does a thing") -> dict:
    return {"id": pid, "sequenceNumber": pid[-4:], "type": {"id": "1", "text": type_text}, "text": text,
            "period": {"number": period}, "clock": {"displayValue": clock}, "scoringPlay": scoring,
            "wallclock": iso(stamp)}


def espn_summary(plays: list[dict]) -> dict:
    return {"plays": plays}


def cdn_action(n: int, stamp: float, action: str, sub: str = "", shot: str | None = None, period: int = 1,
               clock: str = "PT10M00.00S", edited: float | None = None, person: int | None = 7) -> dict:
    a = {"actionNumber": n, "clock": clock, "timeActual": iso_ms(stamp), "period": period, "actionType": action,
         "subType": sub, "qualifiers": [], "edited": iso_ms(edited if edited is not None else stamp + 3),
         "description": "A. Player something"}
    if person is not None:
        a["personId"] = person
    if shot:
        a["shotResult"] = shot
    return a


def cdn_pbp(actions: list[dict]) -> dict:
    return {"meta": {}, "game": {"gameId": "0022600230", "actions": actions}}


def espn_scoreboard(games: list[tuple]) -> dict:
    """games: (event id, state 'pre'|'in'|'post', away abbr, home abbr, start epoch)"""
    ev = []
    for gid, state, away, home, start in games:
        comps = [{"homeAway": "home", "team": {"abbreviation": home, "id": "1"}, "score": "0"},
                 {"homeAway": "away", "team": {"abbreviation": away, "id": "2"}, "score": "0"}]
        ev.append({"id": gid, "date": iso(start), "status": {"type": {"state": state}, "period": 1},
                   "competitions": [{"competitors": comps, "status": {"type": {"state": state}, "period": 1}}]})
    return {"events": ev}


def nba_scoreboard(games: list[tuple]) -> dict:
    """games: (NBA game id, away tricode, home tricode)"""
    return {"scoreboard": {"games": [{"gameId": g, "gameStatus": 2, "awayTeam": {"teamTricode": a},
                                      "homeTeam": {"teamTricode": h}} for g, a, h in games]}}
