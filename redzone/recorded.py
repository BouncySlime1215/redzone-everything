"""Explicit recorded-game navigation, isolated from live decisions and calibration.

This session never emits a successful live tune or treats yesterday's events as
current. A player title is menu/media evidence, not verification of video frames.

Why: first added in f98e81ba (2026-11-02) as "TV input flip via HOME (CEC Active Source) with stick-side verification; YouTube TV codes verifie..."; no separate decision is recorded for this module.
"""
from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
import re
import tempfile
import time

from redzone.machines.platform import RECORDED_SESSION
from redzone.screen_ocr import OCRResult, prepare, recognize
from redzone.tuners.adb import ADB
from redzone.tuners.prime_search import CapturedScreen, SearchProof, run_prime_search


async def _capture(host: str) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        ADB, "-s", host, "exec-out", "screencap", "-p",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 12)
    except BaseException:
        if proc.returncode is None:
            proc.kill()
        await proc.communicate()
        raise
    if proc.returncode or not out.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("fresh menu capture failed")
    return out


class RecordedPlayback:
    def __init__(self, engine, *, runner=run_prime_search, capture=_capture, ocr=recognize,
                 prepare_ocr=prepare):
        self.engine = engine
        self.runner, self.capture, self.ocr = runner, capture, ocr
        self.prepare_ocr = prepare_ocr
        # recorded.session: idle -> preparing -> navigating -> finished | failed -> (Stop) idle.
        self.session = RECORDED_SESSION.new()
        self.request: dict = {}
        self.proof = SearchProof()
        self.task: asyncio.Task | None = None
        self._transition = asyncio.Lock()
        self._guard_path = Path(engine.cfg.user_path).with_name("recorded_session.active")
        if self._guard_path.exists():
            # The previous process may have died while a recording was playing.
            # Reset persisted hardware authorization before any engine loop starts.
            engine.cfg.update({"mode": {"dry_run": True}})
            engine.decider.user.paused = True
            self._guard_path.unlink(missing_ok=True)

    @property
    def active(self) -> bool:
        """A recorded session holds the screen (anything but idle): live work and device jobs stay off."""
        return not self.session.is_("idle")

    @active.setter
    def active(self, on: bool) -> None:   # kept for callers/tests that flip the old flag directly
        if on and self.session.is_("idle"):
            self.session.to("preparing", "set active directly")
        elif not on and not self.session.is_("idle"):
            self.session.to("idle", "set inactive directly")

    def snapshot(self) -> dict:
        p = self.proof
        return {"active": self.active, "navigating": bool(self.task and not self.task.done()),
                "session": self.session.view(history=0),
                "device_control_allowed": getattr(self.engine.cfg, "allow_live", True) is not False,
                "mode": "recorded", "request": dict(self.request), "status": p.status,
                "reason": p.reason, "stage": p.last_completed_stage, "elapsed_s": p.elapsed_s,
                "event_date_verified": p.event_date_verified,
                "identity": "player_title_observed" if p.status == "player_title_observed" else "unverified",
                "media_state": "playing_observed" if p.status == "player_title_observed" else "unknown",
                "video_verified": False, "live_delay_applicable": False}

    def _forget_live(self):
        e, c, d = self.engine, self.engine.control, self.engine.decider
        e._pending_decision = None  # canceled callbacks cannot commit a stale live proposal
        d.user.paused = True
        d.abort_replay()
        d.current = None
        d._screen_log.clear()
        d._arrival_until = 0.0
        d.after_replay = False
        d.replay_exit_after = 0.0
        c.on_target = None
        c._set_hidden(None, False)
        c.last = None
        c.stage = {"stage": "recorded", "target": None,
                   "reason": "recorded content; live timing does not apply"}
        e.mic_suggestion = None

    async def start(self, body: dict) -> tuple[bool, str]:
        async with self._transition:
            e, c = self.engine, self.engine.control
            if self.active:
                return False, "Stop the current recorded session first"
            if getattr(e.cfg, "allow_live", True) is False:
                return False, "This service enforces dry-run; recorded device control is unavailable"
            if body.get("execute") is not True or body.get("provider") != "prime":
                return False, "Explicit recorded Prime device-control authorization is required"
            was_dry = e.cfg.get("mode.dry_run", True)
            if was_dry and body.get("enable_device_control") is not True:
                return False, "Explicitly authorize enabling device control for this recorded session"
            if not was_dry and not e.decider.user.paused:
                return False, "Pause automatic live switching before starting a recorded session"
            if c._flipping or c._lock.locked():
                return False, "A device operation is still running; wait for it to finish"
            stick = next((s for s in c.sticks if s.host == body.get("host")), None)
            if stick is None or not stick.packages.get("prime") or "vega" in stick.os.lower():
                return False, "Choose a saved Fire OS stick with Prime identified; Vega is unsupported by this app"
            try:
                requested = date.fromisoformat(body.get("date", ""))
                home, away, query = (body.get(k, "") for k in ("home", "away", "query"))
                if (not all(isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9 .'-]{4,90}", v)
                            for v in (home, away)) or home.casefold() == away.casefold()
                        or not isinstance(query, str) or not re.fullmatch(r"[A-Za-z0-9 .-]{3,70}", query)):
                    raise ValueError
                if requested > date.today() or requested.year != date.today().year:
                    raise ValueError
            except (ValueError, TypeError):
                return False, "Supply distinct displayed team names, a simple query, and an exact recording date in this year"
            self.session.to("preparing", f"recorded {requested.isoformat()} on {stick.host}")  # blocks new engine/device jobs before any await
            self.request = {"provider": "prime", "host": stick.host, "date": requested.isoformat(),
                            "home": home, "away": away, "query": query}
            self.proof = SearchProof(requested_date=requested.isoformat(), reason="preparing recorded session")
            self._forget_live()
            c.cancel_pending()
            pending = [t for t in (c._task, c._preload_task, getattr(e, "_house_device_task", None),
                                  getattr(e, "_st_scan_task", None))
                       if t and not t.done()]
            for task in pending:
                task.cancel()
            try:
                self._guard_path.parent.mkdir(parents=True, exist_ok=True)
                self._guard_path.write_text("recorded session: restart with device control off\n")
                await asyncio.gather(*pending, return_exceptions=True)
                await e.discovery.suspend_device_work()
            except Exception as exc:
                self.proof.reason = f"Recorded device control could not start: {exc}"
                # No navigation has started. Keep isolation until Stop, including
                # when a runtime policy disallows enabling hardware control.
                self.session.to("failed", self.proof.reason)
                e.broadcast()
                return False, self.proof.reason
            except BaseException:
                self.proof.reason = "recorded preparation interrupted; automatic switching remains paused"
                self.session.to("failed", self.proof.reason)
                raise
            self.session.to("navigating", "navigation task started")
            self.task = asyncio.create_task(self._run(stick, requested, was_dry))
            e.broadcast()
            return True, "Recorded navigation started on the selected stick; automatic switching is paused"

    async def _run(self, stick, requested, enable_control):
        e = self.engine

        def isolated():
            return self.active and e.decider.user.paused and stick in e.control.sticks

        def may_run():
            return isolated() and not e.cfg.get("mode.dry_run", True) and stick in e.control.usable

        started = time.monotonic()
        ended = "failed"   # until the runner returns on its own
        try:
            async with e.control._lock:
                if not isolated():
                    raise RuntimeError("recorded session is no longer isolated")
                self.proof.reason = "Preparing local screen reader before any device action"
                self.proof.last_completed_stage = "preparing_local_ocr"
                preparation = await asyncio.to_thread(self.prepare_ocr)
                if preparation.status != "ready":
                    self.proof.reason = "Local screen reader is not ready: " + (preparation.error or preparation.status)
                    return
                self.proof.last_completed_stage = "local_ocr_ready"
                if not isolated():
                    raise RuntimeError("recorded session is no longer isolated")
                # Runtime policy is checked BEFORE even a selected-device
                # reconnect. A preview/service that enforces dry run stays inert.
                if enable_control:
                    try:
                        e.cfg.update({"mode": {"dry_run": False}})
                    except Exception as exc:
                        self.proof.reason = f"Recorded device control could not start: {exc}"
                        return
                if e.cfg.get("mode.dry_run", True):
                    raise RuntimeError("device control is disabled by this runtime")
                self.proof.last_completed_stage = "checking_selected_connection"
                self.proof.reason = "Checking only the selected saved stick"
                if not await stick.connect():
                    self.proof.reason = f"Selected stick is {stick.state}: {stick.connect_error or 'connection unavailable'}"
                    return
                if not may_run():
                    raise RuntimeError("selected stick is not available for recorded control")
                if stick.model:
                    model = await stick.shell("getprop ro.product.model", timeout=5)
                    if not model.ok or model.out.strip() != stick.model:
                        self.proof.reason = "Selected address did not verify the saved Fire TV model; no navigation attempted"
                        return
                self.proof.last_completed_stage = "selected_connection_verified"
                stick.preferred_profiles = dict(e.cfg.get("providers.profiles", {}) or {})
                with tempfile.TemporaryDirectory(prefix="redzone-recorded-") as directory:
                    image = Path(directory) / "menu.png"
                    sequence = 0

                    async def observe():
                        nonlocal sequence
                        sequence += 1
                        captured_at = time.monotonic()
                        if not may_run():
                            raise RuntimeError("recorded device control is no longer enabled")
                        try:
                            image.write_bytes(await self.capture(stick.host))
                            observation = await asyncio.to_thread(self.ocr, image)
                        except (OSError, RuntimeError, asyncio.TimeoutError):
                            observation = OCRResult("error", error="fresh local menu capture failed")
                        return CapturedScreen(observation, sequence, captured_at)

                    await asyncio.wait_for(self.runner(stick, observe, requested=requested,
                        query=self.request["query"], home=self.request["home"], away=self.request["away"],
                        allow_play=True, may_run=may_run, proof=self.proof), timeout=180)
                    ended = "finished"
        except asyncio.CancelledError:
            self.proof.status, self.proof.reason = "stopped", "recorded navigation canceled; playback may remain on the device"
            raise
        except asyncio.TimeoutError:
            self.proof.status, self.proof.reason = "stopped", "recorded navigation timed out; no commands retried"
        except Exception as exc:
            self.proof.status, self.proof.reason = "stopped", f"recorded navigation stopped: {type(exc).__name__}"
        finally:
            self.proof.elapsed_s = round(time.monotonic() - started, 3)
            if self.session.is_("navigating"):   # Stop may already have ended the session
                self.session.to(ended, self.proof.reason or self.proof.status or ended)
            self._forget_live()
            e.broadcast()

    def cancel_navigation(self):
        """A device-control-off action also cancels pending prewarm authorization."""
        if self.task and not self.task.done():
            self.task.cancel()

    async def stop(self) -> tuple[bool, str]:
        async with self._transition:
            if self.task and not self.task.done():
                self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
            if self.active:
                self._forget_live()
                # Leaving recorded mode must not release overnight/device jobs
                # against a recording that is still playing on the physical stick.
                self.engine.cfg.update({"mode": {"dry_run": True}})
                self._guard_path.unlink(missing_ok=True)
            self.session.to("idle", "stopped by request")
            self.engine.discovery.resume_device_work()
            self.engine.broadcast()
            return True, "Recorded session stopped; automatic switching is paused and device control is off. Playback may remain on the stick."

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
