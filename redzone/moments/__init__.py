"""Moments: the per-league tier list and the detectors that fill it from ESPN play-by-play.

Why: one place says what is worth showing in every league (build plan WP-07). Pure and offline: nothing
here touches a stick, the TV or the live loop; flags and wiring belong to the packages that read it.
"""
from redzone.moments.detect import Moment, detect, game_span  # noqa: F401
from redzone.moments.tiers import must_show, tier_of  # noqa: F401
