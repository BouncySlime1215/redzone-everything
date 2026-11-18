"""[WV loop, Nick Nov 5 ~5 PM] Never-seen-data harness: forward-chain by whole night.
Each new night is SCORED FIRST by the current model (that first-pass score is the only evidence), then analyzed
(mistake ledger), then moved into training. Past seasons replay in time order the same way.
Leakage checks (raise, never warn): a night is scored before anything learned from it; nothing trains on a night
before its first pass; locked nights are never scored here, never enter the ledger, never train.
personal-use, not for commercial release."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Callable, Iterable


class Leak(RuntimeError):
    pass


@dataclass
class Chain:
    locked: set[str]
    seen: list[str] = field(default_factory=list)          # nights whose first pass is done (training may use them)
    log: list[dict] = field(default_factory=list)

    def _digest(self) -> str:
        return hashlib.sha1(",".join(self.seen).encode()).hexdigest()[:12]

    def step(self, night: str, score: Callable[[str], dict], analyze: Callable[[str], object],
             fit: Callable[[list[str]], None]) -> dict:
        night = str(night)
        if night in self.locked:
            raise Leak(f"{night} is locked: never in the forward chain")
        if night in self.seen:
            raise Leak(f"{night} was already seen: a second first pass is not evidence")
        if self.seen and night <= self.seen[-1]:
            raise Leak(f"{night} is not after {self.seen[-1]}: forward chaining is in time order")
        trained_on = list(self.seen)
        res = dict(score(night))                           # FIRST PASS: the model has never touched this night
        row = {"night": night, "first_pass": res, "trained_on_n": len(trained_on), "trained_on_sha": self._digest(),
               "evidence": True}
        analyze(night)                                     # ledger (after scoring, never before)
        self.seen.append(night)
        fit(list(self.seen))                               # the night joins training only now
        self.log.append(row)
        return row

    def run(self, nights: Iterable[str], score, analyze, fit) -> list[dict]:
        for n in sorted(str(x) for x in nights if str(x) not in self.locked):
            self.step(n, score, analyze, fit)
        return self.log

    def jsonl(self) -> str:
        return "".join(json.dumps(r) + "\n" for r in self.log)
