"""[SYNC probe] What the sticks expose for auto-anchors and network features: read-only, automatic.

Nick (Nov 4, 1:45 and 1:50 AM): no manual sync, no extra setup. Delay is re-estimated all night from
anchors the screen gives away (ad breaks, a game clock, captions) and from how the network behaves.
Before any anchor is built, this logs what YouTube TV and Prime actually expose on real sticks.

Per stick, every tick (PROBE_EVERY_S), one row in recordings/<day>/sync_probe.jsonl:
    ms      dumpsys media_session: state, position, updated, speed, title/subtitle/description
    stall   buffering since the last tick: stick-clock elapsed - position elapsed (same stream)
    wifi    RSSI, link speed, frequency (numbers only: no SSID, BSSID or MAC is ever kept)
    ui      the hidden stick's UI tree text, flagged for ad / countdown / game-clock hints
    log     logcat lines since the last read that mention ads, cues, buffering, bitrate or resolution

Never presses a key, launches nothing, changes no setting. Skips a tick while the controller is
switching, flipping or preloading, in dry run, and when the Mac is short of room (governor).
The visible stick's UI tree is only dumped with sync.probe.visible_ui (a dump costs the stick ~1-3 s
of CPU; unmeasured whether that can stutter the picture Nick is watching).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from redzone.tuners.adb import parse_playback

log = logging.getLogger(__name__)

PROBE_EVERY_S = 5.0       # media_session on each stick (cheap: the controller reads it as often)
WIFI_EVERY_S = 15.0
LOG_EVERY_S = 15.0
UI_EVERY_S = 20.0         # uiautomator dump on the hidden stick
UI_KEEP_CHARS = 600
COND_WINDOW_S = 300.0     # [SYNC 4] stalls per minute over this window
RSSI_WEAK = -65           # below this every dB widens the delay's error bar
RSSI_DROP_DB = 10         # a drop this big within RSSI_DROP_WINDOW_S: predicted drift
RSSI_DROP_WINDOW_S = 120.0
LOG_KEEP_LINES = 40

_WIFI = {"rssi": r"RSSI:\s*(-?\d+)", "link_mbps": r"(?<![TR]x )Link speed:\s*(\d+)",
         "tx_mbps": r"Tx Link speed:\s*(\d+)", "rx_mbps": r"Rx Link speed:\s*(\d+)",
         "freq_mhz": r"Frequency:\s*(\d+)"}

UI_HINTS = {
    "ad": re.compile(r"\bAd\b|\bAds?\s*\d+\s*of\s*\d+|Advertisement|Sponsored|Skip ad|"
                     r"will (?:resume|return|be right back)|after (?:this|the) break|commercial", re.I),
    "countdown": re.compile(r"(?<![\d:])0?\d:[0-5]\d(?![\d:])"),
    "clock": re.compile(r"\b(?:Q[1-4]|[1-4](?:st|nd|rd|th)(?:\s+(?:Qtr|Quarter|Half|Period))?|OT|Halftime|"
                        r"End of|Period|Inning|Top|Bot)\b", re.I),
    "score": re.compile(r"\b\d{1,3}\s*[-–]\s*\d{1,3}\b"),
    "captions": re.compile(r"\bCC\b|Subtitles|Captions", re.I),
}

LOG_RE = (r"ad ?break|ad ?pod|adstart|ad_start|adend|ad_end|cue ?in|cue ?out|scte|insertion|"
          r"rebuffer|buffering|stall|bitrate|bandwidth|resolution|videoformat|video format|"
          r"dropped frames|quality|"
          # Live lab Nov 4: YouTube TV's media session republishes its state in bursts (4 updates over
          # ~60 s) at the SAME stream position on both sticks, i.e. tied to the content (candidate ad
          # break / segment marker). The picture is DRM-black in screencaps, so this line is the evidence.
          r"onPlaybackStateChanged")


# [yttv anchors] YouTube TV (Cobalt) draws its own UI: uiautomator sees no text (live lab, Nov 4), so its
# ad edges must come from logcat. Defaults are the generic SCTE / ad-pod words; the probe logs every
# matching line (sync_probe.jsonl "log"), so the real YTTV strings refit these (sync.probe.log_ad_start/end).
LOG_AD_START = r"cue ?out|ad ?break ?start|adstart|ad_start|ad ?pod ?start|onadstart"
LOG_AD_END = r"cue ?in|ad ?break ?end|adend|ad_end|ad ?pod ?end|onadend|onadscomplete"
_EPOCH = re.compile(r"^\s*(\d{9,11}\.\d{1,6})\s")

# [burst anchor] YouTube TV's MediaSession republishes its PLAYING state in bursts tied to the content
# (live lab, Nov 4: 4-8 updates over 1-2 min, 15-30 min apart, at the same stream position on both
# sticks and exactly one hold later on a held stick). The onset is the content anchor. A tune, seek
# or pause also fires the line, with a stopped state or speed 0 first: those are not bursts.
_PSC = re.compile(r"onPlaybackStateChanged.*?state=(\d+), position=(-?\d+).*?speed=(-?[\d.]+)")
BURST_GAP_S = 300.0        # a quiet gap this long before an update makes it a new burst's onset
BURST_AFTER_TUNE_S = 30.0  # updates this soon after a tune, seek or pause are the player settling
BURST_CONFIRM_S = 60.0     # ...and a second update within this confirms it (a burst has 4-8)
BURST_SD_S = 0.3           # the onset's doubt on the stick's own clock (burst vs position: 0.1-0.4 s)


def playback_updates(lines: list[str]) -> list[tuple[float, int, int, float]]:
    """(stick epoch time, state, position ms, speed) of each MediaSession onPlaybackStateChanged line."""
    out = []
    for line in lines:
        m, e = _PSC.search(line), _EPOCH.match(line)
        if m and e:
            out.append((float(e.group(1)), int(m.group(1)), int(m.group(2)), float(m.group(3))))
    return sorted(out)


class BurstWatch:
    """One stick's burst onsets from its MediaSession log lines, fed in order across reads. An onset is
    confirmed by a second playing update within BURST_CONFIRM_S (a lone update is a key press or the
    player settling, not a burst), and reported then with the FIRST update's time and position."""

    def __init__(self) -> None:
        self.last_update: float | None = None     # last playing update that belonged to a burst
        self.settle_until = float("-inf")         # a tune / seek / pause: ignore updates until then
        self.playing = True                       # the last update was a playing one
        self.pending: tuple[float, float] | None = None

    def feed(self, lines: list[str]) -> list[tuple[float, float]]:
        """New confirmed onsets as (stick epoch time, stream position s)."""
        onsets = []
        for t, state, pos, speed in playback_updates(lines):
            if state != 3 or speed <= 0 or pos <= 0:
                self.settle_until, self.last_update, self.pending, self.playing = t + BURST_AFTER_TUNE_S, None, None, False
                continue
            if not self.playing:                  # the PLAY after a pause, seek or tune
                self.settle_until, self.playing = max(self.settle_until, t + BURST_AFTER_TUNE_S), True
            if t < self.settle_until:
                continue
            if self.pending is not None and t - self.pending[0] <= BURST_CONFIRM_S:
                onsets.append(self.pending)
                self.pending = None
            elif self.last_update is None or t - self.last_update >= BURST_GAP_S:
                self.pending = (t, pos / 1000.0)
            self.last_update = t
        return onsets


def log_ad_edges(lines: list[str], start_re: str = LOG_AD_START, end_re: str = LOG_AD_END
                 ) -> list[tuple[float, str]]:
    """(stick epoch time, "break_start" | "break_end") for logcat lines (`-v epoch`) that name an ad
    edge. A line matching both counts as neither (ambiguous)."""
    out = []
    s_rx, e_rx = re.compile(start_re, re.I), re.compile(end_re, re.I)
    for line in lines:
        m = _EPOCH.match(line)
        if not m:
            continue
        st, en = bool(s_rx.search(line)), bool(e_rx.search(line))
        if st != en:
            out.append((float(m.group(1)), "break_start" if st else "break_end"))
    return out


def parse_wifi(text: str) -> dict:
    """Numbers only from `dumpsys wifi`'s mWifiInfo line (never the network's name or addresses)."""
    line = next((x for x in text.splitlines() if "mWifiInfo" in x or "RSSI:" in x), "")
    out = {}
    for key, rx in _WIFI.items():
        m = re.search(rx, line)
        if m:
            out[key] = int(m.group(1))
    return out


def ui_hints(text: str) -> list[str]:
    return [k for k, rx in UI_HINTS.items() if rx.search(text or "")]


def ui_text(xml: str) -> str:
    texts = re.findall(r'(?:text|content-desc)="([^"]+)"', xml) if "<hierarchy" in xml else []
    return " | ".join(t for t in texts if t.strip())


def stall_s(prev: dict | None, cur: dict) -> float | None:
    """Seconds the stream fell behind its own clock since `prev` (same app): a buffer or a pause.
    None = can't tell (other app, no positions, a seek back)."""
    if not prev or prev.get("pkg") != cur.get("pkg"):
        return None
    pa, ua, pn, un = prev.get("position"), prev.get("updated"), cur.get("position"), cur.get("updated")
    if None in (pa, ua, pn, un) or pa <= 0 or pn <= 0 or un < ua:
        return None
    return round(((un - ua) - (pn - pa)) / 1000.0, 2)


class SyncProbe:
    def __init__(self, cfg, control, root: Path | Callable[[], Path] | None = None):
        self.cfg, self.control = cfg, control
        self.root = root
        self._last: dict[str, dict] = {}       # host -> last media_session report
        self._due: dict[tuple[str, str], float] = {}
        self._seen_log: dict[str, list[str]] = {}
        self.rows = 0
        self.governor = None     # the Mac's resource governor (engine): no probing when it is critical
        # [SYNC 4] (target, q_scale, features) per stick per tick -> SyncBook.condition (engine wires it)
        self.on_condition: Callable[[str, float, dict], None] | None = None
        # [auto-anchors] (target, host, visible, "break_start"|"break_end", t, sd) -> engine matches it
        self.on_screen_event: Callable[..., None] | None = None
        self._hist: dict[str, list[tuple[float, float | None, int | None]]] = {}   # host -> [(t, stall, rssi)]
        self._ui_last: dict[str, tuple[float, bool, str | None]] = {}           # host -> (t, ad on, target)
        self._bursts: dict[str, BurstWatch] = {}                                 # host -> its burst onsets
        self._log_read: dict[str, float] = {}                                    # host -> last logcat read

    def enabled(self) -> bool:
        return bool(self.cfg.get("sync.probe.enabled", True)) and not self.cfg.get("mode.dry_run", True)

    def _quiet(self) -> bool:
        c = self.control
        return bool(not getattr(c, "live_control", False) or c._busy() or getattr(c, "_flipping", False)
                    or getattr(c, "input_unknown", False)
                    or (self.governor is not None and not self.governor.allow("ocr")))

    def _is_due(self, host: str, what: str, every: float, now: float) -> bool:
        if now < self._due.get((host, what), 0.0):
            return False
        self._due[(host, what)] = now + every
        return True

    async def _sh(self, stick, cmd: str, timeout: float = 6.0) -> str:
        """Its own adb process: never queues behind (or in front of) the controller's shell."""
        try:
            r = await stick.run("shell", cmd, timeout=timeout)
        except Exception:   # a probe read must never break anything
            log.debug("probe read failed on %s", getattr(stick, "host", "?"), exc_info=True)
            return ""
        return r.out if getattr(r, "ok", False) else ""

    async def tick(self, now: float | None = None) -> list[dict]:
        if not self.enabled() or self._quiet():
            return []
        now = time.time() if now is None else now
        c, rows = self.control, []
        pairs = [(c.main, c.on_target, "visible")]
        if getattr(c, "two_stick", False):
            pairs.append((c.hidden, c.hidden_target, "hidden"))
        for stick, target, role in pairs:
            if stick is None:
                continue
            host = stick.host
            row = {"t": round(now, 2), "host": host, "role": role, "target": target,
                   "app": (target or "").split(":", 1)[0] or None}
            ms = parse_playback(await self._sh(stick, "dumpsys media_session"))
            row["ms"] = {k: ms.get(k) for k in ("state", "pkg", "position", "updated", "speed",
                                                "title", "subtitle", "description") if ms.get(k) is not None}
            row["stall_s"] = stall_s(self._last.get(host), ms)
            row["state_changed"] = (self._last.get(host) or {}).get("updated") not in (None, ms.get("updated"))
            self._last[host] = ms
            if self._is_due(host, "wifi", WIFI_EVERY_S, now):
                row["wifi"] = parse_wifi(await self._sh(stick, "dumpsys wifi | grep -m2 -E 'mWifiInfo|RSSI:'"))
            if self._is_due(host, "log", LOG_EVERY_S, now):
                row["log"] = await self._logcat(stick)
                if self.cfg.get("sync.probe.logcat_anchors", False) and self.on_screen_event is not None:
                    self._log_edges(target, host, role == "visible", row["log"])
                bursts = self._burst_onsets(target, host, role == "visible", row["log"])
                if bursts:
                    row["bursts"] = bursts
            if (role == "hidden" or self.cfg.get("sync.probe.visible_ui", False)) \
                    and self._is_due(host, "ui", UI_EVERY_S, now):
                text = ui_text(await self._sh(
                    stick, "uiautomator dump /sdcard/rz_probe.xml >/dev/null 2>&1 && cat /sdcard/rz_probe.xml",
                    timeout=12))
                row["ui"] = {"chars": len(text), "hints": ui_hints(text), "text": text[:UI_KEEP_CHARS]}
            if self._quiet():
                return rows      # a switch started meanwhile: drop the half-read tick
            row["cond"] = self._condition(host, target, now, row)
            if "ui" in row:
                self._screen_edge(host, target, role == "visible", now, "ad" in row["ui"]["hints"])
            rows.append(row)
        self._write(rows)
        return rows

    def _condition(self, host: str, target: str | None, now: float, row: dict) -> dict:
        """[SYNC 4] Network-condition features for this stick and the process-noise multiplier they
        imply for its channel's delay filter. A stall is a delay jump; a weak or falling Wi-Fi link
        predicts more of them."""
        h = self._hist.setdefault(host, [])
        rssi = (row.get("wifi") or {}).get("rssi")
        h.append((now, row.get("stall_s"), rssi))
        del h[:-200]
        recent = [x for x in h if now - x[0] <= COND_WINDOW_S]
        span_min = max(1.0, (now - recent[0][0]) / 60.0) if recent else 1.0
        stalls = [x[1] for x in recent if x[1] is not None and x[1] >= 0.5]
        rssis = [(t, r) for t, _, r in h if r is not None]
        last_rssi = rssis[-1][1] if rssis else None
        peak = max((r for t, r in rssis if now - t <= RSSI_DROP_WINDOW_S), default=None)
        drop = (peak - last_rssi) if peak is not None and last_rssi is not None else 0
        q = (1.0 + 4.0 * len(stalls) / span_min
             + (max(0, RSSI_WEAK - last_rssi) / 2.0 if last_rssi is not None else 0.0)
             + (8.0 if drop >= RSSI_DROP_DB else 0.0))
        feats = {"stalls_per_min": round(len(stalls) / span_min, 2), "stall_s_sum": round(sum(stalls), 2),
                 "rssi": last_rssi, "rssi_drop": drop, "q_scale": round(q, 2)}
        if target and self.on_condition is not None:
            try:
                self.on_condition(target, q, feats)
            except Exception:
                log.exception("sync condition hand-off failed")
        return feats

    def _screen_edge(self, host: str, target: str | None, visible: bool, now: float, ad: bool) -> None:
        """[auto-anchors] An ad label appearing (break_start) or going (break_end) between two UI reads
        of the same stream: the edge is placed midway, with the read gap's uniform doubt."""
        prev = self._ui_last.get(host)
        self._ui_last[host] = (now, ad, target)
        if prev is None or prev[2] != target or not target or prev[1] == ad:
            return
        gap = now - prev[0]
        if gap > 3 * UI_EVERY_S:
            return           # too long between reads (a switch skipped ticks): the edge is anywhere in it
        if self.on_screen_event is not None:
            try:
                self.on_screen_event(target, host, visible, "break_start" if ad else "break_end",
                                     prev[0] + gap / 2.0, gap / 12 ** 0.5)
            except Exception:
                log.exception("screen event hand-off failed")

    def _log_edges(self, target: str | None, host: str, visible: bool, lines: list[str]) -> None:
        """[yttv anchors] Ad edges from the stick's own log, stamped by the stick's clock (sd ~1 s:
        the player logs the edge as it renders it)."""
        if not target:
            return
        for t, kind in log_ad_edges(lines, str(self.cfg.get("sync.probe.log_ad_start") or LOG_AD_START),
                                    str(self.cfg.get("sync.probe.log_ad_end") or LOG_AD_END)):
            try:
                self.on_screen_event(target, host, visible, kind, t, float(self.cfg.get("sync.probe.log_edge_sd_s")))
            except Exception:
                log.exception("log edge hand-off failed")

    def _burst_onsets(self, target: str | None, host: str, visible: bool, lines: list[str]) -> list[list[float]]:
        """[burst anchor] Content-tied MediaSession bursts on this stick: logged in the probe row, and
        handed to the engine as a "burst" screen event (it logs the feed's dead ball it lines up with;
        the filter gets it only with sync.burst_anchor.enabled)."""
        w = self._bursts.setdefault(host, BurstWatch())
        if target is None:
            w.settle_until = float("inf")     # not a channel we tuned: nothing to anchor
            return []
        if w.settle_until == float("inf"):
            w.settle_until = float("-inf")
        out = w.feed(lines)
        if out and self.on_screen_event is not None and self.cfg.get("sync.probe.bursts", True):
            for t, _pos in out:
                try:
                    self.on_screen_event(target, host, visible, "burst", t, BURST_SD_S)
                except Exception:
                    log.exception("burst hand-off failed")
        return [[round(t, 3), round(p, 3)] for t, p in out]

    async def _logcat(self, stick) -> list[str]:
        # since this stick's last read (ticks skip during switches): a missed line could make a burst's
        # second update look like its onset, 15 s late
        now = time.time()
        last = self._log_read.get(stick.host)
        self._log_read[stick.host] = now
        back = int(min(120.0, (now - last) if last else LOG_EVERY_S)) + 2
        out = await self._sh(stick, f"logcat -d -v epoch -T \"$(( $(date +%s) - {back} )).000\" 2>/dev/null"
                                    f" | grep -iE '{LOG_RE}' | tail -n {LOG_KEEP_LINES}")
        seen = self._seen_log.setdefault(stick.host, [])
        new = [x.strip()[:300] for x in out.splitlines() if x.strip() and x.strip()[:300] not in seen]
        seen.extend(new)
        del seen[:-400]
        return new

    def _write(self, rows: list[dict]) -> None:
        root = self.root() if callable(self.root) else self.root
        if not rows or root is None:
            return
        d = root / "recordings" / datetime.now().strftime("%Y-%m-%d")
        try:
            d.mkdir(parents=True, exist_ok=True)
            with open(d / "sync_probe.jsonl", "a") as f:
                for r in rows:
                    f.write(json.dumps(r, separators=(",", ":")) + "\n")
            self.rows += len(rows)
        except OSError:
            log.exception("could not write the sync probe log")

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("sync probe tick failed")
            await asyncio.sleep(PROBE_EVERY_S)
