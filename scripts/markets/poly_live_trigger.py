#!/usr/bin/env python3
"""Polymarket live "something just happened" trigger, SHADOW LOG ONLY (Data Division D, Nick 9:05 AM Nov 5).

Subscribes to Polymarket's public CLOB market WebSocket for tonight's game markets and writes every
price/trade update with our receive time, plus a `trigger` row when the home win probability moves >= JUMP_P
within WINDOW_S. Lane 2 compares the trigger's receive time with each feed's first-seen time for the same play
(the feed-lag race). Nothing reads this live: no decider input, nothing shown, never a betting hook.

Runs on Colab / Kaggle / a cloud box (never on the Mac; the dev container's allowlist blocks the host).
Public endpoints only, no account, no key. Kalshi is NOT used: its Developer Agreement v1.1 requires
membership (§4.1.1), limits use to a member's own trading (§3), bans storing data (§3.1) and bans
benchmarking/monitoring uses (§3.5); see docs/datasources/markets/kalshi.md.

    pip install websockets requests
    python scripts/markets/poly_live_trigger.py --league nfl --date 2026-11-05 --out out/markets_live --hours 5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import polymarket as PM  # noqa: E402

WSS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
JUMP_P = 0.03        # 3 probability points
WINDOW_S = 30.0      # within 30 s
COOLDOWN_S = 20.0    # one trigger per market per 20 s
PING_S = 10.0        # docs: send text PING every 10 s


class Detector:
    """Per-market rolling window: fires when p_home moves >= JUMP_P inside WINDOW_S (pure, testable)."""

    def __init__(self, jump: float = JUMP_P, window: float = WINDOW_S, cooldown: float = COOLDOWN_S):
        self.jump, self.window, self.cooldown = jump, window, cooldown
        self.hist: dict[str, deque] = {}
        self.last_fire: dict[str, float] = {}

    def update(self, key: str, t: float, p: float) -> dict | None:
        q = self.hist.setdefault(key, deque())
        q.append((t, p))
        while q and t - q[0][0] > self.window:
            q.popleft()
        lo = min(q, key=lambda x: x[1])
        hi = max(q, key=lambda x: x[1])
        ref = lo if abs(p - lo[1]) >= abs(p - hi[1]) else hi
        delta = p - ref[1]
        if abs(delta) < self.jump or t - self.last_fire.get(key, -1e18) < self.cooldown:
            return None
        self.last_fire[key] = t
        q.clear()
        q.append((t, p))
        return {"delta": round(delta, 4), "from_t": ref[0], "window_s": round(t - ref[0], 3)}


def p_from_event(ev: dict, asset: str) -> float | None:
    """A market-channel event -> the asset's price (trade price, else bid/ask mid), or None."""
    et = ev.get("event_type")
    try:
        if et == "last_trade_price" and ev.get("asset_id") == asset:
            return float(ev["price"])
        if et == "best_bid_ask" and ev.get("asset_id") == asset:
            return (float(ev["best_bid"]) + float(ev["best_ask"])) / 2
        if et == "price_change":
            for pc in ev.get("price_changes") or []:
                if pc.get("asset_id") == asset and pc.get("best_bid") and pc.get("best_ask"):
                    return (float(pc["best_bid"]) + float(pc["best_ask"])) / 2
    except (KeyError, TypeError, ValueError):
        return None
    return None


def server_ts(ev: dict) -> float | None:
    v = ev.get("timestamp")
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v / 1000.0 if v > 1e11 else v          # 13-digit = ms (docs examples)


def handle(raw: str, markets: dict[str, dict], det: Detector, recv: float) -> list[dict]:
    """One WebSocket frame -> output rows (ticks and triggers). `markets`: token id -> parsed market."""
    if raw in ("PONG", ""):
        return []
    try:
        msg = json.loads(raw)
    except ValueError:
        return []
    out = []
    for ev in msg if isinstance(msg, list) else [msg]:
        assets = {ev.get("asset_id")} | {pc.get("asset_id") for pc in ev.get("price_changes") or []}
        for asset in assets & markets.keys():
            pm = markets[asset]
            p = p_from_event(ev, asset)
            if p is None:
                continue
            p_home = 1.0 - p if pm["invert"] else p
            base = {"source": PM.SOURCE, "game_key": pm["game_key"], "market_id": asset, "recv_ts": recv,
                    "server_ts": server_ts(ev), "event_type": ev.get("event_type"), "p_home": round(p_home, 4),
                    "tag": PM.TAG}
            out.append({"row": "tick", **base})
            fire = det.update(asset, recv, p_home)
            if fire:
                out.append({"row": "trigger", **base, **fire})
    return out


def tonight_markets(league: str, date: str, max_markets: int | None = None) -> dict[str, dict]:
    """Open moneyline game markets for `league` starting on `date` (US Eastern)."""
    c = PM.Client()
    sports = c.get(f"{PM.GAMMA}/sports") or []
    tag = PM.pick_tag(sports, league)
    if tag is None:
        sys.exit(f"no Polymarket sports tag for {league!r}")
    ordering = PM.sport_ordering(sports, league)
    d0 = datetime.fromisoformat(date)
    params = {"tag_id": tag, "closed": "false", "limit": 100, "sports_market_types": "moneyline",
              "end_date_min": f"{date}T00:00:00Z",
              "end_date_max": f"{(d0 + timedelta(days=3)).strftime('%Y-%m-%d')}T23:59:59Z"}
    out: dict[str, dict] = {}
    day = d0.strftime("%Y%m%d")
    while True:
        page = c.get(f"{PM.GAMMA}/markets/keyset", params) or {}
        for m in page.get("markets") or []:
            pm = PM.parse_market(m, league, ordering)
            if pm and PM.local_date(pm["start_ts"]) == day:      # slug dates can be UTC: use the US Eastern start
                pm["check"] = {k: m.get(k) for k in ("question", "outcomes", "outcomePrices", "slug", "volumeNum")}
                out[pm["market_id"]] = pm
        cur = page.get("next_cursor")
        if not cur or (max_markets and len(out) >= max_markets):
            return out
        params = {**params, "after_cursor": cur}


async def listen(markets: dict[str, dict], path: Path, hours: float) -> dict:
    import websockets
    det, stop = Detector(), time.time() + hours * 3600
    n = {"ticks": 0, "triggers": 0, "reconnects": 0}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        while time.time() < stop:
            try:
                async with websockets.connect(WSS, ping_interval=None, user_agent_header=PM.UA) as ws:
                    await ws.send(json.dumps({"assets_ids": list(markets), "type": "market",
                                              "custom_feature_enabled": True}))
                    last_ping = time.monotonic()
                    while time.time() < stop:
                        if time.monotonic() - last_ping >= PING_S:
                            await ws.send("PING")
                            last_ping = time.monotonic()
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=PING_S)
                        except asyncio.TimeoutError:
                            continue
                        for row in handle(raw, markets, det, time.time()):
                            n["ticks" if row["row"] == "tick" else "triggers"] += 1
                            fh.write(json.dumps(row) + "\n")
                        fh.flush()
            except Exception as e:  # noqa: BLE001 - reconnect politely on any socket error
                n["reconnects"] += 1
                print(f"socket error {type(e).__name__}: {e}; reconnect in 5 s", file=sys.stderr)
                await asyncio.sleep(5)
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--league", default="nfl")
    ap.add_argument("--date", default=datetime.now(PM.EASTERN).strftime("%Y-%m-%d"))
    ap.add_argument("--out", type=Path, default=Path("out/markets_live"))
    ap.add_argument("--hours", type=float, default=5.0)
    ap.add_argument("--max-markets", type=int)
    a = ap.parse_args(argv)
    mk = tonight_markets(a.league, a.date, a.max_markets)
    print(json.dumps({"markets": len(mk), "games": sorted({m["game_key"] for m in mk.values()}),
                      "check": [{**m["check"], "invert": m["invert"]} for m in mk.values()]}))
    if not mk:
        return 1
    path = a.out / PM.SOURCE / a.league / f"{a.date.replace('-', '')}.jsonl"
    n = asyncio.run(listen(mk, path, a.hours))
    print(json.dumps({**n, "file": str(path), "clock": "recv_ts is this host's UTC clock; keep NTP on",
                      "started": datetime.now(timezone.utc).isoformat()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
