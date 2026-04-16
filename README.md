# StrangeUtaGame

一款由 RhythmicaLyrics 启发的歌词打轴软件，专注于低延迟、高精度的卡拉OK时间标签制作。

## 功能特性

- **精准打轴**：类似节奏游戏的打轴体验，支持空格键和 F1-F9 功能键
- **卡拉OK预览**：实时ワイプ效果，逐字高亮显示演唱进度
- **日语注音**：自动为汉字添加假名注音（ルビ），支持手动编辑
- **变速播放**：50%~200% 速度调节，便于精准对轴
- **多格式导出**：支持 LRC、KRA、TXT、txt2ass、Nicokara 规则等格式

## 技术栈

- **UI 框架**：PyQt6 + PyQt-Fluent-Widgets
- **音频处理**：sounddevice + soundfile
- **日语处理**：pykakasi + jaconv
- **架构模式**：分层架构（Domain + Application + Infrastructure + Presentation）

## 项目结构

```
strange-uta-game/
├── src/                        # 应用源代码
│   └── strange_uta_game/
│       ├── backend/            # 后端核心逻辑
│       │   ├── domain/         # 领域层：纯数据模型，无外部依赖
│       │   ├── application/    # 应用服务层：业务逻辑协调
│       │   └── infrastructure/ # 基础设施层：具体实现
│       └── frontend/           # 前端 UI 层（PyQt）
│           ├── startup/        # 启动界面
│           ├── editor/         # 编辑器界面
│           └── settings/       # 设置界面
├── tests/                      # 测试文件（与应用代码分离）
│   └── unit/                   # 单元测试
│       ├── domain/             # 领域层测试
│       ├── application/        # 应用层测试
│       └── infrastructure/     # 基础设施层测试
├── docs/                       # 设计文档
├── examples/                   # 示例文件
├── main.py                     # 启动脚本
├── requirements.txt            # 依赖
└── README.md                   # 本文件
```

## 架构概述

本项目采用**分层架构**，确保核心业务逻辑与 UI 解耦，便于测试和扩展。

### 分层说明

```
┌─────────────────────────────────────┐
│  表示层 (Presentation)               │
│  PyQt-Fluent-Widgets                │
│  - 只负责展示和用户输入              │
└─────────────┬───────────────────────┘
              │
┌─────────────▼───────────────────────┐
│  应用服务层 (Application)            │
│  - 协调业务逻辑                      │
│  - 管理业务流程                      │
└─────────────┬───────────────────────┘
              │
┌─────────────▼───────────────────────┐
│  领域层 (Domain)                     │
│  - 核心业务实体                      │
│  - 纯数据，无外部依赖                 │
└─────────────┬───────────────────────┘
              │
┌─────────────▼───────────────────────┐
│  基础设施层 (Infrastructure)         │
│  - 音频、文件、网络等具体实现         │
│  - 可替换的实现细节                   │
└─────────────────────────────────────┘
```

## 快速开始

### 环境要求

- Python 3.11+
- Windows 10/11, macOS, 或 Linux
- 音频输出设备

### 安装依赖

```bash
pip install -r requirements.txt
```

### 运行应用

```bash
python main.py
```

### 开发模式

```bash
# 运行测试
pytest tests/

# 运行特定测试
pytest tests/unit/domain/test_checkpoint.py

# 代码检查
ruff check .
```

## 使用指南

### 1. 创建新项目

1. 启动应用后，在启动界面：
   - 在「歌词输入」区粘贴歌词或导入 TXT/LRC/KRA 文件
   - 在「音频选择」区选择 MP3/WAV/FLAC 音频文件
   - 点击「创建项目」进入编辑器

### 2. 打轴操作

在编辑器界面中：

| 按键 | 功能 |
|------|------|
| `Space` | 在当前位置打轴（按下即记录时间） |
| `A` | 播放/暂停 |
| `S` | 停止播放并回到开头 |
| `Z` | 后退 5 秒 |
| `X` | 前进 5 秒 |
| `Q` | 减速（每次 -10%） |
| `W` | 加速（每次 +10%） |
| `ESC` | 清除当前行所有时间标签 |

### 3. 保存和导出

- **保存项目**：菜单栏「文件」→「保存项目」（.sug 格式）
- **导出歌词**：菜单栏「文件」→「导出」→ 选择格式
  - LRC：通用歌词格式
  - KRA：卡拉OK专用格式
  - TXT：纯文本时间标签
  - ASS：字幕文件
  - Nicokara：ニコカラメーカー格式

### 4. 演唱者管理

- 点击「视图」→「演唱者管理」
- 可添加/删除演唱者
- 设置演唱者颜色和名称
- 演唱者切换由系统自动处理

### 5. 注音编辑

- 点击「视图」→「注音编辑」
- 选择歌词行
- 点击「自动分析」或手动添加注音
- 编辑注音文本和范围

## 快捷键

### 编辑模式

| 按键 | 功能 |
|------|------|
| `Space` | 在当前位置打轴 |
| `A` | 播放/暂停 |
| `S` | 停止 |
| `Z` | 后退 5 秒 |
| `X` | 前进 5 秒 |
| `Q` | 减速 |
| `W` | 加速 |
| `ESC` | 清除当前时间标签 |

### 文件操作

| 快捷键 | 功能 |
|--------|------|
| `Ctrl+N` | 新建项目 |
| `Ctrl+O` | 打开项目 |
| `Ctrl+S` | 保存项目 |
| `Ctrl+Z` | 撤销 |
| `Ctrl+Y` | 重做 |

## 项目文件格式

- **.sug** - StrangeUtaGame 项目文件（S**trange**U**ta**G**ame 的缩写）
  - 基于 JSON 格式
  - 存储歌词、时间标签、节奏点配置、注音等
  - **不存储音频路径**，用户每次使用时重新选择音频（更灵活）
  - 存储音频时长用于验证（可选）

## 支持的导出格式

- **LRC** - 通用歌词格式
- **KRA** - 卡拉OK专用格式（同 LRC，不同扩展名）
- **TXT** - 纯文本时间标签
- **txt2ass** - 用于生成 ASS 字幕
- **Nicokara规则** - 用于ニコカラメーカー
- **ASS** - 直接导出 ASS 字幕文件

## 文档

详细设计文档请查看 [docs/](./docs/) 目录：

- [架构总览](docs/architecture.md)
- [领域层设计](docs/domain.md)
- [应用层设计](docs/application.md)
- [基础设施层设计](docs/infrastructure.md)
- [UI 层设计](docs/ui.md)

## 测试

测试文件位于 `tests/` 目录，与应用代码分离：

```
tests/
└── unit/                     # 单元测试
    ├── domain/               # 领域层测试（85个）
    ├── application/          # 应用层测试（35个）
    └── infrastructure/       # 基础设施层测试（60个）
```

### 运行测试

```bash
# 运行所有测试
pytest tests/

# 运行单元测试
pytest tests/unit/

# 运行特定模块测试
pytest tests/unit/domain/
pytest tests/unit/application/
pytest tests/unit/infrastructure/

# 生成覆盖率报告
pytest tests/ --cov=src --cov-report=html
```

当前共有 **180 个测试**，全部通过。

## 打包发行

### 使用 PyInstaller 打包

```bash
# 安装 PyInstaller
pip install pyinstaller

# 打包（Windows）
pyinstaller --noconfirm --onefile --windowed --name "StrangeUtaGame" --icon=icon.ico main.py

# 打包（包含依赖目录）
pyinstaller --noconfirm --onedir --windowed --name "StrangeUtaGame" main.py
```

### 打包后的文件

打包完成后，可在 `dist/` 目录找到：
- `StrangeUtaGame.exe` - 单个可执行文件（使用 --onefile）
- `StrangeUtaGame/` - 应用程序目录（使用 --onedir）

## 项目信息

- **GitHub 地址**: https://github.com/Xuan-cc/StrangeUtaGame
- **许可证**: MIT License
- **作者**: Xuan-cc
- **开发状态**: 已完成 Phase 1-8，具备完整歌词打轴功能

## 依赖

主要依赖项：
- PyQt6 >= 6.6.0
- PyQt-Fluent-Widgets >= 1.5.0
- sounddevice >= 0.4.6
- soundfile >= 0.12.1
- pykakasi >= 2.2.1

完整依赖列表见 [requirements.txt](requirements.txt)

## 许可

MIT License
