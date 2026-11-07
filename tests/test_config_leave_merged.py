"""[Nick Nov 7] decider.leave was defined twice in default_config.yaml; YAML kept only the second block, so
decider.leave.dead_windows / tune_s silently didn't exist and the play-by-play dead-window leave path never ran."""
import yaml

from redzone.config import DEFAULTS, Config


def test_default_config_has_no_duplicate_top_level_decider_keys():
    # the decider section: keys between 'decider:' and the next top-level key
    text = DEFAULTS.read_text().splitlines()
    start = next(n for n, l in enumerate(text) if l.startswith("decider:"))
    end = next(n for n in range(start + 1, len(text)) if text[n] and not text[n].startswith((" ", "#")))
    keys = [l.split(":")[0].strip() for l in text[start + 1:end]
            if l.startswith("  ") and not l.startswith("   ") and l.strip() and not l.strip().startswith("#")]
    dupes = {k for k in keys if keys.count(k) > 1}
    assert not dupes, f"duplicate decider keys (YAML keeps only the last): {dupes}"


def test_leave_keeps_all_settings():
    d = yaml.safe_load(DEFAULTS.read_text())["decider"]["leave"]
    for k in ("dead_windows", "tune_s", "low_leverage", "sticky_closeness", "break_floor_s"):
        assert k in d, k


def test_dead_windows_settable(tmp_path):
    c = Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "h")
    c.update({"decider": {"leave": {"dead_windows": ["cfb", "nfl", "nhl"]}}})
    assert c.get("decider.leave.dead_windows") == ["cfb", "nfl", "nhl"]
    assert float(c.get("decider.leave.tune_s")) == 6
