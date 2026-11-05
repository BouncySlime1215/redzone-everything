#!/usr/bin/env python3
"""Data Division G: attention at scale. Per-minute COUNTS of what people watch and talk about.

Sources (each run is a free GitHub runner job, never the Mac, never the cloud dev container):
  bsky    Bluesky Jetstream public firehose (no key). Each post's text is matched against
          data/attention/terms.json in memory and dropped; only per-minute counts per term are kept.
  twitch  Twitch Helix /streams in the Sports category (app token from TWITCH_CLIENT_ID/SECRET,
          which Nick adds as Actions secrets). Per minute: streams and viewers per matched term.
          Titles are matched in memory and dropped; no logins, chat or video are stored.
  ledger  Rows, bytes, tokens, label fed and information per GB for every source in --out.

Labels only. These counts say how much people cared about a moment AFTER it happened (with a lag),
so they are labels and evaluation targets for Watch Value / the reward model. They must never be a
same-moment input to the brain (test writers' leakage rule; EXP-1700 notes).

License tag on every row: "personal-use, not for commercial release" (plan B rules).
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

TAG = "personal-use, not for commercial release"
ROOT = Path(__file__).resolve().parents[1]
TERMS_PATH = ROOT / "data" / "attention" / "terms.json"
JETSTREAM = "wss://jetstream2.us-east.bsky.network/subscribe?wantedCollections=app.bsky.feed.post"
TWITCH_SPORTS_GAME_ID = "518203"
UA = "redzone-attention/0.1 (personal research; counts only)"

LICENSES = {
    "bsky": "Bluesky public firehose via Jetstream: public AT Protocol data, no auth; counts only, no text, no DIDs "
            "stored; plan B, personal use, not for commercial release",
    "twitch": "Twitch Helix API under the Twitch Developer Services Agreement, app token Nick created; aggregate "
              "viewer counts only, no chat/video/logins stored; plan B, personal use, not for commercial release",
}
# What each source feeds. G data is LABELS / EVAL only, never a same-moment input.
FEEDS = {
    "bsky": "label: attention_minute (per-team mention spike, z vs team's own baseline, lag 0-5 min) -> Watch Value / reward-model eval",
    "twitch": "label: live_viewers_minute (sports-category viewer curve per team term) -> Watch Value / reward-model eval",
}


# ---------------------------------------------------------------- term matching (pure, tested offline)
def load_terms(path: Path = TERMS_PATH) -> list[str]:
    d = json.loads(path.read_text())
    seen: list[str] = []
    for k, v in d.items():
        if k.startswith("_"):
            continue
        for t in v:
            t = t.lower().strip()
            if t and t not in seen:
                seen.append(t)
    return seen


def compile_terms(terms: list[str]) -> re.Pattern:
    # Longest first so "red wings" wins over "wings"; word boundaries; hashtags count ("#TNF").
    alts = sorted((re.escape(t) for t in terms), key=len, reverse=True)
    return re.compile(r"(?<![\w])#?(" + "|".join(alts) + r")(?![\w])", re.IGNORECASE)


def match_terms(text: str, rx: re.Pattern) -> set[str]:
    """Distinct terms in one text. One post counts once per term, however often it repeats it."""
    if not text:
        return set()
    return {m.group(1).lower() for m in rx.finditer(text)}


def minute_of(ts_s: float) -> str:
    return datetime.fromtimestamp(ts_s, tz=timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


class MinuteCounter:
    """Counts per (minute, term). Holds numbers only."""

    def __init__(self) -> None:
        self.total: dict[str, int] = defaultdict(int)
        self.terms: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    def add_post(self, ts_s: float, text: str, rx: re.Pattern) -> None:
        m = minute_of(ts_s)
        self.total[m] += 1
        for t in match_terms(text, rx):
            self.terms[m][t] += 1

    def rows(self, source: str, upto_minute: str | None = None) -> list[dict]:
        out = []
        for m in sorted(self.total):
            if upto_minute is not None and m >= upto_minute:
                continue
            out.append({"minute_utc": m, "source": source, "posts_total": self.total[m],
                        "terms": dict(sorted(self.terms[m].items())), "license": TAG})
        return out

    def drop_before(self, upto_minute: str) -> None:
        for m in [m for m in self.total if m < upto_minute]:
            self.total.pop(m, None)
            self.terms.pop(m, None)


def handle_jetstream(msg: dict, counter: MinuteCounter, rx: re.Pattern) -> None:
    if msg.get("kind") != "commit":
        return
    c = msg.get("commit") or {}
    if c.get("operation") != "create" or c.get("collection") != "app.bsky.feed.post":
        return
    rec = c.get("record") or {}
    ts = (msg.get("time_us") or 0) / 1e6 or time.time()
    counter.add_post(ts, rec.get("text") or "", rx)  # text dropped right after matching


def append_rows(out: Path, source: str, rows: list[dict]) -> int:
    n = 0
    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_day[r["minute_utc"][:10]].append(r)
    for day, rs in by_day.items():
        p = out / source / f"{day}.jsonl.gz"
        p.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(p, "at") as f:
            for r in rs:
                f.write(json.dumps(r, separators=(",", ":")) + "\n")
                n += 1
    return n


# ---------------------------------------------------------------- bsky
async def run_bsky(out: Path, minutes: float) -> int:
    import websockets  # installed on the runner only

    rx = compile_terms(load_terms())
    counter = MinuteCounter()
    end = time.time() + minutes * 60
    cursor = None
    written = 0
    backoff = 2
    last_flush = time.time()
    while time.time() < end:
        url = JETSTREAM + (f"&cursor={cursor}" if cursor else "")
        try:
            async with websockets.connect(url, max_size=2**22, open_timeout=20,
                                          user_agent_header=UA) as ws:
                backoff = 2
                while time.time() < end:
                    raw = await asyncio.wait_for(ws.recv(), timeout=60)
                    msg = json.loads(raw)
                    cursor = msg.get("time_us") or cursor
                    handle_jetstream(msg, counter, rx)
                    if time.time() - last_flush > 60:
                        cut = minute_of(time.time() - 90)  # keep the last ~2 open minutes
                        written += append_rows(out, "bsky", counter.rows("bsky", cut))
                        counter.drop_before(cut)
                        last_flush = time.time()
        except Exception as e:  # reconnect with the cursor; Jetstream replays from it
            print(f"bsky reconnect after {type(e).__name__}: {e}", file=sys.stderr)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120)
    written += append_rows(out, "bsky", counter.rows("bsky"))
    print(f"bsky: wrote {written} minute rows")
    return written


# ---------------------------------------------------------------- twitch
def _http_json(url: str, headers: dict | None = None, data: bytes | None = None) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})}, data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def twitch_token(cid: str, secret: str) -> str:
    q = urllib.parse.urlencode({"client_id": cid, "client_secret": secret, "grant_type": "client_credentials"})
    return _http_json("https://id.twitch.tv/oauth2/token?" + q, data=b"")["access_token"]


def twitch_snapshot(streams: list[dict], rx: re.Pattern, ts_s: float) -> dict:
    """Aggregate one poll of /streams into one minute row. Titles and logins are dropped here."""
    terms: dict[str, dict[str, int]] = {}
    total = 0
    for s in streams:
        v = int(s.get("viewer_count") or 0)
        total += v
        for t in match_terms(s.get("title") or "", rx):
            a = terms.setdefault(t, {"streams": 0, "viewers": 0, "max_viewers": 0})
            a["streams"] += 1
            a["viewers"] += v
            a["max_viewers"] = max(a["max_viewers"], v)
    return {"minute_utc": minute_of(ts_s), "source": "twitch", "streams_total": len(streams),
            "viewers_total": total, "terms": dict(sorted(terms.items())), "license": TAG}


def run_twitch(out: Path, minutes: float, pages: int = 10) -> int:
    cid, secret = os.environ.get("TWITCH_CLIENT_ID"), os.environ.get("TWITCH_CLIENT_SECRET")
    if not cid or not secret:
        print("twitch: TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET not set (Nick adds them as Actions secrets); skipping")
        return 0
    rx = compile_terms(load_terms())
    token = twitch_token(cid, secret)
    end = time.time() + minutes * 60
    written = 0
    while time.time() < end:
        t0 = time.time()
        streams: list[dict] = []
        after = None
        try:
            for _ in range(pages):  # 100 streams per page, sorted by viewers; 1 point each (limit 800/min)
                q = {"game_id": TWITCH_SPORTS_GAME_ID, "first": "100"}
                if after:
                    q["after"] = after
                d = _http_json("https://api.twitch.tv/helix/streams?" + urllib.parse.urlencode(q),
                               {"Client-Id": cid, "Authorization": f"Bearer {token}"})
                streams += d.get("data") or []
                after = (d.get("pagination") or {}).get("cursor")
                if not after:
                    break
            written += append_rows(out, "twitch", [twitch_snapshot(streams, rx, t0)])
        except urllib.error.HTTPError as e:
            print(f"twitch HTTP {e.code}", file=sys.stderr)
            if e.code == 401:
                token = twitch_token(cid, secret)
            elif e.code == 429:
                time.sleep(30)
        except Exception as e:
            print(f"twitch error {type(e).__name__}: {e}", file=sys.stderr)
        time.sleep(max(0.0, 60 - (time.time() - t0)))
    print(f"twitch: wrote {written} minute rows")
    return written


# ---------------------------------------------------------------- ledger
def ledger(out: Path) -> dict:
    """Ledger per source. Tokens: 1 minute row = 2 base tokens + 2 per nonzero term (term id, bucketed count).
    Kind: every row is NEW information (not derivable from play-by-play), but it is a LABEL, never an input.
    Info per GB = new-info label tokens per stored GB."""
    res = {}
    for src_dir in sorted(p for p in out.iterdir() if p.is_dir() and p.name in LICENSES):
        rows = terms_nz = 0
        size = 0
        days = []
        for f in sorted(src_dir.glob("*.jsonl.gz")):
            size += f.stat().st_size
            days.append(f.stem.split(".")[0])
            with gzip.open(f, "rt") as fh:
                for line in fh:
                    r = json.loads(line)
                    rows += 1
                    terms_nz += len(r.get("terms") or {})
        tokens = 2 * rows + 2 * terms_nz
        gb = size / 1e9
        res[src_dir.name] = {
            "rows_minutes": rows, "term_minutes": terms_nz, "tokens_new_info_label": tokens,
            "tokens_derived": 0, "stored_bytes": size, "days": days,
            "tokens_per_gb": round(tokens / gb) if gb else None,
            "feeds": FEEDS[src_dir.name], "use": "label/eval only, never a same-moment input",
            "license": LICENSES[src_dir.name], "tag": TAG,
        }
    (out / "ledger.json").write_text(json.dumps(res, indent=2) + "\n")
    print(json.dumps(res, indent=2))
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", choices=["bsky", "twitch", "ledger"])
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "attention" / "out")
    ap.add_argument("--minutes", type=float, default=10)
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    if a.source == "bsky":
        asyncio.run(run_bsky(a.out, a.minutes))
    elif a.source == "twitch":
        run_twitch(a.out, a.minutes)
    else:
        ledger(a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
