"""测试 FluentWindow 初始化"""

import sys
from pathlib import Path

src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

print("[1] 导入 QApplication")
from PyQt6.QtWidgets import QApplication, QMainWindow
from PyQt6.QtCore import Qt

print("[2] 设置 DPI")
QApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
)

print("[3] 创建 QApplication")
app = QApplication(sys.argv)

print("[4] 测试普通 QMainWindow")
window1 = QMainWindow()
window1.setWindowTitle("Test 1")
window1.show()
print("QMainWindow 成功！")

print("[5] 导入 FluentWindow")
from qfluentwidgets import FluentWindow

print("[6] 测试 FluentWindow")
try:
    window2 = FluentWindow()
    window2.setWindowTitle("Test 2")
    window2.show()
    print("FluentWindow 成功！")
except Exception as e:
    print(f"FluentWindow 失败: {e}")
    import traceback

    traceback.print_exc()

sys.exit(app.exec())
