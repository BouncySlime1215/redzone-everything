"""Fine-tuning data for the watchdog model: app status -> the right action, as chat examples.

The labels come from a teacher: agent/policy.rules() first, for everything it covers, then
teach_novel() below for errors the rules have never seen (what the model is actually for).

Every kind of new error has 25+ wordings, split three ways BY WORDING, never by example:
    test   held out for the final grade only (evaluate.py)
    valid  held out for tuning (iters, layers, learning rate) and mlx_lm's validation loss
    train  everything the model learns from
A test wording is a different event from every train wording (different component AND
different failure), not a rephrasing of one; check_split() enforces no shared normalized
wording and low word overlap, and the generator refuses to run if a split leaks.

Statuses are built the way agent/watchdog.py sees a real night: signatures folded with its
signature() (numbers -> N), harmless known noise alongside, stderr tracebacks whose frames show
up as their own error lines, several unknown errors at once, and hard-rule situations (app
down, disk low, stick offline) on top of new errors so the model learns the rules win.

    .venv-agent/bin/python agent/train/make_data.py   ->  agent/train/data/{train,valid,test}.jsonl
"""
from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agent.policy import SAFE_STALE_MIN, STALE_FEED_S, SYSTEM, Obs, known, rules  # noqa: E402

OUT = Path(__file__).resolve().parent / "data"
STICKS = ["100.110.165.181:5555", "100.110.165.150:5555"]


def signature(msg: str) -> str:
    """Same folding as agent.watchdog.signature (copied so this script stays stdlib-only)."""
    s = re.sub(r"\d+(\.\d+)?", "N", msg)
    return re.sub(r"\s+", " ", s).strip()[:200]


# ------------------------------------------------------------------------------------------
# kind -> {"test": [...], "valid": [...], "train": [...]} wordings of errors no rule knows.
NOVEL: dict[str, dict[str, list[str]]] = {
    # The app's own decision loop raising. A one-off is shrugged off; a loop that keeps
    # crashing breaks switching, so restart (or tell the user, if crunch time is on screen).
    "core_loop": {
        "test": [
            "ERROR redzone.engine: tick crashed: RecursionError: maximum recursion depth exceeded",
            "ERROR redzone.engine: tick crashed: OverflowError: cannot convert float infinity to integer",
            "ERROR redzone.engine: decide loop failed: re.error: unterminated character set at position 4",
            "ERROR redzone.engine: tick crashed: asyncio.exceptions.InvalidStateError: invalid state",
            "ERROR redzone.engine: loop crashed: decimal.InvalidOperation: [<class 'decimal.ConversionSyntax'>]",
            "ERROR redzone.decider: tiebreak raised TypeError: 'NoneType' object is not subscriptable",
        ],
        "valid": [
            "ERROR redzone.engine: tick crashed: UnboundLocalError: cannot access local variable 'best' where it is not associated with a value",
            "ERROR redzone.engine: loop died: NotImplementedError: Stoppage.REVIEW",
            "ERROR redzone.engine: tick crashed: LookupError: <ContextVar name='night'>",
            "ERROR redzone.engine: decide loop failed: struct.error: unpack requires a buffer of 4 bytes",
            "ERROR redzone.priority: weight calc failed: TypeError: float() argument must be a string or a real number, not 'dict'",
        ],
        "train": [
            "ERROR redzone.engine: decide loop failed: KeyError: 'competitions'",
            "ERROR redzone.engine: tick crashed: TypeError: unsupported operand type(s) for -: 'NoneType' and 'float'",
            "ERROR redzone.engine: poll crashed: AttributeError: 'NoneType' object has no attribute 'phase'",
            "ERROR redzone.decider: decide() raised IndexError: list index out of range",
            "ERROR redzone.engine: score loop failed: ZeroDivisionError: float division by zero",
            "ERROR redzone.engine: decide loop failed: ValueError: 'halftime_show' is not a valid Stoppage",
            "ERROR redzone.decider: KeyError: '401873040' while ranking candidates",
            "ERROR redzone.engine: switch planner crashed: AssertionError: plan has two primaries",
            "ERROR redzone.scheduler: break planner failed: TypeError: '<' not supported between instances of 'NoneType' and 'int'",
            "ERROR redzone.engine: state build failed: json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)",
            "ERROR redzone.engine: Exception in callback Engine._on_tick(): StopIteration",
            "ERROR asyncio: Task exception was never retrieved: future: <Task finished name='engine-loop' coro=<Engine.run()> exception=KeyError('situation')>",
            "ERROR redzone.decider: rank failed: ValueError: max() arg is an empty sequence",
            "ERROR redzone.engine: decide loop failed: pydantic_core.ValidationError: 1 validation error for GameState clock",
            "ERROR redzone.engine: decide loop failed: RuntimeError: dictionary changed size during iteration",
            "ERROR redzone.decider: crunch check raised KeyError: 'possession'",
            "ERROR redzone.engine: tick crashed: AttributeError: 'Game' object has no attribute 'down_distance'",
            "ERROR redzone.engine: main loop error: ValueError: not enough values to unpack (expected 3, got 2)",
            "ERROR redzone.scheduler: next-visit crashed: TypeError: can't compare offset-naive and offset-aware datetimes",
            "ERROR redzone.engine: decide loop failed: TypeError: Game.__init__() missing 1 required positional argument: 'league'",
            "ERROR redzone.decider: lock rule crashed: AttributeError: 'str' object has no attribute 'get'",
            "ERROR redzone.engine: state merge failed: KeyError: 'linescores'",
            "ERROR redzone.engine: decide loop failed: SystemError: error return without exception set",
            "ERROR redzone.engine: tick crashed: ValueError: invalid literal for int() with base 10: '--'",
            "ERROR redzone.scheduler: catch-up planner raised IndexError: deque index out of range",
        ],
    },
    # Talking to a Fire TV stick over adb. The app reports offline sticks itself; when it does,
    # the hard rules already reconnect (or ask for power after 10 min). When no stick is listed
    # offline, the link trouble is invisible to the rules: tell the user.
    "stick_link": {
        "test": [
            "ERROR redzone.tuners.adb: OSError: [Errno 65] No route to host",
            "ERROR redzone.control: adb: failed to authenticate to 100.110.165.181:5555",
            "WARNING redzone.tuners.firetv: mdns: no Fire TV answered on _adb-tls-connect._tcp",
            "WARNING redzone.tuners.firetv: getprop sys.boot_completed on 100.110.165.150 returned nothing",
            "WARNING redzone.control: stick 100.110.165.150 rebooted itself (uptime 12 s)",
            "WARNING redzone.tuners.adb: adb server version (41) doesn't match this client (39); killing...",
        ],
        "valid": [
            "ERROR redzone.tuners.adb: OSError: [Errno 64] Host is down",
            "ERROR redzone.tuners.adb: cannot connect to daemon at tcp:5037: Connection refused",
            "ERROR redzone.tuners.adb: ssl.SSLError: [SSL: WRONG_VERSION_NUMBER] during adb pairing",
            "ERROR redzone.tuners.adb: ConnectionAbortedError: [Errno 53] Software caused connection abort",
            "ERROR redzone.tuners.firetv: HDMI stick 2 stopped responding to ping",
        ],
        "train": [
            "ERROR redzone.tuners.adb: ConnectionRefusedError: [Errno 61] Connect call failed ('100.110.165.181', 5555)",
            "WARNING redzone.tuners.adb: adb: device unauthorized. Please check the confirmation dialog on your device",
            "ERROR redzone.control: adb shell to 100.110.165.150:5555 failed: error: closed",
            "WARNING redzone.tuners.adb: adb: no devices/emulators found for 100.110.165.181:5555",
            "ERROR redzone.control: adb: protocol fault (couldn't read status): Connection reset by peer",
            "ERROR redzone.tuners.adb: failed to connect to '100.110.165.181:5555': Operation timed out",
            "WARNING redzone.tuners.adb: adb: device '100.110.165.150:5555' not found",
            "ERROR redzone.control: input keyevent 3 failed: exit status 255 (stick 100.110.165.181)",
            "ERROR redzone.tuners.firetv: am start com.amazon.firebat timed out after 8 s on 100.110.165.150",
            "ERROR redzone.control: screencap from stick failed: adb: error: failed to read response",
            "ERROR redzone.tuners.adb: BrokenPipeError: [Errno 32] Broken pipe (adb transport 100.110.165.181:5555)",
            "WARNING redzone.tuners.firetv: stick 100.110.165.150 did not answer dumpsys window within 5 s",
            "WARNING redzone.tuners.adb: stick 100.110.165.181 dropped off Wi-Fi (ARP entry incomplete)",
            "ERROR redzone.tuners.firetv: wake stick 100.110.165.150: adb returned 'error: device still authorizing'",
            "ERROR redzone.tuners.adb: TimeoutError: adb connect 100.110.165.181:5555 took longer than 10 s",
            "ERROR redzone.control: adb -s 100.110.165.150:5555 shell: exit 255, no output",
            "ERROR redzone.tuners.adb: socket.gaierror: [Errno 8] firetv-den.local could not be resolved",
            "ERROR redzone.control: stick 100.110.165.181 heartbeat missed 3 times",
            "ERROR redzone.tuners.adb: adb: insufficient permissions for device 100.110.165.181:5555",
            "ERROR redzone.tuners.adb: asyncio.TimeoutError waiting for adb shell on 100.110.165.181:5555",
            "ERROR redzone.control: pm list packages on 100.110.165.150 failed: closed",
            "WARNING redzone.tuners.firetv: stick 100.110.165.181 answered ping but adbd is not listening",
            "ERROR redzone.tuners.adb: adb pull of /sdcard/screen.png from 100.110.165.150 failed: transport error",
            "WARNING redzone.control: stick 100.110.165.150 is on the wrong Wi-Fi network (169.254.x self-assigned)",
            "ERROR redzone.tuners.firetv: monkey -p com.google.android.youtube.tvunplugged on 100.110.165.181 hung",
        ],
    },
    # The app can't write because the disk (or a quota) is full. Freeing the rebuildable caches
    # is the fix, whatever wording the failure comes in.
    "storage": {
        "test": [
            "ERROR redzone.recorder: AVAssetWriter failed: -11807 'Cannot Save' (not enough storage)",
            "WARNING redzone.service: macOS low disk space warning: purgeable space exhausted",
            "ERROR redzone.clips: tempfile: no usable temporary directory found in ['/tmp', '/var/tmp']",
            "WARNING redzone.store: WAL checkpoint failed: SQLITE_FULL",
            "ERROR redzone.store: backup to data/backup.db aborted: SQLITE_FULL (13)",
            "ERROR redzone.cache: diskcache.core: Timeout: cache write stalled, disk at 100%",
        ],
        "valid": [
            "ERROR redzone.store: sqlite3.OperationalError: disk I/O error",
            "WARNING redzone.cache: volume /System/Volumes/Data at 99% capacity",
            "ERROR redzone.cache: thumbnail save failed: [Errno 69] Disc quota exceeded",
            "ERROR redzone.clips: HLS segment write failed: storage is full",
            "ERROR redzone.clips: screenrecord buffer flush failed: ENOSPC",
        ],
        "train": [
            "ERROR redzone.store: sqlite3.OperationalError: database or disk is full",
            "ERROR redzone.clips: could not write data/reels/reel.tmp.mp4: disk quota exceeded",
            "ERROR redzone.recorder: OSError: [Errno 122] Disk quota exceeded",
            "ERROR redzone.recorder: write to data/recordings/2026-11-02.jsonl returned short write (0 of 4096 bytes)",
            "ERROR redzone.clips: ffmpeg: error writing trailer of data/reels/clip_77.mp4: device full",
            "ERROR redzone.store: sqlite3.OperationalError: unable to open database file (out of disk)",
            "ERROR redzone.cache: could not save logo 2440.png: not enough free space on volume",
            "ERROR redzone.recorder: json dump interrupted: disk full while writing frame log",
            "ERROR redzone.config: atomic save failed: rename of config.yaml.tmp left a zero-byte file (disk full?)",
            "ERROR redzone.store: sqlite3.OperationalError: database or disk is full (while inserting into plays)",
            "ERROR redzone.logos: cannot write cache/logos: the volume has insufficient free space",
            "ERROR redzone.recorder: write() failed: errno ENOSPC on data/recordings",
            "WARNING redzone.clips: skipping clip: only 40 MB left in data/reels",
            "ERROR redzone.service: could not rotate service.log: file system full",
            "ERROR redzone.store: sqlite3.OperationalError: database or disk is full in VACUUM",
            "ERROR redzone.logos: imageio: failed to write frame, the disk ran out of room",
            "ERROR redzone.recorder: numpy.save failed: OSError: wrote 0 bytes, disk full",
            "ERROR redzone.config: cannot persist settings: out of disk space",
            "WARNING redzone.cache: Spotlight: volume nearly full, indexing paused",
            "ERROR redzone.clips: screenshot of the TV feed not saved: free space below 50 MB",
            "ERROR redzone.store: journal file data/redzone.db-journal could not grow (volume full)",
            "WARNING redzone.service: Time Machine local snapshots are filling the disk (98% used)",
            "ERROR redzone.recorder: io.BufferedWriter flush failed: [Errno 28] on data/recordings",
            "ERROR redzone.cache: pip/huggingface cache write failed: not enough space on disk",
        ],
    },
    # Something only a person can fix: sign-ins, PINs, subscriptions, payment, a dialog on the
    # TV, or someone using the remote. Always tell the user.
    "person": {
        "test": [
            "ERROR redzone.tuners.providers: fox one: TV provider verification required (activate at fox.com/activate)",
            "ERROR redzone.tuners.providers: nfl+: account locked after too many sign-in attempts",
            "WARNING redzone.tuners.tuners: dazn: 'Confirm you're over 18' age gate",
            "WARNING redzone.tv: CEC: TV reports the user switched to the built-in Netflix app",
            "WARNING redzone.tuners.providers: fubo: 'Your card was declined'",
            "WARNING redzone.tuners.tuners: firetv: 'Accept updated Terms of Use' screen",
        ],
        "valid": [
            "WARNING redzone.tuners.providers: sling: 'Verify your email to keep watching'",
            "WARNING redzone.tv: TV turned off with the remote while games are live",
            "ERROR redzone.tuners.providers: youtubetv: 2-step verification code requested",
            "WARNING redzone.tuners.tuners: firetv: low battery warning on the Alexa remote",
            "ERROR redzone.tuners.providers: espn+: 'This account is in use on another device'",
        ],
        "train": [
            "WARNING redzone.tuners.tuners: youtubetv: 'Sign in to continue' on screen; can't tune",
            "WARNING redzone.tuners.providers: peacock: profile picker needs a PIN",
            "ERROR redzone.fantasy: ESPN fantasy 401 Unauthorized: ESPN_SWID cookie expired",
            "WARNING redzone.tuners.tuners: prime: 'Your subscription has ended' on the detail page",
            "WARNING redzone.tuners.tuners: youtubetv: 'Too many devices are using this account'",
            "WARNING redzone.tv: TV input set to HDMI3 by someone with the remote",
            "WARNING redzone.tuners.providers: espn+: 'Your session expired. Log in again'",
            "WARNING redzone.tuners.tuners: paramount+: 'Who's watching?' profile screen blocks playback",
            "WARNING redzone.tuners.tuners: netflix: 'Update payment method' banner; playback blocked",
            "WARNING redzone.tv: TV is showing 'Software update available' dialog",
            "WARNING redzone.tuners.tuners: peacock: 'This content is not available in your location'",
            "WARNING redzone.fantasy: Sleeper token revoked; reconnect fantasy league in settings",
            "ERROR redzone.tuners.providers: max: 'Your free trial has ended' - choose a plan",
            "WARNING redzone.tuners.tuners: prime: parental controls PIN required for this title",
            "WARNING redzone.tv: TV says 'No signal' on HDMI2 (cable unplugged?)",
            "ERROR redzone.fantasy: Yahoo OAuth refresh failed: invalid_grant (user must re-authorize)",
            "WARNING redzone.tuners.providers: espn: 'Your TV provider sign-in has expired'",
            "WARNING redzone.tuners.tuners: apple tv+: 'Choose a plan to keep watching MLS Season Pass'",
            "WARNING redzone.tuners.tuners: youtubetv: 'Confirm your home area' location check",
            "WARNING redzone.tv: HDMI-CEC pairing request needs to be accepted on the TV",
            "WARNING redzone.tuners.tuners: prime: 'Rent or buy' prompt: game isn't included in the plan",
            "WARNING redzone.fantasy: ESPN fantasy league is private: invite not accepted",
            "WARNING redzone.tv: someone switched the soundbar to Bluetooth (TV audio off)",
            "ERROR redzone.tuners.providers: nba league pass: 'Blackout: sign in with your TV provider'",
            "WARNING redzone.tuners.tuners: firetv: 'Create a profile to continue' screen",
        ],
    },
    # A score source (ESPN, NHL, MLB, ...) failing. It retries by itself; only when it keeps
    # failing (10+ in 15 min) does a person need to know the internet or the site is down.
    "upstream": {
        "test": [
            "WARNING redzone.sources.http: cdn.nba.com returned HTML instead of JSON (maintenance page)",
            "WARNING redzone.sources.fastcast: no heartbeat from fastcast for 90 s",
            "WARNING redzone.sources.http: h2 stream reset by statsapi.mlb.com (REFUSED_STREAM)",
            "WARNING redzone.sources.ncaa: data.ncaa.com stale: lastUpdated 6 min old",
            "WARNING redzone.sources.http: ssl.SSLEOFError: EOF occurred in violation of protocol (site.api.espn.com)",
            "WARNING redzone.sources.nba: cdn.nba.com playbyplay for 0022500211 frozen at period 2 (no new actions)",
        ],
        "valid": [
            "WARNING redzone.sources.http: statsapi.mlb.com 502 Bad Gateway",
            "WARNING redzone.sources.http: httpx.PoolTimeout: no connection available within 5 s",
            "WARNING redzone.sources.fastcast: wss://fastcast.semfs.engsvc.go.com handshake failed: 403",
            "WARNING redzone.sources.nhl: api-web.nhle.com play-by-play lags the scoreboard by 4 min",
            "WARNING redzone.sources.http: api.sleeper.app 503 (fantasy scores unavailable)",
        ],
        "train": [
            "WARNING redzone.sources.http: site.api.espn.com 503 Service Unavailable",
            "WARNING redzone.sources.http: site.api.espn.com served a cached scoreboard 7 min old (Age header)",
            "WARNING redzone.sources.http: site.web.api.espn.com read timeout after 10s",
            "WARNING redzone.sources.fastcast: websocket closed 1006, reconnecting",
            "WARNING redzone.sources.http: api-web.nhle.com 429 Too Many Requests",
            "WARNING redzone.sources.http: httpx.RemoteProtocolError: peer closed connection without sending complete message body",
            "WARNING redzone.sources.http: ssl.SSLCertVerificationError for site.api.espn.com: certificate has expired",
            "WARNING redzone.sources.http: httpx.ReadError: [Errno 54] Connection reset by peer (site.api.espn.com)",
            "WARNING redzone.sources.http: site.api.espn.com 500 Internal Server Error",
            "WARNING redzone.sources.nfl: feeds.nfl.com 404 for gamecenter 2026110205",
            "WARNING redzone.sources.http: httpx.ConnectTimeout: timed out connecting to api-web.nhle.com",
            "WARNING redzone.sources.http: gateway.espn.com 520 (Cloudflare origin error)",
            "WARNING redzone.sources.http: site.api.espn.com scoreboard JSON missing 'events' (partial response)",
            "WARNING redzone.sources.http: site.api.espn.com 504 Gateway Timeout",
            "WARNING redzone.sources.http: DNS lookup for site.api.espn.com took 9 s",
            "WARNING redzone.sources.http: httpx.ReadTimeout on site.web.api.espn.com/apis/v2/scoreboard",
            "WARNING redzone.sources.fastcast: subscription to gp-football-nfl rejected, will retry",
            "WARNING redzone.sources.http: ConnectionResetError: [Errno 54] while reading chunked body from cdn.espn.com",
            "WARNING redzone.sources.http: api-web.nhle.com 403 Forbidden (rate limited by WAF)",
            "WARNING redzone.sources.http: httpx.ConnectError: [Errno 61] Connection refused (site.api.espn.com:443)",
            "ERROR redzone.sources.mlb: statsapi.mlb.com live feed JSON truncated at byte 40960",
            "WARNING redzone.sources.http: site.api.espn.com answered 200 with an empty body",
            "WARNING redzone.sources.nhl: api-web.nhle.com gamecenter 2025020211 returned stale clock",
            "WARNING redzone.sources.http: httpx.ProxyError: 502 from upstream proxy",
            "WARNING redzone.sources.fastcast: message checksum mismatch, resyncing from REST",
        ],
    },
    # Harmless: deprecations, slow-callback notes, 404s for icons, missing cosmetic assets.
    "cosmetic": {
        "test": [
            "WARNING urllib3: NotOpenSSLWarning: urllib3 v2 only supports OpenSSL 1.1.1+",
            "WARNING PIL.PngImagePlugin: iCCP: known incorrect sRGB profile",
            "WARNING uvicorn.error: Invalid HTTP request received.",
            "WARNING redzone.views: game 401773 has no venue name",
            "WARNING watchfiles.main: 3 changes detected",
            "WARNING redzone.web: HEAD / 405 Method Not Allowed",
        ],
        "valid": [
            "WARNING py.warnings: SyntaxWarning: invalid escape sequence '\\d'",
            "WARNING redzone.views: font 'SF Pro Rounded' not found, falling back to Helvetica",
            "WARNING asyncio: socket.send() raised exception.",
            "WARNING redzone.views: ticker text truncated to 80 chars",
            "WARNING py.warnings: EncodingWarning: 'encoding' argument not specified",
        ],
        "train": [
            "WARNING py.warnings: DeprecationWarning: datetime.utcnow() is deprecated",
            "WARNING asyncio: Executing <Task pending name='Task-12'> took 0.152 seconds",
            "WARNING redzone.web: GET /favicon.ico 404",
            "WARNING py.warnings: ResourceWarning: unclosed <socket.socket fd=23>",
            "WARNING redzone.views: logo cache miss for team 2440",
            "WARNING py.warnings: FutureWarning: 'H' is deprecated, use 'h'",
            "WARNING py.warnings: UserWarning: pkg_resources is deprecated as an API",
            "WARNING redzone.web: GET /apple-touch-icon.png 404",
            "WARNING redzone.views: team color missing for 'UMass', using gray",
            "WARNING redzone.web: slow request GET /api/state took 1.3 s",
            "WARNING redzone.web: websocket client 192.168.1.40 sent malformed ping",
            "WARNING redzone.views: headshot for player 4430807 not found",
            "WARNING py.warnings: PydanticDeprecatedSince20: `dict` method is deprecated",
            "WARNING redzone.web: GET /robots.txt 404",
            "WARNING redzone.views: abbreviation 'LV' maps to two teams, using first",
            "WARNING matplotlib.font_manager: findfont: Font family 'Inter' not found",
            "WARNING redzone.web: client disconnected before response finished (GET /api/stream)",
            "WARNING py.warnings: InsecureRequestWarning: Unverified HTTPS request to 192.168.1.20",
            "WARNING py.warnings: DeprecationWarning: There is no current event loop",
            "WARNING redzone.views: logo for 'Kennesaw St' is SVG, rasterizing",
            "WARNING redzone.views: broadcast network logo missing for 'NBCSN'",
            "WARNING redzone.views: play description has unknown emoji, dropped",
            "WARNING redzone.web: 304 Not Modified for /static/app.js served from cache",
            "WARNING py.warnings: PendingDeprecationWarning: the load_module() method is deprecated",
            "WARNING redzone.views: team record missing for 'Saint Mary's', hiding it",
        ],
    },
    # NEW kind. The app process running out of memory, file descriptors, threads or workers, or
    # its event loop wedged. This never heals by itself and gets worse, even at a low count, so
    # restart the service; if crunch time is on screen, tell the user and restart after.
    "resource": {
        "test": [
            "ERROR redzone.vision: cv2.error: (-4:Insufficient memory) Failed to allocate 8294400 bytes",
            "ERROR redzone.service: kqueue: too many registered events",
            "ERROR redzone.engine: deadlock detected: switch lock held 400 s",
            "ERROR redzone.service: zombie helper processes piling up: 212 defunct children",
            "ERROR redzone.service: selectors: filedescriptor out of range in select()",
            "WARNING redzone.engine: GC took 9.8 s, 2.1M objects tracked",
        ],
        "valid": [
            "ERROR redzone.vision: Metal: failed to allocate buffer (out of memory)",
            "ERROR redzone.service: thread pool exhausted: 64 workers busy for 120 s",
            "WARNING redzone.engine: 1240 pending asyncio tasks (expected < 50)",
            "WARNING redzone.service: swap used 6.1 GB; app pages being compressed",
            "ERROR redzone.clips: ffmpeg killed by signal 9 (jetsam: memory limit)",
        ],
        "train": [
            "ERROR redzone.engine: MemoryError",
            "ERROR redzone.service: OSError: [Errno 24] Too many open files",
            "ERROR redzone.engine: RuntimeError: can't start new thread",
            "ERROR redzone.clips: concurrent.futures.process.BrokenProcessPool: A process in the process pool was terminated abruptly",
            "WARNING redzone.service: resident memory 3.9 GB and growing (leak?)",
            "ERROR asyncio: socket.accept() out of system resource: [Errno 24] Too many open files",
            "ERROR redzone.service: BlockingIOError: [Errno 35] Resource temporarily unavailable (fork)",
            "WARNING redzone.engine: event loop lag 14.2 s (blocked)",
            "ERROR redzone.service: subprocess spawn failed: [Errno 23] Too many open files in system",
            "WARNING redzone.service: macOS memory pressure critical; process being throttled",
            "ERROR redzone.engine: asyncio queue full (maxsize 1000): switch events backing up",
            "ERROR redzone.vision: numpy.core._exceptions._ArrayMemoryError: Unable to allocate 1.2 GiB",
            "ERROR redzone.web: uvicorn: [Errno 24] Too many open files accepting connection",
            "ERROR redzone.service: sqlite3.OperationalError: database is locked (held 300 s by a stuck writer)",
            "ERROR redzone.vision: torch.OutOfMemoryError: MPS backend out of memory",
            "ERROR redzone.service: _posixsubprocess: fork failed: Cannot allocate memory",
            "ERROR redzone.engine: watchdog timer fired: engine heartbeat 95 s late",
            "ERROR redzone.vision: CoreML: Unable to allocate compute buffer",
            "WARNING redzone.engine: switch queue has 340 stale commands, oldest 600 s",
            "ERROR redzone.service: asyncio: Too many file descriptors for the default selector",
            "WARNING redzone.service: helper process count 180 and rising (limit 266)",
            "ERROR redzone.vision: frame buffer pool exhausted, dropping all frames",
            "ERROR redzone.engine: executor shut down: cannot schedule new futures after interpreter shutdown",
            "WARNING redzone.service: open sockets 9800 (ulimit 10240)",
            "ERROR redzone.clips: posix_spawn failed: Resource temporarily unavailable",
        ],
    },
    # NEW kind. macOS (or the file system) refusing the app access: Screen Recording, Apple
    # events, keychain, quarantine, read-only files. A restart can't grant permission; a person
    # must, so tell the user.
    "permission": {
        "test": [
            "ERROR redzone.vision: Camera access denied (AVAuthorizationStatusDenied)",
            "ERROR redzone.service: Keychain: User interaction is not allowed (-25308)",
            "ERROR redzone.service: Gatekeeper blocked 'ffmpeg': cannot be opened because the developer cannot be verified",
            "ERROR redzone.store: sqlite3.OperationalError: attempt to write a readonly database",
            "ERROR redzone.service: Microphone access denied (needed for crowd-noise detection)",
            "ERROR redzone.tv: AppleScript error: Script Editor is not allowed assistive access",
        ],
        "valid": [
            "WARNING redzone.vision: CGDisplayStream returned nil: screen capture access denied",
            "ERROR redzone.service: keyring: no password found for 'redzone/ntfy' in login keychain",
            "ERROR redzone.recorder: [Errno 30] Read-only file system: 'data/recordings'",
            "ERROR redzone.tuners.adb: adb: permission denied launching /opt/homebrew/bin/adb (quarantined)",
            "ERROR redzone.service: Operation not permitted: 'data/clips' (sandbox/TCC on Documents folder)",
        ],
        "train": [
            "ERROR redzone.vision: screencapture: could not create image from display (Screen Recording permission?)",
            "ERROR redzone.service: PermissionError: [Errno 13] Permission denied: 'data/agent.jsonl'",
            "ERROR redzone.tv: osascript: Not authorized to send Apple events to System Events (-1743)",
            "ERROR redzone.tuners.adb: PermissionError: [Errno 1] Operation not permitted: '/opt/homebrew/bin/adb'",
            "ERROR redzone.config: PermissionError: [Errno 13] Permission denied: 'data/config.yaml'",
            "ERROR redzone.vision: SCStreamError: The user declined TCC for application, window, display capture",
            "ERROR redzone.service: codesign: helper killed: code signature invalid after update",
            "ERROR redzone.service: PermissionError: [Errno 13] Permission denied: '~/Library/LaunchAgents'",
            "ERROR redzone.vision: ScreenCaptureKit: application not authorized to capture",
            "ERROR redzone.service: Local Network access denied for Python (macOS privacy setting)",
            "ERROR redzone.tv: osascript: execution error: Not authorized to send Apple events to Music (-1743)",
            "ERROR redzone.service: os.chmod failed: [Errno 1] Operation not permitted: 'data/state.json'",
            "ERROR redzone.vision: Quartz: CGWindowListCreateImage returned NULL (no screen recording permission)",
            "ERROR redzone.service: xattr com.apple.quarantine on python3.13 blocks exec",
            "ERROR redzone.service: notifications not allowed for Python in System Settings",
            "ERROR redzone.config: cannot replace data/config.yaml: file is locked (uchg flag set)",
            "ERROR redzone.service: Full Disk Access needed to read ~/Library/Containers/com.amazon",
            "ERROR redzone.store: sqlite3.OperationalError: unable to open database: owned by root",
            "ERROR redzone.service: launchd: Service could not initialize: permission denied for WorkingDirectory",
            "ERROR redzone.vision: AXIsProcessTrusted() is false: Accessibility not granted to python3",
            "ERROR redzone.service: tcc: kTCCServiceScreenCapture revoked after macOS update",
            "ERROR redzone.recorder: shutil.move failed: [Errno 13] Permission denied: 'data/archive'",
            "ERROR redzone.service: SecItemCopyMatching failed: errSecAuthFailed for 'ESPN_SWID'",
            "ERROR redzone.tv: osascript: System Events got an error: python3 is not allowed to send keystrokes",
            "ERROR redzone.service: profile installed by MDM blocks screen recording for unsigned apps",
        ],
    },
    # NEW kind. A part of the app other than its core loop (score feeds, the screen reader, the
    # catch-up clips, stick control, the in-app supervisor) keeps crashing. A one-off recovers
    # (none); 5+ in 15 min: restart just that part (restart_component), which never touches the TV.
    "component": {
        "test": [
            "ERROR redzone.vision: frame grabber thread died: ValueError: cannot reshape array of size 0 into shape (1080,1920,4)",
            "ERROR redzone.supervisor: rule_screen crashed: StopIteration raised inside a generator",
            "ERROR redzone.sources.fastcast: push-feed task died: KeyError: 'pl'",
            "ERROR redzone.clips: clip cutter crashed: subprocess.CalledProcessError: ffmpeg returned non-zero exit status 234",
            "ERROR redzone.control: switch worker crashed: BrokenPipeError writing to the CEC helper",
            "ERROR redzone.screen.scorebug: OCR worker died: cv2.error: (-215:Assertion failed) !_src.empty()",
        ],
        "valid": [
            "ERROR redzone.supervisor: rule_feeds raised TypeError: 'float' object is not iterable",
            "ERROR redzone.sources.espn: scoreboard poller task crashed: UnicodeDecodeError: 'utf-8' codec can't decode byte 0x8b",
            "ERROR redzone.vision: capture loop exited: OSError: [Errno 9] Bad file descriptor",
            "ERROR redzone.clips: highlight packer crashed: KeyError: 'duration'",
            "ERROR redzone.control: standby tuner task died: ValueError: unknown app 'peacock2'",
        ],
        "train": [
            "ERROR redzone.vision: screen reader crashed: ZeroDivisionError: division by zero",
            "ERROR redzone.vision: capture task died: RuntimeError: SCStream stopped unexpectedly",
            "ERROR redzone.vision: frame diff failed: ValueError: operands could not be broadcast together",
            "ERROR redzone.screen.boundary: boundary detector crashed: IndexError: list index out of range",
            "ERROR redzone.screen.scorebug: scorebug reader task died: TypeError: 'NoneType' object is not iterable",
            "ERROR redzone.sources.espn: poller crashed: KeyError: 'competitors'",
            "ERROR redzone.sources.nhl: gamecenter task died: AttributeError: 'list' object has no attribute 'get'",
            "ERROR redzone.sources.fastcast: websocket task crashed: json.decoder.JSONDecodeError: Unterminated string",
            "ERROR redzone.sources.mlb: live-feed task crashed: TypeError: string indices must be integers",
            "ERROR redzone.sources.merger: merge task died: RuntimeError: dictionary changed size during iteration",
            "ERROR redzone.clips: clip writer crashed: ValueError: negative duration",
            "ERROR redzone.clips: reel builder task died: AssertionError: clips out of order",
            "ERROR redzone.clips: thumbnailer crashed: PIL.UnidentifiedImageError: cannot identify image file",
            "ERROR redzone.supervisor: rule_adb crashed: KeyError: 'state'",
            "ERROR redzone.supervisor: supervisor loop died: TypeError: '>' not supported between 'NoneType' and 'float'",
            "ERROR redzone.supervisor: nightly report task crashed: ValueError: max() arg is an empty sequence",
            "ERROR redzone.control: command queue worker died: asyncio.exceptions.InvalidStateError: result is already set",
            "ERROR redzone.control: hdmi map task crashed: KeyError: '100.110.165.150:5555'",
            "ERROR redzone.control: standby keeper crashed: AttributeError: 'Stick' object has no attribute 'app'",
            "ERROR redzone.tuners.tuners: tune worker died: IndexError: pop from empty list",
            "ERROR redzone.vision: OCR helper exited with code 1: vision_ocr: fatal error: unexpectedly found nil",
            "ERROR redzone.sources.ncaa: scoreboard task crashed: KeyError: 'games'",
            "ERROR redzone.supervisor: rule_delay_doubt raised ZeroDivisionError: float division by zero",
            "ERROR redzone.screen.scorebug: template match failed in worker: cv2.error: (-209:Sizes of input arguments do not match)",
            "ERROR redzone.clips: segment index task died: OSError: [Errno 2] No such file or directory: 'data/reels/index.json'",
        ],
    },
    # NEW kind (split from upstream). The Mac itself is off the internet: every source fails at
    # once, DNS is gone, no route. Refreshing a feed can't help; tell the user once it persists
    # (3+), shrug off a single blip.
    "network": {
        "test": [
            "WARNING redzone.sources.http: Wi-Fi: en0 has no IPv4 address; all score requests failing",
            "WARNING redzone.net: default route via 100.110.165.1 is unreachable",
            "WARNING redzone.net: captive portal detected on Wi-Fi (requests redirected to a login page)",
            "WARNING redzone.sources.http: every source failing at once: espn, nhle, mlb, ncaa",
            "WARNING redzone.net: router 100.110.165.1 answers ping but WAN is down",
            "WARNING redzone.net: Tailscale 100.64.0.0/10 route is capturing traffic to the home network",
        ],
        "valid": [
            "WARNING redzone.sources.http: OSError: [Errno 50] Network is down",
            "WARNING redzone.net: Wi-Fi disconnected (en0 inactive)",
            "WARNING redzone.net: DNS resolver 100.110.165.1 not answering",
            "WARNING redzone.sources.http: httpx.ConnectError: [Errno 51] Network is unreachable",
            "WARNING redzone.net: packet loss to 1.1.1.1 at 60%",
        ],
        "train": [
            "WARNING redzone.sources.http: httpx.ConnectError: [Errno 8] nodename nor servname provided, or not known",
            "WARNING redzone.net: no internet: all probes failed (apple.com, 1.1.1.1, espn.com)",
            "WARNING redzone.net: Wi-Fi signal weak (RSSI -86 dBm), requests timing out",
            "WARNING redzone.net: ISP gateway timed out; modem may be rebooting",
            "WARNING redzone.sources.http: socket.gaierror: [Errno 8] Temporary failure in name resolution",
            "WARNING redzone.net: en0 got a self-assigned 169.254 address",
            "WARNING redzone.net: VPN came up and is routing all traffic",
            "WARNING redzone.sources.http: httpx.ConnectError: all connection attempts failed, for every source",
            "WARNING redzone.net: Mac woke from sleep, network not ready yet",
            "WARNING redzone.net: upstream bandwidth 0.1 Mbps, all fetches slow",
            "WARNING redzone.sources.http: all requests time out, including api-web.nhle.com and statsapi.mlb.com",
            "WARNING redzone.net: Wi-Fi joined a different network ('xfinitywifi') than home",
            "WARNING redzone.net: Ethernet unplugged and Wi-Fi off",
            "WARNING redzone.net: router rebooted (uptime 40 s), leases renewing",
            "WARNING redzone.sources.http: ssl.SSLError: certificate verify failed for every host (clock or proxy?)",
            "WARNING redzone.net: DHCP lease on en0 expired and not renewed",
            "WARNING redzone.net: iCloud Private Relay blocking local network requests",
            "WARNING redzone.net: Little Snitch rule blocking python3.13 outbound connections",
            "WARNING redzone.sources.http: proxy settings point to 127.0.0.1:8888, which is not running",
        ],
    },
    # NEW kind. A streaming app put up an "are you still there" prompt: press OK on it
    # (clear_prompt) on that stick: the standby stick if the line says so, else the one on screen.
    "prompt": {
        "test": [
            "WARNING redzone.tuners.tuners: peacock: 'Still there? Continue watching' dialog on the standby stick",
            "WARNING redzone.tuners.tuners: paramount+: 'We noticed you've been idle' overlay on screen",
            "WARNING redzone.tuners.firetv: idle prompt 'Press any button to keep watching' on the hidden stick",
            "WARNING redzone.tuners.tuners: max: 'Keep watching?' timer counting down on screen",
            "WARNING redzone.screen.ocr: on-screen text reads 'Are you still there?'",
            "WARNING redzone.tuners.tuners: fubo: inactivity check 'Tap to continue' on the standby stick",
        ],
        "valid": [
            "WARNING redzone.tuners.tuners: youtubetv: inactivity timeout warning on screen",
            "WARNING redzone.tuners.tuners: espn: 'Continue watching?' modal on the hidden stick",
            "WARNING redzone.tuners.tuners: prime: 'Still watching Thursday Night Football?' on screen",
            "WARNING redzone.screen.ocr: standby stick shows an 'I'm still here' button",
            "WARNING redzone.tuners.tuners: netflix: 'Continue playing?' card on screen",
        ],
        "train": [
            "WARNING redzone.tuners.tuners: youtubetv: 'Still watching? Video will pause soon' on screen",
            "WARNING redzone.tuners.tuners: youtubetv: 'Are you still watching?' prompt on the standby stick",
            "WARNING redzone.tuners.tuners: prime: 'Are you still watching?' dialog on screen",
            "WARNING redzone.tuners.tuners: peacock: 'Are you still watching?' on screen",
            "WARNING redzone.tuners.tuners: espn: 'Still watching?' prompt on the standby stick",
            "WARNING redzone.tuners.tuners: paramount+: 'Continue watching' button waiting on the hidden stick",
            "WARNING redzone.tuners.tuners: max: 'Are you still watching?' on the standby stick",
            "WARNING redzone.tuners.tuners: fox one: 'Still watching? Press OK' on screen",
            "WARNING redzone.tuners.tuners: nfl+: 'Are you still there?' prompt on screen",
            "WARNING redzone.tuners.tuners: dazn: idle check dialog on the hidden stick",
            "WARNING redzone.tuners.tuners: sling: 'Are you still watching?' on screen",
            "WARNING redzone.tuners.tuners: youtube: 'Video paused. Continue watching?' on screen",
            "WARNING redzone.screen.ocr: on-screen text reads 'Continue watching?'",
            "WARNING redzone.screen.ocr: standby stick frame reads 'Are you still watching'",
            "WARNING redzone.tuners.firetv: inactivity dialog on the standby stick",
            "WARNING redzone.tuners.tuners: nba league pass: 'Are you still watching?' on screen",
            "WARNING redzone.tuners.tuners: apple tv+: 'Still watching?' prompt on the hidden stick",
            "WARNING redzone.tuners.tuners: fubo: 'Are you still watching?' on screen",
            "WARNING redzone.tuners.tuners: espn+: autoplay paused: 'Continue watching?' on screen",
            "WARNING redzone.tuners.tuners: hulu live: 'Are you still watching?' on the standby stick",
        ],
    },
    # NEW kind. A stick's player paused by itself (lost the HDMI input, a buffering stop, an app
    # that pauses in the background): press play (resume_player) on that stick. Play never
    # pauses a playing player. The standby stick if the line says so, else the one on screen.
    "paused": {
        "test": [
            "WARNING redzone.tuners.firetv: standby stick reports MediaSession STATE_PAUSED since the flip",
            "WARNING redzone.screen.boundary: picture is the paused-player overlay on screen",
            "WARNING redzone.tuners.tuners: peacock: stopped playing when the TV picked the other HDMI port (hidden stick)",
            "WARNING redzone.tuners.tuners: fubo: media session state=2 (paused) on screen",
            "WARNING redzone.control: hidden stick stopped playing when the TV flipped away",
            "WARNING redzone.tuners.tuners: max: player halted after an ad break, play button showing on screen",
        ],
        "valid": [
            "WARNING redzone.tuners.tuners: youtubetv: playback halted, waiting on play (standby stick)",
            "WARNING redzone.tuners.firetv: media_session reports PAUSED on screen",
            "WARNING redzone.screen.ocr: pause icon visible in the corner on screen",
            "WARNING redzone.tuners.tuners: espn: video paused after a buffering stall (hidden stick)",
            "WARNING redzone.control: standby stick went to paused after HDMI-CEC handoff",
        ],
        "train": [
            "WARNING redzone.tuners.tuners: prime: player paused on screen",
            "WARNING redzone.tuners.tuners: youtubetv: playback paused itself on the hidden stick",
            "WARNING redzone.tuners.firetv: dumpsys media_session: state=PAUSED on the standby stick",
            "WARNING redzone.tuners.tuners: peacock: paused after the TV input changed (standby stick)",
            "WARNING redzone.tuners.tuners: espn+: player stopped at a paused frame on screen",
            "WARNING redzone.screen.boundary: frame shows the pause bar on screen",
            "WARNING redzone.tuners.tuners: paramount+: paused after losing HDMI-CEC active source",
            "WARNING redzone.tuners.tuners: youtube: video paused in background on the standby stick",
            "WARNING redzone.tuners.tuners: nfl+: playback paused, scrubber visible on screen",
            "WARNING redzone.control: standby stick player paused when it lost the input",
            "WARNING redzone.tuners.tuners: sling: player paused for 2 min on the hidden stick",
            "WARNING redzone.tuners.firetv: playback state paused (2) on screen",
            "WARNING redzone.tuners.tuners: dazn: paused after a network hiccup on screen",
            "WARNING redzone.tuners.tuners: hulu live: paused, waiting for play on the standby stick",
            "WARNING redzone.screen.ocr: 'Paused' label shown on screen",
            "WARNING redzone.tuners.tuners: nba league pass: player paused after commercial (hidden stick)",
            "WARNING redzone.tuners.tuners: apple tv+: playback paused on screen",
            "WARNING redzone.tuners.tuners: fox one: stream paused on the standby stick",
            "WARNING redzone.tuners.tuners: prime: X-Ray overlay open and video paused on screen",
            "WARNING redzone.tuners.firetv: ExoPlayer playWhenReady=false on the hidden stick",
        ],
    },
    # NEW kind. A stick still answers adb, but its picture is stuck: frozen frame, endless spinner,
    # black screen, an error page in the streaming app. Re-tune that stick (retune_stick), except
    # the stick on screen during crunch time, where a re-tune would cut away: notify instead.
    "frozen": {
        "test": [
            "WARNING redzone.screen.boundary: screen hash unchanged for 120 s while the clock runs (standby stick)",
            "WARNING redzone.tuners.tuners: peacock: 'Something went wrong (code 6-5)' page on screen",
            "WARNING redzone.vision: picture is solid green on the hidden stick",
            "WARNING redzone.tuners.firetv: ExoPlayer stuck in STATE_BUFFERING 150 s on screen",
            "WARNING redzone.screen.scorebug: scorebug clock frozen at 8:41 while ESPN says 6:02 (on screen)",
            "WARNING redzone.tuners.tuners: youtubetv: 'Playback error. Try again' on the standby stick",
        ],
        "valid": [
            "WARNING redzone.vision: same frame for 90 s on screen",
            "WARNING redzone.tuners.tuners: prime: endless loading spinner on the standby stick",
            "WARNING redzone.screen.boundary: black picture with audio for 60 s on screen",
            "WARNING redzone.tuners.tuners: espn: 'This video is unavailable' on the hidden stick",
            "WARNING redzone.tuners.firetv: decoder stalled: 0 frames rendered in 45 s on screen",
        ],
        "train": [
            "WARNING redzone.vision: picture frozen for 75 s on screen",
            "WARNING redzone.vision: picture frozen on the standby stick",
            "WARNING redzone.tuners.tuners: youtubetv: buffering spinner for 2 min on screen",
            "WARNING redzone.tuners.tuners: prime: stuck on 'Loading...' on the hidden stick",
            "WARNING redzone.screen.boundary: no motion for 100 s during live play on screen",
            "WARNING redzone.tuners.tuners: peacock: error page 'We're having trouble playing this' on the standby stick",
            "WARNING redzone.vision: black screen on the standby stick",
            "WARNING redzone.tuners.firetv: video freeze detected, audio continues on screen",
            "WARNING redzone.tuners.tuners: espn+: 'Unable to play video' on screen",
            "WARNING redzone.screen.scorebug: scorebug unchanged 3 min while the game clock moved (standby stick)",
            "WARNING redzone.tuners.tuners: paramount+: spinner over a frozen frame on screen",
            "WARNING redzone.vision: picture shows a still image, not live video (hidden stick)",
            "WARNING redzone.tuners.tuners: nfl+: 'Stream ended' card on screen during a live game",
            "WARNING redzone.tuners.tuners: max: 'Error code 400' on the standby stick",
            "WARNING redzone.tuners.firetv: MediaCodec stalled, no frames on the hidden stick",
            "WARNING redzone.vision: the screen shows pixelated macroblocks, frozen, on screen",
            "WARNING redzone.tuners.tuners: fubo: stream stuck at 0:00 on the standby stick",
            "WARNING redzone.tuners.tuners: sling: 'Content unavailable' tile on screen",
            "WARNING redzone.tuners.tuners: youtubetv: video stalled at the same frame (standby stick)",
            "WARNING redzone.vision: TV shows the app's splash screen for 2 min on screen",
        ],
    },
}

# [V7] More training wordings for the four kinds V6's watchdog missed most (paused 0/42, prompt 0/42,
# frozen 2/42, upstream 5/42). Train only: valid and test wordings (and so the test rows) are unchanged.
V7_EXTRA: dict[str, list[str]] = {'prompt': ["WARNING redzone.tuners.tuners: youtubetv: 'Are you still watching? Yes / No' card on screen",
            'WARNING redzone.tuners.tuners: peacock: idle dialog asks to continue on the hidden stick',
            "WARNING redzone.screen.ocr: frame text 'Still watching?' detected on the standby stick",
            "WARNING redzone.tuners.firetv: focus is on an 'OK, keep watching' button on screen",
            "WARNING redzone.tuners.tuners: prime video: resume-or-exit question waiting on the standby stick",
            "WARNING redzone.tuners.tuners: fox one: 'Are you there?' popup on the hidden stick",
            'WARNING redzone.tuners.tuners: espn: playback stops soon unless someone confirms (on screen)',
            "WARNING redzone.screen.ocr: 'Keep watching' dialog with a countdown, standby stick",
            'WARNING redzone.tuners.tuners: nba league pass: idle timeout dialog shown on screen',
            'WARNING redzone.tuners.firetv: window com.amazon.tv.idle.ContinueDialog on the hidden stick'],
 'paused': ['WARNING redzone.tuners.firetv: PlaybackState=PAUSED reported by the standby stick',
            'WARNING redzone.tuners.tuners: youtubetv: stream sits paused at the live edge on screen',
            'WARNING redzone.control: player on the hidden stick is not playing (paused) after the input switch',
            'WARNING redzone.screen.ocr: big play triangle over a still frame on screen',
            'WARNING redzone.tuners.tuners: prime: playback paused when the stick lost focus (standby stick)',
            'WARNING redzone.tuners.tuners: espn+: video paused itself mid-quarter on screen',
            'WARNING redzone.tuners.firetv: media session paused, position not advancing on the hidden stick',
            'WARNING redzone.tuners.tuners: fubo: player state paused after the HDMI handshake on screen',
            'WARNING redzone.control: on-screen stick shows the pause icon and the timeline is not moving',
            'WARNING redzone.tuners.tuners: max: player paused itself in the background (standby stick)'],
 'frozen': ['WARNING redzone.vision: frame identical for 110 s while the game clock runs (hidden stick)',
            "WARNING redzone.tuners.tuners: youtubetv: 'Something went wrong. Retry' screen on screen",
            'WARNING redzone.vision: solid black frame for 80 s on the standby stick',
            'WARNING redzone.tuners.firetv: player stuck buffering for 3 min on the hidden stick',
            "WARNING redzone.tuners.tuners: prime: error 'Video unavailable (7031)' on screen",
            "WARNING redzone.screen.boundary: image hasn't changed in 2 min on the standby stick",
            'WARNING redzone.vision: picture is a gray test card, no video, on screen',
            'WARNING redzone.tuners.tuners: espn: loading wheel never stops on the standby stick',
            'WARNING redzone.tuners.firetv: 0 fps rendered for 60 s on screen',
            'WARNING redzone.screen.scorebug: the scorebug has been stuck for 4 min on the hidden stick'],
 'upstream': ['WARNING redzone.sources.http: cdn.espn.com 502 from the origin while scores update',
              'WARNING redzone.sources.http: api-web.nhle.com returned an empty body',
              'WARNING redzone.sources.mlb: statsapi.mlb.com live feed timestamp 5 min old',
              'WARNING redzone.sources.http: httpx.ConnectError: [Errno 111] Connection refused (cdn.nba.com)',
              'WARNING redzone.sources.fastcast: subscription to gp-football-nfl dropped',
              'WARNING redzone.sources.http: site.api.espn.com 429 rate limited, retry-after 30',
              'WARNING redzone.sources.cbb: ncaa scoreboard JSON truncated mid-array',
              'WARNING redzone.sources.http: TLS handshake timeout to sports.core.api.espn.com',
              'WARNING redzone.sources.nhl: api-web.nhle.com gamecenter landing 500 Internal Server Error',
              "WARNING redzone.sources.http: espn summary endpoint came back without 'boxscore' (partial)"]}
for _k, _ws in V7_EXTRA.items():
    NOVEL[_k]["train"] += _ws
# ... and three times the rows per train wording for them (16 -> 48).
V7_BOOST = {k: 3 for k in V7_EXTRA}

# Lines a stderr traceback leaves behind, one per log line (watchdog only skips bodies that
# start with a space, "File ", "^" or "Traceback" right after the logger; "stderr: ..." isn't).
FRAMES = {
    "core_loop": ["redzone/engine.py\", line 412, in decide", "redzone/decider.py\", line 88, in rank",
                  "redzone/engine.py\", line 230, in _tick", "redzone/scheduler.py\", line 154, in plan"],
    "stick_link": ["redzone/tuners/adb.py\", line 61, in shell", "redzone/control.py\", line 203, in keyevent",
                   "asyncio/streams.py\", line 48, in open_connection"],
    "storage": ["redzone/store.py\", line 77, in insert", "redzone/recorder.py\", line 140, in write_frame",
                "sqlite3/dbapi2.py\", line 33, in execute"],
    "resource": ["redzone/vision.py\", line 95, in grab", "concurrent/futures/process.py\", line 452, in submit",
                 "subprocess.py\", line 1955, in _execute_child"],
    "permission": ["redzone/vision.py\", line 41, in capture", "redzone/tv.py\", line 120, in send_event",
                   "pathlib.py\", line 1013, in open"],
    "upstream": ["httpx/_transports/default.py\", line 69, in map_httpcore_exceptions",
                 "redzone/sources/http.py\", line 52, in get_json"],
    "component": ["redzone/vision.py\", line 211, in _loop", "redzone/sources/espn.py\", line 140, in poll",
                  "redzone/supervisor.py\", line 402, in tick", "redzone/control.py\", line 990, in _worker",
                  "asyncio/tasks.py\", line 314, in __step_run_and_handle_result"],
}

KNOWN_NOISE = [
    "ERROR redzone.engine: score bug read failed",
    "ERROR redzone.service: asyncio.exceptions.CancelledError",
    "WARNING asyncio: Unknown child process pid 4411, will report returncode 255",
    "ERROR redzone.web: Exception in ASGI application",
    "WARNING redzone.engine: reel: catch-up reel trimmed to 3 clips",
    "WARNING redzone.engine: nhl enrich failed for game 2025020211",
    "ERROR redzone.service: Traceback (most recent call last):",
]


# ------------------------------------------------------------------------------------------
# ------------------------------------------------------------------------------------------
COMPONENT_OF = (("redzone.vision", "screen"), ("redzone.screen", "screen"), ("redzone.sources", "feeds"),
                ("redzone.clips", "reel"), ("redzone.supervisor", "supervisor"), ("redzone.control", "control"),
                ("redzone.tuners", "control"))


def component_of(wording: str) -> str:
    logger = wording.split(" ", 2)[1].rstrip(":")
    for prefix, comp in COMPONENT_OF:
        if logger.startswith(prefix):
            return comp
    raise ValueError(f"no component for {wording}")


def which_stick(o: Obs, wording: str) -> str:
    """The stick a line is about: the standby one when it says so, else the one on screen."""
    w = wording.lower()
    if ("standby" in w or "hidden" in w) and o.standby:
        return o.standby
    return o.on_screen


def teach_novel(o: Obs, kind: str, n: int, wording: str = "") -> tuple[str, str, str]:
    """(action, target, why) for an observation whose only unexplained error is of this kind.

    The judgment, per kind:
      core_loop   one-off crash (<5 in 15 min): none, the loop recovers. Keeps crashing (5+):
                  restart_service, except notify while crunch time is on screen.
      component   (new) one part of the app (feeds, screen reader, clips, stick control, the
                  supervisor) crashing: <5 none; 5+ restart_component with that part as target.
                  Never touches the TV, so crunch time doesn't matter.
      stick_link  sticks listed offline never get here (rules reconnect them). Link trouble the
                  app doesn't report as offline: notify, a person checks power/Wi-Fi.
      storage     prune_disk: the app can't write, free the rebuildable caches.
      person      notify: an account, a dialog or the remote needs a human.
      upstream    one score site failing while the internet works: a hiccup (<10) is none, it
                  retries; 10+, or the feed stale 3+ min: refresh_feed (target feeds).
      network     (new) the Mac's own internet is down: refreshing can't help. 3+ (or feeds stale
                  3+ min): notify; a blip: none. With a site error too, still notify, not refresh.
      cosmetic    none.
      resource    (new) out of memory / fds / threads, or the loop wedged: restart_service even
                  at a low count, since it only gets worse; notify while crunch is on screen.
      permission  (new) macOS or the file system refusing access: notify, a restart can't fix it.
      prompt      (new) "are you still watching?": clear_prompt on that stick (standby if the line
                  says so, else on screen).
      paused      (new) a player paused by itself: resume_player on that stick.
      frozen      (new) a stick's picture is stuck (frozen, spinner, black, error page): retune_stick
                  on that stick; but notify instead for the stick on screen during crunch time.
    """
    if kind == "core_loop":
        if n >= 5:
            if o.crunch_on_screen:
                return "notify", "", f"the app's main loop keeps crashing ({n}x) but crunch time is on: restart after it"
            return "restart_service", "", f"the app's main loop crashed {n} times in 15 minutes: restart it"
        return "none", "", f"one-off crash in the main loop ({n}x); it recovered"
    if kind == "component":
        c = component_of(wording)
        if n >= 5:
            return "restart_component", c, f"the {c} part keeps crashing ({n}x in 15 min): restart just it"
        return "none", "", f"one-off crash in the {c} part ({n}x); it recovered"
    if kind == "stick_link":
        if o.sticks_offline:
            return "reconnect_stick", o.sticks_offline[0], "a stick's adb link broke: reconnect it"
        return "notify", "", "a stick's adb link is failing: check it's powered and on Wi-Fi"
    if kind == "storage":
        return "prune_disk", "", "the app can't write: free disk space"
    if kind == "person":
        return "notify", "", "an account or the remote needs a person: tell the user"
    if kind == "upstream":
        if n >= 10 or (o.live_games and o.feed_stale_s >= STALE_FEED_S):
            return "refresh_feed", "feeds", f"a score source keeps failing ({n}x in 15 min): refresh the feed"
        return "none", "", "a score source hiccuped; it retries by itself"
    if kind == "network":
        if n >= 3 or (o.live_games and o.feed_stale_s >= STALE_FEED_S):
            return "notify", "", f"the Mac looks offline ({n}x in 15 min): check the Wi-Fi/router"
        return "none", "", "a network blip; it retries by itself"
    if kind == "resource":
        if o.crunch_on_screen:
            return "notify", "", "the app is running out of memory or handles; restart it after crunch time"
        return "restart_service", "", "the app is running out of memory or handles: restart it"
    if kind == "permission":
        return "notify", "", "macOS is refusing the app access: a person must grant the permission"
    if kind == "prompt":
        return "clear_prompt", which_stick(o, wording), "a 'still watching?' prompt is up: press OK on it"
    if kind == "paused":
        return "resume_player", which_stick(o, wording), "the player paused by itself: press play"
    if kind == "frozen":
        s = which_stick(o, wording)
        if o.crunch_on_screen and s == o.on_screen:
            return "notify", "", "the picture on screen is stuck but crunch time is on: re-tune after it"
        return "retune_stick", s, "the stick's picture is stuck: re-tune it"
    return "none", "", "harmless warning"


# Several unknown errors at once: the most urgent verdict wins. Storage first (a full disk is
# often what makes everything else fail), then a restart, then fixes on one part or one stick,
# then telling a person, then nothing.
PRIORITY = ("prune_disk", "restart_service", "restart_component", "reconnect_stick", "retune_stick",
            "clear_prompt", "resume_player", "refresh_feed", "notify", "safe_mode_off", "none")


def teach_mixed(o: Obs, items: list[tuple[str, str, int]]) -> tuple[str, str, str]:
    verdicts = [teach_novel(o, k, n, w) for k, w, n in items]
    if any(k == "network" and v[0] == "notify" for (k, _, _), v in zip(items, verdicts)):
        verdicts = [v for v in verdicts if v[0] != "refresh_feed"]   # no internet: a refresh can't help
    return min(verdicts, key=lambda v: PRIORITY.index(v[0]))


def settle(o: Obs, v: tuple[str, str, str]) -> tuple[str, str, str]:
    """When the new errors need nothing, the same follow-ups as the rules: leave a safe mode whose
    cause is gone, refresh a stale feed."""
    if v[0] != "none":
        return v
    if o.safe_mode and o.safe_min >= SAFE_STALE_MIN and not o.paused:
        return "safe_mode_off", "", f"safe mode has been on {int(o.safe_min)} min and only harmless errors are left"
    if o.live_games and o.feed_stale_s >= STALE_FEED_S:
        return "refresh_feed", "feeds", f"live games but the score feed hasn't answered in {int(o.feed_stale_s)} s"
    return v


# ------------------------------------------------------------------------------------------
STANDBY_WORDS = ("standby", "hidden")


def base_obs(rng: random.Random, two_stick: bool | None = None) -> Obs:
    o = Obs(api_ok=True, live_games=rng.choice([0, 0, 1, 2, 3, 5, 8]),
            crunch_on_screen=rng.random() < 0.25,
            disk_free_mb=float(rng.choice([2500, 4000, 9000, 20000, 60000, 140000])),
            restarts_last_hour=rng.choice([0, 0, 0, 1, 2]), log_age_s=round(rng.uniform(0, 90), 1))
    if o.crunch_on_screen and not o.live_games:
        o.live_games = 1
    a, b = rng.sample(STICKS, 2)
    o.on_screen = a
    if two_stick if two_stick is not None else rng.random() < 0.7:
        o.standby = b
    if rng.random() < 0.15:
        o.safe_mode, o.safe_min = True, round(rng.choice([rng.uniform(0, 3), rng.uniform(3, 20)]), 1)
        o.paused = rng.random() < 0.25
    elif rng.random() < 0.06:
        o.paused = True
    if o.live_games:
        o.feed_stale_s = round(rng.choice([rng.uniform(0, 40)] * 5 + [rng.uniform(60, 170), rng.uniform(180, 900)]), 1)
    return o


def count(rng: random.Random) -> int:
    return rng.choice([1, 1, 1, 2, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 15, 25, 40])


def render(rng: random.Random, kind: str, wording: str, n: int) -> dict[str, int]:
    """The signatures one error leaves in the window: the line itself, sometimes with the
    stderr traceback it came with."""
    out = {signature(wording): n}
    if kind in FRAMES and wording.startswith("ERROR") and rng.random() < 0.35:
        exc = wording.split(": ", 2)[-1]
        out[signature("ERROR redzone.service: stderr: Traceback (most recent call last):")] = n
        for fr in rng.sample(FRAMES[kind], min(len(FRAMES[kind]), rng.randint(1, 2))):
            out[signature(f'ERROR redzone.service: stderr: File "/Users/me/redzone/{fr}')] = n
        out[signature(f"ERROR redzone.service: stderr: {exc}")] = n
    return out


def add_noise(rng: random.Random, o: Obs, cosmetic_pool: list[str] | None) -> None:
    for sig in rng.sample(KNOWN_NOISE, rng.choice([0, 0, 1, 1, 2, 3])):
        o.errors.setdefault(signature(sig), rng.randint(1, 40))
    if cosmetic_pool and rng.random() < 0.3:
        o.errors.setdefault(signature(rng.choice(cosmetic_pool)), rng.randint(1, 20))


def precedence(rng: random.Random, o: Obs) -> None:
    """Sometimes a hard-rule situation sits on top of the new error: the rules must win."""
    r = rng.random()
    if r < 0.25:
        o.api_ok, o.api_fail_streak = False, rng.choice([2, 3, 4, 6])
    elif r < 0.45:
        o.live_games = max(1, o.live_games)
        o.log_age_s = round(rng.uniform(250, 1200), 1)
    elif r < 0.7:
        o.disk_free_mb = float(rng.choice([150, 400, 900, 1100]))
    else:
        o.sticks_offline = [rng.choice(STICKS)]
        o.offline_min = round(rng.uniform(0, 30), 1)


def finish(o: Obs, kind: str, action: str, target: str, why: str) -> dict:
    o.errors = dict(list(o.errors.items())[:8])   # Obs.text() shows 8; the cause goes in first, noise after
    return {"messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": o.text()},
                         {"role": "assistant", "content": json.dumps({"action": action, "target": target, "why": why})}],
            "kind": kind, "label": action, "target": target}


def label(o: Obs, teach) -> tuple[str, str, str]:
    r = rules(o)
    if r is not None and r.action != "none":
        return r.action, r.target, r.why
    return settle(o, teach())


def sample(rng: random.Random, kind: str, wording: str, cosmetic_pool: list[str]) -> dict:
    standby_line = any(x in wording.lower() for x in STANDBY_WORDS)
    o = base_obs(rng, two_stick=True if standby_line else None)
    if kind == "stick_link" and rng.random() < 0.45:
        o.sticks_offline = [rng.choice(STICKS)]
        o.offline_min = round(rng.uniform(0, 14), 1)
    elif rng.random() < 0.12:
        precedence(rng, o)
    if kind == "upstream" and o.live_games and rng.random() < 0.3:
        o.feed_stale_s = round(rng.uniform(100, 900), 1)
    if kind == "network" and o.live_games and rng.random() < 0.6:
        o.feed_stale_s = round(rng.uniform(60, 900), 1)
    n = count(rng)
    o.errors = render(rng, kind, wording, n)
    add_noise(rng, o, cosmetic_pool if kind != "cosmetic" else None)
    return finish(o, kind, *label(o, lambda: teach_novel(o, kind, n, wording)))


def mixed_sample(rng: random.Random, split_words: dict[str, list[str]]) -> dict:
    """Two or three unknown errors of different kinds in one status."""
    o = base_obs(rng, two_stick=True)
    if rng.random() < 0.1:
        precedence(rng, o)
    kinds = rng.sample(sorted(split_words), rng.choice([2, 2, 3]))
    items = []
    for k in kinds:
        n, w = count(rng), rng.choice(split_words[k])
        items.append((k, w, n))
        o.errors[signature(w)] = n
    add_noise(rng, o, None)
    return finish(o, "mixed", *label(o, lambda: teach_mixed(o, items)))


def rule_sample(rng: random.Random) -> dict:
    """Every hard-rule branch (and its thresholds), so the model agrees with the rules."""
    o = base_obs(rng)
    o.crunch_on_screen = o.crunch_on_screen and rng.random() < 0.5
    o.safe_mode, o.safe_min, o.paused, o.feed_stale_s = False, 0.0, False, min(o.feed_stale_s, 60.0)
    pick = rng.randrange(17)
    if pick == 0:
        o.api_ok, o.api_fail_streak = False, rng.choice([2, 3, 5, 9])
    elif pick == 1:
        o.live_games, o.log_age_s = max(1, o.live_games), round(rng.uniform(240, 1500), 1)
    elif pick == 2:
        o.disk_free_mb = float(rng.choice([90, 300, 700, 1150]))
    elif pick == 3:
        o.errors = {signature("ERROR redzone.recorder: OSError: [Errno 28] No space left on device"): rng.randint(1, 9)}
    elif pick == 4:
        o.sticks_offline, o.offline_min = [rng.choice(STICKS)], round(rng.uniform(0, 40), 1)
    elif pick == 5:
        o.errors = {signature("WARNING redzone.engine: switch to 401 still running after 30s: giving up on it"):
                    rng.randint(1, 6)}
    elif pick == 6:
        o.errors = {signature("WARNING redzone.tv: HDMI2 never became active source"): rng.randint(1, 6)}
    elif pick == 7:
        o.errors = {signature("WARNING redzone.engine: espn poll failed: timeout"): rng.choice([2, 5, 9, 10, 14, 30])}
    elif pick == 8:
        o.errors = {signature("WARNING redzone.tuners.tuners: re-loading prime timed out"): rng.randint(1, 8)}
    elif pick == 9:
        o.errors = {signature("WARNING redzone.config: config: ignoring a setting 'fav_teams'"): rng.randint(1, 3)}
    elif pick == 10:
        o.errors = {signature("WARNING redzone.engine: nhl enrich failed for game 2025020211"):
                    rng.choice([3, 20, 49, 50, 80, 140])}
    elif pick == 11:
        o.errors = {signature("WARNING redzone.tuners.adb: drop adb transport 100.110.165.181:5555"): rng.randint(1, 5)}
    elif pick in (12, 13):   # safe mode: leave it once its errors are gone, never over a person's pause
        o.safe_mode, o.safe_min = True, round(rng.choice([rng.uniform(0, 3), rng.uniform(3, 30)]), 1)
        o.paused = rng.random() < 0.3
    elif pick == 14:          # a live score source gone quiet
        o.live_games = max(1, o.live_games)
        o.feed_stale_s = round(rng.choice([rng.uniform(60, 179), rng.uniform(180, 1200)]), 1)
    else:
        o.errors = {}
    add_noise(rng, o, None)
    if pick >= 15 and rng.random() < 0.3:
        o.errors = {}
    v = rules(o)
    return finish(o, "rule", v.action, v.target, v.why)


# ------------------------------------------------------------------------------------------
def _words(s: str) -> set[str]:
    body = signature(s).split(": ", 1)[-1].lower()
    return set(re.findall(r"[a-z][a-z0-9_+.-]{2,}", body)) - {"the", "standby", "stick", "hidden", "screen"}


def check_split() -> None:
    """Refuse to build if a held-out wording leaks into training: no shared normalized wording,
    no test wording with high word overlap (Jaccard >= 0.5) with any train wording, no wording a
    hard rule already knows, and 25+ wordings per kind."""
    train_all = [(k, w) for k, d in NOVEL.items() for w in d["train"]]
    seen: dict[str, str] = {}
    for k, d in NOVEL.items():
        total = sum(len(v) for v in d.values())
        assert total >= 25, f"{k}: only {total} wordings"
        for split, ws in d.items():
            for w in ws:
                s = signature(w)
                assert s not in seen, f"duplicate wording in {split}/{seen[s]}: {w}"
                seen[s] = split
                assert known(s) is None, f"{k}/{split} wording is matched by a hard rule: {w}"
                if k == "component":
                    component_of(w)
        for split in ("test", "valid"):
            for w in d[split]:
                a = _words(w)
                for k2, t in train_all:
                    b = _words(t)
                    j = len(a & b) / max(1, len(a | b))
                    assert j < 0.5, f"{k}/{split} wording too close to train ({k2}, J={j:.2f}):\n  {w}\n  {t}"


def build(rng: random.Random, split: str, per_wording: int, n_mixed: int, n_rules: int,
          boost: dict[str, int] | None = None) -> list[dict]:
    rows = []
    cos = NOVEL["cosmetic"][split]
    for kind, d in NOVEL.items():
        for w in d[split]:
            rows += [sample(rng, kind, w, cos) for _ in range(per_wording * (boost or {}).get(kind, 1))]
    words = {k: d[split] for k, d in NOVEL.items()}
    rows += [mixed_sample(rng, words) for _ in range(n_mixed)]
    rows += [rule_sample(rng) for _ in range(n_rules)]
    rng.shuffle(rows)
    return rows


def main(seed: int = 7) -> None:
    check_split()
    sets = {"train": build(random.Random(seed), "train", 16, 1200, 800, boost=V7_BOOST),
            "valid": build(random.Random(seed + 1), "valid", 3, 40, 40),
            "test": build(random.Random(seed + 2), "test", 7, 70, 70)}
    OUT.mkdir(parents=True, exist_ok=True)
    for name, rows in sets.items():
        with (OUT / f"{name}.jsonl").open("w") as f:
            for r in rows:
                f.write(json.dumps({"messages": r["messages"]}) + "\n")
        with (OUT / f"{name}.labels.jsonl").open("w") as f:
            for r in rows:
                f.write(json.dumps({"kind": r["kind"], "label": r["label"], "target": r["target"]}) + "\n")
    print({k: len(v) for k, v in sets.items()})


if __name__ == "__main__":
    main()
