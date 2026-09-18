from .canvas import make_canvas_init_script
from .navigator_props import make_navigator_props_init_script
from .permissions import make_permissions_init_script
from .plugins import make_plugins_init_script
from .webdriver import make_webdriver_init_script
from .webgl import make_webgl_init_script

__all__ = [
    "make_canvas_init_script",
    "make_navigator_props_init_script",
    "make_permissions_init_script",
    "make_plugins_init_script",
    "make_webdriver_init_script",
    "make_webgl_init_script",
]
