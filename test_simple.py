"""测试简化版 main_window"""

import sys
from pathlib import Path

src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

print("[1] 导入 QApplication")
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

print("[2] 设置 DPI")
QApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
)

print("[3] 创建 QApplication")
app = QApplication(sys.argv)

print("[4] 导入简化版 MainWindow")
from strange_uta_game.frontend.main_window_simple import MainWindow

print("[5] 创建 MainWindow")
window = MainWindow()

print("[6] 显示窗口")
window.show()

print("[7] 运行应用")
sys.exit(app.exec())
