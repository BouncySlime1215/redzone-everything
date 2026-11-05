"""Checkpoints on the exchange (compute exchange item c): every save also goes to the store, and a site with no
local checkpoint resumes from the newest one any site pushed.

Layout: runs/<run>/ckpt.pt (latest), runs/<run>/best.pt, runs/<run>/STATE.json ({"step", "site", "time", "valid"}).
Used by scripts/nfm_train.py --exchange hf:<owner>/<repo> --run <name> (model repo, private).
"""
from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path

from store import open_store


class CkptSync:
    def __init__(self, spec: str, run: str, site: str | None = None):
        self.st = open_store(spec, "model")
        self.run = run
        self.site = site or os.environ.get("RZ_SITE") or socket.gethostname()

    def state(self) -> dict | None:
        tmp = Path(f"/tmp/rz_state_{os.getpid()}.json")
        if not self.st.get(f"runs/{self.run}/STATE.json", tmp):
            return None
        return json.loads(tmp.read_text())

    def push(self, local_ckpt: Path, step: int, valid=None, best: Path | None = None) -> None:
        cur = self.state()
        if cur and cur.get("step", -1) > step:            # another site is ahead: never move the run backwards
            print(f"exchange: {cur['site']} is at step {cur['step']} > {step}; not overwriting", flush=True)
            return
        self.st.put(local_ckpt, f"runs/{self.run}/ckpt.pt", f"{self.run} step {step} from {self.site}")
        if best is not None and best.exists():
            self.st.put(best, f"runs/{self.run}/best.pt", f"{self.run} best (valid {valid})")
        tmp = local_ckpt.with_name("STATE.json")
        tmp.write_text(json.dumps({"step": step, "site": self.site, "time": time.time(), "valid": valid}))
        self.st.put(tmp, f"runs/{self.run}/STATE.json", f"{self.run} state")

    def pull(self, local_ckpt: Path, local_step: int = -1) -> bool:
        """Fetch the store's checkpoint when it is newer than the local one; True if it was fetched."""
        cur = self.state()
        if not cur or cur.get("step", -1) <= local_step:
            return False
        ok = self.st.get(f"runs/{self.run}/ckpt.pt", local_ckpt) is not None
        if ok:
            self.st.get(f"runs/{self.run}/best.pt", local_ckpt.with_name("best.pt"))
            print(f"exchange: resumed from {cur['site']} at step {cur['step']}", flush=True)
        return ok
