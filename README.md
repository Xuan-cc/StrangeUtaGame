# StrangeUtaGame

一款由 RhythmicaLyrics 启发的歌词打轴软件，专注于低延迟、高精度的卡拉OK时间标签制作。

## 功能概览

- **精准打轴** — 类似节奏游戏的打轴体验，空格键 + 功能键一气呵成
- **变速播放** — 50%~200% 速度调节，WSOLA 流式变速不变调，实时拖动无爆音
- **日语注音** — 基于 SudachiPy 的上下文感知自动注音，内置 1757 条常用词典
- **多格式支持** — 导入/导出 LRC、ASS、SRT、TXT、KRA、Nicokara 等格式
- **多演唱者** — 逐字级别演唱者指定，颜色区分，拖拽划词选择
- **卡拉OK预览** — 实时走字高亮，Ruby 注音同步，连词平滑渲染
- **全文本编辑** — 自由修改歌词文本，智能保留时间标签和注音数据
- **Offset 校准** — 可视化节拍器校准窗口，精确补偿延迟
- **定时自动保存** — 可配置周期保存，支持闪退恢复
- **拖拽加载** — 音频/歌词/项目文件直接拖入窗口

## 快速开始

### 方式一：直接运行（推荐）

从 [Releases](https://github.com/Xuan-cc/StrangeUtaGame/releases) 页面下载最新版本的压缩包，解压后运行 `StrangeUtaGame.exe` 即可。

### 方式二：从源码运行

**环境要求**：Python 3.11+、音频输出设备

```bash
# 安装依赖
pip install -r requirements.txt

# 启动应用
python main.py
```

### 方式三：自行打包

```bash
pip install pyinstaller
python build.py
```

打包完成后可在 `dist/StrangeUtaGame/` 目录找到可执行文件。

## 使用流程

### 1. 创建项目

启动后在主页：
1. 粘贴歌词或导入歌词文件（LRC / ASS / SRT / TXT / KRA / Nicokara）
2. 选择音频文件（MP3 / WAV / FLAC / OGG）
3. 点击「创建项目」

### 2. 打轴

| 按键 | 功能 |
|------|------|
| `Space` | 打轴（打轴模式）/ 增加节奏点（编辑模式） |
| `A` | 播放 / 暂停 |
| `S` | 停止 |
| `Z` / `X` | 后退 / 前进 5 秒（仅打轴模式） |
| `Q` / `W` | 减速 / 加速 (±10%) |
| `↑` / `↓` | 上一行 / 下一行 |
| `←` / `→` | 上一字符 / 下一字符（行首/末自动跨行） |
| `Backspace` | 减少节奏点（编辑模式） |
| `F2` | 编辑注音 |
| `F3` | 连词 / 取消连词 |
| `F4` | 切换句尾（打轴模式） |
| `F5` / `F6` | 增加 / 减少节奏点（打轴模式） |
| `.` | 切换句尾（编辑模式） |
| `Alt+←` / `Alt+→` | 当前字符内节奏点循环切换（上一个 / 下一个） |
| `Alt+↑` / `Alt+↓` | 当前时间戳 ± 步长 |
| `Ctrl+Z` / `Ctrl+Y` | 撤销 / 重做 |
| `Ctrl+S` | 保存项目 |

### 3. 注音与编辑

- **自动注音**：在全文本编辑界面点击「自动分析全部注音」
- **手动调整**：F2 键编辑单字注音，全文本编辑支持 `{漢字|かんじ}` 连词格式
- **按类型删除**：可选择性移除平假名、片假名、英文等不需要的注音

### 4. 保存与导出

- `Ctrl+S` 保存项目（.sug 格式）
- 侧边栏「导出」→ 选择格式 → 设置文件名 → 导出

**支持导出格式**：LRC（增强型/逐行/逐字）、KRA、SRT、TXT、txt2ass、ASS、Nicokara

### 5. 演唱者管理

- 在打轴预览中拖拽选中文字 → 右键 → 选择演唱者
- 支持逐字级别的演唱者指定
- 演唱者预设跨项目保持

## 项目文件

| 格式 | 说明 |
|------|------|
| `.sug` | StrangeUtaGame 项目文件（JSON 格式） |
| `.sug.temp` | 自动保存临时文件（正常退出时自动清理） |

项目文件不保存音频路径，每次使用时需重新选择音频文件。

## 常见问题

**Q: 打轴时延迟很大怎么办？**
在设置 → Offset 校准中使用可视化校准工具调整偏移量。

**Q: 变速后声音有杂音？**
调速瞬间由 audiotsm WSOLA 状态机平滑衔接，立体声相位一致，正常情况下不会有爆音。如遇异常请截取片段反馈。

**Q: 如何使用已有的 RhythmicaLyrics 字典？**
在设置 → 用户字典中点击「导入 RL 字典」。

**Q: 项目打不开？**
确认文件扩展名为 `.sug`，且为有效的 JSON 格式。启动时如检测到自动保存文件会提示恢复。

## 技术栈

- UI 框架：PyQt6 + PyQt-Fluent-Widgets
- 音频处理：sounddevice + soundfile + audiotsm (WSOLA)
- 日语处理：SudachiPy + pykakasi + jaconv
- 架构：分层架构（Domain / Application / Infrastructure / Presentation）

## 开发者

如需了解项目架构、测试和开发细节，请参阅 [README_DEV.md](README_DEV.md) 和 [docs/](./docs/) 目录。

## 许可

MIT License

## 链接

- [GitHub](https://github.com/Xuan-cc/StrangeUtaGame)
- [开发者文档](README_DEV.md)
