"""Phone control page + JSON API. LAN only.

Guardrail: requests from anything other than a private/home-network address get a 403,
so even if a port were ever forwarded by mistake, the internet can't drive your TV.

Why: first added in 9faa350e (2026-11-02) as "RedZone Everything: core engine, scorer, decider, replays, backtest lab, server"; no separate decision is recorded for this module.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import socket
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import re

from redzone.channels import parse_yttv_link
from redzone import userstate
from redzone.config import LIMITS, ConfigError
from redzone.engine import Engine
from redzone.web import phone_v2

STATIC = Path(__file__).with_name("static")
ROOT = Path(__file__).resolve().parent.parent.parent
IMG_CACHE = ROOT / "data" / "imgcache"
IMG_PATH = re.compile(r"[A-Za-z0-9_/.\-]{3,200}\.(png|jpg|jpeg|svg|webp)")
IMG_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "svg": "image/svg+xml", "webp": "image/webp"}


# Settings the phone page may not change: server.port is read from the PORT env var in the plist, so editing it
# did nothing, and a re-rendered plist without PORT would have moved the app off the 8778 that the watchdog and
# deploy.py hard-code (P-19).
READ_ONLY = ("server.port",)
SECRET_KEY = re.compile(r"topic|token|secret|password|passwd|api_?key|credential", re.I)


def redact(node):
    """GET /api/settings without secrets: the ntfy topic is the only thing between the public ntfy.sh server and
    this Mac's pushes, so any guest on the Wi-Fi could read it (P-14). A set secret reads "(set)", an empty one ""."""
    if isinstance(node, dict):
        return {k: (("(set)" if v else "") if SECRET_KEY.search(str(k)) and isinstance(v, str) else redact(v))
                for k, v in node.items()}
    if isinstance(node, list):
        return [redact(v) for v in node]
    return node


def _changes_key(changes, dotted: str) -> bool:
    node = changes
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


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


def _tap_status(engine: Engine, rec: dict) -> dict:
    """"state" of a tap: failed | done | waiting (a "Watch this" the TV hasn't followed yet) and,
    for a game tap, on_tv = that game is what the TV shows now."""
    if not rec["ok"]:
        return {"state": "failed"}
    if rec["action"] in ("hold", "watch", "redzone", "focus") and rec.get("game_id"):
        on = engine.decider.current == rec["game_id"]
        return {"state": "done" if on else "waiting", "on_tv": on}
    return {"state": "done"}


# ---- phone queue card (flag queue.enabled; WP-14) --------------------------------------------------------
# Why: the phone shows what the hidden stick is lining up, in plain words, and never what happens next.
# Only these keys ever leave the server: no tier, join time, value or outcome (gamequeue.view is already
# spoiler-safe; this is the second lock).
_CARD_KEYS = ("game", "level", "app", "prep_s", "owner", "reason", "flippable")
LEVEL_WORDS = {
    "R0": "No way to reach it", "R1": "Found, not tried yet", "R2": "Tried tonight, works",
    "R3": "Playing on the other stick", "R4": "Buffered and read", "R5": "Ready to flip",
}
QUEUE_ROWS = 5


def _queue_on(engine) -> bool:
    try:
        return bool(engine.cfg.get("queue.enabled", False))
    except Exception:
        return False


def _safe_card(c) -> dict | None:
    if not isinstance(c, dict) or not c.get("game"):
        return None
    out = {k: c.get(k) for k in _CARD_KEYS}
    out["level_word"] = LEVEL_WORDS.get(str(c.get("level")), "")
    return out


def queue_card(gq, pins=()) -> dict:
    """The phone card from snapshot["game_queue"]: top 5 rows (hidden stick first), plus the can't-reach list."""
    gq = gq if isinstance(gq, dict) else {}
    cards = [_safe_card(gq.get("hidden"))] + [_safe_card(c) for c in gq.get("up_next") or []]
    rows = [c for c in cards if c][:QUEUE_ROWS]
    pinned = set(pins)
    for r in rows:
        r["pinned"] = r["game"] in pinned or r.get("owner") == "pin"
    return {"rows": rows, "cant_reach": [g for g in gq.get("cant_reach") or [] if isinstance(g, str)],
            "pins": list(pins)}


def build_app(engine: Engine) -> FastAPI:
    app = FastAPI(title="RedZone Everything", docs_url=None, redoc_url=None)
    def _pins() -> list[str]:
        """Nick's pins, in order. The engine owns and saves them (user_state.json); the phone only shows them."""
        return list(getattr(engine, "queue_pins", None) or [])

    def _snap() -> dict:
        snap = engine.snapshot()
        if _queue_on(engine) and "game_queue" in snap:
            snap = {**snap, "game_queue_card": queue_card(snap["game_queue"], _pins())}
        return snap

    @app.middleware("http")
    async def lan_only(request: Request, call_next):
        if not _private(request.client.host if request.client else None):
            return JSONResponse({"ok": False, "message": "home network only"}, status_code=403)
        # A malicious internet page can send a simple POST to a LAN address
        # even without CORS permission. Check its origin BEFORE handling a TV
        # command, and reject internet Host names (DNS rebinding).
        host = request.url.hostname or ""
        local_names = {"localhost", socket.gethostname().lower(), socket.gethostname().split(".")[0].lower()}
        trusted_host = _private(host) or host.lower() in local_names or host.lower().endswith(".local")
        if request.client and request.client.host == "testclient" and host == "testserver":
            trusted_host = True
        if not trusted_host:
            return JSONResponse({"ok": False, "message": "use the Mac's local address"}, status_code=403)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin:
                try:
                    parsed = urlsplit(origin)
                    same = parsed.scheme in {"http", "https"} and parsed.netloc.lower() == request.url.netloc.lower()
                except ValueError:
                    same = False
                if not same:
                    return JSONResponse({"ok": False, "message": "open the local phone page to control the TV"}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"ok": False, "message": "cross-site commands are not allowed"}, status_code=403)
            has_body = request.headers.get("content-length", "0") != "0" or "transfer-encoding" in request.headers
            if has_body and request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                return JSONResponse({"ok": False, "message": "commands require application/json"}, status_code=415)
        resp = await call_next(request)
        if request.url.path.startswith("/api"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.include_router(phone_v2.router(engine))   # flag phone.v2 (off): reasons, 👍/👎, "I missed that", L2

    @app.get("/reel.mp4")
    def reel_file():
        r = engine.reeler.current
        if r is None or not r.path.exists():
            return JSONResponse({"error": "no reel ready"}, status_code=404)
        # Range requests work (Starlette), so a TV player can seek and resume.
        return FileResponse(r.path, media_type="video/mp4", headers={"Cache-Control": "no-store"})

    @app.get("/api/reel")
    def reel_info():
        return engine.reel_view() or {"seconds": 0, "clips": []}

    @app.get("/api/state")
    async def state():
        return JSONResponse(json.loads(json.dumps(_snap(), default=str)))

    @app.get("/api/machines")
    def machines_view(name: str = ""):
        """Every explicit state machine: state, since, why, recent moves (redzone/fsm.py)."""
        from redzone import fsm
        rows = fsm.snapshot_all()
        if name:
            rows = [r for r in rows if r["machine"] == name or r["machine"].startswith(name + ".")]
        return JSONResponse(json.loads(json.dumps({"machines": rows}, default=str)))

    @app.get("/api/machines/history")
    def machines_history(name: str = "", limit: int = 50):
        """Saved moves from data/transitions.jsonl (survives restarts), newest last."""
        from pathlib import Path

        from redzone import fsm
        from redzone.config import DATA
        rows = fsm.read_transitions(DATA / "transitions.jsonl", name, max(1, min(limit, 500)))
        return JSONResponse({"transitions": rows})

    @app.get("/api/stream")
    async def stream(request: Request):
        q: asyncio.Queue = asyncio.Queue()
        engine.subscribers.add(q)
        q.put_nowait(("state", json.dumps(_snap(), default=str)))

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

    @app.get("/api/queue")
    async def queue_api():
        if not _queue_on(engine):
            return JSONResponse({"ok": False, "message": "queue is off"}, status_code=404)
        return {"ok": True, **queue_card(engine.snapshot().get("game_queue"), _pins())}

    async def _queue_tap(kind: str, body: dict):
        if not _queue_on(engine):
            return JSONResponse({"ok": False, "message": "queue is off"}, status_code=404)
        gid = body.get("game_id")
        if not isinstance(gid, str) or not gid:
            return {"ok": False, "message": "which game?"}
        value = body.get("value", True if kind == "pin" else "up")
        if kind == "pin":
            value = bool(value)
        elif value not in ("up", "down"):
            return {"ok": False, "message": "move up or down"}
        # a tap id makes a retry or a double tap land once ("Move up" twice moved a game two places)
        rec = engine.commands.run(body.get("id"), kind, gid, value, lambda: engine.action(kind, gid, value))
        if rec["ok"] and not rec["duplicate"]:
            for fn in ("decide", "broadcast"):
                getattr(engine, fn, lambda: None)()
        return {"ok": rec["ok"], "message": rec["message"], "duplicate": rec["duplicate"]}

    @app.post("/api/queue/pin")
    async def queue_pin(body: dict):
        return await _queue_tap("pin", body)

    @app.post("/api/queue/move")
    async def queue_move(body: dict):
        return await _queue_tap("move", body)

    @app.post("/api/action")
    async def action(body: dict):
        """One phone tap. With an "id" it runs once: a retry with the same id gets the first answer
        back ("duplicate": true) instead of running again (redzone/commands.py)."""
        act, gid, value = str(body.get("action", "")), body.get("game_id"), body.get("value")
        rec = engine.commands.run(body.get("id"), act, gid, value, lambda: engine.action(act, gid, value))
        if not rec["duplicate"]:
            engine.decide()
            engine.broadcast()
        return {"ok": rec["ok"], "message": rec["message"], "id": rec["id"], "duplicate": rec["duplicate"],
                **_tap_status(engine, rec)}

    @app.get("/api/action/{cid}")
    async def action_status(cid: str):
        """A tap's status by id: what it answered, and whether the TV has done it yet."""
        rec = engine.commands.status(cid)
        if rec is None:
            return JSONResponse({"ok": False, "message": "unknown or expired tap id"}, status_code=404)
        return {"ok": rec["ok"], "message": rec["message"], "id": cid, "action": rec["action"],
                "game_id": rec["game_id"], **_tap_status(engine, rec)}

    @app.post("/api/recorded/start")
    async def recorded_start(body: dict):
        ok, message = await engine.recorded.start(body)
        return {"ok": ok, "message": message, "recorded_playback": engine.recorded.snapshot()}

    @app.post("/api/recorded/stop")
    async def recorded_stop():
        ok, message = await engine.recorded.stop()
        return {"ok": ok, "message": message, "recorded_playback": engine.recorded.snapshot()}

    @app.get("/api/settings")
    async def get_settings():
        return {"config": redact(engine.cfg.data),
                "limits": {k: list(v) for k, v in LIMITS.items() if k not in READ_ONLY}}

    @app.post("/api/settings")
    async def set_settings(body: dict):
        locked = [k for k in READ_ONLY if _changes_key(body.get("changes"), k)]
        if locked:   # a control that would do nothing (the port comes from the plist) or could look like an outage
            return {"ok": False, "message": f"{locked[0]} can't be changed from the phone"}
        try:
            engine.cfg.update(body.get("changes") or {})
        except ConfigError as e:
            return {"ok": False, "message": str(e)}
        if engine.recorded.active and engine.cfg.get("mode.dry_run", True):
            engine.recorded.cancel_navigation()
        engine.broadcast()
        return {"ok": True, "message": "Saved"}

    @app.post("/api/settings/rollback")
    async def rollback():
        ok = engine.cfg.rollback()
        note = getattr(engine.cfg, "rollback_note", "") or ("Undid the last change" if ok else "Nothing to undo")
        return {"ok": ok, "message": note}

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

    @app.post("/api/channels/sunday-ticket")
    async def set_st(body: dict):
        try:
            codes = engine.channels.set_st_codes(str(body.get("links", "")))
        except ValueError as e:
            return {"ok": False, "message": str(e)}
        return {"ok": True, "message": f"Saved {len(codes)} Sunday Ticket channels"}

    @app.post("/api/override")
    async def override(body: dict):
        gid = body.get("game_id")
        if not isinstance(gid, str) or not gid.strip():
            return {"ok": False, "message": "Pick a game first"}
        target = body.get("target")
        if isinstance(target, str) and ("://" in target or "gti=" in target):
            target = _target_from_link(target)
            if not target:
                return {"ok": False, "message": "Couldn't read that link — paste a tv.youtube.com/watch/… or Prime link"}
        engine.channels.set_override(gid, target)
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

    @app.get("/img/combiner/i")
    async def img_resized(img: str, w: int = 0, h: int = 0):
        """ESPN's own resizer (e.g. 96px headshots instead of 200KB originals), cached like /img."""
        img = img.lstrip("/")
        if not IMG_PATH.fullmatch(img) or ".." in img or not (0 <= w <= 2000 and 0 <= h <= 2000):
            return JSONResponse({"ok": False}, status_code=404)
        q = f"img=/{img}" + (f"&w={w}" if w else "") + (f"&h={h}" if h else "")
        key = hashlib.sha1(("combiner?" + q).encode()).hexdigest()
        ext = img.rsplit(".", 1)[-1].lower()
        f = IMG_CACHE / f"{key}.{ext}"
        if not f.exists():
            got = await engine.fetcher.get_bytes(f"https://a.espncdn.com/combiner/i?{q}")
            if not got or not got[1].startswith("image/"):
                return JSONResponse({"ok": False}, status_code=404)
            IMG_CACHE.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp")
            tmp.write_bytes(got[0])
            tmp.replace(f)
        return FileResponse(f, media_type=IMG_TYPES.get(ext, "image/png"),
                            headers={"Cache-Control": "public, max-age=604800, immutable"})

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

    @app.post("/api/agent/fix")
    async def agent_fix(body: dict, request: Request):
        """The watchdog (agent/watchdog.py, same Mac) asks for one narrow fix. Local callers only:
        the phone page never needs it. Off unless hardening.watchdog (redzone/platform/agent_fix.py)."""
        host = request.client.host if request.client else ""
        if host not in ("127.0.0.1", "::1", "localhost", "testclient"):
            return JSONResponse({"ok": False, "message": "local only"}, status_code=403)
        from redzone.platform.agent_fix import run_fix
        try:
            ok, msg = await run_fix(engine, str(body.get("fix", "")), str(body.get("target", "") or ""))
        except Exception as e:   # a fix never takes the server down
            ok, msg = False, f"crashed: {type(e).__name__}: {str(e)[:100]}"
        return {"ok": ok, "message": msg}

    @app.get("/api/health")
    async def health():
        return engine.snapshot()["health"]

    return app
