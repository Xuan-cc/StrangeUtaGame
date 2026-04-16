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
├── backend/                    # 后端核心逻辑
│   ├── domain/                 # 领域层：纯数据模型，无外部依赖
│   │   ├── models.py           # 实体与值对象定义
│   │   └── project.py          # 项目聚合根
│   │
│   ├── application/            # 应用服务层：业务逻辑协调
│   │   ├── timing_service.py   # 打轴服务
│   │   ├── project_service.py  # 项目管理服务
│   │   └── export_service.py   # 导出服务
│   │
│   └── infrastructure/         # 基础设施层：具体实现
│       ├── audio/              # 音频引擎
│       ├── parsers/            # 文件解析器
│       └── exporters/          # 导出器
│
├── frontend/                   # 前端 UI 层（PyQt）
│   ├── widgets/                # UI 组件
│   ├── components/             # 自定义控件
│   └── main.py                 # 应用入口
│
├── docs/                       # 设计文档
│   ├── architecture.md         # 架构总览
│   ├── domain.md               # 领域层设计
│   ├── application.md          # 应用层设计
│   ├── infrastructure.md       # 基础设施层设计
│   └── ui.md                   # UI 层设计
│
├── tests/                      # 测试
├── examples/                   # 示例文件
├── requirements.txt            # 依赖
└── main.py                     # 启动脚本
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

### 数据流向

```
用户操作 → UI 组件 → 应用服务 → 领域模型 → 基础设施
                              ↓
UI 更新 ← 回调/信号 ← 状态变更 ← 业务计算
```

## 快速开始

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

# 代码检查
ruff check .
```

## 快捷键

| 按键 | 功能 |
|------|------|
| `Space` | 在当前位置打轴 |
| `F1-F9` | 快速打轴 / 功能键 |
| `A` | 播放/暂停 |
| `S` | 停止 |
| `Z` | 后退 5 秒 |
| `X` | 前进 5 秒 |
| `Q` | 减速 |
| `W` | 加速 |
| `ESC` | 清除当前时间标签 |

## 支持的导出格式

- **LRC** - 通用歌词格式
- **KRA** - 卡拉OK专用格式
- **TXT** - 纯文本时间标签
- **txt2ass** - 用于生成 ASS 字幕
- **Nicokara规则** - 用于ニコカラメーカー

## 文档

详细设计文档请查看 [docs/](./docs/) 目录：

- [架构总览](docs/architecture.md)
- [领域层设计](docs/domain.md)
- [应用层设计](docs/application.md)
- [基础设施层设计](docs/infrastructure.md)
- [UI 层设计](docs/ui.md)

## 许可

MIT License
