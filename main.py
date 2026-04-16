"""StrangeUtaGame 应用程序入口。

启动歌词打轴软件的主入口点。
"""

import sys
from pathlib import Path

# 添加 src 到路径
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

# 必须先创建 QApplication，再导入任何 QWidget
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

# 启用 DPI 缩放
QApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
)

# 创建应用实例
app = QApplication(sys.argv)

# 现在可以安全导入其他模块
from strange_uta_game.frontend.main_window import MainWindow


def main():
    """应用入口"""
    # 创建主窗口
    window = MainWindow()
    window.show()

    # 运行应用
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
