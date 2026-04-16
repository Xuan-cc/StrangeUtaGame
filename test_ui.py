"""测试 UI 是否正常显示"""

import sys
from pathlib import Path

src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

print("Step 1: Importing QApplication...")
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

print("Step 2: Setting DPI...")
QApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
)

print("Step 3: Creating QApplication...")
app = QApplication(sys.argv)

print("Step 4: Testing basic widget creation...")
from PyQt6.QtWidgets import QLabel, QPushButton, QTextEdit

label = QLabel("Test")
button = QPushButton("Test")
text = QTextEdit()
print("Basic widgets OK")

print("Step 5: Testing qfluentwidgets...")
try:
    from qfluentwidgets import PushButton, PrimaryPushButton, InfoBar

    print("qfluentwidgets import OK")

    # Test creating a fluent button
    fluent_btn = PushButton("Test")
    print("Fluent button creation OK")
except Exception as e:
    print(f"qfluentwidgets error: {e}")
    import traceback

    traceback.print_exc()
    sys.exit(1)

print("Step 6: Importing MainWindow...")
from strange_uta_game.frontend.main_window import MainWindow

print("Step 7: Creating MainWindow...")
try:
    window = MainWindow()
    print("MainWindow created OK")
    window.show()
    print("Window shown")

    # Close immediately for test
    window.close()
    print("Test passed!")
except Exception as e:
    print(f"Error: {e}")
    import traceback

    traceback.print_exc()
    sys.exit(1)

sys.exit(0)
