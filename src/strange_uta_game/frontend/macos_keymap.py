"""macOS 虚拟键码 → 按键名称映射。

Qt 的 ``QKeyEvent.key()`` 在 macOS 上对符号按键（``.``, ``,``, ``;`` 等）和
非 QWERTY 键盘布局会返回 ``Qt.Key_unknown`` (0)，导致快捷键系统无法识别这些按键。

本模块提供基于 ``nativeVirtualKey()`` 的 fallback 映射，参考
Carbon HIToolbox ``Events.h`` 中的 ``kVK_*`` 常量定义。
"""

from __future__ import annotations

import sys
from typing import Optional

# macOS 虚拟键码 (kVK_*) → 按键名
# 值必须与 _KeyCaptureButton._build_key_name / timing_interface._qt_key_to_name
# 的输出格式完全一致，其中逗号键使用占位名 "COMMA"。
_MACOS_VK_TO_KEY: dict[int, str] = {
    # ---- 字母键（物理位置，US QWERTY 为准） ----
    0x00: "A",
    0x01: "S",
    0x02: "D",
    0x03: "F",
    0x04: "H",
    0x05: "G",
    0x06: "Z",
    0x07: "X",
    0x08: "C",
    0x09: "V",
    0x0B: "B",
    0x0C: "Q",
    0x0D: "W",
    0x0E: "E",
    0x0F: "R",
    0x10: "Y",
    0x11: "T",
    0x1F: "O",
    0x20: "U",
    0x22: "I",
    0x23: "P",
    0x25: "L",
    0x26: "J",
    0x28: "K",
    0x2D: "N",
    0x2E: "M",

    # ---- 数字键（主键盘区） ----
    0x12: "1",
    0x13: "2",
    0x14: "3",
    0x15: "4",
    0x16: "6",
    0x17: "5",
    0x19: "9",
    0x1A: "7",
    0x1C: "8",
    0x1D: "0",

    # ---- 符号键（必须与 _build_key_name / _qt_key_to_name 一致） ----
    0x18: "=",           # kVK_ANSI_Equal
    0x1B: "-",           # kVK_ANSI_Minus
    0x1E: "]",           # kVK_ANSI_RightBracket
    0x21: "[",           # kVK_ANSI_LeftBracket
    0x27: "'",           # kVK_ANSI_Quote
    0x29: ";",           # kVK_ANSI_Semicolon
    0x2A: "\\",          # kVK_ANSI_Backslash
    0x2B: "COMMA",       # kVK_ANSI_Comma（内部占位名，显示时还原为 ","）
    0x2C: "/",           # kVK_ANSI_Slash
    0x2F: ".",           # kVK_ANSI_Period
    0x32: "`",           # kVK_ANSI_Grave

    # ---- 功能键 ----
    0x7A: "F1",
    0x78: "F2",
    0x63: "F3",
    0x76: "F4",
    0x60: "F5",
    0x61: "F6",
    0x62: "F7",
    0x64: "F8",
    0x65: "F9",
    0x6D: "F10",
    0x67: "F11",
    0x6F: "F12",

    # ---- 方向键 ----
    0x7E: "UP",
    0x7D: "DOWN",
    0x7B: "LEFT",
    0x7C: "RIGHT",

    # ---- 特殊键 ----
    0x24: "ENTER",       # kVK_Return
    0x30: "TAB",         # kVK_Tab
    0x31: "SPACE",       # kVK_Space
    0x33: "DELETE",      # kVK_Delete（macOS 上等效 Backspace）
    0x35: "ESCAPE",      # kVK_Escape
    0x73: "HOME",        # kVK_Home
    0x77: "END",         # kVK_End
    0x74: "PAGEUP",      # kVK_PageUp
    0x79: "PAGEDOWN",    # kVK_PageDown
    0x72: "INSERT",      # kVK_Help（通常映射到 Insert）
    0x75: "DELETE",      # kVK_ForwardDelete（真正的 Delete 键）

    # ---- 小键盘 ----
    0x52: "0",
    0x53: "1",
    0x54: "2",
    0x55: "3",
    0x56: "4",
    0x57: "5",
    0x58: "6",
    0x59: "7",
    0x5B: "8",
    0x5C: "9",
    0x41: ".",           # kVK_ANSI_KeypadDecimal
    0x4B: "/",           # kVK_ANSI_KeypadDivide
    0x43: "*",           # kVK_ANSI_KeypadMultiply
    0x4E: "-",           # kVK_ANSI_KeypadMinus
    0x45: "+",           # kVK_ANSI_KeypadPlus
    0x4C: "ENTER",       # kVK_ANSI_KeypadEnter
    0x51: "=",           # kVK_ANSI_KeypadEquals
}


def macos_vk_to_key_name(
    native_virtual_key: int,
    native_scan_code: int = 0,
) -> Optional[str]:
    """将 macOS 原生虚拟键码转换为快捷键系统使用的按键名称。

    Args:
        native_virtual_key: ``QKeyEvent.nativeVirtualKey()`` 返回值。
        native_scan_code: ``QKeyEvent.nativeScanCode()`` 返回值。
            仅用于在 ``kVK_ANSI_A = 0`` 与无键事件标记值 0 冲突时做区分。

    Returns:
        按键名称字符串（如 ``"SPACE"`` / ``"."`` / ``"COMMA"``），
        无法识别时返回 ``None``。
    """
    if sys.platform != "darwin":
        return None

    # kVK_ANSI_A = 0 与"无键/修饰键"的 sentinel 0 冲突。
    # macOS 上真实的按键按下时 nativeScanCode() 总是 1，
    # 而修饰键或合成事件的 nativeScanCode() 为 0。
    if native_virtual_key == 0:
        if native_scan_code == 1:
            return "A"
        return None

    # 修饰键本身不应该通过 VK 映射（由 Qt 的 modifiers() 处理）。
    # kVK_Shift=0x38, kVK_Control=0x3B, kVK_Option=0x3A, kVK_Command=0x37
    if native_virtual_key in (0x38, 0x3C, 0x3B, 0x3E, 0x3A, 0x3D, 0x37, 0x39, 0x3F):
        return None

    return _MACOS_VK_TO_KEY.get(native_virtual_key)
