"""Find the YouTube TV watch code (tv.youtube.com/watch/<code>) of a channel we lack, on a SPARE stick.

    # no hardware: replay the recorded fixtures through the whole flow
    .venv/bin/python scripts/find_yttv_code.py --self-test

    # on a stick that is NOT the one on screen (it plays video and moves focus):
    .venv/bin/python scripts/find_yttv_code.py --stick 100.110.165.150 --spare --profile Serge
    .venv/bin/python scripts/find_yttv_code.py --stick 100.110.165.150 --spare --channels nbc --query "WRAL"

    # prove a code you found by hand (e.g. in a saved logcat), then save it:
    .venv/bin/python scripts/find_yttv_code.py --stick 100.110.165.150 --spare --channels nbcsn --candidate XXXXXXXXXXX

Per channel: open YouTube TV search with the channel name -> read the screen (screencap + Vision
OCR; YouTube TV's UI is visible there, only its video is DRM-black) -> walk the focus ring to the
channel's tile and select it -> the media session must name the channel ('..., NBCSN') -> collect
code candidates (YouTube Lounge now-playing id, media session, activity intents/recents, Cobalt
logcat, watch-next, screen text) -> prove one: tune a different channel, deep-link the candidate,
and the media session must come back to this channel -> save to data/channels.json (codes) and
data/yttv_verified.json. Screenshots, probe output and steps go to data/lab/find_yttv_code/ so a
miss can be read by hand.

Never sends HOME or WAKEUP (either can flip the TV over HDMI-CEC); a sleeping stick is left alone.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.discovery.yttv import Verdict, VerifyState, load_table  # noqa: E402
from redzone.screen_ocr import FocusBox, OCRResult, TextBox  # noqa: E402
from redzone.tuners.adb import RunResult  # noqa: E402
from redzone.tuners.prime_search import CapturedScreen  # noqa: E402
from redzone.tuners.yttv_search import hunt_channel, prove_code  # noqa: E402

DEFAULT_CHANNELS = ("nbcsn", "nbc", "nhlnetwork")
FIXTURES = ROOT / "tests" / "fixtures"
LAB = ROOT / "data" / "lab" / "find_yttv_code"
MAX_PROOFS = 4


# --------------------------------------------------------------------------- one channel

async def find_one(stick, observe, key: str, table, *, cm, state: VerifyState, profile: str = "",
                   query: str = "", decoy: str = "", candidate: str = "", lounge=None, save: bool = True,
                   out=print, sleep=asyncio.sleep, clock=time.monotonic) -> dict:
    """Hunt one channel and prove its code. Returns a plain report (also written by the caller)."""
    entry = table.channels.get(key)
    name = entry.name if entry else key
    report = {"channel": key, "name": name, "status": "stopped", "code": "", "reason": "", "candidates": []}
    if entry is None:
        report["reason"] = f"unknown channel key (known: {', '.join(sorted(table.channels))})"
        out(f"  {key}: {report['reason']}")
        return report
    others = {c for k, c in cm.codes.items() if k != key}
    decoy = decoy or cm.codes.get("espn") or next(iter(sorted(others)), "")
    if candidate:
        cands = [{"code": candidate, "source": "given on the command line"}]
        out(f"  {name}: proving the code you gave ({candidate})")
    else:
        out(f"  {name}: searching YouTube TV for {query or entry.search!r} ...")
        hunt = await hunt_channel(stick, observe, key=key, table=table, query=query, profile=profile,
                                  sleep=sleep, clock=clock, lounge=lounge)
        report.update(steps=hunt.steps, probes=hunt.probes, tile=hunt.tile, description=hunt.description)
        for step in hunt.steps:
            if step["action"] in ("open_search", "key", "profile", "watch_button"):
                out(f"    {step['at_s']:6.1f}s  {step['action']:12} {step['detail']}")
        if hunt.status != "confirmed":
            report["reason"] = hunt.reason
            out(f"  {name}: STOPPED - {hunt.reason}")
            return report
        out(f"  {name}: tile {hunt.tile!r} selected; stick plays {hunt.description!r}")
        cands = [c for c in hunt.candidates if c["code"] not in others and c["code"] != decoy]
        skipped = len(hunt.candidates) - len(cands)
        out(f"    code candidates: {', '.join(c['code'] + ' (' + c['source'] + ')' for c in cands) or 'none'}"
            + (f"  [{skipped} known codes of other channels ignored]" if skipped else ""))
    report["candidates"] = cands
    if not cands:
        report["reason"] = ("channel confirmed, but no code leaked on the stick; read the saved probe output, "
                            "or paste the link in the phone UI")
        out(f"  {name}: NO CODE - {report['reason']}")
        return report
    if not decoy:
        report["reason"] = "no decoy channel code to prove against (pass --decoy <code>)"
        out(f"  {name}: {report['reason']}")
        return report
    for cand in cands[:MAX_PROOFS]:
        out(f"    proving {cand['code']}: decoy {decoy} first, then the candidate ...")
        proven, detail = await prove_code(stick, key=key, code=cand["code"], table=table, decoy=decoy,
                                          sleep=sleep, clock=clock)
        now = time.time()
        state.record_code(cand["code"], key, Verdict("verified" if proven else "plays", key if proven else "",
                                                     detail), now, cand["source"])
        out(f"      {'PROVEN' if proven else 'not proven'}: {detail}")
        if proven:
            report.update(status="found", code=cand["code"], reason=detail, source=cand["source"])
            state.set_channel(key, cand["code"], "verified", now, f"in-app search + OCR; {detail}",
                              how="in-app search (OCR)")
            if save:
                cm.codes[key] = cand["code"]
                cm.save()
                state.save()
                out(f"  {name}: FOUND {cand['code']} -> saved to {cm.path} (codes.{key})")
            else:
                out(f"  {name}: FOUND {cand['code']} (not saved: --no-save)")
            return report
    if save:
        state.save()
    report["reason"] = "no candidate brought the channel back on a clean re-tune"
    out(f"  {name}: NO CODE - {report['reason']}")
    return report


# --------------------------------------------------------------------------- recorded fixtures

def _ocr(scene: dict) -> OCRResult:
    # [text, x, y, width, height(, confidence)] in normalized top-left coordinates
    boxes = tuple(TextBox(b[0], float(b[5]) if len(b) > 5 else 1.0, *map(float, b[1:5]))
                  for b in scene.get("boxes", []))
    focus = scene.get("focus")
    fb = (FocusBox(*focus[:4], 1.0, focus[5] if len(focus) > 5 else "", focus[4]),) if focus else ()
    return OCRResult("ok" if boxes else "empty", boxes, 1920, 1080, focus_boxes=fb,
                     focus_status="unique" if fb else "none")


class FixtureStick:
    """Plays back a recorded session: scenes change on keys, deep links change what plays."""

    def __init__(self, fx: dict, clock):
        self.fx, self.clock = fx, clock
        self.host = "fixture:5555"
        self.name = "fixture stick"
        self.packages = {"youtubetv": "com.amazon.firetv.youtube.tv"}
        self.scene = fx.get("idle_scene", "player")
        self.playing = fx.get("playing", "")
        self.calls: list[str] = []

    def _media(self) -> str:
        desc = self.fx.get("watch", {}).get(self.playing)
        if not desc:
            return ""
        return (f"    package={self.packages['youtubetv']}\n"
                f"      state=PlaybackState {{state={self.fx.get('media_state', 3)}, position={self.fx.get('positions', {}).get(self.playing, 1000)}, buffered position=0, speed=1.0, "
                f"updated={int(self.clock() * 1000)}, actions=0}}\n"
                f"      metadata: size=3, description={desc}\n")

    async def shell(self, cmd: str, timeout: float = 8.0) -> RunResult:
        self.calls.append(cmd)
        if cmd.startswith("dumpsys power"):
            return RunResult(True, f"  mWakefulness={self.fx.get('wakefulness', 'Awake')}\n", .01)
        if cmd.startswith("dumpsys media_session"):
            return RunResult(True, self._media(), .01)
        for label, text in self.fx.get("probes", {}).items():
            if label in cmd:
                return RunResult(True, text, .01)
        return RunResult(True, "", .01)

    async def key(self, *codes: str) -> RunResult:
        for code in codes:
            self.calls.append(f"key {code}")
            assert code not in ("KEYCODE_HOME", "KEYCODE_WAKEUP"), "fixture: CEC-flipping key sent"
            nxt = self.fx["scenes"].get(self.scene, {}).get("keys", {}).get(code)
            if nxt:
                if nxt.startswith("play:"):
                    self.playing, self.scene = nxt[5:], "player"
                else:
                    self.scene = nxt
        return RunResult(True, "", .01)

    async def view(self, url: str, package: str, wait: bool = False, fresh: bool = False) -> RunResult:
        self.calls.append(f"view {url}")
        m = re.search(r"/watch/([A-Za-z0-9_-]{11})", url)
        if m:
            self.playing, self.scene = m.group(1), "player"
        elif "/search" in url:
            self.scene = self.fx.get("search_scene_after_profile", "search") if self.scene == "after_profile" \
                else self.fx.get("start_scene", "search")
        return RunResult(True, "", .01)


async def run_fixture(path: Path, out=print) -> bool:
    fx = json.loads(path.read_text())
    now = [1000.0]
    clock = lambda: now[0]  # noqa: E731

    async def sleep(s):
        now[0] += s

    stick = FixtureStick(fx, clock)
    seq = [0]

    async def observe():
        seq[0] += 1
        now[0] += .3
        scene = fx["scenes"].get(stick.scene, {"boxes": [["(video)", .45, .5, .1, .03]]})
        return CapturedScreen(_ocr(scene), seq[0], now[0])

    class CM:
        path = Path("(fixture channels.json)")

        def __init__(self, codes):
            self.codes = dict(codes)

        def save(self):
            pass

    with tempfile.TemporaryDirectory() as d:
        state = VerifyState(Path(d) / "verified.json")
        cm = CM(fx.get("known_codes", {}))
        out(f"[fixture] {path.name}: {fx.get('about', '')}")
        report = await find_one(stick, observe, fx["channel"], load_table(), cm=cm, state=state,
                                profile=fx.get("profile", ""), lounge=None, out=out, sleep=sleep, clock=clock)
    want = fx.get("expect", {})
    ok = (report.get("code") == want["code"] and cm.codes.get(fx["channel"]) == want["code"]) if "code" in want \
        else (report["status"] != "found" and want.get("stopped", "") in report["reason"])
    bad_keys = [c for c in stick.calls if c in ("key KEYCODE_HOME", "key KEYCODE_WAKEUP")]
    ok = ok and not bad_keys
    out(f"[fixture] {path.name}: {'PASS' if ok else 'FAIL'}")
    return ok


# --------------------------------------------------------------------------- hardware

async def _hardware(a) -> int:
    from redzone.channels import ChannelMap
    from redzone.discovery.yttv import _connect
    from redzone.recorded import _capture
    from redzone.screen_ocr import prepare, recognize

    if not a.spare:
        print("Refusing: this plays video and moves focus on the stick. Use a stick that is NOT on the TV "
              "input people are watching, and add --spare to confirm.")
        return 2
    prep = await asyncio.to_thread(prepare)
    if prep.status != "ready":
        print(f"Vision OCR helper is not ready ({prep.status}: {prep.error}).")
        return 2
    stick = await _connect(a.stick)
    if stick is None:
        return 2
    try:
        hdmi = await stick.hdmi_port()
        print(f"Stick {stick.name or stick.host} ({stick.host}) on {hdmi or 'an unknown HDMI input'}; "
              f"YouTube TV package {stick.packages.get('youtubetv') or '?'}")
        run_dir = LAB / time.strftime("%Y%m%d-%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=True)
        seq = [0]

        async def observe():
            seq[0] += 1
            png = await _capture(stick.host)
            path = run_dir / f"{seq[0]:03d}.png"
            path.write_bytes(png)
            result = await asyncio.to_thread(recognize, path)
            return CapturedScreen(result, seq[0], time.monotonic())

        lounge = None
        if not a.no_lounge:
            from redzone.discovery import lounge as lounge_mod
            lounge = lounge_mod.now_playing
        table, state, cm = load_table(), VerifyState(), ChannelMap()
        profile = a.profile
        if not profile:
            from redzone.config import Config
            profile = (Config().get("providers.profiles", {}) or {}).get("youtubetv", "") or ""
        keys = [k.strip() for k in a.channels.split(",") if k.strip()]
        reports = []
        for i, key in enumerate(keys, 1):
            print(f"[{i}/{len(keys)}] {key}")
            rep = await find_one(stick, observe, key, table, cm=cm, state=state, profile=profile,
                                 query=a.query if len(keys) == 1 else "", decoy=a.decoy, candidate=a.candidate,
                                 lounge=lounge, save=not a.no_save)
            reports.append(rep)
            (run_dir / f"{key}.json").write_text(json.dumps(rep, indent=1, default=str))
        print("\nSummary:")
        for rep in reports:
            print(f"  {rep['name']}: {rep['status'].upper()} {rep['code']}  {rep['reason']}")
        print(f"Screenshots, probe output and steps: {run_dir}")
        return 0 if all(r["status"] == "found" for r in reports) else 1
    finally:
        await stick.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--self-test", nargs="*", metavar="FIXTURE",
                   help="replay recorded fixtures (default: tests/fixtures/yttv_find_code_*.json); no adb")
    p.add_argument("--stick", help="IP of the SPARE stick (required for hardware)")
    p.add_argument("--spare", action="store_true", help="confirm the stick is not the one people are watching")
    p.add_argument("--channels", default=",".join(DEFAULT_CHANNELS), help="channel keys, e.g. nbcsn,nbc,nhlnetwork")
    p.add_argument("--query", default="", help="search text (one channel only); default from the channel table")
    p.add_argument("--profile", default="", help="YouTube TV profile for 'Who's watching?' (e.g. Serge)")
    p.add_argument("--decoy", default="", help="a known code of another channel (default: ESPN's)")
    p.add_argument("--candidate", default="", help="skip the search; just prove this code and save it")
    p.add_argument("--no-lounge", action="store_true", help="don't ask the YouTube Lounge API for the now-playing id")
    p.add_argument("--no-save", action="store_true", help="report only; don't write data/channels.json")
    a = p.parse_args(argv)
    if a.self_test is not None:
        paths = [Path(x) for x in a.self_test] or sorted(FIXTURES.glob("yttv_find_code_*.json"))
        results = [asyncio.run(run_fixture(path)) for path in paths]
        print(f"self-test: {sum(results)}/{len(results)} fixtures passed")
        return 0 if results and all(results) else 1
    if not a.stick:
        p.error("--stick is required (or use --self-test)")
    return asyncio.run(_hardware(a))


if __name__ == "__main__":
    sys.exit(main())
