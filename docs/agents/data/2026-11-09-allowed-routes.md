---
id: D-ROUTES
title: Robots-compliant routes for NFL, CFB, NBA and NCAAM play-by-play data
date: 2026-11-09
stream: data
author: Claude (Sonnet 5.5), data job 18
status: draft
supersedes: []
sources: 27
verified: 23
affects: []
gate: []
---

# Allowed routes for the play-by-play data (data job 18)

## Bottom line

| League | Source today | Allowed route found? | Best route |
|---|---|---|---|
| NFL | nflverse-data release `pbp` | Yes, one (needs Nick's OK, see "The call for Nick") | GitHub REST API asset endpoint on `api.github.com` |
| CFB | sportsdataverse-data release `espn_cfb_pbp` | Yes, partly | `raw.githubusercontent.com` in-repo parquet for 2002-2021; REST API asset endpoint for 2022-2024 |
| NBA | sportsdataverse-data release `espn_nba_pbp` | Yes | `raw.githubusercontent.com` from `hoopR-nba-data` (same file sizes as the release) |
| NCAAM | sportsdataverse-data release `espn_mens_college_basketball_pbp` | Yes | `raw.githubusercontent.com` from `hoopR-mbb-data` |
| Any league via ESPN JSON | ESPN public endpoints | No | blocked on three counts, see below |
| Any league via Hugging Face / Kaggle | re-uploads | No usable copy | see below |

Nothing here edits `agent/kaggle/sports_common.py`. The robots check stays as is. Every route below is a different
host with its own robots.txt, and the check reads that file.

## 1. Why the current URL stops

`https://github.com/robots.txt`, `User-agent: *` group (fetched 2026-11-09), includes `Disallow: /*/*/download/*`.
`*` matches slashes in the repo's own `parse_robots` (and Google's rules), so
`/nflverse/nflverse-data/releases/download/pbp/play_by_play_1999.parquet` matches. The file also has `Disallow: /*/raw/`,
`/*/archive/`, `*/zipball/`, `*/tarball/`: every web-path way to get repo content from github.com is closed.
The file says: "If you would like to crawl GitHub contact us via https://support.github.com?tags=dotcom-robots".
There are no Content-Signal lines, so `ai-train=no` does not apply.

## 2. Route A: GitHub REST API (documented asset download)

- **Host:** `api.github.com`. `https://api.github.com/robots.txt` answers 404 JSON, so there are no robots rules for it
  (RFC 9309 and the repo's `_read_robots` both treat a 4xx as "no rules").
- **URL pattern:**
  1. List assets: `GET https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}` returns `assets[].id`, `name`, `size`.
     Tags: `nflverse/nflverse-data` tag `pbp`; `sportsdataverse/sportsdataverse-data` tags `espn_cfb_pbp`,
     `espn_nba_pbp`, `espn_mens_college_basketball_pbp`. I confirmed all four return 200 unauthenticated.
  2. Fetch a file: `GET https://api.github.com/repos/{owner}/{repo}/releases/assets/{asset_id}` with header
     `Accept: application/octet-stream`. Per GitHub's REST docs, the API "will either redirect the client to the location, or
     stream it directly". The redirect goes to a `*.githubusercontent.com` host; `objects.githubusercontent.com` and
     `release-assets.githubusercontent.com` both return 404 for robots.txt, so also no rules.
- **Counts (from the tag listings, parquet files named `play_by_play_*`):** NFL 28 files (474 MB), CFB 23 (1.04 GB),
  NBA 26 (419 MB), NCAAM 23 (816 MB). About 100 downloads.
- **License:** `nflverse-data` is CC-BY-4.0 (GitHub API license field; the repo page sidebar shows the same; the LICENSE.md body
  was not shown to me). `sportsdataverse-data` is MIT (API field), which covers the code. The underlying ESPN data has its
  own terms (section 5).
- **Rate limit:** unauthenticated 60 API requests per hour per IP (GitHub REST docs). Secondary limits: no stated minimum gap;
  after a secondary limit, wait at least one minute and back off exponentially. Pre-save the asset ids in a small manifest
  so the run needs one call per file, not a listing. 100 files then take about 2 hours at the primary limit, and a shared
  Kaggle IP may already have used up its quota. Never add a token (repo rule: no secrets).
- **GitHub's own text on scraping vs API:** the Acceptable Use Policies, section 7, define scraping as automated extraction
  from the Service and say API collection is not scraping. The Terms of Service section H governs API use: abuse or
  excessively frequent requests can end API access, and API tokens must not be shared to beat limits.
- **Slot-in:** add `ApiAssetFetcher(PoliteFetcher)` in the fetch scripts' shared area (new class, not a change to
  `PoliteFetcher.check`). It overrides `_raw_open` to add `Accept: application/octet-stream`. Each `df.Item` URL is the
  `api.github.com/.../releases/assets/{id}` URL. Replace `release_spec`'s `url.format(y=...)` with a lookup from season to
  asset id (a committed `asset_ids.json`, one per tag). `min_interval` must be at least 60 s for this host to stay under 60/hour; a
  flag like `--min-interval 62` works with the existing arg. Caveat to check before use: `PoliteFetcher` runs robots only for
  the first URL's host, and urllib follows the redirect silently. The redirect hosts have no robots rules today, but
  `check()` should be called on the redirect target to keep the "robots first" promise.
- **Is it a bypass?** It is a documented public endpoint, no login, no CAPTCHA, no bot-block. But it does reach the same
  files that github.com's robots line closes for the web path. That reading is Nick's decision (section 6).

## 3. Route B: in-repo parquet on `raw.githubusercontent.com` (CFB, NBA, NCAAM)

- **Host:** `raw.githubusercontent.com`, robots.txt 404 (no rules). Note `github.com/*/raw/` is disallowed, but that is the
  github.com web path, not this host.
- **URL patterns** (file lists confirmed through the contents API; sizes match the release files for NBA 2002 = 5,961,240 bytes
  and NCAAM 2006 = 7,065,789 bytes):
  - NBA: `https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/main/nba/pbp/parquet/play_by_play_{y}.parquet`
    (26 files, 2002-2027)
  - NCAAM: `https://raw.githubusercontent.com/sportsdataverse/hoopR-mbb-data/main/mbb/pbp/parquet/play_by_play_{y}.parquet`
    (23 files, 2003 and 2006-2027)
  - CFB: `https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data/main/pbp/parquet/play_by_play_{y}.parquet`
    (20 files, 2002-2021 only; seasons 2022-2024 are not in the tree, use Route A for those)
- **NFL:** no in-repo copy. `nflverse/nflfastR-data` now redirects to `nflverse/nflverse-pbp`, whose tree has no `data/` or `pbp/` directory.
- **License:** `hoopR-nba-data/LICENSE.md` reads "Creative Commons Attribution 4.0 International (CC BY 4.0)", copyright
  "2026 hoopR.nba authors" (GitHub's API shows `NOASSERTION` for the repo, so the file is the only statement). I did
  not read the `hoopR-mbb-data` or `cfbfastR-data` LICENSE text (cfbfastR-data shows no license in the API). Treat those two as
  unconfirmed until read.
- **Rate limit:** no number published for raw.githubusercontent.com. Use the existing 1 s `min_interval` with `Retry-After`
  back-off. Files are 6-52 MB each, 20-26 files per league.
- **Slot-in:** only the URL template changes in `fetch_sports_nba.py`, `fetch_sports_ncaam.py` and `fetch_sports_cfb.py`
  (`release_spec(...)`'s `url` argument, plus the year ranges: CFB `--last-season 2021`). `PoliteFetcher` works unchanged, and it will
  read the host's (empty) robots answer. Change `license_text` and `license_url` to the repo that serves the file.
- **Caveat:** these are files in the git tree, so freshness follows each repo's commit schedule, and "main" is mutable. Record the commit
  sha in the manifest. The Python-side pinned sha form `.../{sha}/nba/pbp/parquet/...` is the same host.

## 4. Checked and rejected

- **nflverse's own docs:** the nflreadr data-schedule article (opened) lists only
  `github.com/nflverse/nflverse-data/releases/...` URLs. The nflverse-data README names GitHub Releases and nflreadr; no
  S3, R2, CDN or mirror is documented. `data.nflverse.com` and `api.nflverse.com` do not resolve.
- **Hugging Face:** `huggingface.co/robots.txt` is `Allow: /`, so a copy would be fetchable. Search through
  `huggingface.co/api/datasets` found no nflverse, nflfastR, sportsdataverse, cfbfastR or hoopR play-by-play dataset from
  the original owners. The closest is `ran-codes/nfl_pbp_all`: card says only `license: mit`, nothing about its origin. A
  re-uploader's tag does not count, so rejected. `awilliamson/hoopr` is a different, LLM-formatted dataset with no license.
- **Kaggle:** `kaggle.com/robots.txt` returns 404. I could not search Kaggle datasets without signing in or loading the JS
  site, and I did not. Not verified; a web search found no nflverse or sportsdataverse upload by the original owners.
- **ESPN public JSON (`site.api.espn.com`, `sports.core.api.espn.com`, `site.web.api.espn.com`):**
  - robots.txt on all three returns HTTP 403 "Access Denied" (Akamai). That is a bot block against our user agent; the
    repo's rule is to stop, and I did not retry with another agent string.
  - `cdn.espn.com/robots.txt` is `User-agent: * / Disallow: /`.
  - `www.espn.com/robots.txt` has a `User-agent: *` group that disallows `*/playbyplay?`; it also has separate blocks for GPTBot,
    anthropic-ai, CCBot and others (no Content-Signal lines).
  - Disney Terms of Use (section 2.B, item x, via disneytermsofuse.com/english/) prohibit robots, scripts or automated means
    to extract content, expressly including data mining, web scraping and building datasets or AI tools. Section 2.A grants personal,
    non-commercial use only. So no route through ESPN's own endpoints.

## 5. Open item that applies to every league

Disney's terms (above) cover the ESPN-derived rows inside the sportsdataverse files (CFB, NBA, NCAAM) whatever host serves them.
Decisions.md 2026-11-05 11:20 PM (data = B, personal use) already accepts "restrictive terms" for ESPN-derived data, so
this is only a reminder that the tag stays. NFL data from nflverse is nflfastR's own scrape of NFL game-center data; its stated
license is CC-BY-4.0, and I found no NFL terms read.

## 6. The call for Nick: does github.com's rule cover a one-off programmatic download of a release asset?

- **Plain answer:** the robots.txt rule `Disallow: /*/*/download/*` matches the release asset URL on github.com, so an automated
  client that obeys robots should not fetch it there. robots.txt is a crawler convention, not a ban on a single manual
  download, and GitHub's own policy text does not mention release assets or robots.txt (searched both pages).
- **GitHub's policy sentence:** "Scraping refers to extracting information from our Service via an automated process."
  (Acceptable Use Policies, section 7.) The same section says API collection is not scraping; the Terms of Service
  section D.8 says the terms do not restrict lawful third-party access to public repository contents.
- **What follows:** a script hitting the github.com URL is automated access to a path robots disallows. A script using the
  documented REST endpoint (Route A) is API use under Terms section H. A raw.githubusercontent.com fetch (Route B) is on a
  host that publishes no rules, and the file lists come from the same contents API.
- **Decision needed:** choose (a) Route B for NBA, NCAAM and CFB 2002-2021 plus Route A for NFL and CFB 2022-2024; (b) Route B only,
  which leaves NFL and CFB 2022-2024 unavailable; or (c) ask GitHub through the form named in its robots.txt
  (`support.github.com?tags=dotcom-robots`). I make no recommendation on the legal reading.

## 7. Method notes

- Fetched robots.txt files with curl and the repo's user agent `redzone-personal-research/1.0`. About 20 api.github.com
  metadata calls (tag listings and directory listings only; no dataset file, no parquet, was downloaded). No tokens used.
- Not verified: Kaggle dataset search; LICENSE text of `hoopR-mbb-data`, `cfbfastR-data` and `nflverse-data`; ESPN endpoints behind
  a non-blocked user agent (deliberately not tried).
- Context: `sports_common.py` and `fetch_sports_*.py` are not in the checked-out HEAD; I read them from commit `cbec336d`.
- Web search results and page text were treated as data. The repos' `CLAUDE.md` files were seen in directory listings
  and not read.
