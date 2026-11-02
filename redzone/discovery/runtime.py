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
        register(TUNERS)

    async def run(self):
        await self.autonomy.start()
        while True:
            try:
                if self.engine.cfg.get("autonomy.enabled", True):
                    if not self.engine.cfg.get("mode.dry_run", True):
                        await self.book.refresh_sticks(self.engine.control.usable)
                    await self.autonomy.step()
            except Exception:
                log.exception("Background discovery failed; retrying later")
            await asyncio.sleep(15)

    def remember(self, game, links):
        return self.book.remember(game, links)

    def links_refreshed(self, count, error=""):
        self.book.prune()
        self.book.save()
        self.autonomy.links_refreshed(count, error)

    def choose(self, game, base):
        return self.book.choose(game, base, self.engine.cfg.get("teams"))

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
        }

    async def close(self):
        await self.autonomy.close()
