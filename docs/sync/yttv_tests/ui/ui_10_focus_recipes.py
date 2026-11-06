#!/usr/bin/env python3
"""ui_10 Focus-safe navigation recipes for the YTTV player overlay (run this FIRST tomorrow).

HYPOTHESIS: the overlay's focus graph is fixed: DPAD_DOWN reveals it, the button row reads
[more] HD CC [check] | Networks Multiview Episodes 'More to watch' (+ 'Stats'/'Key plays' during games),
DPAD_UP from the row focuses the progress bar and shows the scrub label WITHOUT seeking, BACK hides
the overlay. None of these moves the playhead. If so, ui_02/06/07/08 can use exact key lists
(saved to yttv_tests/ui/recipes.json) instead of searching by OCR every time.

PROCEDURE (hidden stick only; never DPAD_CENTER except with --open-safe on the whitelist):
  1. deep-link tune --channel; wait 5 s; E0 = media_session E
  2. DPAD_DOWN -> screencap/OCR: which label holds focus (focus rectangle from the OCR helper)
  3. DPAD_DOWN (max 3) until a known row button holds focus; then LEFT one guarded step at a time to the
     row start and RIGHT one step at a time, screencap each: map index->label. Never LEFT/RIGHT while
     the focus is unknown or on the scrubber (there they SEEK: Nov 5 night, LEFT x9 cost 60 s).
  4. DPAD_UP -> screencap: scrub label present? (bar focus) ; DPAD_UP again -> screencap ; DPAD_DOWN x2
  5. --open-safe: for label in ('More to watch','Episodes','Stats','Key plays'): go there, CENTER,
     screencap + OCR the panel text, BACK, screencap (overlay still up? player still playing?)
  6. BACK until no overlay (max 2); E1 = media_session E; re-tune.
  Writes recipes: 'row' (index->label), 'scrub_focus' (keys from fresh overlay to bar focus),
  'cc_button', 'stats_panel', 'key_plays' (keys from no overlay to the focused button, + CENTER).

PASS: |E1 - E0| <= 0.5 s (no seek, no pause) AND the row map has >= 4 labels AND scrub_focus found.
"""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_common as U  # noqa: E402

ID = "ui_10_focus_recipes"
SAFE_OPEN = ("More to watch", "Episodes", "Stats", "Key plays", "Key Plays", "Scores")
ROW_ICONS = ["more", "HD", "CC", "check", "Networks", "Multiview", "Episodes", "More to watch"]


def extra(ap):
    ap.add_argument("--open-safe", action="store_true")


def row_from_ocr(o: dict) -> list[tuple[float, str]]:
    labels = []
    for t in o["obs"]:
        if t["y"] > 0.85:
            labels.append((round(t["x"], 3), t["text"].strip()))
    return sorted(labels)


def hide_overlay(g):
    """BACK only while the overlay is visibly up (BACK with no overlay would leave the player)."""
    for _ in range(2):
        _, wB, oB = U.snap(g, f"{ID}_hide")
        if not U.parse_overlay(oB, wB)["overlay"]:
            return
        g.key("BACK"); time.sleep(0.8)


def live(args, g, out):
    if not g.ok():
        return dict(skipped="stick on screen or unknown", pass_=None)
    g.tune(args.channel); time.sleep(5)
    E0 = U.ms_raw(g.host).get("E")
    g.key("DPAD_DOWN"); _, _, o = U.snap(g, f"{ID}_reveal")
    first = U.focused_text(o)
    out.write("reveal", focused=first, row=row_from_ocr(o), focus_boxes=o["focus"],
              focus_status=o.get("focus_status"))
    # Nov 5 night: focus was unknown here and LEFT x9 SEEKED the stick ~60 s back. Now: DOWN (never
    # LEFT/RIGHT) until a known row button holds focus; if none does, stop without mapping.
    ft, down_keys = U.reach_row(g, reveal=())
    out.write("reach_row", focused=ft, keys=down_keys)
    rowmap = []
    if ft:
        # walk LEFT to the row start, one guarded step at a time
        lefts = 0
        while lefts < 9:
            g.key("DPAD_LEFT"); lefts += 1
            _, _, o = U.snap(g, f"{ID}_left{lefts}")
            nxt = U.focused_text(o).strip()
            if not U.is_row_button(nxt):
                # focus left the row (unknown counts as the scrubber): press nothing more sideways
                out.write("row_stop", i=0, focused=nxt, why="LEFT left the button row: stopped")
                ft = ""
                break
            if nxt == ft:              # focus did not move: row start
                break
            ft = nxt
        for i in range(10 if ft else 0):
            _, _, o = U.snap(g, f"{ID}_row{i}")
            cur = U.focused_text(o).strip()
            fb = o["focus"][0] if o["focus"] else None
            if not U.is_row_button(cur):
                out.write("row_stop", i=i, focused=cur, why="not a row button: no more LEFT/RIGHT")
                break
            if rowmap and cur == rowmap[-1]["focused"]:
                break                  # row end
            rowmap.append(dict(i=i, focused=cur, x=fb and round(fb["x"], 3)))
            out.write("row_step", i=i, focused=cur, focus=fb)
            g.key("DPAD_RIGHT")
    else:
        out.write("row_stop", i=0, focused=first, why="no row button reached after DOWN x3: no LEFT/RIGHT pressed")
    # up to the bar (UP does not seek), read the scrub label, then hide
    g.key("DPAD_UP"); _, wU, oU = U.snap(g, f"{ID}_up1")
    ov1 = U.parse_overlay(oU, wU)
    g.key("DPAD_UP"); _, wU2, oU2 = U.snap(g, f"{ID}_up2")
    ov2 = U.parse_overlay(oU2, wU2)
    out.write("up", up1_label=ov1["scrub_label"], up2_label=ov2["scrub_label"], up1_focus=U.focused_text(oU))
    scrub_keys = None
    if ov1["scrub_label"]:
        scrub_keys = ["DPAD_UP"]
    elif ov2["scrub_label"]:
        scrub_keys = ["DPAD_UP", "DPAD_UP"]
    labels = [r["focused"] for r in rowmap]
    for name, rx in (("cc_button", r"^\W*c\s*c\W*$"), ("stats_panel", r"stats|scores|box ?score"),
                     ("key_plays", r"key ?plays|highlights")):
        idx = next((i for i, l in enumerate(labels) if l and re.search(rx, l, re.I)), None)
        if idx is not None:
            # label only: consumers navigate by focus (navigate_to), never by a blind key list
            U.save_recipe(name, dict(label=labels[idx], index=idx, from_="row start"))
    if scrub_keys:
        U.save_recipe("scrub_focus", dict(keys=scrub_keys, from_="overlay just revealed"))
    U.save_recipe("row", dict(labels=labels))
    if args.open_safe:
        for i, l in enumerate(labels):
            if l and any(s.lower() == l.lower().strip("():[]=E ").strip() or s.lower() in l.lower() for s in SAFE_OPEN):
                hide_overlay(g)
                nav = U.navigate_to(g, "^" + re.escape(l.strip()) + "$")
                if not nav["found"]:
                    out.write("skip_open", label=l, focused=nav.get("text"), why=nav.get("why") or "not reached")
                    continue
                g.key("DPAD_CENTER"); time.sleep(1.5)
                _, _, oP = U.snap(g, f"{ID}_open{i}")
                out.write("panel", label=l, texts=[t["text"] for t in oP["obs"]][:40])
                g.key("BACK"); time.sleep(1)
                st = U.ms_raw(g.host)
                out.write("after_back", label=l, state=st.get("st"), E=st.get("E"))
    hide_overlay(g)
    E1 = U.ms_raw(g.host).get("E")
    g.tune(args.channel)
    dE = (E1 - E0) if (E0 and E1) else None
    ok = dE is not None and abs(dE) <= 0.5 and sum(1 for l in labels if l) >= 4 and bool(scrub_keys)
    return dict(E_drift=dE, row=labels, scrub_keys=scrub_keys, pass_=bool(ok))


def dry(args, out):
    p, w = U.fixture("overlay")
    o = U.ocr(p)
    row = row_from_ocr(o)
    out.write("fixture_row", png=str(p), row=row)
    ps, ws = U.fixture("scrub")
    os_ = U.ocr(ps)
    out.write("fixture_scrub_focus", png=str(ps), focus_status=os_["focus_status"], focus=os_["focus"],
              scrub_label=U.parse_overlay(os_, ws)["scrub_label"])
    # write what the recipes WOULD be from the fixture layout to a dry file (never the real recipes.json)
    dry_rec = dict(row_from_fixture=[t for _, t in row], icons_expected=ROW_ICONS,
                   scrub_focus_guess=["DPAD_UP"], note="a3_cc_focus.png shows the bar focused + label after UP")
    out.write("would_save", **dry_rec)
    return dict(row_labels=len(row), scrub_label_fixture=U.parse_overlay(os_, ws)["scrub_label"], pass_=None)


if __name__ == "__main__":
    sys.exit(U.run(ID, __doc__, live, dry, extra))
