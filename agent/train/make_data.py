"""Fine-tuning data for the watchdog model: app status -> the right action, as chat examples.

The labels come from a teacher: agent/policy.rules() for everything it covers, plus the judgment
below for errors the rules have never seen (what the model is actually for). Each kind of new
error has many wordings; whole wordings are held out for the test set, so the score measures
carrying the judgment over to NEW error text, not memorizing it.

    .venv-agent/bin/python agent/train/make_data.py   ->  agent/train/data/{train,valid,test}.jsonl
Why: Builds fine-tuning data for the watchdog model: app status to action (docstring).
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agent.policy import SYSTEM, Obs, rules  # noqa: E402

OUT = Path(__file__).resolve().parent / "data"

# kind -> wordings. Index 0-1 of each list are TEST-ONLY wordings (never trained on).
NOVEL = {
    "core_loop": [
        "ERROR redzone.engine: decide loop failed: KeyError: 'competitions'",
        "ERROR redzone.engine: tick crashed: TypeError: unsupported operand type(s) for -: 'NoneType' and 'float'",
        "ERROR redzone.engine: poll crashed: AttributeError: 'NoneType' object has no attribute 'phase'",
        "ERROR redzone.decider: decide() raised IndexError: list index out of range",
        "ERROR redzone.engine: score loop failed: ZeroDivisionError: float division by zero",
        "ERROR redzone.engine: decide loop failed: ValueError: 'halftime_show' is not a valid Stoppage",
        "ERROR redzone.engine: tick crashed: RecursionError: maximum recursion depth exceeded",
        "ERROR redzone.decider: KeyError: '401873040' while ranking candidates",
    ],
    "stick_link": [
        "ERROR redzone.tuners.adb: ConnectionRefusedError: [Errno 61] Connect call failed ('100.110.165.181', 5555)",
        "WARNING redzone.tuners.adb: adb: device unauthorized. Please check the confirmation dialog on your device",
        "ERROR redzone.control: adb shell to 100.110.165.150:5555 failed: error: closed",
        "WARNING redzone.tuners.adb: adb: no devices/emulators found for 100.110.165.181:5555",
        "ERROR redzone.tuners.adb: OSError: [Errno 64] Host is down",
        "ERROR redzone.control: adb: protocol fault (couldn't read status): Connection reset by peer",
    ],
    "storage": [
        "ERROR redzone.store: sqlite3.OperationalError: database or disk is full",
        "ERROR redzone.recorder: OSError: [Errno 28] No space left on device: 'data/recordings/x.jsonl'",
        "ERROR redzone.store: sqlite3.OperationalError: disk I/O error",
        "ERROR redzone.reel: could not write data/reels/reel.tmp.mp4: disk quota exceeded",
        "ERROR redzone.config: could not save data/config.yaml: [Errno 28] No space left on device",
    ],
    "person": [
        "WARNING redzone.tuners.tuners: youtubetv: 'Sign in to continue' on screen; can't tune",
        "WARNING redzone.tuners.providers: peacock: profile picker needs a PIN",
        "ERROR redzone.fantasy: ESPN fantasy 401 Unauthorized: ESPN_SWID cookie expired",
        "WARNING redzone.tuners.tuners: prime: 'Your subscription has ended' on the detail page",
        "WARNING redzone.tuners.tuners: youtubetv: 'Too many devices are using this account'",
        "WARNING redzone.tv: TV input set to HDMI3 by someone with the remote",
    ],
    "upstream": [
        "WARNING redzone.sources.http: site.api.espn.com 503 Service Unavailable",
        "WARNING redzone.sources.http: httpx.ConnectError: [Errno 8] nodename nor servname provided, or not known",
        "WARNING redzone.sources.http: site.web.api.espn.com read timeout after 10s",
        "WARNING redzone.sources.fastcast: websocket closed 1006, reconnecting",
        "WARNING redzone.sources.http: api-web.nhle.com 429 Too Many Requests",
        "WARNING redzone.sources.http: httpx.RemoteProtocolError: peer closed connection without sending complete message body",
    ],
    "cosmetic": [
        "WARNING py.warnings: DeprecationWarning: datetime.utcnow() is deprecated",
        "WARNING asyncio: Executing <Task pending name='Task-12'> took 0.152 seconds",
        "WARNING redzone.web: GET /favicon.ico 404",
        "WARNING py.warnings: ResourceWarning: unclosed <socket.socket fd=23>",
        "WARNING redzone.views: logo cache miss for team 2440",
        "WARNING py.warnings: FutureWarning: 'H' is deprecated, use 'h'",
    ],
}


def teach_novel(o: Obs, kind: str, n: int) -> tuple[str, str, str]:
    """(action, target, why) for an observation whose only unexplained error is of this kind."""
    if kind == "core_loop":
        if n >= 5:
            if o.crunch_on_screen:
                return "notify", "", f"the app's main loop keeps crashing ({n}x) but crunch time is on: restart after it"
            return "restart_service", "", f"the app's main loop crashed {n} times in 15 minutes: restart it"
        return "none", "", f"one-off crash in the main loop ({n}x); it recovered"
    if kind == "stick_link":
        if o.sticks_offline:
            return "reconnect_stick", o.sticks_offline[0], "a stick's adb link broke: reconnect it"
        return "notify", "", "a stick's adb link is failing: check it's powered and on Wi-Fi"
    if kind == "storage":
        return "prune_disk", "", "the app can't write: free disk space"
    if kind == "person":
        return "notify", "", "an account or the remote needs a person: tell the user"
    if kind == "upstream":
        if n >= 10:
            return "notify", "", f"a score source keeps failing ({n}x in 15 min): internet or the site is down"
        return "none", "", "a score source hiccuped; it retries by itself"
    return "none", "", "harmless warning"


def sample(rng: random.Random, kind: str, wording: str) -> dict:
    o = Obs(api_ok=True, live_games=rng.choice([0, 0, 1, 2, 3, 5]),
            crunch_on_screen=rng.random() < 0.25, disk_free_mb=rng.choice([4000, 9000, 20000, 60000]),
            restarts_last_hour=rng.choice([0, 0, 0, 1, 2]), log_age_s=rng.uniform(0, 60))
    if o.crunch_on_screen and not o.live_games:
        o.live_games = 1
    if kind == "stick_link" and rng.random() < 0.6:
        o.sticks_offline = [rng.choice(["100.110.165.181:5555", "100.110.165.150:5555"])]
        o.offline_min = rng.uniform(5, 9)
    n = rng.choice([1, 1, 2, 3, 6, 8, 12, 25])
    o.errors = {wording: n}
    # Known harmless noise alongside, as on a real night.
    for sig in rng.sample(["ERROR redzone.engine: score bug read failed",
                           "ERROR redzone.service: asyncio.exceptions.CancelledError",
                           "WARNING asyncio: Unknown child process pid N, will report returncode N"], rng.randint(0, 2)):
        o.errors[sig] = rng.randint(1, 40)
    r = rules(o)
    if r is not None and r.action != "none":
        action, target, why = r.action, r.target, r.why
    else:
        action, target, why = teach_novel(o, kind, n)
        if kind != "stick_link" and o.sticks_offline:
            pass
    return {"messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": o.text()},
                         {"role": "assistant", "content": json.dumps({"action": action, "target": target, "why": why})}],
            "kind": kind, "label": action}


def rule_sample(rng: random.Random) -> dict:
    """Cases the hard rules cover (down, hung, disk, stick, known errors), so the model agrees with them."""
    o = Obs(live_games=rng.choice([0, 1, 3]), crunch_on_screen=False, disk_free_mb=rng.choice([800, 5000, 30000]),
            restarts_last_hour=rng.choice([0, 1]))
    pick = rng.randrange(5)
    if pick == 0:
        o.api_ok, o.api_fail_streak = False, rng.choice([2, 3, 5])
    elif pick == 1:
        o.live_games, o.log_age_s = max(1, o.live_games), rng.uniform(250, 900)
        o.heartbeat_age_s = rng.uniform(0, 20)   # a quiet log with a ticking app is not a hang (P-03): label stays 'none'
    elif pick == 2:
        o.sticks_offline, o.offline_min = ["100.110.165.181:5555"], rng.uniform(5, 40)   # past the app's own reconnect (P-09)
    elif pick == 3:
        o.errors = {"ERROR redzone.engine: score bug read failed": rng.randint(1, 120),
                    "ERROR redzone.engine: nhl enrich": rng.randint(1, 80)}
    else:
        o.errors = {"WARNING redzone.engine: switch to N still running after Ns: giving up on it": rng.randint(1, 6)}
    v = rules(o)
    return {"messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": o.text()},
                         {"role": "assistant", "content": v.json()}], "kind": "rule", "label": v.action}


def main(seed: int = 7) -> None:
    rng = random.Random(seed)
    train, test = [], []
    for kind, words in NOVEL.items():
        for i, w in enumerate(words):
            for _ in range(10 if i >= 2 else 6):
                (test if i < 2 else train).append(sample(rng, kind, w))
    train += [rule_sample(rng) for _ in range(120)]
    test += [rule_sample(rng) for _ in range(20)]
    rng.shuffle(train)
    valid, train = train[:40], train[40:]
    OUT.mkdir(parents=True, exist_ok=True)
    for name, rows in (("train", train), ("valid", valid), ("test", test)):
        with (OUT / f"{name}.jsonl").open("w") as f:
            for r in rows:
                f.write(json.dumps({"messages": r["messages"]}) + "\n")
        with (OUT / f"{name}.labels.jsonl").open("w") as f:
            for r in rows:
                f.write(json.dumps({"kind": r["kind"], "label": r["label"]}) + "\n")
    print({k: len(v) for k, v in (("train", train), ("valid", valid), ("test", test))})


if __name__ == "__main__":
    main()
