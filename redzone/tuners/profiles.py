"""Select an explicitly configured profile using readable focus evidence only.

Why: some Fire TV apps expose no accessible profile nodes, so a profile is chosen only on readable focus evidence, never a blind OK (docstring).

Some Fire TV apps expose no accessible profile nodes. In that case the owner must
select the profile; screen text alone never authorizes a blind OK or grid guess.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from redzone.discovery.providers import classify_screen, is_purchase_screen, normalize_text

_PICKERS = ("who's watching", "who is watching", "choose a profile", "choose your profile",
            "select a profile", "select your profile", "choose an account", "select an account")
_MANAGEMENT = ("edit profiles", "manage profiles", "edit profile", "manage account")
_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
_SENT = "profile selection sent: "
_SYSTEM = "com.amazon.tv."   # Fire OS launcher / system profile picker packages


class ProfileSelectionRequired(RuntimeError):
    def __init__(self, reason: str, steps: list[str]):
        self.steps = list(steps)
        super().__init__(f"profile needs owner selection: {reason}")


def is_profile_screen(text: str) -> bool:
    text = normalize_text(text)
    return any(p in text for p in _PICKERS + _MANAGEMENT)


def preference(stick, app: str, configured: str = "") -> str:
    profiles = getattr(stick, "preferred_profiles", {}) or {}
    value = profiles.get(app, configured) if isinstance(profiles, dict) else configured
    return value.strip() if isinstance(value, str) else ""


def _labels(node) -> set[str]:
    return {normalize_text(n.get(a, "")).strip() for n in node.iter()
            for a in ("text", "content-desc") if n.get(a, "").strip()}


def _focus_cards(root):
    parents = {child: node for node in root.iter() for child in node}
    cards = []
    for node in root.iter():
        if node.get("focusable") != "true" or node.get("enabled", "true") != "true":
            continue
        # Nested focusable controls make an outer wrapper ambiguous.
        if any(n is not node and n.get("focusable") == "true" for n in node.iter()):
            continue
        bounds = _BOUNDS.fullmatch(node.get("bounds", ""))
        if not bounds:
            continue
        x1, y1, x2, y2 = map(int, bounds.groups())
        if x2 <= x1 or y2 <= y1:
            continue
        cards.append((node, parents.get(node), _labels(node),
                      ((x1 + x2) / 2, (y1 + y2) / 2),
                      any(n.get("focused") == "true" for n in node.iter())))
    return cards


async def select_profile(stick, text: str, configured: str, steps: list[str], before_key=None) -> bool | None:
    """None: no picker. True: exact focused profile selection sent.

    Raises ProfileSelectionRequired if evidence is insufficient. ``before_key`` is
    called immediately before each mutation so the tuner's live-play guard survives
    slow accessibility reads. No device command is retried after an uncertain send.
    """
    if not is_profile_screen(text):
        return None

    def stop(reason):
        steps.append(f"profile selection stopped: {reason}")
        raise ProfileSelectionRequired(reason, steps)

    wanted = normalize_text(configured).strip()
    if not wanted:
        stop("no preferred profile configured")
    if any(s.startswith(_SENT) for s in steps):
        stop("the picker remained after selection; no second OK sent")
    seen = set()
    for _ in range(8):
        try:
            # Remove our previous dump first: a failed dump must never reuse old focus.
            result = await stick.run("shell", "rm -f /sdcard/rz_profiles.xml && uiautomator dump /sdcard/rz_profiles.xml >/dev/null 2>&1 && cat /sdcard/rz_profiles.xml", timeout=12)
            if not result.ok:
                stop("could not read profile focus")
            xml = result.out[result.out.find("<"):]
            root = ET.fromstring(xml)
        except ProfileSelectionRequired:
            raise
        except Exception:
            stop("profile controls are not readable")
        if any(n.get("package", "").startswith(_SYSTEM) for n in root.iter()):
            # Fire OS's own "who's watching" (after a wake): the app's profile name means nothing here.
            system = preference(stick, "firetv")
            if not system:
                stop("Fire TV system profile picker (set a 'firetv' profile to pick it automatically)")
            if normalize_text(system).strip() != wanted:
                wanted, configured = normalize_text(system).strip(), system
                steps.append("Fire TV system profile picker: using the 'firetv' profile")
        fresh = " | ".join(_labels(root))
        verdict = classify_screen(fresh)
        if is_purchase_screen(fresh) or (verdict and verdict.stops):
            stop("account, payment, or unavailable-content screen")
        if not any(p in fresh for p in _PICKERS):
            stop("a profile picker is no longer identifiable")
        cards = _focus_cards(root)
        targets = [c for c in cards if wanted in c[2]]
        focused = [c for c in cards if c[4]]
        if len(targets) != 1 or len(focused) != 1:
            stop("requested profile or current focus is missing or ambiguous")
        target, current = targets[0], focused[0]
        if target[2] != {wanted} or len(current[2]) != 1:
            stop("profile controls contain ambiguous labels")
        if any(p in " | ".join(current[2]) for p in _MANAGEMENT + ("add profile", "add account")):
            stop("focus is on profile/account management")
        if current[0] is target[0]:
            key = "KEYCODE_DPAD_CENTER"
        else:
            if current[1] is not target[1]:
                stop("profile navigation layout is ambiguous")
            signature = (tuple(sorted(current[2])), current[3])
            if signature in seen:
                stop("profile focus did not advance")
            seen.add(signature)
            dx, dy = target[3][0] - current[3][0], target[3][1] - current[3][1]
            if dx == dy == 0:
                stop("profile positions overlap")
            key = ("KEYCODE_DPAD_RIGHT" if dx > 0 else "KEYCODE_DPAD_LEFT") if abs(dx) >= abs(dy) \
                else ("KEYCODE_DPAD_DOWN" if dy > 0 else "KEYCODE_DPAD_UP")
        if before_key is not None:
            before_key()
        try:
            sent = await stick.key(key)
        except Exception:
            stop("profile key delivery is uncertain")
        if not getattr(sent, "ok", False):
            stop("profile key delivery is unconfirmed")
        if key == "KEYCODE_DPAD_CENTER":
            steps.append(_SENT + configured + " (exact readable label and focus; playback still unconfirmed)")
            return True
        steps.append("profile navigation: " + key.removeprefix("KEYCODE_"))
    stop("profile navigation did not reach the configured name")
