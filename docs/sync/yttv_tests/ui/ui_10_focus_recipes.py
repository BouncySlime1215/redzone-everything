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
  3. DPAD_LEFT x9 (to the row start), then DPAD_RIGHT x9 one at a time, screencap each: map index->label
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
    out.write("reveal", focused=first, row=row_from_ocr(o), focus_boxes=o["focus"])
    if U.parse_overlay(o, time.time())["scrub_label"]:   # bar has focus: LEFT would SEEK -> go down first
        g.key("DPAD_DOWN")
        out.write("note", msg="overlay opened with the bar focused; moved down to the row")
    g.key(*(["DPAD_LEFT"] * 9), gap=0.6)
    rowmap = []
    for i in range(10):
        _, _, o = U.snap(g, f"{ID}_row{i}")
        ft = U.focused_text(o)
        fb = o["focus"][0] if o["focus"] else None
        rowmap.append(dict(i=i, focused=ft, x=fb and round(fb["x"], 3)))
        out.write("row_step", i=i, focused=ft, focus=fb)
        if i < 9:
            g.key("DPAD_RIGHT")
    # back to the start of the row, then up to the bar
    g.key(*(["DPAD_LEFT"] * 9), gap=0.6)
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
    g.key("DPAD_DOWN", "DPAD_DOWN")
    labels = [r["focused"] for r in rowmap]
    for name, rx in (("cc_button", r"^\s*c\s*c\s*$"), ("stats_panel", r"stats|scores|box ?score"),
                     ("key_plays", r"key ?plays|highlights")):
        idx = next((i for i, l in enumerate(labels) if l and re.search(rx, l, re.I)), None)
        if idx is not None:
            keys = ["DPAD_DOWN"] + ["DPAD_LEFT"] * 9 + ["DPAD_RIGHT"] * idx + ["DPAD_CENTER"]
            U.save_recipe(name, dict(keys=keys, label=labels[idx], from_="no overlay"))
    if scrub_keys:
        U.save_recipe("scrub_focus", dict(keys=scrub_keys, from_="overlay just revealed"))
    U.save_recipe("row", dict(labels=labels))
    if args.open_safe:
        for i, l in enumerate(labels):
            if l and any(s.lower() == l.lower().strip("():[]=E ").strip() or s.lower() in l.lower() for s in SAFE_OPEN):
                hide_overlay(g)
                g.key("DPAD_DOWN", *(["DPAD_LEFT"] * 9), *(["DPAD_RIGHT"] * i), gap=0.6)
                _, _, oF = U.snap(g, f"{ID}_pre{i}")
                if U.focused_text(oF).strip().lower() != l.strip().lower():
                    out.write("skip_open", label=l, focused=U.focused_text(oF), why="focus not where expected")
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
