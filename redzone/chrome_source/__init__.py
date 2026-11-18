"""Chrome tabs on the Mac as picture streams: one YouTube TV tab per game, parked and jumped to by page script.

OFF behind `chrome_source.enabled` (redzone/default_config.yaml). Nothing in the app imports this package: with the
flag off the app decides exactly as before, and with it on nothing is wired yet either (that is a later PR).

    page_script.py   the calls into the web player `#movie_player`, as JavaScript text; each returns small JSON
    bridge.py        run_js(tab, script) -> dict: AppleScriptBridge (osascript, the real Chrome) and FakeBridge (tests)
    streams.py       ChromeStreams: open the tabs, park(stream, content_second), jump(to_stream), position, health

It never touches system volume, TV volume, power or any account setting, never types anything and never signs in.
A tab that is not signed in is reported by health() as having no player; nothing is done about it.

Why: two Fire TV sticks give one spare stream, a 2 s jump and no fast skip ahead; on Nov 10 Nick's Mac played four
YouTube TV channels in four Chrome tabs whose player can be paused exactly, resumed in 0.28 s and seeked to a second,
so tabs can be the streams of the PARK design.
"""
from __future__ import annotations


def _read(cfg, key: str, default=None):
    """`chrome_source.<key>` from a Config (dotted get) or a plain dict."""
    if cfg is None:
        return default
    if isinstance(cfg, dict):
        return (cfg.get("chrome_source") or {}).get(key, default)
    return cfg.get(f"chrome_source.{key}", default)


def enabled(cfg) -> bool:
    """The flag. Off unless the config says true."""
    return bool(_read(cfg, "enabled", False))


def from_config(cfg, channel_map, bridge=None):
    """The streams as the config says, or None while the flag is off (then the bridge and the streams are not even
    imported). `bridge` defaults to the real Chrome on this Mac."""
    if not enabled(cfg):
        return None
    from .bridge import AppleScriptBridge
    from .streams import ChromeStreams
    return ChromeStreams(bridge or AppleScriptBridge(), channel_map, lead_s=float(_read(cfg, "park_lead_s")))
