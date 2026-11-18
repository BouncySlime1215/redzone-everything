"""Job runner for training work on Colab and Kaggle (WORKPLAN v2 F-03); the Mac never trains, the show never imports this.

Why: one boring path every training job (trainer B-03, Decisive-Q data A-02, history fetches B-01) runs through:
spec.py (pinned work unit), worker.py (runs on the VM), cell.py (the Colab cell), parse.py (read the result back),
kaggle_backend.py (unused stub until Nick creates a Kaggle API token). See docs/jobs.md.
"""
