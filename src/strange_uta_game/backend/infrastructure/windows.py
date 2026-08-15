# -*- coding: utf-8 -*-
"""Windows 平台子进程工具（口径与工作台 krok_helper/windows.py 一致）。"""

from __future__ import annotations

import subprocess
import sys


def hidden_subprocess_kwargs() -> dict:
    """返回可并入 ``subprocess.run/Popen`` 的防黑框参数。

    GUI（含打包 windowed 应用）里起控制台程序若不加控制，每次调用
    都会闪一个黑色控制台窗口。``CREATE_NO_WINDOW`` 为主防线；
    隐藏 ``STARTUPINFO`` 作为纵深防御（部分启动器不完全尊重创建标志）。
    非 Windows 平台返回空 dict。
    """
    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        "startupinfo": startupinfo,
    }


__all__ = ["hidden_subprocess_kwargs"]
