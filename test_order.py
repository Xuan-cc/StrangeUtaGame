"""测试不同的导入顺序"""

import sys
from pathlib import Path

src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

print("[1] 先导入 qfluentwidgets（在 QApplication 之前）")
from qfluentwidgets import FluentWindow, setTheme, Theme

print("[2] 导入 QApplication")
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

print("[3] 设置 DPI")
QApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
)

print("[4] 创建 QApplication")
app = QApplication(sys.argv)

print("[5] 设置主题")
setTheme(Theme.LIGHT)

print("[6] 创建 FluentWindow")
window = FluentWindow()
window.setWindowTitle("Test")
window.show()
print("成功！")

sys.exit(app.exec())
