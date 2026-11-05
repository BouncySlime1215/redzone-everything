"""Data G attention puller: offline checks (no network). Counts only, never text."""
import gzip
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location("attention_pull", Path(__file__).resolve().parents[1] / "scripts" / "attention_pull.py")
ap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ap)
RX = ap.compile_terms(ap.load_terms())


def test_terms_match_whole_words_hashtags_and_longest():
    assert ap.match_terms("RED WINGS win in OT!! #Touchdown", RX) == {"red wings", "touchdown"}
    assert ap.match_terms("the wingspan of a hawkish ravenous fan", RX) == set()
    assert ap.match_terms("Commanders commanders COMMANDERS", RX) == {"commanders"}


def test_jetstream_counts_only_and_drops_text(tmp_path):
    c = ap.MinuteCounter()
    t = 1762300800.0  # 2025-11-05T00:00Z
    for i, text in enumerate(["Commanders touchdown!", "lakers lose", "nothing here"]):
        ap.handle_jetstream({"kind": "commit", "time_us": int((t + i) * 1e6),
                             "commit": {"operation": "create", "collection": "app.bsky.feed.post",
                                        "record": {"text": text}}}, c, RX)
    ap.handle_jetstream({"kind": "commit", "commit": {"operation": "delete", "collection": "app.bsky.feed.post"}}, c, RX)
    rows = c.rows("bsky")
    assert rows == [{"minute_utc": "2025-11-05T00:00Z", "source": "bsky", "posts_total": 3,
                     "terms": {"commanders": 1, "lakers": 1, "touchdown": 1}, "license": ap.TAG}]
    ap.append_rows(tmp_path, "bsky", rows)
    blob = gzip.open(tmp_path / "bsky" / "2025-11-05.jsonl.gz", "rt").read()
    assert "lose" not in blob and "nothing here" not in blob  # no text stored
    led = ap.ledger(tmp_path)["bsky"]
    assert led["rows_minutes"] == 1 and led["tokens_new_info_label"] == 2 + 2 * 3
    assert "label" in led["feeds"] and "never a same-moment input" in led["use"] and "personal use" in led["license"]


def test_twitch_snapshot_aggregates_and_drops_titles_and_logins():
    row = ap.twitch_snapshot([{"user_login": "someone", "title": "Lakers vs Kings LIVE watch party", "viewer_count": 900},
                              {"user_login": "other", "title": "Kings postgame", "viewer_count": 100},
                              {"user_login": "x", "title": "chess", "viewer_count": 5}], RX, 1762300800.0)
    assert row["viewers_total"] == 1005 and row["streams_total"] == 3
    assert row["terms"]["kings"] == {"streams": 2, "viewers": 1000, "max_viewers": 900}
    s = json.dumps(row)
    assert "someone" not in s and "watch party" not in s


def test_minute_counter_flush_keeps_open_minutes():
    c = ap.MinuteCounter()
    c.add_post(1762300800.0, "bears", RX)
    c.add_post(1762300870.0, "bears", RX)
    assert len(c.rows("bsky", "2025-11-05T00:01Z")) == 1
    c.drop_before("2025-11-05T00:01Z")
    assert [r["minute_utc"] for r in c.rows("bsky")] == ["2025-11-05T00:01Z"]
