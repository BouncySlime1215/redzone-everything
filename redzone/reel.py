"""Catch-up reel: 5+ plays you missed, back to back, in 30-60 s (RedZone's "here's what you missed").

Where clips come from: ESPN's game summary (already polled every few seconds for every live game)
lists in-game highlight clips with direct MP4 links (`videos[].links.source`). They post a few
minutes after the play (median lag on the archive: NBA 4.5 min, NHL 5.5 min, NFL ~14 min), so the
reel is the catch-up for plays older than YouTube TV's rewind replay is worth.

What gets kept of each clip: ESPN's clips open with the live broadcast angle (score bug on screen),
then cut to a graphic and slow-motion replays without the bug. Only the live angle is kept: the
stretch of frames whose bottom band matches the opening frames' static overlay (the score bug),
plus a beat of reaction. Measured on Nov 2-3 clips, the bug drops out at 7-9 s of a 23-38 s clip.

    find_live_end(frames)   per-frame overlay match -> the second the live angle ends
    plan_reel(clips, ...)   which clips and how long each, so 5+ fit in 30-60 s
    ClipCache / build_reel  download + trim once (uniform 720p30 H.264/AAC), then a stream-copy
                            concat: assembling a reel takes well under a second

Everything here fails soft: a clip that won't download, decode or trim is skipped and remembered;
the reel is built from what's ready, and none at all is fine (the decider just doesn't offer one).
"""
from __future__ import annotations

import asyncio
import math
import logging
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
REEL_DIR = ROOT / "data" / "reels"

# Frame analysis: small grayscale frames are plenty to see an overlay come and go.
# (Measured Nov 3 on NFL/NBA/NHL/CBB clips: the live angle ends at 7-13 s of 16-43 s clips.)
AN_W, AN_H, AN_FPS = 384, 216, 4
BAND = 0.75            # the score bug lives in the bottom (or top) quarter of the frame
WARMUP_S = 2.0         # the opening seconds define the overlay (every clip opens on the live angle)
STATIC_STD = 6.0       # a pixel that barely moves over the warm-up is overlay (or still crowd)
END_DIFF = 26.0        # mean abs difference from the overlay that means it's gone
END_RUN = 2            # ...for this many frames in a row (a flash or a wipe isn't the end)
MIN_MASK_PX = 200      # fewer static pixels than this: no overlay to track
TAIL_S = 0.75          # keep a beat of reaction after the live angle ends

SEG_MIN_S, SEG_MAX_S = 5.0, 11.0     # one play: never shorter (no context), never longer (no replays)
FALLBACK_S = 8.0                     # when the overlay can't be tracked: the clip's first 8 s

ENC_W, ENC_H, ENC_FPS = 1280, 720, 30

# What a headline says about the play. Points are rough "how much would you hate to miss it".
KEYWORDS: list[tuple[str, float]] = [
    (r"game[- ]winn|walk[- ]off|buzzer[- ]beat|wins it|go-ahead|ties it|overtime winner|ot winner", 40),
    (r"\btouchdown\b|\btd\b|pick[- ]six|scoop and score|kick(off)? return|punt return", 30),
    (r"interception|\bint\b|picks? off|fumble|strip[- ]sack|safety|blocked (punt|kick|field goal)", 26),
    (r"power[- ]play goal|shorthanded|short-handed|penalty shot|empty[- ]net|hat trick|\bgoal\b|nets|scores", 24),
    (r"grand slam|home run|homer|\bhr\b|walk-off", 26),
    (r"field goal|\bfg\b", 12),
    (r"sack", 14),
    (r"poster|slam|dunk|jam|flush|alley[- ]oop|throws down", 14),
    (r"and-?1|three|3-pointer|triple|deep ball|logo", 12),
    (r"block|swat|steal|save|robs|stop", 10),
    (r"\bdart\b|\bbomb\b|launch|dime|one-handed|incredible|unreal|wild|huge|massive|clutch", 8),
]
SKIP = re.compile(r"press conference|postgame|pregame|interview|reacts|talks|discusses|explains|"
                  r"recap|highlights|top plays|\b(outduels?|beats?|defeats?|tops|routs?|edges?)\b|\b\d{1,3}-\d{1,3}\b|"
                  r"technical after|ejected after|"
                  r"warm[- ]?up|arrives?|walkout|tunnel", re.I)


@dataclass
class Clip:
    id: str
    game_id: str
    league: str
    headline: str
    url: str
    published: float          # epoch seconds
    duration: float
    points: float = 0.0       # importance (bigger = more need-to-see)
    mine: bool = False        # your team's game
    seg: float = FALLBACK_S   # seconds of it the reel would play (after the trim is measured)
    play_at: float | None = None   # when the play happened (matched to the play-by-play), if known


def _epoch(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def headline_points(headline: str) -> float:
    """How need-to-see a play is from its headline: the best keyword match, plus a little for each
    other one (a "strip-sack TD" is more than either). 0 = not a play worth a reel slot."""
    h = headline.lower()
    hits = sorted((p for pat, p in KEYWORDS if re.search(pat, h)), reverse=True)
    if not hits:
        return 0.0
    return hits[0] + 0.25 * sum(hits[1:3])


def _summary_plays(summary: dict) -> list[tuple[float, str, bool]]:
    """(wallclock, text, scoring) for every play in an ESPN summary, any league's layout."""
    plays = []
    try:
        for dr in ((summary.get("drives") or {}).get("previous") or []):
            plays.extend(dr.get("plays") or [])
        plays.extend(summary.get("plays") or [])
    except (AttributeError, TypeError):
        return []
    out = []
    for p in plays:
        if not isinstance(p, dict):
            continue
        t = _epoch(p.get("wallclock"))
        if t is not None and p.get("text"):
            out.append((t, str(p["text"]), bool(p.get("scoringPlay"))))
    return out


def match_play(headline: str, published: float, plays: list[tuple[float, str, bool]]) -> float | None:
    """When the clip's play happened: the play before the clip posted (within an hour) that shares
    the most names with the headline (scoring plays win ties, then the latest). None when no play
    shares at least two names, or one name and a score (a guess is worse than no answer)."""
    names = set(re.findall(r"[A-Z][A-Za-z'\-]{3,}", headline))
    best = None
    for t, text, scoring in plays:
        if not (0 < published - t < 3600):
            continue
        hit = sum(n in text for n in names) + (1 if scoring else 0)
        if hit >= 2 and (best is None or (hit, t) > best):
            best = (hit, t)
    return best[1] if best else None


def clips_from_summary(summary: dict, league: str, game_id: str, my_teams: set[str] = frozenset(),
                       max_s: float = 60.0) -> list[Clip]:
    """In-game play clips from an ESPN summary. Recaps, interviews and anything over max_s are left
    out; a clip with no MP4 link is left out. Never raises on odd data."""
    out: list[Clip] = []
    teams = set()
    try:
        comp = (summary.get("header") or {}).get("competitions", [{}])[0]
        teams = {(c.get("team") or {}).get("abbreviation", "") for c in comp.get("competitors", [])}
    except (AttributeError, IndexError, TypeError):
        pass
    mine = bool(teams & set(my_teams))
    plays = _summary_plays(summary)
    for v in summary.get("videos") or []:
        try:
            head = str(v.get("headline") or "")
            dur = float(v.get("duration") or 0)
            src = (v.get("links") or {}).get("source") or {}
            url = (src.get("HD") or {}).get("href") or src.get("href")
            pub = _epoch(v.get("originalPublishDate") or v.get("lastModified"))
        except (AttributeError, TypeError, ValueError):
            continue
        if not url or not head or pub is None or not (3 <= dur <= max_s) or SKIP.search(head):
            continue
        pts = headline_points(head)
        if pts <= 0:
            continue
        c = Clip(str(v.get("id") or url), game_id, league, head, url, pub, dur, pts, mine)
        c.play_at = match_play(head, pub, plays)
        out.append(c)
    return out


# --- trimming: where the live angle ends ---------------------------------------------------------

def find_live_end(frames: np.ndarray, fps: float = AN_FPS) -> float | None:
    """frames: (n, h, w) grayscale. The second the opening overlay (score bug) disappears, or None
    when there's no overlay to track (then the caller uses a fixed cut).

    The bug is at the bottom in football/basketball and at the TOP in hockey, and either band can
    hold still scenery for a second or two (a pre-snap field, rink boards). So both bands are
    tracked and the one that stays truest to its opening look for the 2 s after the warm-up is the
    overlay: real overlays don't drift, scenery does."""
    if frames.ndim != 3 or len(frames) < int(WARMUP_S * fps) + END_RUN:
        return None
    f = frames.astype(np.float32)
    h = f.shape[1]
    w0 = int(WARMUP_S * fps)
    best = None   # (drift over the check window, diff series)
    for y0, y1 in ((int(h * BAND), h), (0, int(h * (1 - BAND)))):
        warm = f[:w0, y0:y1, :]
        mask = warm.std(0) < STATIC_STD
        if mask.sum() < MIN_MASK_PX:
            continue
        tmpl = np.median(warm, 0)
        diff = np.array([np.abs(fr[y0:y1, :] - tmpl)[mask].mean() for fr in f])
        drift = float(diff[w0:w0 + int(2 * fps)].mean()) if len(diff) > w0 else 0.0
        if best is None or drift < best[0]:
            best = (drift, diff)
    if best is None:
        return None
    gone = best[1] >= END_DIFF
    for i in range(w0, len(gone) - END_RUN + 1):
        if gone[i:i + END_RUN].all():
            return i / fps
    return None   # the overlay never left: the whole clip is the live angle


def _frames(path: Path, timeout: float = 30.0) -> np.ndarray | None:
    try:
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-t", "30",
                            "-vf", f"fps={AN_FPS},scale={AN_W}:{AN_H},format=gray",
                            "-f", "rawvideo", "-"], capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("reel: can't read frames of %s: %s", path.name, e)
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    n = len(r.stdout) // (AN_W * AN_H)
    return np.frombuffer(r.stdout[: n * AN_W * AN_H], np.uint8).reshape(n, AN_H, AN_W)


def live_window(end: float | None, duration: float) -> tuple[float, float]:
    """(start, length) of the part worth playing: the live angle plus a beat, SEG_MIN..SEG_MAX s.
    A long live angle loses its START (the pre-snap / set-up), never the play's end."""
    stop = (end + TAIL_S) if end is not None else FALLBACK_S
    stop = max(SEG_MIN_S, min(stop, duration if duration > 0 else stop))
    start = max(0.0, stop - SEG_MAX_S)
    return start, stop - start


# --- choosing the reel --------------------------------------------------------------------------

def plan_reel(clips: list[Clip], *, now: float, seen=lambda c: False, league_weight: dict | None = None,
              min_clips: int = 5, min_s: float = 30.0, max_s: float = 60.0, max_age_s: float = 1800.0,
              per_game: int = 2) -> list[Clip]:
    """The reel: the most need-to-see unseen plays, 5+ of them, 30-60 s in all.

    Value of a clip = points x league weight x freshness (older plays fade over max_age_s), your
    team's plays first. At most per_game plays from one game (a reel is a tour, not one game's
    recap), unless there aren't enough games. If the picks run over max_s, every clip is shortened
    from its start (set-up, not the play) down to SEG_MIN_S; if 5 still don't fit, the least valuable
    go. Fewer than min_clips worth showing, or under min_s in all: no reel ([]), not a thin one."""
    lw = league_weight or {}
    pool = []
    for c in clips:
        age = now - c.published
        if age < 0 or age > max_age_s or seen(c):
            continue
        fresh = 1.0 - 0.5 * (age / max_age_s)
        pool.append((c.mine, c.points * float(lw.get(c.league, 1.0)) * fresh, c))
    pool.sort(key=lambda t: (t[0], t[1]), reverse=True)
    # De-dupe: ESPN sometimes posts one play twice (two angles / a re-cut).
    picked, per, heads = [], {}, set()
    for mine, val, c in pool:
        key = re.sub(r"[^a-z0-9]", "", c.headline.lower())[:40]
        if key in heads:
            continue
        if per.get(c.game_id, 0) >= per_game:
            continue
        picked.append(c)
        per[c.game_id] = per.get(c.game_id, 0) + 1
        heads.add(key)
    if len(picked) < min_clips:      # not enough games: let a game give more plays
        for mine, val, c in pool:
            if c not in picked and re.sub(r"[^a-z0-9]", "", c.headline.lower())[:40] not in heads:
                picked.append(c)
                heads.add(re.sub(r"[^a-z0-9]", "", c.headline.lower())[:40])
    reel: list[Clip] = []
    total = 0.0
    for c in picked:
        if total + SEG_MIN_S > max_s:
            break
        reel.append(c)
        total += c.seg
    if len(reel) < min_clips:
        return []
    # Fit max_s: shave every clip's set-up evenly, never below SEG_MIN_S.
    over = sum(c.seg for c in reel) - max_s
    if over > 0:
        room = [max(0.0, c.seg - SEG_MIN_S) for c in reel]
        total_room = sum(room) or 1.0
        for c, r in zip(reel, room):
            c.seg = round(c.seg - over * r / total_room, 2)
    if sum(c.seg for c in reel) < min_s:
        return []
    return reel


# --- building it ----------------------------------------------------------------------------------

class ClipCache:
    """Downloads and trims each clip once, into a uniform encoding, so a reel is a stream copy.
    Bounded on disk; a clip that fails is skipped for a while, not retried every poll."""

    def __init__(self, root: Path = REEL_DIR, max_mb: float = 400.0, min_free_mb: float = 1500.0,
                 fetch=None, clock=time.time):
        self.root = root
        self.dir = root / "clips"
        self.max_mb = max_mb
        self.min_free_mb = min_free_mb
        self._fetch = fetch            # async (url, dest) -> bool; default uses httpx
        self._clock = clock
        self.ready: dict[str, tuple[Path, float]] = {}   # clip id -> (trimmed file, its length)
        self.failed: dict[str, float] = {}                # clip id -> when (retry after 10 min)
        self._busy: set[str] = set()

    def ffmpeg_ok(self) -> bool:
        return shutil.which("ffmpeg") is not None

    def disk_ok(self) -> bool:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            return shutil.disk_usage(self.root).free / 1e6 >= self.min_free_mb
        except OSError:
            return False

    async def _download(self, url: str, dest: Path) -> bool:
        if self._fetch is not None:
            return await self._fetch(url, dest)
        import httpx
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, read=40.0), follow_redirects=True) as cl:
                async with cl.stream("GET", url) as r:
                    if r.status_code != 200:
                        return False
                    n = 0
                    with dest.open("wb") as f:
                        async for chunk in r.aiter_bytes(1 << 16):
                            n += len(chunk)
                            if n > 120e6:          # a "clip" this big is something else
                                return False
                            f.write(chunk)
            return dest.stat().st_size > 10_000
        except (httpx.HTTPError, OSError) as e:
            log.info("reel: download failed %s: %s", url, e)
            return False

    async def prepare(self, c: Clip) -> bool:
        """Make c ready (download, measure the live angle, trim). True when it's ready."""
        if c.id in self.ready:
            c.seg = self.ready[c.id][1]
            return True
        if c.id in self._busy or self._clock() - self.failed.get(c.id, -1e9) < 600:
            return False
        if not self.ffmpeg_ok() or not self.disk_ok():
            return False
        self._busy.add(c.id)
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", c.id)[:60]
        raw, out = self.dir / f"{safe}.src.mp4", self.dir / f"{safe}.mp4"
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            if not await self._download(c.url, raw):
                raise RuntimeError("download")
            frames = await asyncio.to_thread(_frames, raw)
            end = find_live_end(frames) if frames is not None else None
            start, length = live_window(end, c.duration)
            ok = await asyncio.to_thread(_encode, raw, out, start, length)
            if not ok:
                raise RuntimeError("encode")
            self.ready[c.id] = (out, length)
            c.seg = length
            log.info("reel: ready %s (%s, live angle ends %s s, keep %.1f s)", c.headline, c.league,
                     None if end is None else round(end, 1), length)
            return True
        except Exception as e:   # one bad clip never stops the others
            self.failed[c.id] = self._clock()
            log.info("reel: skipped %s: %s", c.headline, e)
            out.unlink(missing_ok=True)
            return False
        finally:
            raw.unlink(missing_ok=True)
            self._busy.discard(c.id)
            self._evict()

    def _evict(self) -> None:
        try:
            files = sorted(self.dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
        except OSError:
            return
        total = sum(p.stat().st_size for p in files) / 1e6
        for p in files:
            if total <= self.max_mb:
                break
            if p.name.endswith(".src.mp4"):
                continue
            total -= p.stat().st_size / 1e6
            p.unlink(missing_ok=True)
            for k, (f, _) in list(self.ready.items()):
                if f == p:
                    del self.ready[k]


def _encode(src: Path, dst: Path, start: float, length: float, timeout: float = 60.0) -> bool:
    """One uniform piece: 720p30 H.264 + 48 kHz stereo AAC, short audio fades so cuts don't pop.
    Hardware encoder first (fast on Apple silicon), software if it isn't there."""
    fade = 0.15
    af = f"afade=t=in:d={fade},afade=t=out:st={max(0.0, length - fade):.2f}:d={fade},aresample=48000"
    vf = f"scale={ENC_W}:{ENC_H}:force_original_aspect_ratio=decrease,pad={ENC_W}:{ENC_H}:(ow-iw)/2:(oh-ih)/2,fps={ENC_FPS},format=yuv420p"
    for venc in (["-c:v", "h264_videotoolbox", "-b:v", "5M"], ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21"]):
        cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-i", str(src), "-t", f"{length:.2f}",
               "-vf", vf, "-af", af, *venc, "-c:a", "aac", "-b:a", "160k", "-ac", "2",
               "-movflags", "+faststart", str(dst)]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0 and dst.exists() and dst.stat().st_size > 10_000:
            return True
    dst.unlink(missing_ok=True)
    return False


def build_reel(parts: list[tuple[Path, float]], dst: Path, timeout: float = 20.0) -> bool:
    """Concatenate ready pieces (same encoding) with a stream copy. Uses a temp file + rename so a
    player never opens a half-written reel."""
    if not parts:
        return False
    lst = dst.with_suffix(".txt")
    tmp = dst.with_suffix(".tmp.mp4")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        lst.write_text("".join(f"file '{p.resolve()}'\n" for p, _ in parts))
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                            "-c", "copy", "-movflags", "+faststart", str(tmp)], capture_output=True, timeout=timeout)
        if r.returncode != 0 or not tmp.exists():
            log.warning("reel: concat failed: %s", r.stderr[-300:])
            return False
        tmp.replace(dst)
        return True
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("reel: concat failed: %s", e)
        return False
    finally:
        lst.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)


@dataclass
class Reel:
    path: Path
    seconds: float
    clips: list[dict] = field(default_factory=list)
    built_at: float = 0.0


class Reeler:
    """Keeps a reel ready at all times so playing one never waits on a download.

    refresh(summaries, ...) is called from the poll loop: it finds new clips, prepares up to `parallel`
    at once in the background, and rebuilds the reel whenever the best set changes. `current` is the
    newest good reel (or None)."""

    def __init__(self, cfg, cache: ClipCache | None = None, clock=time.time):
        self.cfg = cfg
        self.cache = cache or ClipCache(max_mb=float(cfg.get("reel.cache_mb", 400) or 400),
                                        min_free_mb=float(cfg.get("reel.min_free_mb", 1500) or 1500), clock=clock)
        self._clock = clock
        self.clips: dict[str, Clip] = {}
        self.current: Reel | None = None
        self.shown: set[str] = set()           # clip ids already played in a reel
        self._tasks: set[asyncio.Task] = set()
        self._sig: tuple = ()

    def _c(self, key, default):
        v = self.cfg.get(f"reel.{key}", default)
        return default if v is None else v

    def enabled(self) -> bool:
        return bool(self._c("enabled", True))

    def ingest(self, summaries: dict, leagues: dict, my_teams: dict) -> int:
        """summaries: game_id -> raw ESPN summary; leagues: game_id -> league value."""
        n = 0
        for gid, data in list(summaries.items()):
            lg = leagues.get(gid)
            if not lg or not isinstance(data, dict):
                continue
            for c in clips_from_summary(data, lg, gid, set(my_teams.get(lg, ()))):
                if c.id not in self.clips:
                    self.clips[c.id] = c
                    n += 1
        # Forget clips too old to ever be picked.
        cutoff = self._clock() - float(self._c("max_age_s", 1800)) - 600
        for k in [k for k, c in self.clips.items() if c.published < cutoff]:
            del self.clips[k]
        return n

    def _plan(self, seen) -> list[Clip]:
        lw = self.cfg.get("score.league_weight") or {}
        return plan_reel(list(self.clips.values()), now=self._clock(),
                         seen=lambda c: c.id in self.shown or seen(c), league_weight=lw,
                         min_clips=int(self._c("min_clips", 5)), min_s=float(self._c("min_s", 30)),
                         max_s=float(self._c("max_s", 60)), max_age_s=float(self._c("max_age_s", 1800)))

    async def refresh(self, seen=lambda c: False) -> Reel | None:
        if not self.enabled():
            return None
        # Prepare the best candidates (a few more than a reel needs, so one failure doesn't empty it).
        want = sorted((c for c in self.clips.values() if c.id not in self.shown and not seen(c)),
                      key=lambda c: (c.mine, c.points), reverse=True)[: int(self._c("min_clips", 5)) * 2 + 2]
        par = int(self._c("parallel", 2))
        for c in want:
            if len(self._tasks) >= par:
                break
            if c.id in self.cache.ready or c.id in self.cache._busy:
                continue
            t = asyncio.create_task(self.cache.prepare(c))
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)
        ready = {cid: v for cid, v in self.cache.ready.items()}
        for cid, (_, seg) in ready.items():
            if cid in self.clips:
                self.clips[cid].seg = seg
        plan = [c for c in self._plan(seen) if c.id in ready]
        if not plan or len(plan) < int(self._c("min_clips", 5)):
            plan = self._plan(lambda c: seen(c) or c.id not in ready)
        if not plan:
            self.current = None
            return None
        sig = tuple((c.id, c.seg) for c in plan)
        if sig == self._sig and self.current and self.current.path.exists():
            return self.current
        dst = self.cache.root / "reel.mp4"
        parts = []
        for c in plan:
            path, full = ready[c.id]
            parts.append((path, c.seg))
        # Pieces are trimmed to their full live window; a reel that had to shave clips re-trims them.
        if any(abs(c.seg - ready[c.id][1]) > 0.3 for c in plan):
            parts = await asyncio.to_thread(self._shave, plan, ready)
        ok = await asyncio.to_thread(build_reel, parts, dst)
        if not ok:
            return self.current
        self._sig = sig
        self.current = Reel(dst, round(sum(s for _, s in parts), 1),
                            [{"id": c.id, "game_id": c.game_id, "league": c.league, "headline": c.headline,
                              "seconds": c.seg, "mine": c.mine, "play_at": c.play_at,
                              "value": clip_value(c, self._clock(), float(self._c("max_age_s", 1800)),
                                                  self.cfg.get("score.league_weight") or {})}
                             for c in plan], self._clock())
        return self.current

    def _shave(self, plan: list[Clip], ready: dict) -> list[tuple[Path, float]]:
        out = []
        for c in plan:
            src, full = ready[c.id]
            if full - c.seg <= 0.3:
                out.append((src, full))
                continue
            dst = src.with_name(src.stem + f".s{int(c.seg * 10)}.mp4")
            if dst.exists() or _encode(src, dst, full - c.seg, c.seg):
                out.append((dst, c.seg))
            else:
                out.append((src, full))
        return out

    def offer(self, target: str) -> "ReelOffer | None":
        """The current reel as a ReelOffer (clip by clip, so the decider can cut it to fit)."""
        r = self.current
        if r is None or not r.clips:
            return None
        return ReelOffer(target, tuple((float(c["seconds"]), float(c.get("value", self._c("score", 40))))
                                       for c in r.clips),
                         int(self._c("min_clips", 5)), float(self._c("min_s", 30)),
                         tuple((c["game_id"], c.get("play_at")) for c in r.clips))

    def played(self, reel: Reel, n: int | None = None) -> None:
        """A reel aired: its plays (the first n, when it was cut to fit) are seen now and won't come back."""
        self.shown.update(c["id"] for c in (reel.clips if n is None else reel.clips[:n]))
        self._sig = ()
        self.current = None


# --- when to jump to the reel, and when to stay ------------------------------------------------
#
# A pure function of what it's handed: no decider, no clock, no I/O. The decider (and later the one
# decision core) builds the three inputs and acts on the answer; the scorecards and the offline
# policy fit (redzone/lab/reeljump.py) call the same function, so what's measured is what runs.

@dataclass(frozen=True)
class ReelOffer:
    """A ready reel. clips: (seconds, value 0-100) per clip in play order, most need-to-see first,
    so any prefix is the best reel of that length (the player stops at a clip boundary: no re-encode
    is needed to cut it down). value = importance x freshness on the scorer's 0-100 scale."""
    target: str
    clips: tuple[tuple[float, float], ...]
    min_clips: int = 5
    min_s: float = 30.0
    plays: tuple[tuple[str, float | None], ...] = ()   # (game_id, when the play happened) per clip, if known

    @property
    def seconds(self) -> float:
        return round(sum(s for s, _ in self.clips), 2)

    @classmethod
    def whole(cls, target: str, seconds: float, value: float) -> "ReelOffer":
        """A reel known only by its length (one block, can't be cut down)."""
        return cls(target, ((float(seconds), float(value)),), 1, 0.0)


@dataclass(frozen=True)
class JumpWindow:
    """The dead ball we'd leave in. dead_s is the cautious end of its remaining length on screen
    (the p10 quantile once the break model has one; today the floor-based estimate with the delay's
    2 sigma taken off); the two switch costs are this path's (seconds of black each way)."""
    dead_s: float
    out_s: float
    back_s: float
    margin_s: float = 8.0

    @property
    def room_s(self) -> float:
        return self.dead_s - self.out_s - self.back_s - self.margin_s


@dataclass(frozen=True)
class StayView:
    """What staying is worth. live_value: the game on screen (0-100). key_soon_p / key_rate: the
    brain's chance of a key moment soon and the league's usual rate (None = no reading).
    best_visit: the best live game a break visit could go to instead (-1 = none worth it)."""
    live_value: float
    key_soon_p: float | None = None
    key_rate: float | None = None
    crunch: bool = False
    mine_late: bool = False
    must_see: bool = False
    best_visit: float = -1.0


@dataclass(frozen=True)
class ReelJump:
    ev_reel: float      # what the reel is worth (0-100, per second of the window)
    ev_stay: float      # what staying is worth over the same window
    cost_s: float       # seconds of black (both switches)
    ok: bool            # jump now
    reason: str
    n_clips: int = 0    # how many clips of the offer to play
    reel_s: float = 0.0  # ...and how long that is


@dataclass(frozen=True)
class JumpPolicy:
    """The tunable part (reel.jump.* in the config; fit offline by redzone/lab/reeljump.py)."""
    key_ratio: float = 1.5      # a key moment this many times likelier than the league's usual rate: stay
    key_p_no_rate: float = 0.8  # ...or, with no usual rate to compare against, at least this likely
    tail_p: float = 0.10        # chance the dead ball ends before the cautious estimate (p10)
    min_gain: float = 5.0       # the reel must beat staying by this much


def fit_reel(offer: ReelOffer, room_s: float) -> tuple[int, float]:
    """The longest prefix of the offer that fits room_s: (clips, seconds); (0, 0) when no prefix
    has min_clips clips and min_s seconds."""
    n, total = 0, 0.0
    for s, _ in offer.clips:
        if total + s > room_s + 1e-6:
            break
        n, total = n + 1, total + s
    if n < max(1, offer.min_clips) or total < offer.min_s:
        return 0, 0.0
    return n, round(total, 2)


def jump_value(world: StayView, offer: ReelOffer | None, window: JumpWindow,
               policy: JumpPolicy = JumpPolicy()) -> ReelJump:
    """Jump to the reel now, or stay?

    Hard stays first (crunch time, your team late, must-see, a big moment likely soon), then the reel
    has to fit the dead ball's cautious remaining length with both switches and a margin, then it has
    to be worth more than staying (the live picture is a dead ball, so staying is worth the chance the
    restart comes early times the game's value) and at least as much as the best live visit."""
    cost = max(0.0, window.out_s) + max(0.0, window.back_s)

    def no(why: str, ev_r: float = 0.0, ev_s: float = 0.0) -> ReelJump:
        return ReelJump(round(ev_r, 1), round(ev_s, 1), round(cost, 1), False, why)

    if offer is None or not offer.clips:
        return no("no reel ready")
    if world.crunch:
        return no("crunch time: staying")
    if world.mine_late:
        return no("your team, late and close: staying")
    if world.must_see:
        return no("must-see moment: staying")
    p = world.key_soon_p
    bar = min(0.95, policy.key_ratio * world.key_rate) if world.key_rate else policy.key_p_no_rate
    if p is not None and p >= bar:
        return no(f"a big moment is likely soon here ({p:.0%}): staying")
    if not (math.isfinite(window.dead_s) and math.isfinite(cost)):
        return no("no timing for this dead ball")
    n, secs = fit_reel(offer, window.room_s)
    if n == 0:
        return no(f"reel doesn't fit the dead ball ({max(0.0, window.dead_s):.0f}s left, "
                  f"{cost:.0f}s of switching, {window.margin_s:.0f}s margin)")
    used = offer.clips[:n]
    ev_reel = sum(s * v for s, v in used) / max(1e-6, secs)
    risk = policy.tail_p * (1.0 + (p or 0.0))
    ev_stay = risk * max(0.0, world.live_value)
    if ev_reel < ev_stay + policy.min_gain:
        return no("staying is worth more than the reel", ev_reel, ev_stay)
    if world.best_visit >= 0 and world.best_visit > ev_reel:
        return no("a live game is worth more than the reel", ev_reel, ev_stay)
    return ReelJump(round(ev_reel, 1), round(ev_stay, 1), round(cost, 1), True,
                    f"catch-up reel: {n} plays you missed ({secs:.0f}s) in a {window.dead_s:.0f}s dead ball",
                    n, secs)


def reel_rate(j: ReelJump, offer: ReelOffer | None) -> float:
    """Value per second of a planned reel (all its clips' values over its time incl. both switches):
    what a single catch-up replay must beat, per second, to be shown instead."""
    if not j.ok or offer is None or j.n_clips <= 0:
        return 0.0
    return sum(v for _, v in offer.clips[:j.n_clips]) / max(1.0, j.reel_s + j.cost_s)


def clip_value(c: Clip, now: float, max_age_s: float = 1800.0, league_weight: dict | None = None) -> float:
    """A clip on the scorer's 0-100 scale: headline points (a TD is 30, a game-winner 40+) x 2.5,
    times freshness (halves over max_age_s) and the league's weight; your team's plays +15."""
    age = max(0.0, now - c.published)
    fresh = 1.0 - 0.5 * min(1.0, age / max(1.0, max_age_s))
    v = min(100.0, 2.5 * c.points) * fresh * float((league_weight or {}).get(c.league, 1.0))
    return round(min(100.0, v + (15.0 if c.mine else 0.0)), 1)


def offer_of(target: str, plan: list[Clip], now: float, *, min_clips: int = 5, min_s: float = 30.0,
             max_age_s: float = 1800.0, league_weight: dict | None = None) -> ReelOffer | None:
    """The ReelOffer for a planned reel (plan_reel order: your team first, then value)."""
    if not plan:
        return None
    return ReelOffer(target, tuple((float(c.seg), clip_value(c, now, max_age_s, league_weight)) for c in plan),
                     min_clips, min_s, tuple((c.game_id, c.play_at) for c in plan))
