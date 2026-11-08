# On-device perception for live sports (2026-11-08)

Scope: cheap perception that tells RedZone whether the game on screen is live play, dead ball, replay,
commercial or graphics, on an 8 GB M2 that also runs the live app (local AI <= ~2 GB RSS).
Research only; no code changed. Sources marked **[V]** were opened and read this session, **[S]** were
seen only as search snippets (treat as unconfirmed), **[old]** predate 2025. Anything labelled
"my estimate" is arithmetic or inference by the author, not a source number. No model was run locally,
so no RSS or latency here is measured on this Mac.

## 0. Bottom line

1. The cheapest and strongest signal is already in the repo: the score bug. Its presence, its game
   clock running or stopped (`redzone/screen/tap_bug.py`) and its disappearance are free of new RAM.
   Add a "bug gone" state; the 11-03 research already flags it ([01-inputs-and-sources.md](../2026-11-03/01-inputs-and-sources.md), row D2).
2. Next best per MB: the mic (already opened by `redzone/screen/audio.py`). Replace "one loudness
   number per 0.25 s" with YAMNet class scores (3.7M weights; classes Whistle, Cheering, Crowd,
   Applause, Roar, Buzzer, Air horn exist) as extra features, not as truth.
3. Visual live/replay/commercial classification: use a frozen MobileCLIP2-S0 image encoder in Core ML
   plus a small head trained on Nick's own nights (weak labels from the game clock and play log).
   About 23 MB of weights in fp16 (my estimate).
4. Small VLMs (SmolVLM, Moondream, Florence-2) and PaddleOCR are not worth RAM on this Mac. Use them
   only offline on Kaggle to label frames.
5. Nothing here is proven for NFL/CFB/NBA. Public datasets are soccer-heavy. Every piece ships OFF
   and runs in shadow mode first (release gate in CLAUDE.md).

## 1. Ranked techniques (value per MB of RAM)

Numbers: "weights" is file or parameter size from the source, not resident memory. RSS must be measured
before adoption (step 1 of section 2.4).

| Rank | Technique | New RAM | Measured numbers from sources | What it gives RedZone | Fit |
|---|---|---|---|---|---|
| 1 | Score-bug presence + clock stopped/running (Apple Vision, existing) | 0 MB in Python (separate Swift helper process) | Repo comment: band capture + OCR "~1-3 s" per read; HDMI1 stick ~7 s per frame (`tap_bug.py`, `scorebug.py`). Apple confidence values are mostly 0.5 or 1.0 with `.accurate` **[V]** (forum, 2021, unanswered) | Commercial/graphic = bug absent; dead ball = clock stopped (basketball) | Prime only: YouTube TV frames are DRM-black (in-repo finding) |
| 2 | Audio loudness shifts, black/silence cues from the mic (existing `detect_surges`) | ~0 | ffmpeg documents `blackdetect` as "useful to detect ... commercials" and has `silencedetect`, `scdet` **[V]**; Marlow et al. located ad breaks by silent black frames **[S, old 2001]** | Commercial-break entry/exit, crowd surge | Mic hears only the visible stick. Streaming ad insertion may not use black/silence (my inference; test) |
| 3 | YAMNet audio events (CPU/Core ML) | ~4 MB int8 / ~15 MB fp32 (4 MB float, 1 MB int8 from a third-party guide **[S]**; 3.7M weights **[V]**) | 69.2M multiply-adds per 0.96 s window, 16 kHz mono, 10 ms hop, 521 classes **[V]**. Class map has Whistle (396), Whistling (35), Cheering (61), Applause (62), Crowd (64), Roar (105), Buzzer (392), Air horn (312) **[V, checked in yamnet_class_map.csv]** | Whistle = likely dead ball; roar = crunch moment; speech density | Generic AudioSet whistle is not a referee-whistle detector. Fine-tune a head |
| 4 | MobileCLIP2-S0 image encoder (Core ML / ANE) + linear or tree head | ~23 MB fp16 (11.4M params x 2 B, my estimate); text tower is encoded once offline, never loaded | S0: 11.4M + 63.4M params, 1.5 + 3.3 ms (image + text) on iPhone 12 Pro Max, 71.5 % IN-1k zero-shot **[V]**. S2: 35.7M, 3.6 ms, 77.2 % **[V]**. M2 Neural Engine: up to 15.8 TOPS **[S]** | Frame embedding for live-vs-replay/graphic/commercial, plus zero-shot prompts as a cold start | Zero-shot on "replay" is unproven; use as feature extractor. Check model licence (Apple ML Research Model TOU **[V]**) |
| 5 | Persistent Vision helper + region-of-interest crop (no new model) | 0 | No public fast-vs-accurate latency benchmark found. Apple documents a `regionOfInterest` (normalized rect) and recognition levels (doc page not fetchable; general knowledge, **unverified**) | Cuts the per-frame process spawn and temp PNG in `screen_ocr.recognize` | Measure p50/p95 first |
| 6 | PaddleOCR PP-OCRv5 mobile | 16 MB rec model; Paddle runtime far larger (not measured) | Rec model 16 MB, CPU 21.2 ms (8 threads, Xeon Gold 6271C, model time only; excludes pre/post) **[V]**. Full det+rec pipeline about 1.75 s per image on Xeon **[S]**. 81.29 % avg accuracy on multi-scenario set (66.0 % English subset) **[V]** | Fallback for Linux/Kaggle labeling | Apple Vision already works on-device; Paddle adds a heavy runtime and the English subset is weaker |
| 7 | Small VLMs: SmolVLM-256M/500M, Moondream 0.5B, FastVLM-0.5B, Florence-2-base | 0.8-1.2 GB each | SmolVLM-256M "< 1 GB GPU memory" **[V]**; SmolVLM-500M 1.23 GB **[S]**; Moondream 0.5B int8 996 MiB, int4 816 MiB runtime **[V]**; FastVLM 85x faster TTFT than LLaVA-OneVision-0.5B at 1152 px **[V]**; Florence-2 base ~230M params **[S]** | "What is on screen" questions, offline labeling | Eats half the 2 GB budget for a job a bug+audio+embedding head does. MLX runs on CPU/GPU, not the Neural Engine **[V]** |
| 8 | PANNs (CNN14 mAP 0.431; best 0.439) and BEATs (50.6 mAP AudioSet-2M) | Not for this Mac | PANNs **[V]**, BEATs **[V]**. Parameter counts not verified | Kaggle teachers to distill into a tiny head, or to label audio | Training/inference too heavy for the live Mac |

Do not read rank 3 vs 4 as precision; none of these has a RedZone-specific measurement yet.

### 1.1 What the literature says about each task

- **Live vs replay.** Classic work detects replays through logo transitions (Javed and Khan 2022 uses a
  light CNN for shot class plus logo-transition frames for replays, on soccer/baseball/cricket **[S]**;
  Wang 2005 and Tong 2008 are older **[S, old]**). SoccerNet-v2 **[V, old 2020]**: 13 camera types;
  replays are an attribute (10.9 % of shots are action replays, 2.4 % other replays). Camera-shot
  segmentation reached 47.3 mIoU for CALF vs 69.0 human; shot-boundary detection reached 78.5 mAP with
  a plain histogram method (logo transitions 82.2) vs 59.6 for CALF, human 90.2. Takeaway: even a
  trained model is well short of a human, and a cheap histogram detector beats it on logo wipes.
- **Replay grounding.** Best 41.8 Average-AP (CALF) on soccer **[V, old]**. Not a reliable timing tool.
- **Audio highlights.** 2025-2026 work agrees that audio is informative but not sufficient: an
  audio-only GRU on pretrained embeddings is strong, a simple audio-visual fusion is best, and highlight
  clips overlap heavily with non-highlight in loudness **[V: 2609.17923, Sept 2026]**. A lightweight
  mel-spectrogram plus grayscale-frame model reports 89 % audio, 83 % video accuracy on small data
  **[V: 2501.16100]**. A YAMNet-embedding plus compact classifier on SoccerNet exists **[S, unverified]**.
- **Whistles.** No sports-grade deep whistle benchmark found. A 2011 band-pass/energy method reported
  92.5 % sensitivity and 80.5 % reliability on about 12 h of football, soccer, basketball **[S, old]**;
  a 2022 MFCC method about 80 % precision, 70 % recall, confusing female cheers with whistles **[S]**;
  a 2024 RoboCup paper runs a continuous-CNN whistle detector on a NAO robot, with crowd noise
  as the stated failure mode **[V abstract; numbers not verified]**. Expect crowd-noise confusion.
- **Commercial breaks.** Black-frame/silence heuristics are old ([S] Marlow 2001); modern work uses
  audio-visual CNNs ([S] catalogue entry) and one WACV 2023 ad-detection paper ([S], page blocked,
  not read). Comskip combines logo, black frames, silence, aspect ratio **[S]**; its GitHub page did not
  document this, so treat as unconfirmed.

## 2. Design for RedZone

### 2.1 Principle

Perception outputs are **features and vetoes**, never a sole trigger. They feed the existing
decider through the tree runtime. Rule #1 (never join mid-play) means a false "dead ball" is the costly
error, so every new signal must be able to veto a join but not authorize one alone, until it clears the
gate on locked nights.

### 2.2 State machine per watched game (proposed)

States: `LIVE`, `DEAD` (clock stopped / whistle), `REPLAY`, `BREAK` (commercial/other show), `UNKNOWN`.

| Signal | Source today | Change | State it supports |
|---|---|---|---|
| bug present + readable | `screen/scorebug.py` `parse_bug`, `ScoreBugWatcher.last_bug` | count consecutive absent frames, expose `bug_absent_s` | `BREAK`/`REPLAY` when absent for N frames |
| clock running vs stopped | `screen/tap_bug.py` (`READS_KEPT = 8`, min gap 1.5 s) | reuse as is | `LIVE` vs `DEAD` (basketball; football clock rules differ, test) |
| black/near-uniform frame | `scorebug.is_uniform` (DRM-blank guard) | separate "black" from "DRM-blank" so it is not misread as a break | `BREAK` |
| loudness surge | `screen/audio.py` `detect_surges` | keep | crunch/roar |
| audio events | none | new YAMNet scores per 0.48 s hop: whistle, cheering, crowd, applause, speech | `DEAD` (whistle), crunch |
| frame embedding | none | MobileCLIP2-S0 at 0.5-1 fps on Prime frames | `REPLAY`/graphic/`BREAK` head |

### 2.3 Mapped to real files

- `redzone/screen/scorebug.py`: add a bug-gone counter beside `last_bug`; split `is_uniform` outcome
  into DRM-blank vs black frame. Keep `read_png` OCR contract.
- `redzone/screen_ocr.py` + `scripts/vision_ocr.swift`: today each read writes a PNG and runs
  `subprocess.run` on a compiled helper (`recognize`). The Swift side already sets `.accurate` with
  language correction off (lines 168-173). Proposal: a long-lived helper reading paths on stdin, and a
  lower-third `regionOfInterest` (the code already parses lower 1/3 via `LOWER_FROM = 2/3`). Gate by
  measured p50/p95 before and after.
- `redzone/screen/audio.py`: `MicWatcher` already runs at 16 kHz (`RATE = 16000`), exactly YAMNet's
  input rate. Add a second consumer of the same stream that emits (t, class scores) numbers only,
  keeping the privacy rule: audio never saved, numbers kept 10 min.
- New (not created here): `redzone/screen/audio_events.py` (YAMNet runner), `redzone/screen/liveness.py`
  (state machine above), `scripts/kaggle_liveness_train.py` (head training; Kaggle only).
- `redzone/models_rt/forest.py`: serve the small fusion head (inputs: bug_present, bug_absent_s,
  clock_changing, whistle, cheering, crowd, speech, loudness_surge, embedding PCA) as a tree model,
  consistent with "one brain" (no new runtime).
- `redzone/lab/` (`replaycheck.py`, `cutgrade.py`, `splits.py`, `night/`): evaluation harness; use
  `splits.py` whole-night splits and score vs `entries_mid_play`, `plays_missed_entry`, `dead_air_pct`.
- `redzone/governor.py` `background_cmd`: run any compile/export off the fast cores, as the OCR helper does.
- `redzone/engine.py`: only consumer wiring (it already imports the screen modules).

### 2.4 Plan with checkpoints

1. Measure (about 1 hour): on the Mac, time the current helper on 50 saved lower-third frames
   (p50/p95, RSS of the helper), per CLAUDE.md "no heavy jobs"; this fills the missing numbers above.
2. Free wins (about 1 day): bug-absent counter and black-vs-DRM split; log only (shadow).
3. Audio (about 1-2 days): run YAMNet on 30 min of recorded broadcast audio on Kaggle first; check whistle
   recall against referee-whistle times from the play log; only then port to the Mac, and measure RSS.
4. Embedding head (about 2-3 days, Kaggle): export MobileCLIP2-S0 image tower to Core ML on a Mac only
   for conversion, train the head on saved nights; labels: clock-stopped, play-log event times,
   bug-absent. Report mean and worst forward-chaining fold on whole-night splits.
5. Ship OFF behind a flag; promote only if the locked set shows no rise in `entries_mid_play`, high-leverage
   mid-play leaves, `plays_missed_entry`, `dead_air_pct`. Add a `docs/memory/decisions.md` line for
   any default change.

Budget on the Mac (my estimate, to be measured): YAMNet 4-15 MB + MobileCLIP2-S0 ~23 MB + runtime
overhead. Even doubled for runtimes this is far below the 2 GB ceiling; a VLM would not be.

### 2.5 Datasets and applicability

| Dataset | What | Use for RedZone |
|---|---|---|
| SoccerNet-v2 **[V, old]** | 500 broadcast soccer games, ~300k annotations, 764 h (actions/replays), 306 h camera shots | Pretraining replay/logo-transition/shot-type heads; soccer only, not football/basketball. Check licence/access terms before use (not verified) |
| SoccerNet 2025 **[V]** | Ball action spotting, depth, multi-view fouls, game state | Little for dead-ball perception |
| NSVA **[V]** | NBA clips with play-by-play text, 132 games, season 2018-19 (size from search snippet) | Basketball visual semantics; clip-level, not live-vs-replay |
| BasketEvent **[V, July 2026]** | NBA broadcast clips aligned to play-by-play, 1,000-sample timed subset | Basketball event timing; check access |
| Football/other benchmark 2301.06866 **[S]** | Commentary timestamps incl. American football plays | Possible NFL event labels; unverified |
| Nick's own recordings (`eval-data-v1`, recorder) | Real nights, play log, bug clocks | Primary training and test data; weak labels from clock and play log; avoids sport shift |

### 2.4b Legal capture only

Inputs stay: screenshots of Nick's own sticks, mic audio of his room. HDMI capture of the TV output is
fine only where the signal is not HDCP-blocked; do not strip or bypass protection. YouTube TV already comes
out black, so the vision path is Prime-only unless a legitimate capture works.

## 3. Risks

1. **Sport shift.** Public data are soccer-heavy; whistles, crowd and graphics differ in NFL/NBA/NHL. Mitigate: train on own nights.
2. **Mic hears only the visible stick.** Audio cannot inform the hidden stick, which is the one being switched to. Use it for the visible side (leave decisions) only; entry still relies on bug/feed.
3. **False dead ball violates Rule #1.** Whistle/silence/clock-stop must veto, never authorize alone. Football clock stops are not always dead balls (test).
4. **Ad insertion without black/silence.** Streaming ads may cut cleanly; bug-absent is the more robust cue, but a replay or graphic also hides the bug (my hypothesis: not tested).
5. **Apple confidence is coarse** (0.5/1.0, 2021 forum **[V]**); keep the existing parse validation (score never decreases, 10 s ambiguity window) rather than thresholding confidence.
6. **Latency.** 2 s cadence plus ~1-3 s OCR means decisions use 3-5 s old frames; do not use embeddings for sub-second calls.
7. **Memory creep.** Python wheels (TensorFlow/Paddle) can dwarf the model. Prefer Core ML or ONNX; measure RSS before merge.
8. **Licences.** MobileCLIP2 weights under Apple ML Research Model TOU, data CC-BY-NC-ND **[V]**: read before shipping; the repo is public.
9. **Source quality.** Several figures above are [S] (search snippets); the SmolVLM-500M, Florence-2, Javed and Khan, and 2011/2022 whistle numbers must be re-checked at source before they drive a decision.
10. **Gate discipline.** "95% good minimum" on locked set plus fresh nights; no overfitting to one night's broadcaster.

## 4. Bibliography

Verification: V = page opened and read; S = search snippet only.

1. Apple, ml-mobileclip repository (MobileCLIP2 table, licences, iOS app). https://github.com/apple/ml-mobileclip [V]
2. Faghri et al., "MobileCLIP2: Improving Multi-Modal Reinforced Training," arXiv:2508.20691, Aug 2025. https://arxiv.org/abs/2508.20691 [V]
3. Marafioti et al., "SmolVLM: Redefining small and efficient multimodal models," arXiv:2504.05299, Apr 2025. https://arxiv.org/abs/2504.05299 [V]
4. Hugging Face blog, SmolVLM-256M and 500M. https://huggingface.co/blog/smolervlm [V; no memory figures in text]
5. Moondream, "Introducing Moondream 0.5B," Dec 2024. https://moondream.ai/blog/introducing-moondream-0-5b [V]
6. Vasu et al., "FastVLM," arXiv:2412.13303 (CVPR 2025). https://arxiv.org/abs/2412.13303 [V]; code https://github.com/apple/ml-fastvlm [V]
7. Xiao et al., "Florence-2," arXiv:2311.06242, Nov 2023. https://arxiv.org/abs/2311.06242 [V; abstract only, 2023]
8. Cui et al., "PaddleOCR 3.0 Technical Report," arXiv:2507.05595, Jul 2025. https://arxiv.org/abs/2507.05595 [V]
9. PaddleOCR v3.4.1 text recognition docs (PP-OCRv5_mobile_rec: 16 MB, CPU 21.20 ms, test setup). https://www.paddleocr.ai/v3.4.1/en/version3.x/module_usage/text_recognition.html [V]
10. Apple Developer Forums, "VNRecognizedText confidence values are only 0.5 and 1.0," Nov 2021. https://developer.apple.com/forums/thread/695693 [V, old, unanswered]
11. Google, YAMNet (TensorFlow models repo; 3.7M weights, 69.2M mult-adds, 521 classes). https://github.com/tensorflow/models/tree/master/research/audioset/yamnet [V]; class map https://raw.githubusercontent.com/tensorflow/models/master/research/audioset/yamnet/yamnet_class_map.csv [V]
12. Kong et al., "PANNs," arXiv:1912.10211 (2019/2020) and repo https://github.com/qiuqiangkong/audioset_tagging_cnn [V, old]
13. Chen et al., "BEATs," arXiv:2212.09058, Dec 2022. https://arxiv.org/abs/2212.09058 [V, old]
14. Xu et al., "Audio for Sports Highlight Detection: A Comparative Empirical Study," arXiv:2609.17923, Sept 2026 (MMSports 2026). https://arxiv.org/abs/2609.17923 [V]
15. Della Santa and Lalli, "Automated Detection of Sport Highlights from Audio and Video Sources," arXiv:2501.16100, Jan 2025. https://arxiv.org/abs/2501.16100 [V]
16. "Real-Time Multimodal Signal Processing for HRI in RoboCup: Understanding a Human Referee," arXiv:2411.17347, Nov 2024. https://arxiv.org/abs/2411.17347 [V abstract]
17. Deliege et al., "SoccerNet-v2," arXiv:2011.13367 (CVPRW 2021). https://arxiv.org/abs/2011.13367 [V; tables via ar5iv https://ar5iv.labs.arxiv.org/html/2011.13367, old]
18. Giancola et al., "SoccerNet 2025 Challenges Results," arXiv:2508.19182, Aug 2025. https://arxiv.org/abs/2508.19182 [V]
19. "Sports Video Analysis on Large-Scale Data" (NSVA), arXiv:2208.04897, Aug 2022. https://arxiv.org/abs/2208.04897 [V, old]
20. "BasketEvent," arXiv:2607.21267, Jul 2026. https://arxiv.org/abs/2607.21267 [V abstract]
21. Apple, ml-ane-transformers (up to 10x faster, 14x lower peak memory claims; iPhone 13, iOS 16). https://github.com/apple/ml-ane-transformers [V, old 2022]
22. Apple, MLX (CPU and GPU on Apple silicon, unified memory). https://github.com/ml-explore/mlx [V]
23. FFmpeg filters documentation (`blackdetect`, `silencedetect`, `scdet`). https://ffmpeg.org/ffmpeg-filters.html [V]
24. Apple M2 announcement, Jun 2022 (15.8 TOPS Neural Engine), via Business Wire. https://www.businesswire.com/news/home/20220606005752/en/Apple-Unveils-M2-Taking-the-Breakthrough-Performance-and-Capabilities-of-M1-Even-Further [S; page returned 403]
25. Javed and Khan, "Shot classification and replay detection for sports video summarization," Frontiers of Information Technology & Electronic Engineering 2022. https://link.springer.com/article/10.1631/FITEE.2000414 [S]
26. Liu et al., "A Deep Neural Framework to Detect Individual Advertisement (Ad) from Videos," WACV 2023. https://openaccess.thecvf.com/content/WACV2023/papers/Liu_A_Deep_Neural_Framework_To_Detect_Individual_Advertisement_Ad_From_WACV_2023_paper.pdf [S; 403]
27. Marlow et al., audio and video processing for automatic TV advertisement detection (DCU, 2001). https://doras.dcu.ie/334 [S, old]
28. Building scalable video understanding benchmarks through sports, arXiv:2301.06866. https://arxiv.org/abs/2301.06866 [S]

Not included because unverifiable: the Hugging Face / Gigazine "52.6 % OCRBench" figure for SmolVLM-256M,
Comskip's method list (repo page lacks it), and the YAMNet-on-SoccerNet repository paper (bot-blocked).

## 5. Next action (under 2 minutes)

Pick step 2.4-1: save 50 lower-third PNGs from tonight's Prime game into the scratch folder, so the
OCR latency measurement can run on the Mac.
