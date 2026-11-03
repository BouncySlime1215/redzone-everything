"""A fake `adb` for tests: a tiny shell script standing in for the adb binary.

"Device" commands run in a real local /bin/sh, so the always-open shell protocol is exercised for
real (pipes, partial reads, exit codes, dying shells). Stand-ins for dumpsys / input / am / getprop
read and write files in one folder:

    fake = FakeAdb(tmp_path / "dev"); monkeypatch.setattr(adb, "ADB", str(fake.adb))
    fake.mode("nosession")   # the always-open shell won't start (one adb process per command still works)
    fake.calls()             # ["session", "oneshot echo hi", ...] -- every adb start, in order
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

ADB = r'''#!/bin/sh
D='@DIR@'
[ "$1" = "-s" ] && shift 2
what="$1"; shift
mode=$(cat "$D/mode" 2>/dev/null)
case "$what" in
  version) echo "Android Debug Bridge version 1.0.41 (fake)"; exit 0 ;;
  connect) echo "already connected to $1"; exit 0 ;;
  shell) ;;
  *) echo "fake adb: can't do $what"; exit 1 ;;
esac
if [ "$mode" = offline ]; then echo "error: device offline"; exit 1; fi
PATH="$D/bin:$PATH"; export PATH
if [ $# -gt 0 ]; then
  echo "oneshot $*" >> "$D/calls.log"
  exec /bin/sh -c "$*"
fi
echo session >> "$D/calls.log"
echo $$ > "$D/session.pid"
case "$mode" in
  nosession) echo "error: closed"; exit 1 ;;
  silent) exec cat > /dev/null ;;
  pty) while IFS= read -r line; do printf '%s\r\n' "$line"; eval "$line"; done ;;
  garble)
    n=0
    while IFS= read -r line; do
      n=$((n + 1))
      if [ "$n" -eq 1 ]; then eval "$line"; continue; fi
      m=$(printf '%s\n' "$line" | sed -n 's/.*\(__RZ[0-9a-f]*__\).*/\1/p')
      printf 'garbage\n\n%s notanumber\n' "$m"
    done ;;
  *) exec /bin/sh ;;
esac
'''

# die_once: the command kills the always-open shell while it runs (the "stick" drops mid-command).
DIE = '''if [ -f "$D/die_once" ]; then rm -f "$D/die_once"; kill -9 "$(cat "$D/session.pid")"; exit 0; fi'''

DEVICE = {
    "dumpsys": f'''#!/bin/sh
D='@DIR@'
{DIE}
case "$1" in
  media_session) cat "$D/media_session.txt" 2>/dev/null ;;
  power) w=Awake; [ -f "$D/wakefulness" ] && w=$(cat "$D/wakefulness")
         echo "  mWakefulness=$w"; echo "  mWakefulnessChanging=false" ;;
  *) echo "fake dumpsys $*" ;;
esac
''',
    "input": f'''#!/bin/sh
D='@DIR@'
echo "input $*" >> "$D/device.log"
case "$*" in *WAKEUP*) echo Awake > "$D/wakefulness" ;; esac
{DIE}
exit 0
''',
    "am": '''#!/bin/sh
D='@DIR@'
echo "am $*" >> "$D/device.log"
[ -f "$D/media_after_am.txt" ] && cp "$D/media_after_am.txt" "$D/media_session.txt"
echo "Starting: Intent { act=android.intent.action.VIEW }"
''',
    "getprop": '''#!/bin/sh
case "$1" in
  ro.product.model) echo AFTKM ;;
  ro.build.version.release) echo 11 ;;
  ro.build.version.name) echo "Fire OS 8.1.0.3 (PS8103)" ;;
  *) echo ;;
esac
''',
}


def media(pkg: str, state: int, position: int = 0, updated: int = 0) -> str:
    """One session as `dumpsys media_session` prints it (the lines our grep keeps, plus a few it drops)."""
    return (f"  {pkg}/MediaSession (userId=0)\n"
            f"    ownerPid=1234, ownerUid=10080, userId=0\n"
            f"    package={pkg}\n"
            f"    active=true\n"
            f"    state=PlaybackState {{state={state}, position={position}, buffered position=0, speed=1.0, "
            f"updated={updated}, actions=822, custom actions=[], active item id=-1, error=null}}\n"
            f"    metadata: size=4, description=Game, Channel, null\n")


class FakeAdb:
    def __init__(self, root: Path):
        self.root = Path(root)
        (self.root / "bin").mkdir(parents=True, exist_ok=True)
        self.adb = self.root / "adb"
        self._write(self.adb, ADB)
        for name, body in DEVICE.items():
            self._write(self.root / "bin" / name, body)

    def _write(self, path: Path, body: str) -> None:
        path.write_text(body.replace("@DIR@", str(self.root)))
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def reset(self) -> FakeAdb:
        """Forget everything a test did. (Build one FakeAdb per test module and reset it per test:
        macOS scans a freshly written script on its first run, ~200 ms each.)"""
        for name in ("calls.log", "device.log", "mode", "die_once", "media_session.txt", "media_after_am.txt",
                     "wakefulness", "session.pid"):
            (self.root / name).unlink(missing_ok=True)
        return self

    def mode(self, m: str) -> None:
        (self.root / "mode").write_text(m)

    def die_once(self) -> None:
        (self.root / "die_once").write_text("1")

    def set_media(self, text: str, after_am: str | None = None) -> None:
        (self.root / "media_session.txt").write_text(text)
        if after_am is not None:
            (self.root / "media_after_am.txt").write_text(after_am)

    def set_wakefulness(self, w: str) -> None:
        (self.root / "wakefulness").write_text(w)

    def calls(self) -> list[str]:
        f = self.root / "calls.log"
        return f.read_text().splitlines() if f.exists() else []

    def sessions(self) -> int:
        return self.calls().count("session")

    def device_log(self) -> list[str]:
        f = self.root / "device.log"
        return f.read_text().splitlines() if f.exists() else []

    def session_pid(self) -> int | None:
        f = self.root / "session.pid"
        return int(f.read_text().strip()) if f.exists() else None


def alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
