"""Tuners for the other streaming apps: Peacock, Fubo, Paramount+, HBO Max, Netflix, NFL,
NBA, Apple TV and DAZN.  (YouTube TV, Prime Video and ESPN live in tuners.py.)

Each tuner = one deep link + the button presses that app needs, then the same ground truth
as the built-in tuners: the stick's own playback report (dumpsys media_session).

What each app takes on a Fire TV stick (sources and confidence: providers report; check
yours with  .venv/bin/python scripts/providers_check.py):
  Peacock    com.peacock.peacockfiretv    -n .../com.peacock.peacocktv.AmazonMainActivity
             https://www.peacocktv.com/deeplink?deeplinkData={"pvid":<id>,"type":"SLE","action":"PLAY"}
  Fubo       com.fubo.firetv.screen       https://link.fubo.tv/al1%3Fv%3D1%26a%3Dplay%26t%3Dchannel%26channel_id%3D<id>
  Paramount+ com.cbs.ott                  pplus://www.paramountplus.com/live-tv/stream/<slug>/<id>, OK at 8 s and 16 s
  HBO Max    com.hbo.hbonow / com.wbd.stream   https://play.hbomax.com/sport/<id>, OK at 10 s (Watch Live)
  Netflix    com.netflix.ninja            -n .../.MainActivity -d netflix://title/<id> -e amzn_deeplink_data <id>
  NFL        com.gotv.nflgamecenter.us.lite    nflctv://livestream/<id>  (NFL+ live games are phone-only)
  NBA        com.nbaimd.gametime.nba2011(.amazon)  gametime://game/<id>  (no ?source=), OK at 8 s
  Apple TV   com.apple.atve.amazon.appletv     https://tv.apple.com/us/sporting-event/<slug>/<id>
  DAZN       com.dazn                     https://open.dazn.com/media/open/<id>  (unverified)

Guardrails:
- Never press OK on a screen offering a purchase, trial or plan (is_purchase_screen): on
  Fire TV a blind OK there has started a paid subscription for other people.  OK is only
  pressed for a known prompt (profile picker, "still watching", "Watch Live") or on the
  app's schedule when the screen shows a play-type button.
- A sign-in / sign-up / blackout screen ends the tune at once with a plain-English reason;
  ProviderBook.learn() turns it into "signed out" / "not subscribed" / "blacked out".
- Links come from outside feeds (Apple, ESPN): a ref with unexpected characters is refused
  and the URL is single-quoted for the stick's shell.
- Attempt 1 uses the build's known activity; if that activity is missing, attempt 2 sends a
  plain VIEW intent. An uncertain launch failure is never retried automatically.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from urllib.parse import quote, urlsplit

from redzone.discovery.providers import BY_KEY, Verdict, classify_screen, is_purchase_screen, normalize_text
from redzone.tuners.adb import Stick
from redzone.tuners.profiles import preference, select_profile
from redzone.tuners.tuners import PROMPTS, Tuner, TuneResult, NewStream, require_cut

log = logging.getLogger(__name__)

# What a provider ref may contain (ids, slugs, an https URL).  Anything else is refused.
_SAFE_REF = re.compile(r"[A-Za-z0-9._~:/?&=%+-]{1,300}")
REF_ORIGINS = {
    "peacock": {("https", "www.peacocktv.com"), ("https", "peacocktv.com")},
    "paramount": {("pplus", "www.paramountplus.com"), ("https", "www.paramountplus.com")},
    "max": {("https", "play.hbomax.com"), ("https", "play.max.com")},
    "nflplus": {("nflctv", "livestream"), ("nflctv", "nflredzone")},
    "appletv": {("https", "tv.apple.com")},
}


def _shell_url(url: str) -> str:
    """The URL goes inside '...' on the stick's shell: a quote or newline could break out."""
    return re.sub(r"['\r\n]", "", url)


class AppTuner(Tuner):
    """Deep link -> wait for new video, clearing known prompts; stop at account walls."""
    app = ""
    role = ""
    name = ""
    components: dict[str, str] = {}     # package -> activity for `am start -n` (builds that need it)
    press_after: tuple[float, ...] = () # seconds after launch: press OK if a play button shows
    confirm_timeout = 30.0
    attempts = 2
    poll_s = 1.0
    screen_every_s = 4.0
    first_screen_s = 3.0

    def __init__(self, sleep=None, clock=None):
        self._sleep = sleep or asyncio.sleep     # injectable so tests run in milliseconds
        self._clock = clock or time.monotonic

    @property
    def packages(self) -> tuple[str, ...]:
        p = BY_KEY.get(self.app)
        return p.packages if p else ()

    def valid(self, ref: str) -> bool:
        """Provider-specific id check (beyond the safe-character check)."""
        return True

    def url(self, ref: str) -> str:
        raise NotImplementedError

    def command(self, ref: str, pkg: str, use_component: bool = True) -> str:
        """The `am start` line: the build's known activity when it needs one, else a VIEW
        intent addressed to the package (what Stick.view sends)."""
        url = _shell_url(self.url(ref))
        comp = self.components.get(pkg)
        if comp and use_component:
            return f"am start -W -n {pkg}/{comp} -a android.intent.action.VIEW -d '{url}'"
        return f"am start -W -a android.intent.action.VIEW -d '{url}' {pkg}"

    async def _tune(self, stick: Stick, ref: str, target: str, may_cut=None) -> TuneResult:
        t0 = self._clock()
        steps: list[str] = []
        pkg = stick.packages.get(self.role)
        if not pkg:
            return TuneResult(False, target, 0.0, steps,
                              f"{self.name} app isn't installed on {stick.name or stick.host}")
        if not _SAFE_REF.fullmatch(ref or ""):
            return TuneResult(False, target, 0.0, steps, f"{self.name}: that link doesn't look right")
        parsed = urlsplit(ref) if "://" in ref else None
        origin_ok = parsed is None or ((parsed.scheme, parsed.hostname) in REF_ORIGINS.get(self.app, set())
                                       and parsed.netloc.lower() == parsed.hostname)
        if not (_SAFE_REF.fullmatch(ref or "") and self.valid(ref) and origin_ok):
            return TuneResult(False, target, 0.0, steps, f"{self.name}: that link doesn't look right")
        before = await stick.playback(pkg)
        if stick.still_on(target, before):
            return TuneResult(True, target, self._clock() - t0, ["already on it"])
        require_cut(may_cut, steps)
        if may_cut is not None:
            await stick.ensure_awake(may_cut=lambda: require_cut(may_cut, steps, mutating=True))
        else:
            await stick.ensure_awake()
        use_comp = True
        for attempt in range(1, self.attempts + 1):
            if attempt > 1:
                before = await stick.playback(pkg)
            require_cut(may_cut, steps, mutating=True)
            r = await stick.shell(self.command(ref, pkg, use_comp), timeout=15)
            launched = r.ok and "error" not in r.out.lower()[:400]
            steps.append(f"deep link #{attempt}: {'ok' if launched else r.out.strip()[:80]}")
            if not launched:
                # Retry only a definite missing activity, never an uncertain timeout.
                if use_comp and ("does not exist" in r.out or "unable to resolve" in r.out):
                    use_comp = False
                    continue
                return TuneResult(False, target, self._clock() - t0, steps, "deep link failed; playback is unconfirmed")
            if "brought to the front" in r.out.lower():
                return TuneResult(False, target, self._clock() - t0, steps, "the app did not accept the game link")
            watch = NewStream(before)
            out = await self._watch(stick, pkg, steps, watch, may_cut)
            if out == "playing":
                stick.remember(target, watch.report or {})
                return TuneResult(True, target, self._clock() - t0, steps)
            if isinstance(out, Verdict):
                return TuneResult(False, target, self._clock() - t0, steps, out.message(self.name))
        return TuneResult(False, target, self._clock() - t0, steps, f"{self.name}: video never started")

    async def _watch(self, stick: Stick, pkg: str, steps: list[str], watch: NewStream, may_cut=None):
        """Poll the stick's playback report; every few seconds read the screen to clear a
        prompt or stop at a wall.  Returns "playing", a stopping Verdict, or None (timeout)."""
        start = self._clock()
        presses = list(self.press_after)
        next_screen = self.first_screen_s
        while (elapsed := self._clock() - start) < self.confirm_timeout:
            pb = await stick.playback(pkg)
            if watch.check(pb):
                steps.append(f"playing (confirmed by stick) after {elapsed:.0f}s")
                return "playing"
            due = bool(presses) and elapsed >= presses[0]
            if due or elapsed >= next_screen:
                next_screen = elapsed + self.screen_every_s
                if due:
                    presses.pop(0)
                verdict = await self._read_screen(stick, steps, scheduled=due, may_cut=may_cut)
                if verdict is not None:
                    return verdict
            await self._sleep(self.poll_s)
        return None

    async def _read_screen(self, stick: Stick, steps: list[str], scheduled: bool, may_cut=None) -> Verdict | None:
        text = normalize_text(await stick.screen_text())
        verdict = classify_screen(text)
        if verdict is not None and verdict.stops:
            steps.append(verdict.step)
            return verdict
        selected = await select_profile(stick, text, preference(stick, self.app, self.preferred_profile),
                                        steps, lambda: require_cut(may_cut, steps, mutating=True))
        if selected is not None:
            return None
        keys, why = self._keys_for(text, scheduled)
        if not keys:
            return None
        if is_purchase_screen(text):
            # Guardrail: OK here could buy something.  Wait instead; the tune may time out.
            steps.append("didn't press OK: this screen offers a purchase, trial or plan")
            return None
        require_cut(may_cut, steps, mutating=True)
        await stick.key(*keys)
        steps.append(f"pressed {'+'.join(k.replace('KEYCODE_', '') for k in keys)}: {why}")
        return None

    def _keys_for(self, text: str, scheduled: bool) -> tuple[list[str], str]:
        for prompt, keys in PROMPTS.items():
            if prompt in text:
                return list(keys), prompt
        return [], ""


class PeacockTuner(AppTuner):
    """Peacock.  Its Fire TV build only takes links through AmazonMainActivity (a plain
    VIEW intent goes nowhere there); type SLE + action PLAY starts the live event itself."""
    app = role = "peacock"
    name = "Peacock"
    components = {"com.peacock.peacockfiretv": "com.peacock.peacocktv.AmazonMainActivity"}
    press_after = (12.0,)

    def url(self, ref: str) -> str:
        if ref.startswith("https://"):
            return ref
        data = json.dumps({"pvid": ref, "type": "SLE", "action": "PLAY"}, separators=(",", ":"))
        return "https://www.peacocktv.com/deeplink?deeplinkData=" + quote(data, safe="")


class FuboTuner(AppTuner):
    """Fubo: a live channel by station id (FUBO_CHANNELS).  Its dispatch activity accepts
    only link.fubo.tv / fubotv:// links; this percent-encoded "al1" form tuned in ~12 s."""
    app = role = "fubo"
    name = "Fubo"
    confirm_timeout = 25.0

    def valid(self, ref: str) -> bool:
        return ref.isdigit()

    def url(self, ref: str) -> str:
        return "https://link.fubo.tv/al1" + quote(f"?v=1&a=play&t=channel&channel_id={ref}", safe="")


class ParamountTuner(AppTuner):
    """Paramount+: a live event by "<slug>/<id>" (Apple's punchout path).  The app stops on
    "Who's watching" and then a Watch Live page, so OK at ~8 s and ~16 s."""
    app = role = "paramount"
    name = "Paramount+"
    press_after = (8.0, 16.0)
    confirm_timeout = 35.0

    def url(self, ref: str) -> str:
        if "://" in ref:
            return ref
        return f"pplus://www.paramountplus.com/live-tv/stream/{ref.strip('/')}"


class MaxTuner(AppTuner):
    """HBO Max (TNT Sports games stream here): https links only.  It lands on "Watch Live /
    From Beginning"; OK at ~10 s takes the focused Watch Live."""
    app = role = "max"
    name = "HBO Max"
    components = {"com.hbo.hbonow": "com.wbd.beam.BeamActivity"}
    press_after = (10.0,)

    def url(self, ref: str) -> str:
        return ref if ref.startswith("https://") else f"https://play.hbomax.com/sport/{ref}"


class NetflixTuner(AppTuner):
    """Netflix (Christmas NFL games, live events).  Fire TV wants netflix://title/<id> plus
    the amzn_deeplink_data extra; the title page's focused button plays it."""
    app = role = "netflix"
    name = "Netflix"
    press_after = (6.0,)

    def valid(self, ref: str) -> bool:
        return ref.isdigit()

    def url(self, ref: str) -> str:
        return f"netflix://title/{ref}"

    def command(self, ref: str, pkg: str, use_component: bool = True) -> str:
        if use_component:
            return (f"am start -W -n {pkg}/.MainActivity -a android.intent.action.VIEW "
                    f"-d '{_shell_url(self.url(ref))}' -f 0x10000020 -e amzn_deeplink_data {ref}")
        return (f"am start -W -a android.intent.action.VIEW -d 'https://www.netflix.com/watch/{ref}' "
                f"-e source 30 {pkg}")


class NflTuner(AppTuner):
    """NFL app.  NFL+ shows live local/primetime games on phones and tablets only, so on a
    TV this mostly helps with NFL+ Premium's RedZone ("redzone")."""
    app = role = "nflplus"
    name = "NFL app"
    press_after = (8.0,)

    def url(self, ref: str) -> str:
        if "://" in ref:
            return ref
        if ref == "redzone":
            return "nflctv://nflredzone"
        return f"nflctv://livestream/{ref}?provider=lura"


class NbaTuner(AppTuner):
    """NBA app: gametime://game/<id> WITHOUT Apple's ?source=atv-search (with it the app
    only opened its home screen); the game page then needs OK on Watch Live."""
    app = role = "nba"
    name = "NBA app"
    press_after = (8.0,)

    def valid(self, ref: str) -> bool:
        return bool(re.fullmatch(r"\d{10}", ref))

    def url(self, ref: str) -> str:
        return f"gametime://game/{ref}"


class AppleTvTuner(AppTuner):
    """Apple TV app (MLB Friday Night Baseball, MLS): the game's tv.apple.com page."""
    app = role = "appletv"
    name = "Apple TV"
    press_after = (6.0,)

    def url(self, ref: str) -> str:
        return ref if ref.startswith("https://") else f"https://tv.apple.com/us/sporting-event/{ref}"


class DaznTuner(AppTuner):
    """DAZN (unverified on Fire TV): the web link Apple's punchout maps to."""
    app = role = "dazn"
    name = "DAZN"
    press_after = (8.0,)

    def url(self, ref: str) -> str:
        return f"https://open.dazn.com/media/open/{ref}"


class DiagnosingTuner:
    """Wraps a built-in tuner (YouTube TV, Prime, ESPN).  When its tune fails, read the
    screen once and name the reason (signed out / not subscribed / blacked out) in the
    error, so ProviderBook.learn() can use it.  Success costs nothing extra."""

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, attr):
        return getattr(self.inner, attr)

    @property
    def preferred_profile(self):
        return self.inner.preferred_profile

    @preferred_profile.setter
    def preferred_profile(self, value):
        self.inner.preferred_profile = value

    async def tune(self, stick: Stick, ref: str, target: str, may_cut=None) -> TuneResult:
        res = await self.inner.tune(stick, ref, target, may_cut=may_cut) if may_cut is not None \
            else await self.inner.tune(stick, ref, target)
        if res.ok:
            return res
        try:
            text = await stick.screen_text()
        except Exception:  # diagnosis is a bonus; never turn a failure into a crash
            return res
        v = classify_screen(text)
        if v is not None and v.stops:
            name = BY_KEY[self.app].name if self.app in BY_KEY else self.app
            res.steps.append(v.step)
            res.error = f"{v.message(name)} | {res.error}" if res.error else v.message(name)
        return res


EXTRA_TUNERS: dict[str, AppTuner] = {t.app: t for t in (
    PeacockTuner(), FuboTuner(), ParamountTuner(), MaxTuner(), NetflixTuner(), NflTuner(),
    NbaTuner(), AppleTvTuner(), DaznTuner())}


def register(tuners: dict, *, replace: bool = False, diagnose_builtin: bool = True) -> list[str]:
    """Add the extra app tuners to TUNERS (keys = target prefixes, e.g. "peacock:<pvid>").

    Existing entries are kept unless replace=True.  diagnose_builtin wraps the built-in
    youtubetv/prime/espn tuners so their failures also say "signed out" / "not subscribed"
    / "blacked out".  Safe to call twice.  Returns the keys that were added."""
    added = []
    for key, tuner in EXTRA_TUNERS.items():
        if key not in tuners or replace:
            tuners[key] = tuner
            added.append(key)
    if diagnose_builtin:
        for key in ("youtubetv", "prime", "espn"):
            t = tuners.get(key)
            if t is not None and not isinstance(t, DiagnosingTuner):
                tuners[key] = DiagnosingTuner(t)
    return added
