"""最小化测试 - 逐步实例化 MainWindow"""

import sys
from pathlib import Path

# 添加 src 到路径
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

print("[4] 导入 FluentWindow")
from qfluentwidgets import FluentWindow

print("[5] 导入 Project")
from strange_uta_game.backend.domain import Project

print("[6] 导入 StartupInterface")
from strange_uta_game.frontend.startup.startup_interface import StartupInterface

print("[7] 定义 MainWindow 类")


class MainWindow(FluentWindow):
    def __init__(self):
        print("[7.1] MainWindow.__init__ 开始")
        super().__init__()
        print("[7.2] super().__init__ 完成")
        self.setWindowTitle("StrangeUtaGame - 歌词打轴工具")
        self.resize(1400, 900)
        print("[7.3] 窗口属性设置完成")

        print("[7.4] 准备创建 StartupInterface")
        self.startup_interface = StartupInterface(self)
        print("[7.5] StartupInterface 创建完成")

        self.setCentralWidget(self.startup_interface)
        print("[7.6] setCentralWidget 完成")


print("[8] MainWindow 类定义完成")

print("[9] 准备实例化 MainWindow")
window = MainWindow()
print("[10] MainWindow 实例化成功！")

window.show()
print("[11] 窗口显示")

sys.exit(app.exec())
