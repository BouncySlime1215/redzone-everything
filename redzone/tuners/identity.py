"""Conservative matchup evidence from fresh provider metadata.

Why: unknown (needs owner)

A matching title identifies the named matchup, not its date, live status, or
video frames. A provider may retain metadata while changing playback; this does
not replace the tuner's transition checks or verify that a switch succeeded.
"""
from __future__ import annotations

import re
import time


def _words(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def matchup_evidence(report: dict, home_names, away_names, *, now=None, max_age_s=3.0) -> dict:
    now = time.monotonic() if now is None else now
    title = str(report.get("title") or "")
    result = {"status": "unknown", "source": "provider_media_metadata", "title": title,
              "event_date_verified": False, "video_verified": False,
              "reason": "fresh matchup metadata unavailable"}
    finished = report.get("read_finished")
    started = report.get("read_started")
    if (not report.get("read_ok") or not isinstance(finished, (int, float))
            or not isinstance(started, (int, float)) or not 0 <= now - started <= max_age_s
            or not started <= finished <= now):
        return result
    normalized = f" {_words(title)} "

    def matches(names):
        # Callers supply explicit full names/mascots. Short abbreviations are too
        # easy to confuse with ordinary UI words (LA, WAS, AT, etc.).
        return any(len(alias := _words(name)) >= 4 and f" {alias} " in normalized for name in names)

    if matches(home_names) and matches(away_names):
        result.update(status="matchup_match", reason="provider title names both expected teams; date and video unverified")
    return result
