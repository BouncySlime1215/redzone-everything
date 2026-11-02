"""Phone control page + JSON API. LAN only.

Guardrail: requests from anything other than a private/home-network address get a 403,
so even if a port were ever forwarded by mistake, the internet can't drive your TV.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import re

from redzone.channels import parse_yttv_link
from redzone.config import LIMITS, ConfigError
from redzone.engine import Engine

STATIC = Path(__file__).with_name("static")
ROOT = Path(__file__).resolve().parent.parent.parent
IMG_CACHE = ROOT / "data" / "imgcache"
IMG_PATH = re.compile(r"[A-Za-z0-9_/.\-]{3,200}\.(png|jpg|jpeg|svg|webp)")
IMG_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "svg": "image/svg+xml", "webp": "image/webp"}


def _private(host: str | None) -> bool:
    if not host:
        return False
    if host in ("localhost", "testclient"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip in ipaddress.ip_network("100.64.0.0/10")


def _target_from_link(link: str) -> str | None:
    """A pasted YouTube TV or Prime Video link -> tuner target."""
    code = parse_yttv_link(link)
    if code:
        return f"youtubetv:ev:{code}"
    m = re.search(r"gti=(amzn1\.dv\.gti\.[0-9a-fA-F-]+)", link)
    return f"prime:{m.group(1)}" if m else None


def build_app(engine: Engine) -> FastAPI:
    app = FastAPI(title="RedZone Everything", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def lan_only(request: Request, call_next):
        if not _private(request.client.host if request.client else None):
            return JSONResponse({"ok": False, "message": "home network only"}, status_code=403)
        resp = await call_next(request)
        if request.url.path.startswith("/api"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/api/state")
    async def state():
        return JSONResponse(json.loads(json.dumps(engine.snapshot(), default=str)))

    @app.get("/api/stream")
    async def stream(request: Request):
        q: asyncio.Queue = asyncio.Queue()
        engine.subscribers.add(q)
        q.put_nowait(("state", json.dumps(engine.snapshot(), default=str)))

        async def gen():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        ev, data = await asyncio.wait_for(q.get(), timeout=15)
                        yield f"event: {ev}\ndata: {data}\n\n"
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"  # keeps phones and proxies from dropping the stream
            finally:
                engine.subscribers.discard(q)
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/action")
    async def action(body: dict):
        try:
            ok, msg = engine.action(str(body.get("action", "")), body.get("game_id"), body.get("value"))
        except Exception as e:  # a bad tap never crashes the server
            ok, msg = False, f"Something went wrong: {e}"
        engine.decide()
        engine.broadcast()
        return {"ok": ok, "message": msg}

    @app.get("/api/settings")
    async def get_settings():
        return {"config": engine.cfg.data, "limits": {k: list(v) for k, v in LIMITS.items()}}

    @app.post("/api/settings")
    async def set_settings(body: dict):
        try:
            engine.cfg.update(body.get("changes") or {})
        except ConfigError as e:
            return {"ok": False, "message": str(e)}
        engine.broadcast()
        return {"ok": True, "message": "Saved"}

    @app.post("/api/settings/rollback")
    async def rollback():
        ok = engine.cfg.rollback()
        return {"ok": ok, "message": "Undid the last change" if ok else "Nothing to undo"}

    @app.get("/api/channels")
    async def channels():
        return engine.channels_view()

    @app.post("/api/channels")
    async def set_channel(body: dict):
        try:
            code = engine.channels.set_code_from_link(str(body.get("channel")), str(body.get("link", "")))
        except ValueError as e:
            return {"ok": False, "message": str(e)}
        return {"ok": True, "message": f"Saved ({code})"}

    @app.post("/api/override")
    async def override(body: dict):
        target = body.get("target")
        if isinstance(target, str) and ("://" in target or "gti=" in target):
            target = _target_from_link(target)
            if not target:
                return {"ok": False, "message": "Couldn't read that link — paste a tv.youtube.com/watch/… or Prime link"}
        engine.channels.set_override(str(body.get("game_id")), target)
        engine.decide()
        engine.broadcast()
        return {"ok": True, "message": "Saved — I'll remember this"}

    @app.get("/api/bets")
    async def bets():
        return engine.bets_view()

    @app.post("/api/bets")
    async def add_bet(body: dict):
        if not body.get("game_id") or body.get("kind") not in ("spread", "moneyline", "total", "prop"):
            return {"ok": False, "message": "Pick a game and a bet type"}
        line = body.get("line")
        try:
            line = float(line) if line not in (None, "") else None
        except (TypeError, ValueError):
            return {"ok": False, "message": "The line should be a number like -3.5"}
        if body["kind"] in ("spread", "total") and line is None:
            return {"ok": False, "message": "Add the line (e.g. -3.5 or 46.5)"}
        engine.store.add_bet(str(body["game_id"]), body["kind"], str(body.get("team") or "").upper(),
                             line, str(body.get("side") or ""), str(body.get("note") or "")[:120])
        engine.decide()
        engine.broadcast()
        return {"ok": True, "message": "Bet added — I'll prioritize the sweat"}

    @app.delete("/api/bets/{bet_id}")
    async def del_bet(bet_id: int):
        engine.store.remove_bet(bet_id)
        engine.broadcast()
        return {"ok": True, "message": "Removed"}

    @app.get("/api/lab")
    async def lab():
        out = {"backtests": [], "optimize": None, "feedback": engine.store.feedback_counts()}
        lab_dir = ROOT / "data" / "lab"
        for p in sorted(lab_dir.glob("backtest_*.json")) if lab_dir.exists() else []:
            try:
                out["backtests"].append(json.loads(p.read_text()))
            except ValueError:
                pass
        opt = lab_dir / "optimize.json"
        if opt.exists():
            try:
                d = json.loads(opt.read_text())
                out["optimize"] = {"current": d.get("current"), "best": d.get("best"), "slates": d.get("slates")}
            except ValueError:
                pass
        return out

    @app.get("/api/game/{game_id}")
    async def game(game_id: str):
        d = await engine.game_detail(game_id)
        if d is None:
            return JSONResponse({"ok": False, "message": "game not found"}, status_code=404)
        return JSONResponse(json.loads(json.dumps(d, default=str)))

    @app.get("/api/teams")
    async def teams():
        return await engine.teams()

    @app.get("/api/teams/{league}/{team_id}")
    async def team(league: str, team_id: str):
        if league not in ("nfl", "nba", "nhl", "mlb") or not team_id.isdigit():
            return JSONResponse({"ok": False, "message": "unknown team"}, status_code=404)
        return JSONResponse(json.loads(json.dumps(await engine.team_detail(league, team_id), default=str)))

    @app.get("/img/{path:path}")
    async def img(path: str):
        """Logos, headshots and stadium photos from ESPN's CDN, cached on disk.
        Only a.espncdn.com, only image-looking paths: this can't be used to fetch anything else."""
        if not IMG_PATH.fullmatch(path) or ".." in path:
            return JSONResponse({"ok": False}, status_code=404)
        key = hashlib.sha1(path.encode()).hexdigest()
        ext = path.rsplit(".", 1)[-1].lower()
        f = IMG_CACHE / f"{key}.{ext}"
        if not f.exists():
            got = await engine.fetcher.get_bytes(f"https://a.espncdn.com/{path}")
            if not got or not got[1].startswith("image/"):
                return JSONResponse({"ok": False}, status_code=404)
            IMG_CACHE.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp")
            tmp.write_bytes(got[0])
            tmp.replace(f)
        return FileResponse(f, media_type=IMG_TYPES.get(ext, "image/png"),
                            headers={"Cache-Control": "public, max-age=604800, immutable"})

    @app.get("/api/health")
    async def health():
        return engine.snapshot()["health"]

    return app
