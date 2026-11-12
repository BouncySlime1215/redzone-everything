"""[park.verify_game, OFF] Is the game on a stick's picture the game PARK thinks that channel carries?

Nov 11, the first in-app show on the real TV: the show called ESPN's picture "CLE @ NY" for 15 minutes after that game
ended and ESPN moved on to GS @ HOU (a doubleheader). A game is mapped to a channel from the schedule (ESPN's
broadcast field, or a guess such as Peacock -> NBCSN), never from the picture. With the flag on, PARK reads the words on
the picture after every tune and decides one of three things, from the team names alone:

  verified  the expected game's two teams are named, and no other live game's two teams are
  wrong     another live game's two teams are named, and the expected game's are not: PROOF of the wrong game
  unknown   anything else: nothing read, a DRM-blank frame, both games named (an "up next" line), one team only

Where the words come from (ParkHands._repo_texts): YouTube TV's video is DRM-blank in a screenshot, but the overlay is
not, and the read gesture already takes one full-frame screenshot and reads its text for the time label: its words
(the programme title, "... at ...") are kept and matched here, so YouTube TV adds no screenshot and no key. Prime Video's
picture is not blanked: one screenshot and one local text read (the score bug) after the tune.

Team words are the score-bug reader's own (`screen.scorebug._tokens`: the abbreviation and the longer words of the
name); a word both teams share names neither. Pure: no device, no clock.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from redzone.screen.scorebug import _match, _tokens

VERIFIED, WRONG, UNKNOWN = "verified", "wrong", "unknown"


def names_both(texts: Iterable[str], home, away) -> bool:
    """Both teams of one game are named somewhere in these words (each by a word only it has)."""
    texts = [str(t) for t in texts if t]
    ht, at = _tokens(home), _tokens(away)
    ht, at = ht - at, at - ht
    return bool(ht and at) and any(_match(t, ht) for t in texts) and any(_match(t, at) for t in texts)


def verdict(texts: Iterable[str] | None, expected: tuple, others: Mapping[str, tuple]) -> tuple[str, str]:
    """(verified | wrong | unknown, the other game's label when wrong). `expected` = (home, away) Teams; `others` =
    {label: (home, away)} for every OTHER live game tonight."""
    texts = [str(t) for t in (texts or []) if t]
    if not texts:
        return UNKNOWN, ""
    exp = names_both(texts, *expected)
    hits = [label for label, (home, away) in others.items() if names_both(texts, home, away)]
    if exp and not hits:
        return VERIFIED, ""
    if hits and not exp:
        return WRONG, hits[0]
    return UNKNOWN, ""
