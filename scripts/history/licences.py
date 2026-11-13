"""Source registry for the history fetchers: terms class, licence note, status and the finding behind it.

Why: B-01 must not fetch from a source whose terms forbid it ("write the finding down instead"), and every shard
needs the licence line that scripts/license_check.py demands (decisions.md 2026-11-05 11:20 PM: restrictive public
data is allowed for this personal project, tagged "personal-use, not for commercial release"). The keys are the
names in docs/datasources/terms.json (a test checks the classes agree).

Status:
  ok-with-ack  fetchable once the operator passes --accept-restrictive (restrictive terms; the tag stays on every shard)
  ok           open licence, fetchable
  blocked      do not fetch; `finding` says why and what would unblock it
  unverified   licence or URL not confirmed; do not fetch until the note is updated
Findings were read on 2026-11-13 (robots.txt and LICENSE files fetched once each) unless the text says otherwise.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass

PERSONAL_TAG = "personal-use, not for commercial release"


@dataclass(frozen=True)
class Source:
    key: str                 # name in docs/datasources/terms.json
    terms_class: str         # open | restrictive | red (terms.json)
    status: str
    licence: str             # the licence line that travels with every shard
    finding: str             # what was found and where; the reason for the status
    decision: str            # the project decision that allows it (or the one that is missing)
    robots_ok_when_unavailable: bool = False   # an unavailable robots.txt (403/5xx) counts as "no rules" (RFC 9309)


SOURCES: dict[str, Source] = {s.key: s for s in (
    Source(
        "espn", "restrictive", "ok-with-ack",
        "ESPN site API (public JSON, no login). No published licence; personal, non-commercial use only. Tag: " + PERSONAL_TAG,
        "robots.txt on site.api.espn.com, sports.core.api.espn.com and site.web.api.espn.com answers HTTP 403 (a CDN deny page, "
        "not a rules file); RFC 9309 and agent/kaggle/sports_common.PoliteFetcher read a 4xx robots.txt as 'no rules', "
        "docs/agents/data/2026-11-09-allowed-routes.md read it as a bot block. The API endpoints themselves answer 200 to an "
        "honest User-Agent. Disney's Terms of Use (section 2.B, as quoted in that doc) prohibit automated extraction, "
        "including building datasets. This fetcher stops on any 403 from an API call and never changes its User-Agent.",
        "decisions.md 2026-11-05 11:20 PM (Nick, data = B: ESPN-derived data allowed for personal use, tagged); "
        "docs/data-sources.md lists the ESPN site API as in use at <= 1 request/s. The strict reading of the Disney terms is "
        "Nick's call: set this source to blocked in this file to disable it.",
        robots_ok_when_unavailable=True),
    Source(
        "hoopr", "restrictive", "ok-with-ack",
        "sportsdataverse hoopR data (hoopR-nba-data, hoopR-mbb-data): CC BY 4.0 for the repository (LICENSE.md, read 2026-11-13); "
        "the underlying play-by-play is ESPN's, personal non-commercial use. Credit: hoopR / sportsdataverse. Tag: " + PERSONAL_TAG,
        "Served from raw.githubusercontent.com, whose robots.txt answers 404 (no rules). github.com release URLs are closed by "
        "github.com/robots.txt (Disallow: /*/*/download/*), so this fetcher only uses the raw host.",
        "decisions.md 2026-11-05 11:20 PM (data = B); 2026-11-09 robots ruling (raw.githubusercontent.com only, never github.com releases)"),
    Source(
        "wehoop", "restrictive", "ok-with-ack",
        "sportsdataverse wehoop data (wehoop-wbb-data): CC BY 4.0 for the repository (LICENSE.md, read 2026-11-13); the underlying "
        "play-by-play is ESPN's, personal non-commercial use. Credit: wehoop / sportsdataverse. Tag: " + PERSONAL_TAG,
        "Same host and rules as hoopr. Women's college basketball is not a product league (decisions.md Nov 4 7:55 PM) but is allowed as "
        "pretraining data (Nov 4 11:35 PM; tracks/B-foundation-model.md B0).",
        "decisions.md 2026-11-05 11:20 PM (data = B); 2026-11-04 11:35 PM (pretraining allowed)"),
    Source(
        "nflverse", "open", "blocked",
        "nflverse-data play-by-play: CC BY 4.0 (attribution 'nflverse / nflfastR').",
        "The only documented download route is github.com/nflverse/nflverse-data/releases/download/..., which github.com/robots.txt "
        "disallows. The alternative (api.github.com release-asset endpoint, 60 requests/hour unauthenticated) is a reading of "
        "GitHub's policy that docs/agents/data/2026-11-09-allowed-routes.md section 6 leaves to Nick. nflverse-pbp has no in-repo copy. "
        "No fetch is attempted.",
        "Missing: Nick's call on 'The call for Nick' in docs/agents/data/2026-11-09-allowed-routes.md (route A, B or C)."),
    Source(
        "cfbfastr", "restrictive", "unverified",
        "cfbfastR-data: no LICENSE file (HTTP 404 for LICENSE and LICENSE.md, 2026-11-13); CFBD-derived, no wall clocks.",
        "Only seasons 2002-2020 are in the repo, without wall clocks; the ESPN site API covers college football 2004+ with them "
        "(docs/data-sources.md). Left unfetched until a licence is found.",
        "Missing: a licence for cfbfastR-data."),
)}


def manifest_licences(accepted: bool) -> dict:
    """The per-source note written into MANIFEST.json."""
    return {k: {**asdict(s), "tag": PERSONAL_TAG if s.terms_class != "open" else "", "operator_accepted_restrictive": accepted,
                "recorded": dt.date.today().isoformat()} for k, s in SOURCES.items()}


def gate(key: str, accepted: bool) -> Source:
    """The source if it may be fetched now; raises SystemExit with the finding otherwise (loud, never an empty run)."""
    s = SOURCES.get(key)
    if s is None:
        raise SystemExit(f"unknown source {key!r}; known: {', '.join(SOURCES)}")
    if s.status in ("blocked", "unverified"):
        raise SystemExit(f"source {key} is {s.status}: not fetching.\n  finding: {s.finding}\n  needs: {s.decision}")
    if s.terms_class != "open" and not accepted:
        raise SystemExit(f"source {key} has restrictive terms ({s.licence}).\n  allowed by: {s.decision}\n"
                         f"  pass --accept-restrictive to confirm; every shard keeps the tag '{PERSONAL_TAG}'.")
    return s
