"""Profile choices require owner configuration and fresh, exact focus evidence."""
from html import escape
from types import SimpleNamespace

import pytest

from redzone.discovery.stickio import clear_prompt
from redzone.tuners.profiles import ProfileSelectionRequired, select_profile
from redzone.tuners.providers import DiagnosingTuner, PeacockTuner
from redzone.tuners.tuners import CutGuard, CutWindowClosed, YouTubeTVTuner


def picker(focus="Guest", names=("Guest", "Taylor"), heading="Who's watching?"):
    cards = "".join(
        f'<node focusable="true" focused="{str(name == focus).lower()}" '
        f'bounds="[{i * 200},100][{i * 200 + 150},250]"><node text="{escape(name)}"/></node>'
        for i, name in enumerate(names))
    return f'<hierarchy><node text="{escape(heading)}"/><node>{cards}</node></hierarchy>'


class FakeStick:
    def __init__(self, snapshots, text="Who's watching? | Guest | Taylor"):
        self.snapshots = list(snapshots)
        self.text = text
        self.keys = []
        self.reads = 0
        self.preferred_profiles = {}
        self.key_ok = True

    async def run(self, *args, **kwargs):
        self.reads += 1
        xml = self.snapshots.pop(0) if len(self.snapshots) > 1 else self.snapshots[0]
        return SimpleNamespace(ok=True, out=xml)

    async def key(self, *keys):
        self.keys.extend(keys)
        return SimpleNamespace(ok=self.key_ok)

    async def screen_text(self):
        return self.text


async def test_navigates_only_observed_focus_then_selects_exact_profile():
    st = FakeStick([picker(), picker("Taylor")])
    steps = []
    assert await select_profile(st, st.text, "Taylor", steps)
    assert st.keys == ["KEYCODE_DPAD_RIGHT", "KEYCODE_DPAD_CENTER"]
    assert st.reads == 2 and "playback still unconfirmed" in steps[-1]


@pytest.mark.parametrize("xml,reason", [
    ("not accessible", "not readable"),
    (picker(names=("Guest", "Taylor Kids")), "missing or ambiguous"),
    (picker(names=("Guest", "Taylor", "Taylor")), "missing or ambiguous"),
    (picker(focus="Nobody"), "missing or ambiguous"),
    (picker("Taylor", heading="Who's watching? | Rent $9.99"), "payment"),
    (picker("Taylor", heading="Who's watching? | Sign in"), "account"),
])
async def test_unsafe_or_ambiguous_fresh_evidence_never_selects(xml, reason):
    st = FakeStick([xml])
    with pytest.raises(ProfileSelectionRequired, match=reason):
        await select_profile(st, st.text, "Taylor", [])
    assert st.keys == []


async def test_focus_that_does_not_move_is_not_followed_by_blind_ok():
    st = FakeStick([picker()])
    with pytest.raises(ProfileSelectionRequired, match="did not advance"):
        await select_profile(st, st.text, "Taylor", [])
    assert st.keys == ["KEYCODE_DPAD_RIGHT"]


async def test_unknown_key_delivery_stops_without_retry():
    st = FakeStick([picker("Taylor")])
    st.key_ok = False
    with pytest.raises(ProfileSelectionRequired, match="delivery is unconfirmed"):
        await select_profile(st, st.text, "Taylor", [])
    assert st.keys == ["KEYCODE_DPAD_CENTER"]


async def test_picker_remaining_after_selection_does_not_get_second_ok():
    st = FakeStick([picker("Taylor")])
    steps = []
    await select_profile(st, st.text, "Taylor", steps)
    with pytest.raises(ProfileSelectionRequired, match="no second OK"):
        await select_profile(st, st.text, "Taylor", steps)
    assert st.keys == ["KEYCODE_DPAD_CENTER"]


async def test_cut_window_rechecked_after_each_slow_profile_read():
    st = FakeStick([picker(), picker("Taylor")])
    tuner = YouTubeTVTuner()
    tuner.preferred_profile = "Taylor"
    guard = CutGuard(lambda: st.reads < 2)
    with pytest.raises(CutWindowClosed):
        await tuner._press_prompt(st, st.text, [], "clear", guard)
    assert guard.changed and st.keys == ["KEYCODE_DPAD_RIGHT"]


async def test_diagnosing_wrapper_forwards_preference_and_app_tuner_uses_it():
    tuner = DiagnosingTuner(PeacockTuner())
    tuner.preferred_profile = "Taylor"
    st = FakeStick([picker("Taylor")])
    await tuner._read_screen(st, [], scheduled=True)
    assert tuner.inner.preferred_profile == "Taylor"
    assert st.keys == ["KEYCODE_DPAD_CENTER"]


async def test_discovery_reads_explicit_profile_preference():
    st = FakeStick([picker("Taylor")])
    st.preferred_profiles = {"youtubetv": "Taylor"}
    assert await clear_prompt(st, [])
    assert st.keys == ["KEYCODE_DPAD_CENTER"]


@pytest.mark.parametrize("text", ["Who's watching?", "Choose an account", "Edit profiles"])
async def test_discovery_never_blindly_clears_profile_or_account_picker(text):
    st = FakeStick([picker()], text=text)
    assert not await clear_prompt(st, [])
    assert not st.keys and not st.reads


def system_picker(focus="Guest", names=("Guest", "Nick")):
    return picker(focus, names).replace("<hierarchy>", '<hierarchy><node package="com.amazon.tv.launcher"/>', 1)


async def test_fire_tv_system_picker_never_uses_the_apps_profile_name():
    st = FakeStick([system_picker(names=("Guest", "Serge"))], text="Who's watching? | Guest | Serge")
    steps = []
    with pytest.raises(ProfileSelectionRequired, match="Fire TV system profile picker"):
        await select_profile(st, st.text, "Serge", steps)
    assert st.keys == []


async def test_fire_tv_system_picker_uses_its_own_configured_profile():
    st = FakeStick([system_picker(), system_picker("Nick")], text="Who's watching? | Guest | Nick")
    st.preferred_profiles = {"firetv": "Nick"}
    steps = []
    assert await select_profile(st, st.text, "Serge", steps)
    assert st.keys == ["KEYCODE_DPAD_RIGHT", "KEYCODE_DPAD_CENTER"]
    assert any("'firetv' profile" in s for s in steps) and "Nick" in steps[-1]
