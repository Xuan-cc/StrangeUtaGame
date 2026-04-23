"""设置卡片自定义组件。

基于 qfluentwidgets 的 ``SettingCard`` 扩展出的卡片类型，供 ``SettingsInterface`` 使用。
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QKeyEvent
from PyQt6.QtWidgets import QFileDialog, QLabel
from qfluentwidgets import (
    ComboBox,
    DoubleSpinBox,
    LineEdit,
    PushButton,
    SettingCard,
    SpinBox,
    SwitchButton,
)


class SpinSettingCard(SettingCard):
    """数值设定卡片 — 整数 SpinBox"""

    value_changed = pyqtSignal(int)

    def __init__(
        self,
        icon,
        title: str,
        content: str,
        min_val: int = 0,
        max_val: int = 100,
        step: int = 1,
        suffix: str = "",
        parent=None,
    ):
        super().__init__(icon, title, content, parent)
        self.spin = SpinBox(self)
        self.spin.setRange(min_val, max_val)
        self.spin.setSingleStep(step)
        if suffix:
            self.spin.setSuffix(suffix)
        self.spin.setFixedWidth(180)
        self.spin.valueChanged.connect(self.value_changed.emit)
        self.hBoxLayout.addWidget(self.spin, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def setValue(self, value: int):
        self.spin.setValue(value)

    def value(self) -> int:
        return self.spin.value()


class DoubleSpinSettingCard(SettingCard):
    """数值设定卡片 — 浮点 DoubleSpinBox"""

    value_changed = pyqtSignal(float)

    def __init__(
        self,
        icon,
        title: str,
        content: str,
        min_val: float = 0.0,
        max_val: float = 100.0,
        step: float = 0.1,
        decimals: int = 1,
        suffix: str = "",
        parent=None,
    ):
        super().__init__(icon, title, content, parent)
        self.spin = DoubleSpinBox(self)
        self.spin.setRange(min_val, max_val)
        self.spin.setSingleStep(step)
        self.spin.setDecimals(decimals)
        if suffix:
            self.spin.setSuffix(suffix)
        self.spin.setFixedWidth(180)
        self.spin.valueChanged.connect(self.value_changed.emit)
        self.hBoxLayout.addWidget(self.spin, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def setValue(self, value: float):
        self.spin.setValue(value)

    def value(self) -> float:
        return self.spin.value()


class SwitchSettingCard(SettingCard):
    """开关设定卡片"""

    checked_changed = pyqtSignal(bool)

    def __init__(self, icon, title: str, content: str, parent=None):
        super().__init__(icon, title, content, parent)
        self.switch = SwitchButton(self)
        self.switch.checkedChanged.connect(self.checked_changed.emit)
        self.hBoxLayout.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def setChecked(self, checked: bool):
        self.switch.setChecked(checked)

    def isChecked(self) -> bool:
        return self.switch.isChecked()


class ComboSettingCard(SettingCard):
    """下拉选择设定卡片"""

    index_changed = pyqtSignal(int)

    def __init__(
        self,
        icon,
        title: str,
        content: str,
        items: list,
        parent=None,
    ):
        super().__init__(icon, title, content, parent)
        self.combo = ComboBox(self)
        self.combo.addItems(items)
        self.combo.setFixedWidth(140)
        self.combo.currentIndexChanged.connect(self.index_changed.emit)
        self.hBoxLayout.addWidget(self.combo, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def setCurrentIndex(self, idx: int):
        self.combo.setCurrentIndex(idx)

    def currentIndex(self) -> int:
        return self.combo.currentIndex()


class BrowseSettingCard(SettingCard):
    """目录浏览设定卡片"""

    path_changed = pyqtSignal(str)

    def __init__(self, icon, title: str, content: str, parent=None):
        super().__init__(icon, title, content, parent)
        self.line = LineEdit(self)
        self.line.setPlaceholderText("点击选择...")
        self.line.setReadOnly(True)
        self.line.setFixedWidth(200)
        self.btn = PushButton("浏览", self)
        self.btn.setFixedWidth(60)
        self.btn.clicked.connect(self._on_browse)
        self.hBoxLayout.addWidget(self.line, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addWidget(self.btn, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def _on_browse(self):
        dir_path = QFileDialog.getExistingDirectory(self, "选择目录", "")
        if dir_path:
            self.line.setText(dir_path)
            self.path_changed.emit(dir_path)

    def setText(self, text: str):
        self.line.setText(text)

    def text(self) -> str:
        return self.line.text()


class _KeyCaptureButton(PushButton):
    """按键捕获按钮 — 点击后进入监听模式，捕获下一次按键组合。"""

    key_captured = pyqtSignal(str)  # 捕获到的按键名称
    key_restored = pyqtSignal()     # 因冲突或其他原因恢复原值时触发

    def _postInit(self):
        super()._postInit()
        self._captured_key = ""
        self._original_key = ""
        self._listening = False
        self.setFixedWidth(120)
        self.setFont(QFont("Microsoft YaHei", 9))
        self.clicked.connect(self._start_listening)

    def _start_listening(self):
        self._listening = True
        self._original_key = self._captured_key
        self.setText("按下按键...")
        self.setStyleSheet("border: 2px solid #0078D4; border-radius: 4px;")
        self.setFocus()

    def restore_original_key(self):
        """恢复修改前的按键（用于冲突处理）。"""
        self._captured_key = self._original_key
        self._update_display()
        self.key_restored.emit()

    def keyPressEvent(self, a0: QKeyEvent | None):
        if a0 is None or not self._listening:
            super().keyPressEvent(a0)
            return
        key = a0.key()
        # 忽略单独的修饰键
        if key in (
            Qt.Key.Key_Control,
            Qt.Key.Key_Shift,
            Qt.Key.Key_Alt,
            Qt.Key.Key_Meta,
        ):
            a0.accept()
            return
        # ESC 取消捕获，不设置按键
        if key == Qt.Key.Key_Escape:
            self._listening = False
            self._update_display()
            self.setStyleSheet("")
            self.clearFocus()
            a0.accept()
            return
        modifiers = a0.modifiers()
        key_name = _KeyCaptureButton._build_key_name(key, modifiers)
        if key_name:
            self._captured_key = key_name
            self._listening = False
            self._update_display()
            self.setStyleSheet("")
            self.key_captured.emit(key_name)
            self.clearFocus()
        a0.accept()

    def focusOutEvent(self, a0):
        if self._listening:
            self._listening = False
            self._update_display()
            self.setStyleSheet("")
        super().focusOutEvent(a0)

    def _update_display(self):
        self.setText(self._captured_key if self._captured_key else "未设置")

    def set_key(self, key_name: str):
        self._captured_key = key_name
        self._original_key = key_name
        self._update_display()

    def get_key(self) -> str:
        return self._captured_key

    def clear_key(self):
        self._captured_key = ""
        self._update_display()

    @staticmethod
    def _build_key_name(key, modifiers) -> Optional[str]:
        """将 Qt key + modifiers 转换为规范化字符串，如 'CTRL+F4'、'SPACE'。"""
        parts = []
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            parts.append("CTRL")
        if modifiers & Qt.KeyboardModifier.AltModifier:
            parts.append("ALT")
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            parts.append("SHIFT")

        _key_names = {
            Qt.Key.Key_Space: "SPACE",
            Qt.Key.Key_Escape: "ESCAPE",
            Qt.Key.Key_F1: "F1",
            Qt.Key.Key_F2: "F2",
            Qt.Key.Key_F3: "F3",
            Qt.Key.Key_F4: "F4",
            Qt.Key.Key_F5: "F5",
            Qt.Key.Key_F6: "F6",
            Qt.Key.Key_F7: "F7",
            Qt.Key.Key_F8: "F8",
            Qt.Key.Key_F9: "F9",
            Qt.Key.Key_F10: "F10",
            Qt.Key.Key_F11: "F11",
            Qt.Key.Key_F12: "F12",
            Qt.Key.Key_Up: "UP",
            Qt.Key.Key_Down: "DOWN",
            Qt.Key.Key_Left: "LEFT",
            Qt.Key.Key_Right: "RIGHT",
            Qt.Key.Key_Return: "ENTER",
            Qt.Key.Key_Enter: "ENTER",
            Qt.Key.Key_Tab: "TAB",
            Qt.Key.Key_Backspace: "BACKSPACE",
            Qt.Key.Key_Delete: "DELETE",
            Qt.Key.Key_Home: "HOME",
            Qt.Key.Key_End: "END",
            Qt.Key.Key_PageUp: "PAGEUP",
            Qt.Key.Key_PageDown: "PAGEDOWN",
            Qt.Key.Key_Insert: "INSERT",
            # 标点键（#11 修复：支持字面量键名）
            Qt.Key.Key_Comma: ",",
            Qt.Key.Key_Period: ".",
            Qt.Key.Key_Slash: "/",
            Qt.Key.Key_Semicolon: ";",
            Qt.Key.Key_Apostrophe: "'",
            Qt.Key.Key_BracketLeft: "[",
            Qt.Key.Key_BracketRight: "]",
            Qt.Key.Key_Backslash: "\\",
            Qt.Key.Key_Minus: "-",
            Qt.Key.Key_Equal: "=",
            Qt.Key.Key_QuoteLeft: "`",
        }
        if key in _key_names:
            parts.append(_key_names[key])
        elif Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            parts.append(chr(key))
        elif Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            parts.append(chr(key))
        else:
            return None
        return "+".join(parts)


class ShortcutSettingCard(SettingCard):
    """快捷键设定卡片 — 支持键盘监听捕获和双快捷键绑定。"""

    value_changed = pyqtSignal(str)

    def __init__(
        self, icon, title: str, content: str, default_key: str = "", parent=None
    ):
        super().__init__(icon, title, content, parent)
        # 解析默认值（可能是 "Space,A" 这样的双键位格式）
        keys = (
            [k.strip() for k in default_key.split(",") if k.strip()]
            if default_key
            else []
        )
        key1 = keys[0] if len(keys) >= 1 else default_key
        key2 = keys[1] if len(keys) >= 2 else ""

        self.btn_key1 = _KeyCaptureButton("点击设置", self)
        self.btn_key1.set_key(key1)
        self.btn_key2 = _KeyCaptureButton("点击设置", self)
        self.btn_key2.set_key(key2)

        lbl_or = QLabel("或", self)
        lbl_or.setFont(QFont("Microsoft YaHei", 9))

        self.btn_key1.key_captured.connect(lambda k: self._on_key_changed(self.btn_key1, k))
        self.btn_key2.key_captured.connect(lambda k: self._on_key_changed(self.btn_key2, k))

        self.hBoxLayout.addWidget(self.btn_key1, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addWidget(lbl_or, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addWidget(self.btn_key2, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def _on_key_changed(self, btn: _KeyCaptureButton, key_name: str):
        self.value_changed.emit(self.value())

    def restore_key(self, key_name: str):
        """将指定按钮恢复为原值（针对 #2）。"""
        if self.btn_key1.get_key().strip().upper() == key_name.upper():
            self.btn_key1.restore_original_key()
        if self.btn_key2.get_key().strip().upper() == key_name.upper():
            self.btn_key2.restore_original_key()

    def setValue(self, value: str):
        """设置快捷键值，支持 'Space' 或 'Space,A' 格式。"""
        keys = [k.strip() for k in value.split(",") if k.strip()] if value else []
        self.btn_key1.set_key(keys[0] if len(keys) >= 1 else "")
        self.btn_key2.set_key(keys[1] if len(keys) >= 2 else "")

    def value(self) -> str:
        """返回快捷键值，格式为 'Space' 或 'Space,A'。"""
        k1 = self.btn_key1.get_key().strip()
        k2 = self.btn_key2.get_key().strip()
        if k1 and k2:
            return f"{k1},{k2}"
        return k1 or k2

    def all_keys(self) -> list[str]:
        """返回所有已设置的快捷键列表。"""
        keys = []
        k1 = self.btn_key1.get_key().strip()
        k2 = self.btn_key2.get_key().strip()
        if k1:
            keys.append(k1.upper())
        if k2:
            keys.append(k2.upper())
        return keys

    def clear_key_by_name(self, key_name: str):
        """清除指定的快捷键（用于冲突解决）。"""
        if self.btn_key1.get_key().strip().upper() == key_name.upper():
            self.btn_key1.clear_key()
        if self.btn_key2.get_key().strip().upper() == key_name.upper():
            self.btn_key2.clear_key()
