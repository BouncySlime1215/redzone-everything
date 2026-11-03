"""The live game reasoner: per-game "what does this game have going" readings.

Two parts, both optional and both off-able without changing anything else:

  head      (redzone/reasoner/head.py) a small tree model per league, numpy only, < 1 ms per
            game state. It reads the same facts as the brief and gives lock_in odds: the chance a
            scorecard big play or crunch play happens in the next few minutes. This is what the
            scorer uses, inside hard limits (redzone/reasoner/reasoner.py, `lock_points`).
  model     the "game-reasoner" LoRA adapter on the shared local base (agent/llm_server.py). It
            reads the brief text and writes the short JSON for the phone: storyline, lock_in, why,
            what to expect next and when, how long to stay, confidence, safe to leave.

The brief (redzone/reasoner/brief.py) is rebuilt on every new play from the live GameState, the
brain's reading and your context, exactly as the training rows were built from the archive
(redzone/lab/reasoner_data.py), so what the models learn is what they see live.
"""
