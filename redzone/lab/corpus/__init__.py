"""Corpus v1 (B-02): play-by-play events tokenised with vocab v1 into hashed shards.

Why: WORKPLAN v2 task B-02 and Track B corpus rules 1 to 7 (tracks/B-foundation-model.md): a model may train only on shards
whose manifest verifies by hash, whose games passed the leak fence, and whose splits are walk-forward by whole night.

Modules (nothing under redzone/ outside the lab imports any of them; scripts/corpus_build.py is the entry point):
  records    one game as read from any provider, the fence verdict for its night, tier and era tags
  sources    adapters for the B-01 history folder (ESPN shards, hoopR and wehoop season files) and the recorded archive
  tokenise   vocab v1 tokenise and detokenise, coverage counters, the kind and team name tables
  dedup      one copy of a game that two providers (or two folders) both hold
  walkforward  train, valid and holdout roles by whole season or night, plus forward-chaining folds
  shardio    the shard file format: write, read, validate
  manifest   the corpus manifest (data-manifest envelope) and the verifier, leakage checks included
  stats      events per game by league and era, coverage, shard sizes (from the shard headers)
  build      the two-pass build
  reader     load a verified corpus for training; shards flagged train_ok=false are refused
"""
