"""StrangeUtaGame 在线自动更新模块。

设计原则（与 March7thAssistant 看齐）：

* **独立 Updater.exe** 接管文件替换 —— Windows 上正在运行的进程会锁住自身 EXE
  与 ``_internal``，主程序无法在不退出的前提下替换。
* **不触碰用户数据** —— 仅替换主程序所在目录下的 ``StrangeUtaGame.exe`` 与
  ``_internal/`` 内的内容。``config.json`` / ``dictionary.json`` /
  ``network_dictionary.json`` / ``singers.json`` 以及任何
  ``.config_redirect`` 指向的目录保持原样。
* **多源接力** —— 默认顺序 ``github → ghproxy → gh-proxy → ghproxy-net``，
  可由用户在设置中拖动排序；前一源失败后自动降级到下一源。
* **代理友好** —— 主动检测 Windows 系统代理；亦可扫描常见本地代理端口；用户
  也可手动指定 ``http://127.0.0.1:port``。
* **失败不影响主功能** —— 检查更新出错只记录到日志（不弹窗、不阻塞启动）。

模块组织：

==============  ==========================================================
模块             职责
==============  ==========================================================
``version``     语义化版本号比较、tag 解析
``sources``     三源 URL 模板及命名约定
``proxy``       系统代理读取 + 常用端口扫描
``http_client`` 集成代理、按源接力的 HTTP 客户端（基于 ``requests``）
``manifest``    GitHub Release API 抽象（取 latest release）
``settings``    updater 自身配置读写（复用 ``AppSettings``）
``installer``   调起独立 ``Updater.exe`` 并完成主程序退出
``worker``      QThread 异步检查工作器（不阻塞 UI）
``ui``          PyQt6 / qfluentwidgets 风格的设置卡片与对话框
==============  ==========================================================

公共入口：

* :func:`check_for_updates_async`  ——  从 ``MainWindow`` 启动时调起
* :func:`check_for_updates_blocking` —— 同步检查（供 ``Updater.exe`` 复用）
"""

from __future__ import annotations

from .manifest import LatestRelease, fetch_latest_release
from .settings import UpdaterSettings
from .sources import SOURCE_IDS, SourceId, build_release_urls
from .version import is_newer_version, parse_version

__all__ = [
    "LatestRelease",
    "SOURCE_IDS",
    "SourceId",
    "UpdaterSettings",
    "build_release_urls",
    "fetch_latest_release",
    "is_newer_version",
    "parse_version",
]
