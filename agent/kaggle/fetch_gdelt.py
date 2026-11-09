"""Fetch a day-stride sample of GDELT 1.0 events (public daily zips) into parquet shards.

    python agent/kaggle/fetch_gdelt.py [--cap-gb 18] [--out-dir /kaggle/working/data] [--resume-from <prev output>]

Runs in a Kaggle notebook; needs no secret. Engine, resume and manifest: agent/kaggle/data_fetch.py.
Why: data job 10: PASS in docs/agents/data/2026-11-09-data-confirmation.md (unrestricted use); full set is too big for one session so it is sampled.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.kaggle import data_fetch as df  # noqa: E402

COLUMNS = (
    "GLOBALEVENTID SQLDATE MonthYear Year FractionDate "
    + " ".join(f"Actor{n}{c}" for n in (1, 2) for c in (
        "Code", "Name", "CountryCode", "KnownGroupCode", "EthnicCode", "Religion1Code", "Religion2Code",
        "Type1Code", "Type2Code", "Type3Code"))
    + " IsRootEvent EventCode EventBaseCode EventRootCode QuadClass GoldsteinScale NumMentions NumSources"
    " NumArticles AvgTone "
    + " ".join(f"{g}Geo_{c}" for g in ("Actor1", "Actor2", "Action") for c in (
        "Type", "FullName", "CountryCode", "ADM1Code", "Lat", "Long", "FeatureID"))
    + " DATEADDED SOURCEURL"
).split()
BASE = "http://data.gdeltproject.org/events/"


def _args(ap) -> None:
    ap.add_argument("--stride", type=int, default=7, help="keep every Nth day (default 7)")
    ap.add_argument("--start", default="2013-04-01", help="first day of GDELT 1.0 daily files")
    ap.add_argument("--end", default="", help="last day, default today")


def _days(f, a) -> list[df.Item]:
    day = df.dt.date.fromisoformat(a.start)
    end = df.dt.date.fromisoformat(a.end) if a.end else df.dt.date.today()
    groups: dict[str, list[str]] = {}
    while day <= end:
        if (day - df.dt.date.fromisoformat(a.start)).days % a.stride == 0:
            groups.setdefault(day.strftime("%Y%m"), []).append(f"{BASE}{day:%Y%m%d}.export.CSV.zip")
        day += df.dt.timedelta(days=1)
    return [df.Item(month, urls) for month, urls in sorted(groups.items())]


SPEC = df.SourceSpec(
    name="gdelt",
    license_text="GDELT: unlimited and unrestricted use for any academic, commercial, or governmental use; cite GDELT.",
    license_url="https://www.gdeltproject.org/about.html",
    sampling_rule="every 7th calendar day from 2013-04-01 (day index % stride == 0; --stride changes it); one shard group per month; all rows of a kept day",
    list_items=_days,
    csv_columns=COLUMNS,
    csv_delimiter="\t",
    all_strings=True,
    add_args=_args,
)

if __name__ == "__main__":
    raise SystemExit(df.cli(SPEC))
