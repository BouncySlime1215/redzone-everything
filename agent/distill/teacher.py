"""Teacher prompts, request files and answer parsing. Stdlib only.

The teacher is a big open model (Qwen3-32B-AWQ, or 14B) run on a free Kaggle GPU by
``agent/distill/kaggle/teacher_kaggle.ipynb``. Everything model-specific stays in that notebook; this
module renders every prompt here, so the notebook is a dumb "messages in, text out" loop and the
prompts are reviewed and tested in the repo.

Input rows are the game-reasoner thread's export (scripts/export_reasoner_llm.py, rows_*.jsonl): one
sampled moment of one game, whole nights per split (test = the nights the scorecards grade)::

    {"id": "nfl:401772510:5", "league": "nfl", "night": "20250905", "split": "train|valid|test",
     "brief": "<exactly what the student sees live>",
     "target": {"lock_in", "safe_to_leave": "yes|no", "confidence", "storyline", "why", "next", "stay_s"},
     "y": 1, "big_next": 1, "crunch_next": 0, "t_big": 75.0, "t_break": 75.0,
     "next_text": "Rushing Touchdown", "garbage": false, "crunch": false, "late": false, "mine": false,
     "head_p": 0.86}

lock_in / safe_to_leave / stay_s in ``target`` come from hindsight + the decider's rules and are what
gets graded; the teacher never overrides them. It writes the words: storyline, why, next.

Tasks
-----
reasoner_blind     brief only -> the reasoner's JSON (same schema the student writes). Asked on test
                   nights (plus an optional sample of train nights): teacher vs student vs today.
reasoner_grounded  brief + what actually happened -> storyline / why / next written as a FORECAST a
                   sharp viewer could have made from the brief. Train and valid nights only.
jump_blind         dead ball + reel context -> jump/stay JSON (for the highlight-jump thread's rows).
wd_paraphrase      watchdog: new wordings of one error kind (augmentation for unseen errors).
wd_classify        watchdog: blind check of a teacher wording's kind; only agreements are kept.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

LOCK = ("none", "watch", "must_see")
ORDER = ("lock_in", "safe_to_leave", "confidence", "storyline", "why", "next", "stay_s")
MAX_STAY_S = 900

# The student's system prompt lives in redzone/reasoner/reasoner.py (SYSTEM) on the reasoner branch;
# the CLI reads it from chat_*.jsonl so the teacher answers the exact same question. This copy is the
# fallback for tests and for rows exported without chat files.
STUDENT_SYSTEM = (
    "You are RedZone's live game reasoner. You read a brief of one live game and say what it has going "
    "right now, for a viewer flipping between games. Answer with ONE JSON object only, keys in this order: "
    "lock_in (none|watch|must_see), safe_to_leave (yes|no: safe to leave at the next dead ball), "
    "confidence (0-1), storyline (one short line), why (one short line), next (what to expect and roughly "
    "when), stay_s (seconds worth staying, 0-900). Never invent facts that are not in the brief. Garbage "
    "time is never must_see."
)

# Extra guidance for the big teacher's blind answers (the student learns it from data instead).
BLIND_EXTRA = (
    "\nmust_see = a big play (score, turnover, goal, explosive play) is likely within the next ~2-3 "
    "minutes of game time, or crunch time / the viewer's team late; watch = live and competitive; "
    "none = nothing building, a long break, or garbage time. safe_to_leave = no when something big is "
    "likely right after the next dead ball. Be calibrated: most moments are not must_see. In next, give "
    "the time as 'within M:SS' or 'in N s'."
)

GROUNDED_SYSTEM = (
    "You write training examples for a small live-game reasoner. You see a brief of one live game AND "
    "what actually happened next (hidden from the small model). Write the reasoner's words as a "
    "FORECAST a sharp viewer could have made from the brief alone: why must cite signals visible in the "
    "brief (score, clock, field position, down and distance, win-prob swing, momentum, timeouts, power "
    "play, fouls, bonus, lock-in odds), never the future. If the brief gave little sign of what came "
    "next, next should hedge (\"routine play next\", \"maybe a shot on the power play\").\n"
    "lock_in and safe_to_leave are GIVEN and final; your words must agree with them.\n"
    "Answer with ONE JSON object only: "
    '{"storyline": "<one line, <=90 chars>", "why": "<one short line, <=110 chars>", '
    '"next": "<what to expect and roughly when, e.g. within 1:15, <=80 chars>"}'
)

JUMP_SYSTEM = (
    "You decide whether RedZone Everything should jump from the live game on screen to a catch-up reel "
    "of big moments the viewer missed elsewhere, or stay. Hard rules: jump only at a dead ball whose "
    "cautious remaining length covers the reel plus both switch costs plus a margin; return at the "
    "restart, never mid-play; stay when the live game has a big moment coming soon, crunch time, the "
    "viewer's team late, or the reasoner says must_see. Weigh the reel's unseen clips (importance x "
    "freshness) against what the live game is likely to show over the same window.\n"
    'Answer with ONE JSON object only: {"decision": "jump|stay", "reel_s": <seconds, 0 if stay>, '
    '"why": "<one short sentence>", "confidence": <0..1>}'
)

# What each watchdog error kind means (agent/train/make_data.py NOVEL). Unknown kinds fall back to the
# kind's name, so a kind the watchdog thread adds still gets prompts.
KIND_HELP = {
    "core_loop": "the app's own decision/score loop crashed with a Python exception (a bug in redzone code)",
    "component": "one part other than the core loop (score feeds, screen reader, catch-up clips, stick control, "
                 "supervisor) crashed or stopped responding",
    "stick_link": "the adb network link to a Fire TV stick failed (refused, unauthorized, reset, host down)",
    "storage": "disk or database storage is failing (full, quota, I/O error)",
    "person": "something only a person can fix: sign-in, PIN, subscription, payment, a dialog on the TV, "
              "someone using the remote",
    "upstream": "one score source (ESPN, NHL, MLB API, fastcast websocket) failed, timed out or rate-limited",
    "network": "the Mac itself is off the internet: every source fails at once, DNS gone, no route",
    "cosmetic": "harmless warning noise (deprecations, slow-task warnings, favicon 404s, cache misses)",
    "resource": "the app process running out of memory, file descriptors, threads or workers, or its event "
                "loop wedged",
    "permission": "macOS or the file system refusing access: Screen Recording, Apple events, keychain, "
                  "quarantine, read-only files",
    "prompt": "a streaming app shows an 'are you still watching / still there' prompt on a stick "
              "(say 'standby' or 'hidden' stick, or 'on screen')",
    "paused": "a stick's player paused by itself (lost HDMI, buffering stop, paused in background) "
              "(say 'standby' or 'hidden' stick, or 'on screen')",
    "frozen": "a stick answers adb but its picture is stuck: frozen frame, endless spinner, black screen, "
              "error page (say 'standby' or 'hidden' stick, or 'on screen')",
}

WD_PARAPHRASE_SYSTEM = (
    "You write realistic log lines for testing a monitoring agent. The app is RedZone Everything, a "
    "Python service (modules redzone.engine, redzone.decider, redzone.tuners.adb, redzone.tuners.tuners, "
    "redzone.sources.http, redzone.sources.fastcast, redzone.store, redzone.reel, redzone.tv, ...) "
    "controlling Fire TV sticks over adb and a Samsung TV. Write NEW lines of the requested kind: vary "
    "the module, exception type, wording, hosts, numbers and app names; never copy an example. One "
    'line each, in the usual "LEVEL module: message" shape. Answer with JSON only: {"lines": ["...", ...]}'
)

WD_CLASSIFY_SYSTEM = (
    "Classify ONE log line from a Python service that switches a TV between live games. Pick the single "
    'best kind. Answer with JSON only: {"kind": "<one of the kinds>"}'
)


# ---------------------------------------------------------------------------------------------- io

def read_jsonl(path: str | Path) -> list[dict]:
    """Rows of a .jsonl or .jsonl.gz file (the Kaggle dataset ships the big ones gzipped)."""
    p = Path(path)
    if not p.exists() and Path(str(p) + ".gz").exists():
        p = Path(str(p) + ".gz")
    if p.suffix == ".gz":
        import gzip
        with gzip.open(p, "rt") as f:
            return [json.loads(line) for line in f if line.strip()]
    with open(p) as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: str | Path, rows: Iterable[dict]) -> int:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
            n += 1
    tmp.replace(p)       # never leave a half-written dataset behind
    return n


# --------------------------------------------------------------------------------------- requests

def _req(rid: str, task: str, system: str, user: str, max_tokens: int, **extra) -> dict:
    return {"id": rid, "task": task, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], **extra}


def _mmss(x: float) -> str:
    x = int(round(x))
    return f"{x // 60}:{x % 60:02d}"


def hindsight_text(r: dict) -> str:
    """What actually happened after this moment, in words (grounded pass only, never test nights)."""
    lines = []
    if r.get("big_next") and r.get("t_big") is not None:
        lines.append(f"- next big play: {r.get('next_text') or 'big play'} {_mmss(r['t_big'])} later")
    elif r.get("t_big") is not None:
        lines.append(f"- nothing big for {_mmss(r['t_big'])}" + (f" (then {r['next_text']})" if r.get("next_text") else ""))
    else:
        lines.append("- no big play for the rest of the game")
    if r.get("crunch_next"):
        lines.append("- crunch time followed")
    if r.get("t_break") is not None:
        lines.append(f"- next real break {_mmss(r['t_break'])} later")
    for k, txt in (("garbage", "garbage time (decided)"), ("crunch", "crunch time now"),
                   ("late", "late in the game"), ("mine", "the viewer's own team")):
        if r.get(k):
            lines.append(f"- {txt}")
    return "\n".join(lines)


def given_of(r: dict) -> dict:
    t = r["target"]
    return {"lock_in": t["lock_in"], "safe_to_leave": t["safe_to_leave"], "stay_s": int(t.get("stay_s") or 0)}


def reasoner_requests(rows: Iterable[dict], system: str = STUDENT_SYSTEM, grounded_splits=("train", "valid"),
                      blind_splits=("test",), blind_train_every: int = 0) -> list[dict]:
    """Blind questions on held-out nights (eval), grounded ones on train/valid nights (data).

    blind_train_every > 0 also asks the blind question for every n-th train moment: a check that the
    teacher's own forecasts beat today's before its words are trusted, and soft targets if wanted.
    """
    out = []
    for i, r in enumerate(rows):
        split = r.get("split", "train")
        if split in blind_splits or (blind_train_every and split == "train" and i % blind_train_every == 0):
            out.append(_req(f"{r['id']}|blind", "reasoner_blind", system + BLIND_EXTRA, r["brief"], 220))
        if split in grounded_splits and r.get("target"):
            user = (f"BRIEF (what the small model sees):\n{r['brief']}\n\n"
                    f"WHAT ACTUALLY HAPPENED NEXT (hidden from it):\n{hindsight_text(r)}\n\n"
                    f"GIVEN: {json.dumps(given_of(r))}")
            out.append(_req(f"{r['id']}|grounded", "reasoner_grounded", GROUNDED_SYSTEM, user, 160))
    return out


def jump_requests(rows: Iterable[dict], splits=("train", "valid", "test")) -> list[dict]:
    """Blind jump/stay questions for rows that carry a ``jump`` context (dead balls only).

    The teacher sees the brief and the reel/switch context, never the real break length or the label.
    """
    out = []
    for r in rows:
        j = r.get("jump")
        if not j or r.get("split", "train") not in splits:
            continue
        out.append(_req(f"{r['id']}|jump", "jump_blind", JUMP_SYSTEM,
                        f"{r['brief']}\n\nREEL / SWITCH:\n{j['text']}", 120))
    return out


def train_wordings(novel: dict) -> dict[str, list[str]]:
    """Wordings the teacher may see as examples: the TRAIN wordings only.

    make_data.NOVEL is {kind: {"test": [...], "valid": [...], "train": [...]}} (older shape: a flat list
    whose first two entries were test-only). Held-out wordings are never shown to the teacher, so the
    augmented data can't leak them.
    """
    out = {}
    for k, v in novel.items():
        out[k] = list(v.get("train", [])) if isinstance(v, dict) else list(v[2:])
    return out


def watchdog_requests(novel: dict, per_kind: int = 6, lines_each: int = 12) -> list[dict]:
    """Paraphrase requests per error kind, each with a blind classify follow-up per generated line."""
    examples_by_kind = train_wordings(novel)
    kinds = sorted(examples_by_kind)
    menu = "\n".join(f"- {k}: {KIND_HELP.get(k, k.replace('_', ' '))}" for k in kinds)
    out = []
    for k in kinds:
        examples = examples_by_kind[k]
        if not examples:
            continue
        for j in range(per_kind):
            r = j % len(examples)
            ex = examples[r:] + examples[:r]
            user = (f"Kind: {k} = {KIND_HELP.get(k, k.replace('_', ' '))}\n"
                    f"Examples (do not copy):\n" + "\n".join(f"  {e}" for e in ex[:4]) +
                    f"\nWrite {lines_each} new, different lines of this kind (variation seed {j}).")
            follow = {"task": "wd_classify", "max_tokens": 24, "field": "lines",
                      "messages": [{"role": "system", "content": WD_CLASSIFY_SYSTEM},
                                   {"role": "user", "content": f"Kinds:\n{menu}\n\nLine: {{item}}"}]}
            out.append(_req(f"wd|{k}|{j}", "wd_paraphrase", WD_PARAPHRASE_SYSTEM, user, 900,
                            kind=k, followup=follow))
    return out


# ---------------------------------------------------------------------------------------- answers

def extract_json(text: str) -> dict | None:
    """The first JSON object in a model answer (tolerates <think> blocks, code fences, prose)."""
    if not text:
        return None
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"^.*?</think>", "", text, flags=re.S)      # a cut-off think block
    start = text.find("{")
    while start != -1:
        depth, instr, esc = 0, False, False
        for i in range(start, len(text)):
            c = text[i]
            if instr:
                esc = (c == "\\") and not esc
                if c == '"' and not esc:
                    instr = False
                continue
            if c == '"':
                instr = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        d = json.loads(text[start:i + 1])
                        return d if isinstance(d, dict) else None
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def _num(v, lo: float, hi: float) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:        # NaN
        return None
    return max(lo, min(hi, x))


def _bool(v) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ("true", "yes", "false", "no"):
        return v.strip().lower() in ("true", "yes")
    return None


def _yesno(v) -> str | None:
    b = _bool(v)
    return None if b is None else ("yes" if b else "no")


def clean_reasoner(d: dict | None, given: dict | None = None) -> dict | None:
    """A valid, bounded reasoner answer in ORDER (the student's schema), or None.

    ``given`` overrides fields (the grounded pass keeps hindsight's lock_in / safe_to_leave / stay_s).
    """
    if not isinstance(d, dict):
        return None
    d = {**d, **(given or {})}
    lock = str(d.get("lock_in", "")).strip().lower().replace("-", "_").replace(" ", "_")
    safe = _yesno(d.get("safe_to_leave"))
    conf = _num(d.get("confidence"), 0.0, 1.0)
    stay = _num(d.get("stay_s"), 0, MAX_STAY_S)
    story, why, nxt = (str(d.get(k) or "").strip() for k in ("storyline", "why", "next"))
    if lock not in LOCK or safe is None or conf is None or stay is None or not story or not why:
        return None
    return {"lock_in": lock, "safe_to_leave": safe, "confidence": round(conf, 2), "storyline": story[:100],
            "why": why[:140], "next": nxt[:100], "stay_s": int(stay)}


def eta_s(text: str) -> float | None:
    """Seconds named in a 'next' line ('within 1:15', 'in 45 s', 'in 2 min'), else None."""
    m = re.search(r"(\d{1,2}):(\d{2})", text or "")
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.search(r"(\d+)\s*(s|sec|secs|seconds)\b", text or "")
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+)\s*(m|min|mins|minutes)\b", text or "")
    return float(m.group(1)) * 60 if m else None


def clean_jump(d: dict | None) -> dict | None:
    if not isinstance(d, dict):
        return None
    dec = str(d.get("decision", "")).strip().lower()
    conf = _num(d.get("confidence"), 0.0, 1.0)
    reel = _num(d.get("reel_s"), 0, 120)
    if dec not in ("jump", "stay") or conf is None or reel is None:
        return None
    return {"decision": dec, "reel_s": 0 if dec == "stay" else int(reel),
            "why": str(d.get("why") or "")[:140], "confidence": round(conf, 2)}


def index_answers(answers: Iterable[dict]) -> dict[str, dict]:
    """Teacher output rows by request id (last answer wins, so a resumed run can re-answer)."""
    return {a["id"]: a for a in answers if "id" in a}


def watchdog_lines(answers: Iterable[dict], known_wordings: set[str] | None = None) -> list[dict]:
    """Teacher wordings that passed the blind classify check, de-duplicated, held-out wordings dropped.

    The notebook writes the paraphrase answer with ``followups``: [{"item", "text"}] per line.
    """
    known_wordings = {w.strip().lower() for w in (known_wordings or set())}
    seen: set[str] = set()
    out = []
    for a in answers:
        if a.get("task") != "wd_paraphrase":
            continue
        kind = a.get("kind")
        for f in a.get("followups") or []:
            line = str(f.get("item") or "").strip()
            key = line.lower()
            if not line or len(line) > 220 or key in seen or key in known_wordings:
                continue
            got = extract_json(f.get("text", "")) or {}
            agree = str(got.get("kind", "")).strip() == kind
            seen.add(key)
            out.append({"kind": kind, "line": line, "agree": agree})
    return out


def watchdog_wordings(answers: Iterable[dict], held_out: Iterable[str] = ()) -> dict[str, list[str]]:
    """{kind: [line, ...]} for agent/train/wordings_teacher.json: blind-check agreements only.

    The watchdog's make_data runs its own leak check (number folding + word Jaccard) against the
    held-out wordings; this drops exact repeats early so they never reach the file.
    """
    out: dict[str, list[str]] = {}
    for r in watchdog_lines(answers, set(held_out)):
        if r["agree"]:
            out.setdefault(r["kind"], []).append(r["line"])
    return {k: sorted(v) for k, v in sorted(out.items())}
