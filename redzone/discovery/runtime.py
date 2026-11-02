"""Connect recovered provider discovery and background maintenance to the engine.

Dry run may fetch public game links, but never probes or drives a device. First-run
pairing stays with the setup checker so the owner selects the intended TV/sticks.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from redzone.autonomy import Autonomy, Paths
from redzone.discovery.providers import ProviderBook
from redzone.tuners.providers import register
from redzone.tuners.tuners import TUNERS

log = logging.getLogger(__name__)


class DiscoveryRuntime:
    def __init__(self, engine):
        self.engine = engine
        user_path = getattr(engine.cfg, "user_path", None)
        root = Path(user_path).parent if user_path else Path(__file__).resolve().parents[2] / "data"
        self.book = ProviderBook(root / "providers.json")
        self.autonomy = Autonomy.for_engine(engine, paths=Paths.under(root))
        self._device_iteration = None
        self._device_suspended = False
        register(TUNERS)
        self.configure_profiles()

    def configure_profiles(self):
        profiles = self.engine.cfg.get("providers.profiles", {}) or {}
        for key, tuner in TUNERS.items():
            tuner.preferred_profile = profiles.get(key, "")
        for stick in self.engine.control.usable:
            stick.preferred_profiles = dict(profiles)

    async def run(self):
        await self.autonomy.start()
        while True:
            try:
                self.configure_profiles()
                if not self.device_suspended and self.engine.cfg.get("autonomy.enabled", True):
                    self._device_iteration = asyncio.create_task(self._step())
                    try:
                        await self._device_iteration
                    except asyncio.CancelledError:
                        # A recorded session cancels just the device iteration.
                        # Cancellation of this parent loop must still shut down.
                        if asyncio.current_task().cancelling() or not self.device_suspended:
                            raise
                    finally:
                        self._device_iteration = None
            except Exception:
                log.exception("Background discovery failed; retrying later")
            await asyncio.sleep(15)

    @property
    def device_suspended(self):
        return self._device_suspended or bool(getattr(getattr(self.engine, "recorded", None), "active", False))

    async def _step(self):
        if self.device_suspended:
            return
        if not self.engine.cfg.get("mode.dry_run", True):
            await self.book.refresh_sticks(self.engine.control.usable)
        if not self.device_suspended:
            await self.autonomy.step()

    async def suspend_device_work(self):
        """Drain device maintenance before a dedicated recorded-playback session."""
        self._device_suspended = True
        task = self._device_iteration
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self.autonomy.close()

    def resume_device_work(self):
        """Called only after recorded mode exits back to paused dry run."""
        self._device_suspended = False

    def remember(self, game, links):
        return self.book.remember(game, links)

    def links_refreshed(self, count, error=""):
        self.book.prune()
        self.book.save()
        self.autonomy.links_refreshed(count, error)

    def choose(self, game, base):
        return self.book.choose(game, base, self.engine.cfg.get("teams"), self.engine.channels,
                                sunday_ticket=self.engine.cfg.get("sunday_ticket", True))

    def on_outcome(self, outcome, game_id=None, replay=False):
        if outcome.dry_run:
            return
        note = self.book.learn(outcome.target, outcome.ok, outcome.detail, game_id)
        if note:
            self.engine.toast(note, "info")
        if outcome.ok:
            self.autonomy.on_tune(outcome.target, replay)

    def status(self):
        return {
            "jobs": self.autonomy.status(),
            "providers": [{"key": key, "installed": s.installed, "subscribed": s.subscribed,
                           "why": s.why_not()} for key, s in self.book.states().items()],
            "setup": self.autonomy.steps,
            "dry_run": self.engine.cfg.get("mode.dry_run", True),
            "device_work_suspended": self.device_suspended,
        }

    async def close(self):
        await self.suspend_device_work()
