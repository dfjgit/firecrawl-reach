"""
Human-like typing: log-normal inter-key delays + occasional typos with
backspace correction.

Uniform delays (page.type's fixed `delay` option) produce a metronome
pattern; real keystroke intervals are heavy-tailed, hence log-normal.

Port of stealth-kit/src/behavior/typing.ts. Randomness comes from the
`random` module; pass a seeded `random.Random` instance as `rng` for
reproducible runs.
"""

from __future__ import annotations

import math
import random
import time
from typing import Dict, List, Optional

from playwright.sync_api import Page

_DEFAULT_RNG = random.Random()


def _sleep(ms: float) -> None:
    time.sleep(ms / 1000.0)


def _rand(rng: random.Random, mn: float, mx: float) -> float:
    return mn + rng.random() * (mx - mn)


def _key_delay(rng: random.Random) -> float:
    """Log-normal sample; mu/sigma tuned for ~60-220ms typical gaps."""
    mu = 4.9
    sigma = 0.45
    z = math.sqrt(-2 * math.log(rng.random())) * math.cos(2 * math.pi * rng.random())
    return min(600, math.exp(mu + sigma * z))


# Neighbor keys on a QWERTY layout, for plausible typos.
NEIGHBORS: Dict[str, List[str]] = {
    k: list(v)
    for k, v in {
        "a": "sqwz", "b": "vghn", "c": "xdfv", "d": "serfcx", "e": "wrsd",
        "f": "drtgvc", "g": "ftyhbv", "h": "gyujnb", "i": "ujko", "j": "huikmn",
        "k": "jiolm", "l": "kop", "m": "njk", "n": "bhjm", "o": "iklp",
        "p": "ol", "q": "wa", "r": "edft", "s": "awedxz", "t": "rfgy",
        "u": "yhji", "v": "cfgb", "w": "qase", "x": "zsdc", "y": "tghu",
        "z": "asx",
    }.items()
}


def human_type(
    page: Page,
    selector: str,
    text: str,
    typo_rate: float = 0.02,
    click_first: bool = True,
    rng: Optional[random.Random] = None,
) -> None:
    """
    - typo_rate: probability of a typo+backspace per character. Default 0.02.
    - click_first: focus the element before typing. Default True.
    """
    if rng is None:
        rng = _DEFAULT_RNG
    if click_first:
        page.locator(selector).first.click()
        _sleep(_rand(rng, 150, 400))

    for ch in text:
        neighbors = NEIGHBORS.get(ch.lower())
        if rng.random() < typo_rate and neighbors:
            wrong = neighbors[int(rng.random() * len(neighbors))]
            page.keyboard.type(wrong)
            _sleep(_key_delay(rng) * 1.5)  # noticing the mistake takes a moment
            page.keyboard.press("Backspace")
            _sleep(_key_delay(rng))
        page.keyboard.type(ch)
        _sleep(_key_delay(rng))
