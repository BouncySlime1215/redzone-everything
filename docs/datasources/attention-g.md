# Data Division G: attention and hype at scale

**Source:** Nick, 12:30 AM ET Nov 5: measure what people actually watch and care about, tied to sports moments,
legal and public only. Purpose: learned labels for "how much people cared about THIS moment", for Watch Value and
the reward model. "Real parameters that help, not bullshit": every source names the label it feeds and its
information per GB. Ids EXP-1700..1799.

**Hard rule (test writers):** G data is a **label and evaluation target only**. View counts, pageviews, post
counts and viewer curves are known only after the moment, so they never enter the brain as a same-moment input.
A G label is judged only by its lift on % of oracle and the worth-it line, never on watch time.

**Common rules:** license line per source; plan B tag "personal-use, not for commercial release"; official APIs and
public endpoints only; no logins, CAPTCHAs or bypasses; rate limits respected; **numbers only** (no post text,
titles, user ids, chat or video stored, enforced by tests/test_attention_pull.py); free GitHub runners, never the
Mac; no spending; keys are made by Nick and added as Actions secrets, never pasted in chat.

## Crew

| Worker | Job | Runs as |
|---|---|---|
| G1 live collectors | Bluesky Jetstream minute counts; Twitch Helix sports viewer curves | `attention-factory/<src>-*` push runs, one per evening slate (up to 5h55m) |
| G2 published ratings | per-game TV viewers (and quarter peaks where printed) from league and network press releases | one-shot pull per week; numbers only |
| G3 label builder | joins G1 + B's Wikipedia hourly + C's YouTube velocity into one `attention` label per (game, minute), and runs the lift tests | CPU job after each night; owns EXP-1701/1702 |

## Sources

| # | Source | Status | License line | Feeds | Info per GB |
|---|---|---|---|---|---|
| G-a | Bluesky Jetstream (public firehose, no key) | **landing** (EXP-1700) | Public AT Protocol data via Jetstream, no auth; counts per term per minute only, no text or DIDs; plan B, personal use, not for commercial release | label `attention_minute`: per-team mention spike (z vs that team's own baseline), lag 0-5 min | measured by the ledger after the first run (expect ~0.1-0.2M label tokens/day at < 1 MB/day) |
| G-b | Twitch Helix `/streams`, Sports category | code ready, **needs Nick's key** (EXP-1703) | Twitch Developer Services Agreement, free app token; aggregate viewers only; plan B, personal use | label `live_viewers_minute`: viewer curve per team term (watch parties, official co-streams) | same shape as G-a, ~1 row/min |
| G-c | League and network press releases (NFL, NBA, NHL, ESPN, NBC, Fox, CBS, Amazon) | planned (G2) | Public press releases read for numbers only; plan B | label `game_viewers` (game level, plus quarter peaks when printed) | tiny storage, few thousand rows/season; highest value per row |
| G-d | D's prediction-market ticks (volume field) | **held for Nick** | D's license lines | would be label `market_attention_minute` (volume spike); held because Nick's rule says market data is an input only, never a label | counted in D's ledger, not ours |
| G-e | B's Wikipedia hourly pageviews, C's YouTube view velocity, B's Trends | read only | B's and C's license lines | labels `wiki_hourly`, `highlight_velocity` (hindsight) | counted in B's and C's ledgers |
| G-f | Academic suspense, arousal and audience datasets (open licenses) | G3 hunt | per dataset; only open licenses | eval-only priors if sports-specific | honest prior: most are small and off-domain (movies, music); kept only if they have sports moments with timestamps |

**Not taken:**
- **Reddit.** The Data API needs an approved app, and its terms bar ML training on user content without permission.
  Held until Nick decides; Bluesky covers the same signal (social chatter per minute) with no key.
- **External excitement indexes.** Sites like these compute the index from the same play-by-play WP we already
  have, so the information is derived (novelty 0). We compute it ourselves as the **baseline** a G label must beat.
- **Minute-by-minute Nielsen.** Not public. Only the published game-level and quarter-peak numbers (G-c) are used.

## Pre-registered rows

| id | claim | bar | status |
|---|---|---|---|
| EXP-1700 | Bluesky minute counts land for a full evening slate (Thu Nov 5 ET, 23:00-04:45 UTC) | ≥ 95% of minutes present; 0 text or id fields (test); license line and B tag on every row; ledger row with feeds and tokens/GB | running |
| EXP-1701 | Social attention carries moment signal, not noise | per game, a team's mention z-score over t..t+5 min after each play vs that play's realized \|ΔWP\|: Spearman ≥ 0.15 with a game-cluster bootstrap 95% CI above 0, AND above a clock-only baseline; valid nights only. Fail = G-a dropped from labels | queued (needs ≥ 8 nights) |
| EXP-1702 | Attention labels improve the reward model | Watch Value trained with the attention label as an auxiliary target beats the WP-swing-only label, and a within-game shuffled placebo of the same label, on % of oracle and the worth-it line on valid (shared harness `scripts/wv_aux_label.py` with crew H's EXP-1800, one label source per run, each graded separately) (grader frozen by the integrator); never judged on watch time | queued (after 1701) |
| EXP-1703 | Twitch sports viewer curves land | ≥ 95% of minutes for one slate; aggregate only; license line and B tag | blocked on Nick's Twitch key |

## Running it

```
git push origin HEAD:attention-factory/bsky-<date>     # 345 min by default
echo 15 > scripts/attention_minutes.txt                 # on the run branch, for a short test
```

Outputs land on branch `attention-out/<src>` (`<src>/<day>.jsonl.gz` + `ledger.json`), counts only.
