"""调试版本 - 找出 QWidget 实例化位置"""

import sys
from pathlib import Path

# 添加 src 到路径
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

# 在创建 QApplication 之前设置跟踪
print("Step 1: 准备导入 QApplication")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

print("Step 2: QApplication 类已导入")

# 启用 DPI 缩放
QApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
)

print("Step 3: DPI 策略已设置")

# 创建应用实例
app = QApplication(sys.argv)
print("Step 4: QApplication 实例已创建")

# 现在可以安全导入其他模块
print("Step 5: 开始导入 MainWindow...")

try:
    from strange_uta_game.frontend.main_window import MainWindow

    print("Step 6: MainWindow 导入成功")
except Exception as e:
    print(f"Error during import: {e}")
    import traceback

    traceback.print_exc()
    sys.exit(1)

print("Step 7: 创建主窗口...")
window = MainWindow()
print("Step 8: 主窗口创建成功")

window.show()
print("Step 9: 窗口已显示")

sys.exit(app.exec())
