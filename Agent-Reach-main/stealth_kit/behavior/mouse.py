"""
Human-like mouse movement: cubic Bézier path + sub-pixel jitter.

page.mouse.click teleports the cursor — a synthetic event pattern trivial
to spot in event-stream analysis. Sensitive interactions should go through
human_click/hover instead.

Port of stealth-kit/src/behavior/mouse.ts. Randomness comes from the
`random` module; pass a seeded `random.Random` instance as `rng` for
reproducible runs.
"""

from __future__ import annotations

import math
import random
import time
import weakref
from typing import Dict, Literal, Optional

from playwright.sync_api import Page

# Last known cursor position per page; Playwright doesn't expose it.
_last_position: "weakref.WeakKeyDictionary[Page, Dict[str, float]]" = (
    weakref.WeakKeyDictionary()
)
_DEFAULT_RNG = random.Random()


def _rand(rng: random.Random, mn: float, mx: float) -> float:
    return mn + rng.random() * (mx - mn)


def _sleep(ms: float) -> None:
    time.sleep(ms / 1000.0)


def _bezier_point(
    t: float,
    p0: Dict[str, float],
    p1: Dict[str, float],
    p2: Dict[str, float],
    p3: Dict[str, float],
) -> Dict[str, float]:
    u = 1 - t
    return {
        "x": u**3 * p0["x"] + 3 * u**2 * t * p1["x"] + 3 * u * t**2 * p2["x"] + t**3 * p3["x"],
        "y": u**3 * p0["y"] + 3 * u**2 * t * p1["y"] + 3 * u * t**2 * p2["y"] + t**3 * p3["y"],
    }


def move_mouse(
    page: Page,
    x: float,
    y: float,
    steps: Optional[int] = None,
    rng: Optional[random.Random] = None,
) -> None:
    """Moves the cursor along a curved path to (x, y)."""
    if rng is None:
        rng = _DEFAULT_RNG
    from_ = _last_position.get(page) or {
        "x": _rand(rng, 0, 400),
        "y": _rand(rng, 0, 300),
    }
    distance = math.hypot(x - from_["x"], y - from_["y"])
    if steps is None:
        steps = max(10, min(60, round(distance / 15)))

    # Control points scattered around the straight line give a natural arc.
    spread = distance / 4
    c1 = {
        "x": from_["x"] + (x - from_["x"]) * _rand(rng, 0.2, 0.4) + _rand(rng, -spread, spread),
        "y": from_["y"] + (y - from_["y"]) * _rand(rng, 0.2, 0.4) + _rand(rng, -spread, spread),
    }
    c2 = {
        "x": from_["x"] + (x - from_["x"]) * _rand(rng, 0.6, 0.8) + _rand(rng, -spread, spread),
        "y": from_["y"] + (y - from_["y"]) * _rand(rng, 0.6, 0.8) + _rand(rng, -spread, spread),
    }

    for i in range(1, steps + 1):
        # Ease-in-out: humans accelerate then decelerate into the target.
        t = i / steps
        eased = t * t * (3 - 2 * t)
        p = _bezier_point(eased, from_, c1, c2, {"x": x, "y": y})
        page.mouse.move(p["x"] + _rand(rng, -0.8, 0.8), p["y"] + _rand(rng, -0.8, 0.8))
        _sleep(_rand(rng, 2, 12))
    _last_position[page] = {"x": x, "y": y}


def human_click(
    page: Page,
    selector: str,
    button: Literal["left", "middle", "right"] = "left",
    timeout: Optional[float] = None,
    rng: Optional[random.Random] = None,
) -> None:
    """
    Hovers to a human-plausible point inside the element, then clicks with
    realistic press timing.
    """
    if rng is None:
        rng = _DEFAULT_RNG
    locator = page.locator(selector).first
    locator.wait_for(state="visible", timeout=timeout)
    box = locator.bounding_box()
    if box is None:
        raise RuntimeError(f'human_click: no bounding box for "{selector}"')

    # Aim near the center with a bell-ish offset — never the exact geometric
    # center every time (a classic bot tell).
    target = {
        "x": box["x"] + box["width"] * (0.5 + _rand(rng, -0.2, 0.2)),
        "y": box["y"] + box["height"] * (0.5 + _rand(rng, -0.2, 0.2)),
    }
    move_mouse(page, target["x"], target["y"], rng=rng)
    _sleep(_rand(rng, 80, 250))  # dwell on the hover before committing
    page.mouse.down(button=button)
    _sleep(_rand(rng, 45, 120))  # human press duration
    page.mouse.up(button=button)
