"""进程级字体信息缓存与预热 —— 字体枚举的单一真源。

系统字体库可能非常庞大（数百上千族）。此前每次构造控件（``ui_font``）、
解析字体设置（``resolve_font_family``）都会重新枚举
``QFontDatabase.families()`` 并重建映射；字体选择器每次打开还要逐族查询
``isSmoothlyScalable``/``writingSystems``，并解析每个字体文件的 OpenType
``name`` 表（Windows 下首次约 1~2 秒）。字体库大的用户因此卡顿明显。

本模块把这些结果缓存在进程内：

- :func:`installed_families` / :func:`installed_family_map` /
  :func:`system_general_family` —— ``families()`` 快照；
- :func:`font_picker_entries` —— 字体选择器条目（族名/显示名/搜索文本）；
- :func:`prewarm` / :func:`prewarm_async` —— 同步 / 后台预热（standalone
  启动即调用，见 ``main.py``）；
- :func:`invalidate` —— 运行期安装/卸载字体后清空缓存。

**嵌入式宿主**（见 docs/EMBEDDING.md §8）同样可能携带庞大字体库，可复用
本模块：启动流程中调用 ``prewarm``/``prewarm_async``；动态安装字体（含
``QFontDatabase.addApplicationFont``）后调用 ``invalidate``。缓存读写有锁
保护，但 Qt 字体库枚举不是线程安全的，请在主线程触发 Qt 侧调用
（``prewarm_async`` 已按此约定把 Qt 侧预热排回主线程，后台线程只做纯
stdlib 的字体文件扫描）。
"""

from __future__ import annotations

import threading

from PyQt6.QtGui import QFontDatabase

# 写法系统 → 优先本地化语言ID（用于为字体挑选「自己语言」的名字显示）
_WS = QFontDatabase.WritingSystem
_WS_LANG_PREF = [
    (_WS.Japanese, (0x0411,)),
    (_WS.Korean, (0x0412,)),
    (_WS.SimplifiedChinese, (0x0804, 0x1004)),
    (_WS.TraditionalChinese, (0x0404, 0x0C04)),
]

_LOCK = threading.RLock()
_ALIAS_LOCK = threading.Lock()

_snapshot: "_FontSnapshot | None" = None
_picker_entries: tuple[tuple[str, str, str], ...] | None = None
# prewarm_async 是否已启动过后台扫描（避免 standalone + 宿主重复起线程）
_alias_warm_started = False
# 后台扫描完成事件（测试/宿主可等待；再次 prewarm_async 前会被清除）
_alias_warm_done = threading.Event()


class _FontSnapshot:
    """一次 ``QFontDatabase.families()`` 枚举的完整快照。"""

    __slots__ = ("families", "family_set", "family_map", "system_general")

    def __init__(self) -> None:
        families = tuple(QFontDatabase.families())
        self.families = families
        self.family_set = frozenset(families)
        self.family_map = {name.casefold(): name for name in families}
        self.system_general = QFontDatabase.systemFont(
            QFontDatabase.SystemFont.GeneralFont
        ).family()


def _get_snapshot() -> _FontSnapshot:
    global _snapshot
    with _LOCK:
        if _snapshot is None:
            _snapshot = _FontSnapshot()
        return _snapshot


def installed_families() -> tuple[str, ...]:
    """已安装字体族（含 Qt 应用字体），进程级缓存。"""
    return _get_snapshot().families


def installed_family_map() -> dict[str, str]:
    """``{casefold 名: 规范族名}`` 映射，进程级缓存。"""
    return _get_snapshot().family_map


def has_installed_family(family: str) -> bool:
    """精确匹配（大小写敏感）已安装族名，语义同 ``family in families()``。"""
    return family in _get_snapshot().family_set


def system_general_family() -> str:
    """系统通用字体族名，随快照缓存。"""
    return _get_snapshot().system_general


def _alias_map() -> dict[str, dict[int, str]]:
    try:
        from strange_uta_game.frontend.font_names import localized_alias_map

        return localized_alias_map()
    except Exception:
        return {}


def _has_cjk(text: str) -> bool:
    return any(ord(c) > 0x7F for c in text)


def preferred_native(family: str, natives: dict[int, str] | None = None) -> str:
    """按字体支持的书写系统挑选其「母语」名；否则取任一非 ASCII 名，无则空串。"""
    if natives is None:
        natives = _alias_map().get(family, {})
    if not natives:
        return ""
    try:
        ws = set(QFontDatabase.writingSystems(family))
    except Exception:
        ws = set()
    for system, langs in _WS_LANG_PREF:
        if system in ws:
            for lid in langs:
                if lid in natives:
                    return natives[lid]
    for name in natives.values():
        if _has_cjk(name):
            return name
    return ""


def font_display_label(family: str) -> str:
    """字体的友好显示名：有本地化名时为「本地名 (英文族名)」，否则为英文族名。"""
    if not family:
        return ""
    native = preferred_native(family)
    return f"{native}  ({family})" if native and native != family else family


def font_picker_entries() -> list[tuple[str, str, str]]:
    """字体选择器条目 ``[(qt_family, 显示名, 搜索文本)]``，进程级缓存。

    仅含可平滑缩放字体（排除 Terminal/Fixedsys 等位图字体——DirectWrite
    无法加载会刷 ``CreateFontFaceFromHDC() failed`` 报错）；显示名与搜索
    文本附字体的本地化名称（解析来自 :mod:`...frontend.font_names`）。
    返回列表是缓存的副本，调用方可自由持有/过滤。
    """
    global _picker_entries
    with _LOCK:
        if _picker_entries is None:
            _picker_entries = tuple(_build_picker_entries())
        return list(_picker_entries)


def _build_picker_entries():
    alias_map = _alias_map()
    for fam in installed_families():
        if not QFontDatabase.isSmoothlyScalable(fam):
            continue
        natives = alias_map.get(fam, {})
        native = preferred_native(fam, natives)
        display = f"{native}  ({fam})" if native and native != fam else fam
        # 搜索文本含 Qt 族名 + 所有本地化名
        search = " ".join([fam, *natives.values()]).lower()
        yield (fam, display, search)


def prewarm(include_picker_entries: bool = True) -> None:
    """同步预热字体缓存；Qt 枚举部分应在主线程调用。

    ``include_picker_entries=False`` 时只预热族名快照（字体选择器条目延后
    到首次打开时构建），适合只想消除控件构造开销的调用方。
    """
    _get_snapshot()
    if include_picker_entries:
        font_picker_entries()


def _warm_alias_map_once() -> None:
    try:
        _alias_map()
    except Exception:
        pass
    finally:
        _alias_warm_done.set()


def prewarm_async(qt_delay_ms: int = 1500, include_picker_entries: bool = True) -> bool:
    """后台预热字体缓存，不阻塞启动 / 宿主首帧。

    - 字体文件本地化名扫描（纯 stdlib，线程安全）放进后台线程；
    - Qt 侧枚举（族名快照 + 可选的选择器条目）通过 ``QTimer.singleShot``
      排回主线程，在启动后 ``qt_delay_ms`` 毫秒执行（默认 1.5 秒，此时
      首帧已渲染）。

    返回是否**新启动**了后台扫描线程；重复调用幂等（Qt 侧预热始终安排，
    已有的缓存不会重建）。当前线程无 QApplication 时跳过 Qt 侧，只做
    后台扫描。
    """
    global _alias_warm_started
    with _ALIAS_LOCK:
        started = not _alias_warm_started
        if started:
            _alias_warm_started = True
            _alias_warm_done.clear()
            threading.Thread(
                target=_warm_alias_map_once,
                name="sug-font-prewarm",
                daemon=True,
            ).start()

    try:
        from PyQt6.QtWidgets import QApplication
    except Exception:
        return started
    if QApplication.instance() is None:
        return started

    from PyQt6.QtCore import QTimer

    def _warm_qt_side() -> None:
        try:
            prewarm(include_picker_entries=include_picker_entries)
        except Exception:
            pass

    QTimer.singleShot(max(0, int(qt_delay_ms)), _warm_qt_side)
    return started


def invalidate(clear_alias_map: bool = True) -> None:
    """清空字体缓存；运行期字体库变化后调用。

    适用场景：应用运行中安装/卸载系统字体，或宿主调用
    ``QFontDatabase.addApplicationFont`` 注册新字体之后。清空后下一次访问
    会重新枚举。``clear_alias_map=False`` 保留本地化字体名的磁盘扫描结果
    （该扫描昂贵，Windows 下约 1~2 秒；确认字体文件未变化时可用）。
    """
    global _snapshot, _picker_entries, _alias_warm_started
    with _LOCK:
        _snapshot = None
        _picker_entries = None
    if clear_alias_map:
        with _ALIAS_LOCK:
            _alias_warm_started = False
        try:
            from strange_uta_game.frontend.font_names import localized_alias_map

            localized_alias_map.cache_clear()
        except Exception:
            pass
