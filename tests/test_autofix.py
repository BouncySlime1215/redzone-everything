"""Auto-fix: the watchdog's issue filer (redzone/platform/autofix.py) and the guard on Claude's diff
(scripts/autofix_guard.py). Secrets never leave the Mac, limits hold, and the guard refuses any
change that weakens CI, gates or tests."""
import json
from types import SimpleNamespace

from redzone.platform import autofix as A
from scripts import autofix_guard as G

# --- redaction ----------------------------------------------------------------------------------


def test_redact_removes_tokens_keys_topics_and_emails():
    text = "\n".join([
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456",
        "gh token ghp_" + "a" * 36 + " and github_pat_" + "b" * 30,
        "anthropic sk-ant-oat01-" + "c" * 40,
        "ntfy_topic: redzone-secret-topic-123",
        "supervisor: {ntfy_topic: 'xyz-private'}",
        "password=hunter2&user=nick",
        "https://example.com/clip.mp4?sig=abc123&exp=1",
        "mail me at someone@example.com from /Users/nick/redzone",
        "stick 192.168.1.20:5555 timeout",
    ])
    out = A.redact(text)
    for leak in ("abcdefghijklmnop", "ghp_", "github_pat_", "sk-ant-", "redzone-secret-topic", "xyz-private",
                 "hunter2", "sig=abc123", "someone@example.com", "/Users/nick"):
        assert leak not in out, leak
    assert "192.168.1.20" not in out and "<ip>.20:5555 timeout" in out   # sticks still told apart
    assert "user=nick" in out


# --- the filer ----------------------------------------------------------------------------------

class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def obs(errors=None, restarts=0):
    return SimpleNamespace(errors=errors or {}, restarts_last_hour=restarts)


def filer(tmp_path, posted, token="tok", **opts):
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data/service.log").write_text(
        "2026-11-03 20:00:00,000 ERROR redzone.engine: KeyError 'clock' in decide\n"
        "token=supersecretvalue123\n")
    (tmp_path / "data/config.yaml").write_text("supervisor:\n  ntfy_topic: my-private-topic\nteams: {nfl: [WSH]}\n")
    notes = []

    def post(slug, tok, title, body):
        posted.append((slug, tok, title, body))
        return f"https://github.com/{slug}/issues/{len(posted)}"
    f = A.Filer(tmp_path, opts={"repo": "o/r", **opts}, clock=Clock(), post=post,
                token=lambda root: token, notify=notes.append, health=lambda: {"faults": ["stale_feed"]})
    f.notes = notes
    return f


def test_files_a_new_error_only_after_it_persists(tmp_path):
    posted = []
    f = filer(tmp_path, posted)
    sig = "KeyError 'clock' in decide"
    assert f.consider(obs({sig: 4}), [sig]) is None
    assert f.consider(obs({sig: 9}), [sig]) is None
    row = f.consider(obs({sig: 14}), [sig])
    assert row and row["url"].endswith("/issues/1") and row["kind"] == "new_error"
    slug, tok, title, body = posted[0]
    assert slug == "o/r" and title.startswith("[auto-fix]")
    assert "KeyError 'clock'" in body and "stale_feed" in body
    assert "supersecretvalue123" not in body and "my-private-topic" not in body
    assert f.notes and "filed" in f.notes[0]
    assert f.consider(obs({sig: 20}), [sig]) is None       # one issue per error
    assert len(posted) == 1


def test_a_streak_resets_when_the_error_stops(tmp_path):
    posted = []
    f = filer(tmp_path, posted)
    for errs in ([("x", 1)], [("x", 1)], [], [("x", 1)], [("x", 1)]):
        f.consider(obs(dict(errs)), [k for k, _ in errs])
    assert posted == []


def test_crash_loop_is_filed(tmp_path):
    posted = []
    row = filer(tmp_path, posted).consider(obs(restarts=3), [])
    assert row["kind"] == "crash_loop" and len(posted) == 1


def test_daily_limit_and_kill_switch(tmp_path):
    posted = []
    f = filer(tmp_path, posted, max_per_day=2)
    for sig in ("a", "b", "c"):
        f.file("new_error", sig, sig)
    assert len(posted) == 2
    f._clock.t += 86400 + 1
    f.file("new_error", "c", "c")
    assert len(posted) == 3                                 # a new day
    (tmp_path / "x").mkdir()
    off_posted = []
    assert filer(tmp_path / "x", off_posted, enabled=False).file("new_error", "d", "d") is None
    assert off_posted == []


def test_without_a_github_sign_in_the_report_is_kept_and_you_are_told(tmp_path):
    posted = []
    f = filer(tmp_path, posted, token="")
    row = f.file("new_error", "boom", "boom")
    assert posted == [] and row["queued"].startswith("data/autofix/outbox/")
    assert (tmp_path / row["queued"]).read_text().startswith("# [auto-fix]")
    assert "no sign-in" in f.notes[0]


def test_a_failing_post_is_queued_not_raised(tmp_path):
    def post(*a):
        raise OSError("offline")
    f = A.Filer(tmp_path, opts={"repo": "o/r"}, clock=Clock(), post=post, token=lambda r: "t")
    row = f.file("new_error", "boom", "boom")
    assert row["queued"] and "offline" in row["error"]


def test_night_report_lists_what_was_sent(tmp_path):
    posted = []
    f = filer(tmp_path, posted)
    f.file("new_error", "boom", "the thing broke")
    lines = A.night_lines(tmp_path / "data", 0, 2e9)
    assert len(lines) == 1 and "the thing broke" in lines[0] and "issues/1" in lines[0]


def test_default_config_has_the_settings():
    from redzone.config import Config
    import tempfile
    from pathlib import Path
    d = Path(tempfile.mkdtemp())
    c = Config(user_path=d / "c.yaml", history_dir=d / "h")
    assert c.get("autofix.enabled") is True and c.get("autofix.max_per_day") == 3


# --- the guard ----------------------------------------------------------------------------------

def fd(path, status="M", added=(), removed=()):
    return G.FileDiff(path, status, list(added), list(removed))


GOOD = [fd("redzone/engine.py", added=["    if g is None:", "        return"]),
        fd("tests/test_engine_fix.py", "A", added=["def test_none_game():", "    assert decide(None) is None"])]


def test_guard_accepts_a_fix_with_a_regression_test():
    assert G.check(GOOD) == []


def test_guard_refuses_ci_gates_and_secrets():
    for path in (".github/workflows/ci.yml", "scripts/release_gate.py", "scripts/ci_scorecard.py",
                 "docs/gate_baseline.json", "data/eval_config.yaml", "pyproject.toml", "tests/conftest.py",
                 "scripts/deploy.py", "requirements.txt", "deploy/secret.env", "redzone/platform/autofix.py"):
        assert any("protected" in w for w in G.check(GOOD + [fd(path, added=["x"])])), path
    why = G.check(GOOD + [fd("redzone/x.py", added=['KEY = "sk-ant-abcdefghijk"'])])
    assert any("credential" in w for w in why)
    assert any("credential" in w for w in G.check(GOOD + [fd("redzone/x.py", added=["os.environ['ANTHROPIC_API_KEY']"])]))


def test_guard_refuses_weaker_tests():
    cases = [
        fd("tests/test_a.py", "D", removed=["def test_x():", "    assert 1"]),
        fd("tests/test_a.py", removed=["def test_x():", "    assert f() == 1"], added=["# gone"]),
        fd("tests/test_a.py", removed=["    assert f() == 1"], added=["    f()"]),
        fd("tests/test_a.py", added=["@pytest.mark.skip(reason='flaky')", "def test_y():", "    assert 1"]),
        fd("tests/test_a.py", added=["    pytest.xfail('later')", "    assert 1"]),
    ]
    for c in cases:
        assert G.check([GOOD[0], c]), c


def test_guard_needs_both_a_fix_and_a_test():
    assert any("regression test" in w for w in G.check([GOOD[0]]))
    assert any("only tests" in w for w in G.check([GOOD[1]]))
    assert G.check([]) == ["no change"]


def test_guard_limits_size():
    big = [fd(f"redzone/m{i}.py", added=["x"]) for i in range(13)] + [GOOD[1]]
    assert any("files changed" in w for w in G.check(big))
    huge = [fd("redzone/m.py", added=["x"] * 700), GOOD[1]]
    assert any("lines changed" in w for w in G.check(huge))


def test_guard_parses_a_real_git_diff():
    ns = "M\tredzone/engine.py\nA\ttests/test_new.py\nR100\tredzone/old.py\t.github/workflows/x.yml\n"
    patch = "\n".join([
        "diff --git a/redzone/engine.py b/redzone/engine.py", "--- a/redzone/engine.py", "+++ b/redzone/engine.py",
        "@@ -1,2 +1,2 @@", "-    old()", "+    new()",
        "diff --git a/tests/test_new.py b/tests/test_new.py", "--- /dev/null", "+++ b/tests/test_new.py",
        "@@ -0,0 +1,2 @@", "+def test_new():", "+    assert new()",
    ])
    files = {f.path: f for f in G.parse(ns, patch)}
    assert files["redzone/engine.py"].added == ["    new()"] and files["redzone/engine.py"].removed == ["    old()"]
    assert files["tests/test_new.py"].status == "A" and len(files["tests/test_new.py"].added) == 2
    assert any("protected" in w for w in G.check(list(files.values())))   # the rename into .github


def test_workflow_is_gated_and_uses_the_subscription_token():
    import yaml
    from pathlib import Path
    wf = yaml.safe_load(Path(".github/workflows/auto-fix.yml").read_text())
    job = wf["jobs"]["fix"]
    assert "AUTOFIX_ENABLED" in job["if"] and "repository_owner" in job["if"]
    claude = next(s for s in job["steps"] if "claude-code-action" in str(s.get("uses", "")))
    assert claude["with"]["claude_code_oauth_token"] == "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}"
    assert "anthropic_api_key" not in claude["with"]
    names = [s.get("name", "") for s in job["steps"]]
    assert names.index("guard (no CI/gate/test weakening, no secrets, has a regression test)") \
        < names.index("open a draft PR into integration/next and start CI on it")
    assert "--draft" in json.dumps(job)


def test_the_watchdog_files_through_its_filer_and_never_from_a_test_build(monkeypatch):
    from agent import watchdog as W
    w = W.Watchdog(dry_run=False, use_model=False)
    assert w.autofix is None                                  # only main() wires up real filing
    seen = []
    w.autofix = SimpleNamespace(consider=lambda o, unknown: seen.append(unknown) or {"kind": "new_error", "sig": "x"})
    o = W.Obs(errors={"WeirdError N happened": 3, "poll failed N": 2})
    monkeypatch.setattr(w, "observe", lambda: o)
    monkeypatch.setattr(w, "act", lambda v, o: "ok")
    monkeypatch.setattr(W, "_journal", lambda row: seen.append(row))
    w.tick()
    assert seen[0] == ["WeirdError N happened"]               # only errors no rule knows
    assert any(isinstance(r, dict) and r.get("kind") == "autofix" for r in seen[1:])
