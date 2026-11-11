"""agent/install.py renders the watchdog's plist (P-02): no WorkingDirectory, no log under ~/Documents."""
import plistlib
from pathlib import Path

import pytest

from agent import install


def test_p02_rendered_plist_has_no_working_directory_and_no_documents_log(tmp_path):
    """Repro: the plist on the Mac had a WorkingDirectory and a log under ~/Documents; launchd failed it with
    EX_CONFIG 78 and the watchdog ran no check from Nov 3 to Nov 8. Nothing installed the repo's template."""
    home = tmp_path / "home"
    root = home / "Documents" / "Codex" / "redzone"
    text = install.render(root, home)
    d = plistlib.loads(text.encode())
    assert "WorkingDirectory" not in d
    assert d["Label"] == "com.redzone.agent" and d["KeepAlive"] is True
    for key in ("StandardOutPath", "StandardErrorPath"):
        assert d[key] == str(home / "Library" / "Logs" / "redzone" / "agent.log")
        assert not d[key].startswith(str(home / "Documents"))
    args = d["ProgramArguments"]
    assert args[0] == str(root / ".venv-agent" / "bin" / "python")
    assert str(root) in args[-1] and "agent.watchdog" in args[-1]
    assert install.TEMPLATE_ROOT not in text and install.TEMPLATE_HOME not in text


def test_the_checked_in_template_itself_is_launchd_safe():
    assert install.problems_in(install.TEMPLATE.read_text(), Path(install.TEMPLATE_HOME)) == []


def test_a_plist_with_the_old_faults_is_named():
    bad = plistlib.dumps({"Label": "com.redzone.agent", "WorkingDirectory": "/Users/n/Documents/x",
                          "StandardOutPath": "/Users/n/Documents/x/data/agent.log"}).decode()
    got = install.problems_in(bad, Path("/Users/n"))
    assert any("WorkingDirectory" in g for g in got) and any("StandardOutPath" in g for g in got)


def test_write_and_check_roundtrip_never_touches_launchctl(tmp_path, monkeypatch):
    home, root = tmp_path / "home", tmp_path / "home" / "rz"
    monkeypatch.setattr(install.os, "getuid", lambda: 501)
    assert any("not installed" in x for x in install.check(home, root))
    path = install.write(root, home)
    assert path == home / "Library" / "LaunchAgents" / "com.redzone.agent.plist"
    assert (home / "Library" / "Logs" / "redzone").is_dir()
    assert install.check(home, root) == []
    path.write_text(path.read_text().replace("<key>RunAtLoad</key>", "<key>RunAtLoad2</key>"))
    assert any("differs" in x for x in install.check(home, root))


def test_render_refuses_a_template_that_would_fail_under_documents(tmp_path):
    t = tmp_path / "t.plist"
    t.write_bytes(plistlib.dumps({"Label": "com.redzone.agent", "WorkingDirectory": "/x"}))
    with pytest.raises(ValueError):
        install.render(tmp_path, tmp_path, t)
