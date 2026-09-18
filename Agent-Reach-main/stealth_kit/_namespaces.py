"""
The `stealth` / `human` namespaces, matching the TS exports.

Split out of __init__.py so the package can lazy-load it: nothing here is
imported until a browser-facing entry point is actually used, which keeps
`import stealth_kit` working without playwright installed.
"""

from .behavior.mouse import human_click, move_mouse
from .behavior.scroll import human_scroll
from .behavior.typing import human_type
from .core.context_factory import new_context
from .core.launcher import launch, session


class _Stealth:
    """Namespace matching the TS `stealth` export."""

    launch = staticmethod(launch)
    new_context = staticmethod(new_context)
    session = staticmethod(session)


class _Human:
    """Namespace matching the TS `human` export."""

    click = staticmethod(human_click)
    type = staticmethod(human_type)
    scroll = staticmethod(human_scroll)
    move_mouse = staticmethod(move_mouse)


stealth = _Stealth()
human = _Human()

__all__ = ["human", "stealth"]
