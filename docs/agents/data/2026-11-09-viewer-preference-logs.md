---
id: D-VIEWERS
title: Public logs of what people choose, keep watching and abandon
date: 2026-11-09
stream: data
author: data-6 (Claude Sonnet 5.5)
status: draft
supersedes: []
sources: 36
verified: 27
affects: []
gate: []
---

# D-VIEWERS: public viewer-choice and abandonment logs

## Bottom line

- **True total against the 250M target:** 5,145,692,476 rows are published, counted from the sources' own pages, with a licence that permits use. Almost all of it (4.79B) is Yambda, which is music. It is a stand-in, not sports.
- **Total that is sports-adjacent and fetchable at $0:** 355,660,135 rows of short-video watch/skip logs (KuaiRand-27K, KuaiRec, KuaiSAR). That clears 250M, but it is indirect evidence.
- **Direct sports-viewing preference logs: 0 rows.** No open, licensed minute-by-minute TV audience, set-top-box or game-state ratings log exists in what I could open. Nielsen-style data is proprietary.
- Rule applied: unclear licence = no. Rows from unlicensed or gated sets are listed but not counted in the totals.

Totals, by how I counted:

| Total | Rows |
|---|---|
| Counted, licence permits, count from source page (A) | 5,145,692,476 |
| A, but only what a free Kaggle notebook can sensibly fetch (Yambda 500M subset = 480M rows, not 5B) | 835,692,476 |
| A, excluding music (short video + live comments) | 355,692,476 |
| Counted, licence missing/conflicting/restricted (B): Twitch 124M, KuaiLive 5.36M, MIND 15M, YTLive 0.5M, Mr.HiSum 31,892 videos | about 144.9M rows (not usable until cleared) |

Overlap rule: KuaiRand-1K and -Pure, and the KuaiRec small matrix, are not added because they may overlap the larger files. Counted: KuaiRand-27K = 322,278,385 + 1,186,059 random-policy rows.

## Table

| dataset | true data points (source page) | license | training allowed | fetch at $0 | how direct |
|---|---|---|---|---|---|
| KuaiRand-27K (Kuaishou short video; play_time_ms, duration_ms, random-exposure rows) | 322,278,385 standard + 1,186,059 random = 323,464,444 (27,285 users; 32M videos). 1K: 11,713,045 and Pure: 1,436,609 not added (overlap) | CC BY 4.0 on Zenodo record; site badge says CC BY-SA 4.0. Either allows reuse with credit | Yes, with attribution (no ML ban stated; SA only if the site badge governs) | Yes: Zenodo 10439422, open, 27K tar.gz 9.9 GB (1K 1.1 GB, Pure 47 MB) | Indirect: real keep-watching vs abandon on short video, not sports |
| KuaiRec (Kuaishou; watch ratio; fully observed small matrix) | Big 12,530,806 (7,176 users x 10,728 videos). Small 4,676,570 (1,411 x 3,327) not added (possible overlap) | CC BY 4.0 on Zenodo; site badge CC BY-SA 4.0 | Yes, with attribution | Yes: Zenodo 18164998, 432 MB | Indirect: clean unbiased watch-ratio ground truth |
| KuaiSAR (Kuaishou search + recommendation) | 19,664,885 actions (5,059,169 search + 14,605,716 recommend; 25,877 users). Watch-time field not confirmed on page | CC BY-NC-SA 4.0 | Yes for non-commercial personal use; no commercial | Yes: Zenodo 8181109 (size not on page) | Weak: search and click mix |
| Yambda-5B (Yandex Music; listens with played_ratio, likes, dislikes) | 4.79B rows flat-5b (listens 4,649,567,411; likes 89,334,605; dislikes 11,579,143). 500M subset 480M rows; 50M subset 47.8M | Apache-2.0, but card says "published exclusively for scientific and research purposes" | Conditional: permitted by Apache-2.0; research-only wording is Nick's call. Counted in A, flagged | Yes: Hugging Face, no gate. Repo 195 GB, so fetch flat-500m only (stream) | Weak: music completion and skip as attention stand-in |
| Twitch LiveRec (Rappaz+ RecSys21; viewer-to-streamer in 10-min steps) | 124M interactions, 15.5M users, 465k streamers (100k subset: 3M); 43 days | None stated on page ("cite the paper") | No (unclear = no) | Yes: files full_a.csv.gz and 100k.csv at mcauleylab.ucsd.edu (public) | Indirect: real join/leave on live streams, gaming not sports |
| KuaiLive (live-stream clicks, comments, likes, gifts) | 5,357,998 interactions (clicks 4,909,515; comments 196,526; likes 179,311; gifts 72,646); 23,772 users; 452,621 streamers | Conflict: Zenodo record 16565801 says CC BY 4.0; project page says CC BY-NC-SA 4.0 | Unclear = no until Kuaishou confirms | Yes: Zenodo, 858 MB | Indirect: live-room engagement |
| MIND (Microsoft News; impressions with click labels; has a sports category) | "more than 15 million" impressions, 1M users, about 160k articles | Microsoft Research License (research, non-commercial) | No | Yes: Azure blob, no key | Weak: news, not viewing |
| YTLive (YouTube live viewer counts every 5 min) | 507,290 records, 12,156 streams (reported by data-2 in catalog; my fetch of the repo showed no counts) | None stated | No | Yes: GitHub, tiny CSV | Indirect: audience curves, not sports-specific |
| MR.HiSum (YouTube "most replayed" per-frame importance) | 31,892 videos (frame-level count not on page) | CC BY 4.0, plus YouTube ToS applies; ToS conditions unclear | No until ToS checked | Partly: needs YouTube-8M features separately; label file via Google Drive | Indirect: rewatch curves, the nearest public thing to "which moments are rewatched"; sports share unknown |
| Wikimedia pageviews (hourly per article, Dec 2007 on) | Row count not on any page opened. Monthly user file Jan 2025: 4.22 GB bz2 | CC0 | Yes | Yes: dumps.wikimedia.org, take only needed months | Indirect: attention spikes for teams/games/players around live events |
| Pro Kabaddi League YouTube comments (Mendeley) | 32,341 comments | CC BY 4.0 | Yes | Yes (size not stated) | Weak: sentiment on live, replay and highlight clips |
| MovieLens 32M (GroupLens) | Page opened shows only zip sizes (239 MB); "32M" is in the name, rating count not on page | Per README, not read; page says public redistribution generally not permitted | No (unclear) | Yes, 239 MB | Weak: ratings, not watching |
| ContentWise Impressions (TV/movie views with impressions) | Counts not on the repo page | Paper says CC BY-NC-SA 4.0; repo page names only AGPL for code | No (unclear) | Form link (form "optional"); not signed up | Weak-indirect: TV catalogue views |
| MicroLens (Westlake short video) | Counts not on page | None stated | No | Email request or links; not confirmed | Weak |
| Tenrec (Tencent QQ video/article feeds) | About 140M interactions, 5M users, from the NeurIPS paper via search; GitHub page gives no counts; articles included so video share unknown | CC BY-NC 4.0 | Non-commercial only; counts unverified, not added | Download page needs licence acceptance; not opened | Weak |
| IPTV dataset (IEEE DataPort, southern China set-top logs) | 221k devices, 164 channels; record count not stated | CC BY 4.0 but gated | Rejected: needs DataPort login, registration on another site, password from the author | No | Indirect for TV channel switching; unusable at $0 with no sign-up |
| Spotify Sequential Skip (130M sessions) | About 130M sessions (log entries not stated) | No licence text; page says no longer downloadable (8 Jul 2024) | No | No | Weak |
| LFM-2b (Last.fm) | 2,014,164,872 events (from search result; JKU page confirms removal) | Withdrawn over licence issues | No | No, removed | Weak |
| MLHD (Last.fm via MusicBrainz, 27B logs) | 27 billion per search results; page not opened; MLHD+ page blocked by bot check | Not confirmed | No | Not verified | Weak |

## Rejected or not countable

- **Twitch chat sets on Hugging Face** (Fal7acy/twitch-chat mit, lparkourer10/twitch_chat cc-by-sa-4.0, Michielo/twitchchat cc-by-4.0, others no tag): scraped chat with usernames. Identifiable people, and the tag is the uploader's, not Twitch's. Rejected.
- **Reddit NBA team subreddits (1.5M posts, 43M comments, arXiv 1809.01170):** public release and licence not found; usernames. Rejected.
- **"Buzz to Broadcast" (arXiv 2412.10298):** ties Reddit activity to TV viewership for Super Bowl, World Series, NBA Finals and others. Page shows no data release.
- **Ratings by game state:** only papers (for example a 2004 Colorado College working paper on 496 NFL games, closeness raises viewing). No open minute-by-minute set found. Treat it as the one gap this job cannot close.
- **Sports rewatch and share:** no public per-moment replay counts exist for sports; MR.HiSum is the nearest (generic YouTube).
- **Podcast skip/completion logs:** none public that I could find.

## Three best sets for RedZone

1. **KuaiRand-27K:** largest cleared set with real play_time vs duration and random-exposure rows (unbiased). Indirect.
2. **KuaiRec:** 12.5M watch-ratio rows plus a densely observed matrix. Indirect, small enough to iterate on.
3. **Twitch LiveRec (if the licence is cleared):** the only large live join/leave log. Indirect, needs author permission. Fallback while uncleared: Wikimedia pageviews (CC0) for game-time attention spikes.

## Coverage gaps (stated plainly)

I opened dataset pages directly (27). The pool catalogues (Kaggle, Dataverse, Figshare, AWS Open Data, BigQuery, OpenML, DataCite, re3data, data.gov, data.europa.eu, RecBole's list) were not browsed one by one; they were covered through targeted web search, so a Kaggle-only or Dataverse-only set could be missed. Kaggle and Hugging Face pages that need login were not opened. Nothing was downloaded and nothing was signed up for.

## Sources

kuairec.com; kuairand.com; zenodo.org/records/10439422, 18164998, 16565801; kuaisar.github.io; huggingface.co/datasets/yandex/yambda; aicrowd Spotify skip challenge; cseweb.ucsd.edu/~jmcauley/datasets.html; imgkkk574.github.io/KuaiLive; github.com/westlake-repl/MicroLens; github.com/ghalandar/YTLive; github.com/yuangh-x/2022-NIPS-Tenrec; github.com/ContentWise/contentwise-impressions; cp.jku.at/datasets/LFM-2b; github.com/MRHiSum/MR.HiSum; arxiv.org/abs/2412.10298; data.mendeley.com/datasets/hbpd5rpjjh; dumps.wikimedia.org (pageviews readme, pageview_complete readme, 2025-01 monthly listing); wikitech Pageview_hourly; grouplens.org/datasets/movielens; learn.microsoft.com Azure MIND; ieee-dataport.org/documents/iptv-dataset; huggingface.co/api/datasets?search=twitch; plus web-search-only: Tenrec paper (arXiv 2210.10629), ContentWise paper (arXiv 2008.01212), LFM-2b CHIIR 2022, MLHD ISMIR 2017, Princeton IPTV 2008 paper, TwitchChat AIIDE 2020, arXiv 1809.01170, Colorado College NFL Nielsen paper, NeurIPS 2023 Mr.HiSum, data-2 catalog (/Users/nick_matta/Documents/Codex/2026-11-02/task-2/redzone-agents/data/catalog.md).
