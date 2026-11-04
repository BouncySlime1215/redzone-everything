"""Candidate profile: one config, every flag named, argv is what eval_sports takes."""
from redzone.noggin import profile as P


def test_candidate_argv():
    assert "candidate" in P.names()
    nba = P.argv("candidate", "nba")
    assert nba[:2] == ["--behind-live", "90"]
    assert P.argv("candidate", "nfl")[:2] == ["--behind-live", "75"]
    sets = [nba[i + 1] for i, a in enumerate(nba) if a == "--set"]
    assert "noggin.priority.enabled=true" in sets and "noggin.timing.leagues=[nba]" in sets
