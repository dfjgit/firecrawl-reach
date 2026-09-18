"""
Human-like scrolling: several wheel ticks with varied deltas and pauses
instead of one instant jump to a target offset.

Port of stealth-kit/src/behavior/scroll.ts. Randomness comes from the
`random` module; pass a seeded `random.Random` instance as `rng` for
reproducible runs.
"""

from __future__ import annotations

import random
import time
from typing import Optional

from playwright.sync_api import Page

_DEFAULT_RNG = random.Random()


def _sleep(ms: float) -> None:
    time.sleep(ms / 1000.0)


def _rand(rng: random.Random, mn: float, mx: float) -> float:
    return mn + rng.random() * (mx - mn)


def human_scroll(
    page: Page,
    distance: Optional[float] = None,
    direction: str = "down",
    max_ticks: int = 8,
    rng: Optional[random.Random] = None,
) -> None:
    """
    - distance: total vertical distance in px. Default: one viewport-ish,
      downward.
    - direction: 'down' | 'up'.
    - max_ticks: split the distance into at most this many ticks.
    """
    if rng is None:
        rng = _DEFAULT_RNG
    sign = -1 if direction == "up" else 1
    remaining = abs(distance if distance is not None else _rand(rng, 400, 900))
    ticks = 0

    while remaining > 0 and ticks < max_ticks:
        # Early ticks are larger; the last ones fine-tune, like a real reader.
        delta = (
            _rand(rng, 0.4, 0.6) * remaining
            if ticks == 0
            else _rand(rng, 0.15, 0.45) * remaining
        )
        page.mouse.wheel(0, sign * max(40, delta))
        remaining -= delta
        ticks += 1
        _sleep(_rand(rng, 120, 450))
