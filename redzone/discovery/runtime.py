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
from redzone.machines.platform import DISCOVERY_DEVICE_WORK
from redzone.tuners.providers import register
from redzone.tuners.tuners import TUNERS

log = logging.getLogger(__name__)

# Outcomes where no tune of the target ran (or it isn't the link's fault): learning from
# them would skip a good link, e.g. two "no safe moment" holds making MNF untunable.
NOT_A_TUNE = ("no safe moment", "safe exit window closed", "switch cancelled", "tv input is unknown",
              "tv control was disabled", "no channel for this game", "standby stream is no longer confirmed",
              "replay seek failed", "replay resume failed", "two-stick mode off", "crashed:",
              "stick not responding")


class DiscoveryRuntime:
    def __init__(self, engine):
        self.engine = engine
        user_path = getattr(engine.cfg, "user_path", None)
        root = Path(user_path).parent if user_path else Path(__file__).resolve().parents[2] / "data"
        self.book = ProviderBook(root / "providers.json")
        self.autonomy = Autonomy.for_engine(engine, paths=Paths.under(root))
        self._device_iteration = None
        self.device_work = DISCOVERY_DEVICE_WORK.new()   # running | suspended (by a recorded session)
        register(TUNERS)
        self.configure_profiles()
        if hasattr(engine.control, "on_preload_fail"):
            engine.control.on_preload_fail = self.on_preload_fail
        if hasattr(engine.control, "can_play"):
            engine.control.can_play = self.book.can_play   # per-stick sign-in walls route games

    def configure_profiles(self):
        profiles = self.engine.cfg.get("providers.profiles", {}) or {}
        for key, tuner in TUNERS.items():
            tuner.preferred_profile = profiles.get(key, "")
        for stick in self.engine.control.usable:
            stick.preferred_profiles = dict(profiles)

    async def run(self):
        started = False
        while True:
            try:
                if not started:
                    # A failure here (e.g. a corrupt state file) must not kill the whole engine.
                    await self.autonomy.start()
                    started = True
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
    def _device_suspended(self) -> bool:
        m = self.__dict__.get("device_work")
        return m is not None and m.is_("suspended")

    @_device_suspended.setter
    def _device_suspended(self, on: bool) -> None:   # old flag API (tests build the runtime by hand)
        if "device_work" not in self.__dict__:
            self.device_work = DISCOVERY_DEVICE_WORK.new()
        self.device_work.to("suspended" if on else "running", "set directly")

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
        if "device_work" not in self.__dict__:
            self.device_work = DISCOVERY_DEVICE_WORK.new()
        self.device_work.to("suspended", "recorded session (or shutdown) drains device work")
        task = self._device_iteration
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self.autonomy.close()

    def resume_device_work(self):
        """Called only after recorded mode exits back to paused dry run."""
        if "device_work" not in self.__dict__:
            self.device_work = DISCOVERY_DEVICE_WORK.new()
        self.device_work.to("running", "recorded session stopped")

    def remember(self, game, links):
        return self.book.remember(game, links)

    def links_refreshed(self, count, error=""):
        self.book.prune()
        self.book.save()
        self.autonomy.links_refreshed(count, error)

    def choose(self, game, base):
        return self.book.choose(game, base, self.engine.cfg.get("teams"), self.engine.channels,
                                sunday_ticket=self.engine.cfg.get("sunday_ticket", True),
                                st_wait=bool(self.engine.cfg.get("discovery.st_wait", False)))   # [WP-01] off

    def on_outcome(self, outcome, game_id=None, replay=False):
        if outcome.dry_run:
            return
        if not outcome.ok and (outcome.detail or "").lower().startswith(NOT_A_TUNE):
            return
        note = self.book.learn(outcome.target, outcome.ok, outcome.detail, game_id,
                               host=getattr(outcome, "stick", None))
        if note:
            self.engine.toast(note, "info")
        if outcome.ok:
            self.autonomy.on_tune(outcome.target, replay)

    def on_preload_fail(self, target, detail, host=None):
        """A standby preload that failed is a real tune: learn from it like a switch, so a dead
        standby link isn't retuned every 2 minutes all night.  host = the stick it failed on."""
        try:
            note = self.book.learn(target, False, detail, self.book.game_for(target), host=host)
            if note:
                self.engine.toast(note, "info")
        except Exception:
            log.exception("learning from a failed preload")

    def status(self):
        return {
            "jobs": self.autonomy.status(),
            "providers": [{"key": key, "installed": s.installed, "subscribed": s.subscribed,
                           "why": s.why_not(), "blocked_on": list(s.blocked)}
                          for key, s in self.book.states().items()],
            "setup": self.autonomy.steps,
            "dry_run": self.engine.cfg.get("mode.dry_run", True),
            "device_work_suspended": self.device_suspended,
            "device_work": self.device_work.view(history=0),
        }

    async def close(self):
        await self.suspend_device_work()
