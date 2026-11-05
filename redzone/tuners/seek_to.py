"""[T7 SEEK_TO] Rewind a Fire TV stick's player by an exact amount with MediaSession SEEK_TO, falling back to
key presses (Nick, Nov 5 10:00 AM: "SEEK_TO helper + rewind/ads reliability per app/channel ... a key-based
fallback if it fails").

YouTube TV (actions=383) and Prime (1049455) both advertise SEEK_TO. tools/rzseek/RzSeek.java runs on the stick
as the adb shell user through app_process (nothing installed): `list` reads every session with its position NOW
extrapolated on the stick's own clock, `seekrel <pkg> <delta_ms>` seeks relative to it. Every landing is measured
against the read taken before the first seek, on the stick's clock:

    landed_back_s = (pos_before + (elapsed_now - elapsed_before) * playing) - pos_now     [ms -> s]

i.e. where the picture would be with no seek, minus where it is. err_s = landed_back_s - asked_s; positive = it
went further back than asked (more pre-roll, safe for Rule #1), negative = not far enough (could show the snap).

Contract (agreed with the "Test the SEEK_TO helper" thread):

    await seek_to(stick, back_s, *, app, channel=None, cfg, clock=None, pkg=None) -> SeekResult

It never raises. Order: SEEK_TO, re-measured after each seek, with up to replay.seek_to.retries more seeks
for whatever is still off by more than tolerance_s (a timed-out seek is re-measured too: it may have run). Then:
  * SEEK_TO couldn't run at all (flag off, no dex, no session, no SEEK_TO action): the key path for the whole
    ask, priced per app (key_model: YouTube TV 15 s with a swallowed press, Prime 10 s), method "keys";
  * still short of a rewind after the seeks: presses for the REMAINING distance only, measured from where the
    picture really is ("seek_to+keys", or "keys" if SEEK_TO never moved it), so nothing is ever moved twice;
  * landed further back than asked, or a forward seek fell short of its ask: ok False, nothing else sent;
  * an ad pod after the seek or an unreadable stick: ok False, nothing else sent (the position can't be told).
Every adb call has its own guard (timeout_s + 0.5 s), so a wedged adb session can't hold the switch.

Rule #1 holds: the caller only seeks a stick that is hidden, or one on a dead ball. Nothing here decides when.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable


log = logging.getLogger("redzone.seek_to")

DEX_REMOTE = "/data/local/tmp/rzseek.dex"
RZ = f"CLASSPATH={DEX_REMOTE} app_process / RzSeek"
SEEK_TO_BIT = 256
STATE_PLAYING = 3

SEEK_TO_DEFAULTS = {"enabled": False, "timeout_s": 4.0, "retries": 1, "tolerance_s": 1.0,
            "dex_path": "tools/rzseek/rzseek.dex", "poll_s": 0.05}

# Words an ad pod's MediaMetadata title carries (YouTube TV shows "Ad" / "Advertisement"; Prime "Sponsored").
_AD_WORDS = re.compile(r"\b(ad|ads|advertisement|commercial|sponsored|promo)\b", re.I)

# Per stick host: True once the dex on the stick matches our file's size (pushed or already there), False when
# we know it can't run there (push failed or app_process refused it). Reset by forget_dex().
_DEX: dict[str, bool] = {}


@dataclass
class SeekResult:
    ok: bool
    method: str                      # "seek_to" | "seek_to+keys" | "keys" | "none"
    asked_s: float
    landed_s: float | None = None    # how far it really moved back (stick clock); None when only keys ran
    err_s: float | None = None       # landed_s - asked_s (+ = further back than asked)
    attempts: int = 0                # SEEK_TO commands sent (the first + corrections)
    timed_out: bool = False          # the stick didn't report PLAYING again within timeout_s
    settle_s: float | None = None    # seek -> PLAYING again with a new position, on the stick's clock
    ad_suspect: bool = False         # title / duration changed across the seek, or an ad-like title
    reason: str = ""
    presses: int = 0                 # key presses sent (fallback / top-up)
    took_s: float = 0.0              # host wall time for the whole call
    meta0: dict = field(default_factory=dict)
    meta1: dict = field(default_factory=dict)

    def row(self) -> dict:
        d = asdict(self)
        for k in ("landed_s", "err_s", "settle_s", "took_s"):
            if d[k] is not None:
                d[k] = round(float(d[k]), 3)
        return d


def _cfg(cfg, key: str):
    v = None
    if cfg is not None and hasattr(cfg, "get"):
        v = cfg.get(f"replay.seek_to.{key}")
    return SEEK_TO_DEFAULTS[key] if v is None else v


def parse_list(text: str) -> list[dict]:
    """The sessions in RzSeek output: JSON lines that name a package and are not an error."""
    return [d for d in parse_rows(text) if "pkg" in d and "error" not in d]


def parse_rows(text: str) -> list[dict]:
    """Every JSON line from RzSeek (list / seek / seekrel, errors too). Anything else (adb noise) is skipped."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def landed_back_s(before: dict, after: dict, playing: bool = True) -> float | None:
    """Seconds the picture moved back: where it would be with no seek minus where it is.
    before: {pos_now, elapsed} (ms, stick clock), after: the same. playing=False when the player was paused
    before the seek (its position doesn't run on)."""
    try:
        p0, e0 = float(before["pos_now"]), float(before["elapsed"])
        p1, e1 = float(after["pos_now"]), float(after["elapsed"])
    except (KeyError, TypeError, ValueError):
        return None
    if p0 < 0 or p1 < 0 or e1 < e0:
        return None
    expect = p0 + ((e1 - e0) if playing else 0.0)
    return (expect - p1) / 1000.0


# An ad pod plays as its own media item: its position restarts near 0. A game stream never sits under this.
AD_RESTART_POSITION = 600_000


def is_ad_suspect(before: dict | None, samples) -> bool:
    """Did the seek land in (or force) an ad? `before`: the session read before the seek; `samples`: one read or a
    list of reads after it. An ad-like title, a title or duration change, or the position restarting as a new
    item (it was past 10 minutes, now under). Weak evidence on its own; seek_bench adds self-jumps and stalls."""
    b = before or {}
    if isinstance(samples, dict):
        samples = [samples]
    for x in samples or []:
        if not isinstance(x, dict):
            continue
        t1 = x.get("title")
        if isinstance(t1, str) and _AD_WORDS.search(t1):
            return True
        t0 = b.get("title")
        if isinstance(t0, str) and isinstance(t1, str) and t0.strip() and t1.strip() and t0 != t1:
            return True
        try:
            d0, d1 = b.get("duration"), x.get("duration")
            if d0 is not None and d1 is not None and float(d0) > 0 and float(d1) > 0 \
                    and abs(float(d0) - float(d1)) > 1000:
                return True
            p0, p1 = b.get("pos_now"), x.get("pos_now")
            if p0 is not None and p1 is not None and float(p0) > AD_RESTART_POSITION and 0 <= float(p1) < AD_RESTART_POSITION:
                return True
        except (TypeError, ValueError):
            pass
    return False


def _meta(d: dict) -> dict:
    return {k: d.get(k) for k in ("title", "subtitle", "duration")}


def forget_dex(host: str | None = None) -> None:
    if host is None:
        _DEX.clear()
    else:
        _DEX.pop(host, None)


class _Budget:
    """Every adb call is guarded by its own timeout too: a wedged adb session must not hold the switch."""

    def __init__(self, stick, per_call_s: float):
        self.stick, self.per_call_s, self.timed_out = stick, per_call_s, False

    async def shell(self, cmd: str, timeout: float | None = None):
        from redzone.tuners.adb import RunResult
        t = self.per_call_s if timeout is None else timeout
        try:
            r = await asyncio.wait_for(self.stick.shell(cmd, timeout=t), t + 0.5)
        except asyncio.TimeoutError:
            self.timed_out = True
            return RunResult(False, "timeout/error: seek_to guard", t)
        if not r.ok and (not (r.out or "").strip() or (r.out or "").startswith("timeout/error")):
            self.timed_out = True     # a failed call with no output at all: the command was cut off
        return r

    async def read(self, pkg: str) -> dict | None:
        r = await self.shell(f"{RZ} list")
        rows = [x for x in parse_list(r.out or "") if x.get("pkg") == pkg and "error" not in x]
        return rows[0] if rows else None


def _class_missing(text: str) -> bool:
    t = (text or "").lower()
    return "classnotfound" in t or "could not find or load main class" in t


async def ensure_dex(stick, cfg=None, root: Path | None = None, budget: "_Budget | None" = None) -> bool:
    """Make sure rzseek runs on the stick: a `list` that answers proves it; else push the local dex once (size
    checked) and ask again. False when it can't run there (not built, push failed, refused, hung)."""
    host = getattr(stick, "host", "")
    if host in _DEX:
        return _DEX[host]
    b = budget or _Budget(stick, float(_cfg(cfg, "timeout_s")))
    local = Path(str(_cfg(cfg, "dex_path")))
    if not local.is_absolute():
        local = (root or Path(__file__).resolve().parents[2]) / local
    r = await b.shell(f"{RZ} list")
    if r.ok and not _class_missing(r.out) and not (r.out or "").startswith("timeout/error"):
        size = local.stat().st_size if local.is_file() else None
        if size is not None:
            # an older dex (without a newer verb) answers list too: replace it when the size differs
            sz = await b.shell(f"stat -c %s {DEX_REMOTE} 2>/dev/null")
            have = (sz.out or "").strip()
            if sz.ok and have.isdigit() and int(have) != size:
                await _push(stick, local)
        _DEX[host] = True
        return True
    if b.timed_out:
        return False       # hung: not cached, the next switch asks again
    if not local.is_file():
        log.info("seek_to: %s not built (sh tools/rzseek/build.sh); key presses only", local)
        return False       # not cached: a later build is picked up
    if not await _push(stick, local):
        _DEX[host] = False
        return False
    r = await b.shell(f"{RZ} list")
    _DEX[host] = bool(r.ok and not _class_missing(r.out))
    return _DEX[host]


async def _push(stick, local: Path) -> bool:
    run = getattr(stick, "run", None)
    if run is None:
        return False
    p = await run("push", str(local), DEX_REMOTE, timeout=20)
    if not p.ok:
        log.warning("seek_to: push of rzseek.dex to %s failed: %s", getattr(stick, "name", "") or "stick",
                    (p.out or "")[:120])
    return bool(p.ok)


def key_model(app: str, cfg):
    """How a REWIND press moves this app's picture. YouTube TV: the app's seek model (15 s, first press of a
    batch swallowed, live lab Nov 4). Prime: replay.seek_to.prime_key_step_s (10 s, docs/timing.md), nothing
    swallowed. Both priced with no stall and no late allowance: the fallback never lands short."""
    from redzone.contracts.switching import SeekModel, seek_model
    if app == "prime":
        step = float((cfg.get("replay.seek_to.prime_key_step_s") if cfg is not None else None) or 10.0)
        swallowed = int((cfg.get("replay.seek_to.prime_key_swallowed") if cfg is not None else None) or 0)
        return SeekModel(step=step, swallowed=swallowed, late_ok=0.0)
    m = seek_model(cfg)
    return SeekModel(step=m.step, swallowed=m.swallowed, late_ok=0.0)


async def _keys(stick, back_s: float, cfg, app: str = "youtubetv") -> tuple[bool, int]:
    presses = key_model(app, cfg).sent(back_s) if back_s > 0 else 0
    if not presses:
        return True, 0
    key = (cfg.get("replay.seek_key", "KEYCODE_MEDIA_REWIND") if cfg is not None else None) or "KEYCODE_MEDIA_REWIND"
    r = await stick.shell(" && ".join([f"input keyevent {key}"] * presses), timeout=20)
    return bool(r.ok), presses


async def seek_to(stick, back_s: float, *, app: str, channel: str | None = None, cfg=None,
                  clock: Callable[[], float] | None = None, pkg: str | None = None,
                  allow_keys: bool = True) -> SeekResult:
    """Move the picture back_s seconds (negative = forward). See the module docstring. Never raises."""
    clock = clock or time.monotonic
    t0 = clock()
    res = SeekResult(ok=False, method="none", asked_s=round(float(back_s), 3))
    try:
        await _seek(stick, float(back_s), app, cfg, pkg, allow_keys, res)
    except Exception as e:   # a helper for the live switch path: report, never raise
        log.warning("seek_to failed: %r", e)
        res.ok, res.reason = False, f"exception: {e!r}"[:160]
    res.took_s = clock() - t0
    return res


async def _settled_read(b: _Budget, pkg: str, since_ms: float, timeout_s: float, poll: float) -> tuple[dict | None, bool]:
    """Read the session until it plays again with a position update after `since_ms` (stick clock)."""
    end = time.monotonic() + timeout_s
    last = None
    while True:
        x = await b.read(pkg)
        if x is not None:
            last = x
            if x.get("state") == STATE_PLAYING and float(x.get("updated") or 0) >= since_ms:
                return x, True
        if time.monotonic() >= end or b.timed_out and x is None:
            return last, False
        await asyncio.sleep(poll)


async def _seek(stick, back_s: float, app: str, cfg, pkg: str | None, allow_keys: bool, res: SeekResult) -> None:
    if abs(back_s) < 0.05:
        res.ok, res.method, res.landed_s, res.err_s, res.reason = True, "none", 0.0, 0.0, "nothing to move"
        return
    if cfg is not None and not cfg.get("replay.seek_to.enabled", SEEK_TO_DEFAULTS["enabled"]):
        return await _fallback(stick, back_s, cfg, app, allow_keys, res, "replay.seek_to.enabled is off")
    pkg = pkg or (getattr(stick, "packages", {}) or {}).get(app)
    tol = float(_cfg(cfg, "tolerance_s"))
    timeout = float(_cfg(cfg, "timeout_s"))
    retries = max(0, int(_cfg(cfg, "retries")))
    b = _Budget(stick, timeout)
    if not pkg:
        return await _fallback(stick, back_s, cfg, app, allow_keys, res, f"no package for {app}")
    if not await ensure_dex(stick, cfg, budget=b):
        res.timed_out = b.timed_out
        return await _fallback(stick, back_s, cfg, app, allow_keys, res, "rzseek.dex not runnable on the stick")
    before = await b.read(pkg)
    if before is None:
        res.timed_out = b.timed_out
        return await _fallback(stick, back_s, cfg, app, allow_keys, res, f"no session for {pkg}")
    if not int(before.get("actions") or 0) & SEEK_TO_BIT:
        return await _fallback(stick, back_s, cfg, app, allow_keys, res, "session lacks SEEK_TO")
    res.meta0 = _meta(before)
    playing = before.get("state") == STATE_PLAYING
    reads: list[dict] = []
    now = before
    for _ in range(1 + retries):
        moved = landed_back_s(before, now, playing) or 0.0
        remaining = back_s - moved
        if res.attempts and abs(remaining) <= tol:
            break
        r = await b.shell(f"{RZ} seekrel {pkg} {-int(round(remaining * 1000))}")
        res.attempts += 1
        rows = parse_rows(r.out or "")
        row = next((x for x in rows if "seek_to" in x or "error" in x), None)
        if row is not None and "error" in row:
            res.reason = str(row["error"])[:100]
            break                     # permanent (no session / no SEEK_TO): never retried
        if row is None:
            res.reason = "seek timed out" if b.timed_out else f"seek failed: {(r.out or '')[:80]}"
            if _class_missing(r.out):
                _DEX[getattr(stick, "host", "")] = False
            nxt = await b.read(pkg)   # did it move anyway? re-measure before deciding anything
            if nxt is None:
                break
            now = nxt
            continue
        x, settled = await _settled_read(b, pkg, float(row.get("elapsed") or 0), timeout,
                                         float(_cfg(cfg, "poll_s")))
        if x is None:
            break
        now = x
        reads.append(x)
        if settled and row.get("elapsed") is not None:
            res.settle_s = (res.settle_s or 0.0) + max(0.0, (float(x.get("updated") or 0) - float(row["elapsed"])) / 1000.0)
        res.timed_out = res.timed_out or not settled
        if is_ad_suspect(before, x):
            res.ad_suspect = True
            break
    res.timed_out = res.timed_out or b.timed_out
    final = await b.read(pkg)
    if final is not None:
        now = final
        reads.append(final)
    res.meta1 = _meta(now)
    res.ad_suspect = res.ad_suspect or is_ad_suspect(before, reads)
    landed = landed_back_s(before, now, playing)
    if res.ad_suspect or landed is None or final is None:
        # An ad item's position or an unreadable stick: where the picture is can't be told. Send nothing else.
        res.method = "seek_to" if res.attempts else "none"
        res.ok, res.landed_s = False, landed if not res.ad_suspect else None
        res.reason = res.reason or ("ad after the seek" if res.ad_suspect else "landing unreadable")
        return
    res.landed_s = round(landed, 3)
    res.err_s = res.landed_s - back_s
    moved_any = abs(landed) >= 0.5
    if abs(res.err_s) <= tol and moved_any:
        res.method, res.ok = "seek_to", True
        return
    short = -res.err_s                      # + = not far enough back yet
    if short > tol and back_s > 0 and allow_keys:
        # Short of a rewind (late = the snap could show): price only the REMAINING distance with presses.
        sent, n = await _keys(stick, short, cfg, app)
        res.presses += n
        res.method = "seek_to+keys" if moved_any else "keys"
        res.ok = sent
        res.landed_s = res.err_s = None     # the presses' landing isn't measured here (Controller._seek_sample is)
        res.reason = (res.reason + "; " if res.reason else "") + f"short {short:.1f}s: {n} presses"
        return
    res.method = "seek_to" if moved_any else "none"
    res.ok = False
    res.reason = res.reason or (f"long by {res.err_s:.1f}s" if res.err_s > 0 else f"short {short:.1f}s")


async def _fallback(stick, back_s: float, cfg, app: str, allow_keys: bool, res: SeekResult, why: str) -> None:
    res.reason = why
    if not allow_keys or back_s < 0:
        res.ok, res.method = False, "none"
        return
    sent, n = await _keys(stick, back_s, cfg, app)
    res.ok, res.method, res.presses = sent, "keys", n


def describe(res: SeekResult) -> str:
    if res.method == "keys":
        return f"keys x{res.presses} ({res.reason})"
    if res.landed_s is None:
        return f"{res.method}: {res.reason}"
    return (f"{res.method} {res.landed_s:.1f}s for {res.asked_s:.1f}s (err {res.err_s:+.1f}s, "
            f"{res.attempts} seek{'s' if res.attempts != 1 else ''}"
            + (f", settle {res.settle_s:.1f}s" if res.settle_s is not None else "")
            + (", AD?" if res.ad_suspect else "") + ")")


def as_row(res: SeekResult, **extra: Any) -> dict:
    return {**res.row(), **extra}
