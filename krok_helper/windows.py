from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes


if sys.platform == "win32":
    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32
    shcore = getattr(ctypes.windll, "shcore", None)
    user32.GetDpiForSystem.restype = wintypes.UINT


def enable_high_dpi_awareness() -> None:
    if sys.platform != "win32":
        return

    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass

    if shcore is not None:
        try:
            shcore.SetProcessDpiAwareness(2)
            return
        except Exception:
            pass

    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def set_explicit_app_user_model_id(app_id: str) -> None:
    if sys.platform != "win32":
        return

    try:
        shell32.SetCurrentProcessExplicitAppUserModelID(str(app_id))
    except Exception:
        pass
