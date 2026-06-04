"""StrangeUtaGame 独立更新器（Updater.exe）。

本目录与主程序源码完全解耦：

* 不依赖 PyQt6 / qfluentwidgets；
* 仅依赖标准库 + ``requests`` —— 与主程序完全相同的运行时栈，方便复用
  ``requirements.txt``；
* 控制台输出（``windowed=False`` 打包，弹一个 cmd 窗口给用户看进度）。

打包通过 ``build_updater.py`` 完成，产物 ``dist/Updater/Updater.exe`` 应该被
``build.py`` 复制到主程序产物目录。
"""

__version__ = "1.0.0"
