"""StrangeUtaGame 应用程序入口。

启动歌词打轴软件的主入口点。
"""

import sys
from pathlib import Path

# 添加 src 到路径
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

from strange_uta_game.frontend.main_window import main

if __name__ == "__main__":
    main()
