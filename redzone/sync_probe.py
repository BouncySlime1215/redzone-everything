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
          r"dropped frames|quality")


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

    async def _logcat(self, stick) -> list[str]:
        back = int(LOG_EVERY_S) + 2
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
