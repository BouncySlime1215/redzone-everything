"""Typed interface for a "package pick" label: Nick orders several catch-up clips by which he wants shown first.

Why: WORKPLAN v2 D-01 (package-pick module) is shared with E-03 (learned clip ranker uses package-pick labels). This is
the interface only: no page, no fit. A label converts to the same sequential choice events the Bradley-Terry fits use
(first choice among all clips, second among the rest, ...), so a later fit needs no new likelihood.

    clip = PackageClip(clip_id="c1", game_id="401", league="nba", age_s=40.0, features={"state": "just_after_big_play"})
    label = PackagePickLabel(label_id="p1", at="2026-11-13T20:00:00Z", source="picker", clips=(clip, ...), order=(1, 0))
    label.validate(); label.first_clip(); label.choice_sets()

Nothing in the show imports this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field

PACKAGE_PICK_SOURCES = ("picker", "show")


@dataclass(frozen=True)
class PackageClip:
    """One clip offered in a package: what game it is from, how old it is, and the moment features it carries
    (in the Game Picker's card vocabulary: season_type, phase, margin_bucket, state, storyline, ...)."""
    clip_id: str
    game_id: str
    league: str
    age_s: float | None = None
    play_ids: tuple[str, ...] = ()
    features: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PackagePickLabel:
    """Nick's order over the clips of one package. `order` lists clip positions, first = show first; it may be a prefix
    (he only said which comes first). `propensities` (optional, one per clip) are the probabilities the logging policy
    had of showing each clip first, for off-policy use (track D experiment D2)."""
    label_id: str
    at: str
    source: str
    clips: tuple[PackageClip, ...]
    order: tuple[int, ...]
    propensities: tuple[float, ...] | None = None

    def validate(self) -> None:
        """Raise ValueError when the label cannot be used."""
        if self.source not in PACKAGE_PICK_SOURCES:
            raise ValueError(f"source must be one of {PACKAGE_PICK_SOURCES}")
        if len(self.clips) < 2:
            raise ValueError("a package needs at least 2 clips")
        if len({c.clip_id for c in self.clips}) != len(self.clips):
            raise ValueError("clip ids repeat")
        if not self.order or len(set(self.order)) != len(self.order) \
                or any(not 0 <= i < len(self.clips) for i in self.order):
            raise ValueError("order must list distinct clip positions")
        if self.propensities is not None:
            if len(self.propensities) != len(self.clips) or any(not 0.0 <= p <= 1.0 for p in self.propensities):
                raise ValueError("propensities need one probability per clip")
            if abs(sum(self.propensities) - 1.0) > 1e-6:
                raise ValueError("propensities must sum to 1")

    def first_clip(self) -> PackageClip:
        """The clip he wants shown first."""
        return self.clips[self.order[0]]

    def choice_sets(self) -> list[tuple[list[PackageClip], int]]:
        """Sequential choices (offered clips, index of the one taken): the first among all, the second among the rest,
        ...; a full order gives len(clips) - 1 choices, a prefix gives one per stated position."""
        self.validate()
        rest = list(range(len(self.clips)))
        out = []
        for k in self.order:
            if len(rest) < 2:
                break
            out.append(([self.clips[i] for i in rest], rest.index(k)))
            rest.remove(k)
        return out
