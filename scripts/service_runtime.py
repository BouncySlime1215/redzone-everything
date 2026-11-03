"""launchd entry point. Default service stays dry-run across settings/rollback."""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import io
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from redzone.config import Config, ConfigError
from scripts.service import check


class ServiceConfig(Config):
    def __init__(self, *args, allow_live: bool = False, **kwargs):
        self.allow_live = allow_live
        super().__init__(*args, **kwargs)
        self._pin_mode()

    def _pin_mode(self) -> None:
        if not self.allow_live:
            self.data["mode"]["dry_run"] = True

    def update(self, changes: dict, record: bool = True) -> dict:
        mode = changes.get("mode")
        if not self.allow_live and isinstance(mode, dict) and mode.get("dry_run") is False:
            raise ConfigError("This login service enforces dry-run; live service installation requires explicit --allow-live.")
        return super().update(changes, record=record)

    def _save(self, over: dict, record: bool = True) -> None:
        super()._save(over, record=record)
        self._pin_mode()


class LogStream(io.TextIOBase):
    """Put prints/tracebacks in the same bounded log as application logging."""
    def __init__(self, logger: logging.Logger, level: int):
        self.logger, self.level, self.pending = logger, level, ""

    def write(self, text: str) -> int:
        self.pending += text
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            if line:
                self.logger.log(self.level, line)
        if len(self.pending) > 16384:
            self.flush()
        return len(text)

    def flush(self) -> None:
        if self.pending:
            self.logger.log(self.level, self.pending)
            self.pending = ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-live", action="store_true")
    args = parser.parse_args(argv)
    os.umask(0o077)
    os.chdir(ROOT)
    data = ROOT / "data"
    data.mkdir(parents=True, exist_ok=True)
    with (data / "service.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another service process already owns this checkout.", file=sys.stderr)
            return 1
        handler = RotatingFileHandler(data / "service.log", maxBytes=5 * 1024 * 1024, backupCount=3)
        logging.basicConfig(level=logging.INFO, handlers=[handler], force=True,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        # Handler errors must not recurse through redirected stderr (e.g. full disk).
        logging.raiseExceptions = False
        log = logging.getLogger("redzone.service")
        stdout, stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = LogStream(log, logging.INFO), LogStream(log, logging.ERROR)
        try:
            readiness = check(allow_live=args.allow_live)
            readiness.show()
            if readiness.errors:
                return 1
            from redzone.__main__ import main as app_main
            log.info("Starting login service; mode=%s", "saved configuration" if args.allow_live else "enforced dry-run")
            asyncio.run(app_main(ServiceConfig(allow_live=args.allow_live)))
            return 0
        except Exception:
            log.exception("Service stopped after an error; launchd may restart it")
            return 1
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            sys.stdout, sys.stderr = stdout, stderr
            handler.flush()


if __name__ == "__main__":
    raise SystemExit(main())
