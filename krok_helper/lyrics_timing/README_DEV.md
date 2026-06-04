# StrangeUtaGame — 开发者文档

> 本文档面向开发者。用户使用说明请参阅 [README.md](README.md)。

一款由 RhythmicaLyrics 启发的歌词打轴软件，专注于低延迟、高精度的卡拉OK时间标签制作。

**当前版本**：v0.3.0 | **许可证**：GPL-3.0 | **平台**：Windows 10/11

## 核心特性

### 🎯 打轴与编辑
- **双模式打轴**：打轴模式/编辑模式，左下角实时提示
- **精准时间控制**：Alt+↑/↓ 微调时间戳，Alt+←/→ 字符内切换节奏点
- **批量编辑**：批量替换文本、注音和节奏点，支持撤销/重做

### 🎵 音频处理
- **WSOLA 变速**：pedalboard `time_stretch` 离线预渲染，50%-200% 速度调节
- **SPSC RingBuffer**：零分配音频回调，立体声相位一致
- **低延迟播放**：sounddevice (PortAudio) 输出

### 📝 注音系统
- **日语注音**：SudachiPy 上下文感知，5 级 Pass 拆分策略
- **英文注音**：CMU 词典 + morikatron 规则引擎
- **用户词典**：1757 条内置词典，支持 RL 字典导入

### 📁 文件支持
- **多格式导入**：LRC、ASS、SRT、TXT、KRA、Nicokara
- **多格式导出**：LRC（3 种）、KRA、SRT、TXT、txt2ass、ASS、Nicokara
- **项目格式**：.sug JSON 格式，支持版本迁移

## 技术栈

| 类别 | 技术 |
|------|------|
| **编程语言** | Python 3.11+ |
| **UI 框架** | PyQt6 + PyQt6-Fluent-Widgets (Fluent Design) |
| **音频处理** | sounddevice + soundfile + pedalboard (WSOLA) |
| **日语处理** | SudachiPy + pykakasi |
| **英文注音** | CMU 词典 + morikatron 规则引擎 |
| **打包工具** | PyInstaller |
| **代码质量** | ruff, black, mypy |
| **测试框架** | pytest + pytest-qt + pytest-cov |

## 项目结构

```
StrangeUtaGame/
├── src/                            # 应用源代码
│   └── strange_uta_game/
│       ├── backend/                # 后端核心逻辑
│       │   ├── domain/             # 领域层：纯数据模型，无外部依赖
│       │   ├── application/        # 应用服务层：业务逻辑协调
│       │   └── infrastructure/     # 基础设施层：具体实现
│       │       ├── audio/          # 音频引擎（SoundDeviceEngine, TSMRenderCache, RingBuffer）
│       │       ├── data/           # 内嵌词典数据（default_dictionary.py）
│       │       ├── exporters/      # 导出器集合（LRC/KRA/SRT/TXT/ASS/Nicokara）
│       │       ├── parsers/        # 解析器（lyric_parser, text_splitter, ruby_analyzer, e2k_engine）
│       │       └── persistence/    # 项目持久化（sug_io）
│       ├── frontend/               # 前端 UI 层（PyQt）
│       │   ├── home/               # 主页（项目创建入口）
│       │   ├── editor/             # 编辑器界面（timing_interface + timing/ 子包）
│       │   ├── export/             # 导出界面
│       │   ├── singer/             # 演唱者管理界面
│       │   ├── online/             # 在线查询界面（占位）
│       │   └── settings/           # 设置界面（拆分为 app_settings / cards / dialogs 等子模块）
│       ├── resource/               # 应用资源（icon.ico）
│       └── config/                 # 内嵌默认配置文件（config.json, dictionary.json, singers.json, e2k.txt）
├── tests/                          # 测试文件（与应用代码分离）
│   └── unit/                       # 单元测试
│       ├── domain/                 # 领域层测试
│       ├── application/            # 应用层测试
│       ├── infrastructure/         # 基础设施层测试
│       └── frontend/               # 前端层测试
├── docs/                           # 设计文档
├── scripts/                        # 辅助脚本（迁移工具等）
├── main.py                         # 启动脚本
├── build.py                        # PyInstaller 打包脚本
├── requirements.txt                # 生产依赖
├── requirements-dev.txt            # 开发依赖
├── pyproject.toml                  # 项目元数据与工具配置
└── README.md                       # 用户文档
```

## 架构概述

本项目采用**分层架构**，确保核心业务逻辑与 UI 解耦，便于测试和扩展。

### 分层说明

```
┌─────────────────────────────────────┐
│  表示层 (Presentation)               │
│  PyQt6 + PyQt-Fluent-Widgets        │
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
│  - 核心数据模型 (Ruby/Char/Sentence) │
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

- **操作系统**：Windows 10/11（主要开发平台）
- **Python**：3.11+
- **音频设备**：支持音频输出的设备

### 开发环境设置

```bash
# 克隆仓库
git clone https://github.com/Xuan-cc/StrangeUtaGame.git
cd StrangeUtaGame

# 创建虚拟环境（推荐）
python -m venv venv
venv\Scripts\activate

# 安装生产依赖
pip install -r requirements.txt

# 安装开发依赖
pip install -r requirements-dev.txt

# 运行应用
python main.py
```

### 开发工具

```bash
# 代码检查
ruff check .

# 代码格式化
black .

# 类型检查
mypy src/

# 运行测试
pytest tests/

# 运行测试并生成覆盖率报告
pytest tests/ --cov=src --cov-report=html
```

## 使用指南

### 1. 加载文件

启动后默认进入打轴界面，直接拖入文件即可开始：

- **拖入歌词文件**（LRC / ASS / SRT / TXT / KRA / Nicokara）→ 加载歌词
- **拖入音频文件**（MP3 / WAV / FLAC / OGG）→ 加载音频
- **拖入项目文件**（.sug）→ 打开已有项目

也可以通过侧边栏导航到「主页」手动创建项目。

### 2. 打轴操作

编辑器有**打轴模式**和**编辑模式**两种模式，左下角实时提示当前模式。音乐播放时自动进入打轴模式，暂停时进入编辑模式。

| 按键 | 打轴模式 | 编辑模式 |
|------|---------|---------|
| `Space` | 打轴（按下记录时间） | 增加节奏点 (+1) |
| `D` | 播放/暂停 | 播放/暂停 |
| `S` | 停止 | 停止 |
| `Z` / `X` | 后退 / 前进 5 秒 | 后退 / 前进 5 秒 |
| `Q` / `W` | 减速 / 加速 (±10%) | 减速 / 加速 (±10%) |
| `↑` / `↓` | 上一行 / 下一行 | 上一行 / 下一行 |
| `←` / `→` | 上一字符 / 下一字符（行首/末自动跨行） | 上一字符 / 下一字符 |
| `Enter` | — | 插入换行（在当前字符处拆分新行） |
| `Shift+Enter` | — | 合并上一行（将当前行合并到上一行末尾） |
| `Delete` | — | 删除选中字符（支持划词多选） |
| `Backspace` | — | 减少节奏点 (-1，最小0) |
| `F2` | 编辑注音（支持连词合并/拆分） | 编辑注音 |
| `F3` | 连词/取消连词（可自定义快捷键） | 连词/取消连词 |
| `F5` / `F6` | 增加 / 减少节奏点 | — |
| `.` (长按) | 切换句尾标记（is_sentence_end） | — |
| `.` (短按) | — | 切换句尾标记 |
| `Alt+←` / `Alt+→` | 当前字符内节奏点循环切换（上一个 / 下一个） | 同左 |
| `Alt+↑` / `Alt+↓` | 当前时间戳 ± 10ms（默认步长） | 同左 |
| `Ctrl+Z` / `Ctrl+Y` | 撤销 / 重做 | 撤销 / 重做 |
| `Ctrl+S` | 保存项目 | 保存项目 |
| `Ctrl+H` | 批量变更（替换注音/删除注音/设置节奏点/注册词典） | 同左 |

> 快捷键可在「设置 → 快捷键」中自定义，支持组合键、双快捷键绑定和长按绑定（按键持续 300ms 以上触发），冲突即时检测。

### 右键菜单

在打轴预览中右键点击字符，可快速执行以下操作：

- **删除字符**：删除选中字符（支持划词多选）
- **删除当前时间戳并回滚**：移除当前节奏点的时间戳
- **在此前插入空格**：在当前字符前插入空格
- **在此后插入空格**：在当前字符后插入空格
- **合并上一行**：将当前行合并到上一行末尾
- **删除本行**：删除当前整行
- **在此前插入空行**：在当前行前插入空行
- **在此后插入空行**：在当前行后插入空行
- **增加/减少节奏点**：调整当前字符的节奏点数量
- **设置/取消句尾**：切换句尾标记
- **设置演唱者**：为选中字符指定演唱者（支持划词多选）

### 3. 保存和导出

- **保存项目**：在任意界面按 Ctrl+S 保存项目（.sug 格式）。若已有保存路径则直接保存，首次保存弹出另存为对话框。未保存修改关闭时，使用中文按钮（保存/放弃/取消）提示。
- **导出歌词**：点击侧边栏「导出」→ 选择格式 → 设置文件名（默认使用音频文件名）→ 导出
  - LRC（增强型/逐行/逐字）
  - KRA：卡拉OK专用格式
  - TXT：纯文本时间标签
  - SRT：标准字幕格式
  - txt2ass：ASS 中间格式
  - ASS：直接导出 ASS 字幕文件
  - Nicokara：ニコカラメーカー格式（可设置元数据标签，支持按演唱者筛选，可插入演唱者切换标签）

### 4. 演唱者管理

- 点击「视图」→「演唱者管理」
- 可添加/删除演唱者
- 设置演唱者颜色和名称
- 划词选择功能：在打轴预览中拖拽选中文字 → 右键 → 选择演唱者（直接写入 per-char singer_id）
- 支持同一行内切换演唱者（per-char 级别），每个字符独立 singer_id
- checkpoint 颜色跟随每个字符的实际演唱者设置
- 预设保存/加载：在演唱者管理界面点击「保存为软件预设」/「从软件预设加载」
- 演唱者预设跨项目/跨启动保持

### 5. 全文本编辑（已废弃不建议使用）

- 点击侧边栏「全文本编辑（已废弃不建议使用）」标签
- 全文本视图显示所有歌词及注音标注
- 点击「自动分析全部注音」为所有字符类型生成注音（汉字、假名、英文、数字、符号等均标注）
- 用户通过「按类型删除注音」按钮选择性移除不需要的注音类型（含長音符号ー/～、特殊符号♪等）
- 点击「更新节奏点」根据当前注音和 AutoCheck 设置规则重新计算节奏点（更新前自动保存编辑内容）
- 自由文本编辑：编辑整行文本，支持增删行，时间标签和注音自动保留
- 切换标签页时自动将修改应用到项目数据，无需手动点击按钮
- 连词编辑格式：`{大冒険||だ|い,ぼ|う,け|ん}` 表示各字各节奏点注音，`{One|ワン,,}` 标注英文注音位置。向后兼容旧版格式。

### 6. 行编辑界面

- 点击侧边栏「行编辑」标签
- 歌词表格概览：行号、歌词（连词以 `[chars]` 合并显示）、演唱者（per-char 汇总）、时间标签
- **行级操作**：添加行、删除行、复制行、插入行按钮，支持多行选择
- **键盘快捷键**：Ctrl+C 复制行、Ctrl+V 粘贴行、Delete 删除行
- 双击行打开行详情对话框
- 行详情支持 per-char 编辑：注音、节奏点、演唱者逐字独立修改
- **字符级操作**：行详情中支持添加、删除、复制、插入字符
- **字符快捷键**：Ctrl+C/V/Delete 操作字符
- 连词组合并为一行编辑，各字段用逗号分隔

### 7. Offset 校准

- 在「设置」→「Offset 校准」中点击「开始校准」
- 弹出独立校准窗口：深色画布上 2 个白色滑块按 BPM 匀速从左到右滑动
- 每个滑块穿过中央红色判定线对应一拍，接近判定线时自动放大，到达判定线时播放节拍器点击音
- 用户按空格键跟随节拍敲击，系统以滑块穿过判定线（视觉正中央）的时刻为完美判定点计算偏移量
- 偏移量 = 完美判定时间 - 实际敲击时间（正值=偏早，负值=偏晚）
- 左上角实时显示「最近偏移」和「平均偏移」
- 右上角「重置」清除所有数据重新开始，「应用」将偏移写入全局设置
- 可在校准过程中自由调节 BPM（60-240）

### 8. 拖拽加载

- 在主页或打轴界面，直接将文件拖入窗口即可加载
- 支持音频文件（.mp3/.wav/.flac/.ogg）、歌词文件（.lrc/.txt/.kra/.ass/.srt）、项目文件（.sug）
- 打轴界面中：音频替换当前音频，歌词重新加载歌词，项目文件切换到对应项目
- 打轴界面工具栏的「加载歌词」按钮也可单独加载新歌词文件

### 9. Karaoke 渲染偏移及导出偏移

- 在「设置」→「打轴设定」中设置全局偏移量（毫秒）
- 渲染和导出偏移预计算在 Character 数据结构中（`render_timestamps` / `export_timestamps`）
- 预览走字效果使用 `render_timestamps` 实时应用偏移，导出时使用 `export_timestamps`
- 确保预览与导出效果完全一致，无需手动计算偏移

### 10. 快捷键自定义

- 在「设置」→「快捷键」中配置
- 点击按钮后直接按下键盘按键即可设置（支持组合键如 Ctrl+F4、Alt+A 等）
- 每个功能支持设置两个快捷键
- 快捷键冲突时自动清除被冲突的绑定并提醒用户（即时检测，无需点保存）
- 设置快捷键时按 ESC 可取消设置操作，不做任何改动

### 11. Nicokara 格式支持

#### 导入
- 在主页导入歌词时选择 Nicokara LRC 文件
- 自动解析【svN】演唱者标签，为每个演唱者创建对应的演唱者配置
- 自动解析 @Ruby 注音并应用到歌词
- 自动解析 @Emoji 中的演唱者定义（如有）

#### 导出
- 在导出界面选择 Nicokara 格式
- 可勾选要输出的演唱者（默认全部）
- 可开启「插入演唱者标签」选项，在演唱者切换处自动插入【演唱者名】
- 支持 @Ruby 注音标签和元数据标签（@Title、@Artist 等）

### 12. 定时自动保存与闪退恢复

- 默认每 5 分钟自动保存一次，保存为 `.sug.temp` 文件（每次覆盖）
- 所有临时文件统一存放在程序目录的 `.cache` 文件夹下：
  - 已保存项目：`.cache/.项目名.sug.temp`
  - 未保存项目：`.cache/.untitled.sug.temp`
- 闪退恢复：启动时自动检测 `.cache` 目录下的 `.sug.temp` 文件，若存在则弹窗询问是否恢复
- 用户主动保存或正常退出时自动清理 temp 文件
- 可在「设置」中自定义保存间隔（1~60 分钟）或关闭自动保存

## 项目文件格式

- **.sug** - StrangeUtaGame 项目文件（**S**trange **U**ta **G**ame 的缩写）
  - 基于 JSON 格式（v0.3.0）
  - 存储歌词、时间标签、节奏点配置、注音等
  - **不存储音频路径**，用户每次使用时重新选择音频（更灵活）
  - 存储音频时长用于验证（可选）
  - 旧版 v0.1 文件自动迁移到 v0.3.0（Ruby → RubyPart）

## 支持的导入格式

- **LRC** - 逐行、逐字、增强型三种子格式
- **ASS** - ASS 字幕文件（支持 \kf/\k/\ko 卡拉OK标签）
- **SRT** - SRT 字幕文件
- **TXT** - 纯文本
- **KRA** - 卡拉OK格式（同 LRC）
- **Nicokara** - Nicokara LRC 格式（【svN】标签 + @Ruby 注音）

## 支持的导出格式

- **LRC (增强型)** - 增强型 LRC，逐字尖括号标签
- **LRC (逐行)** - 标准 LRC，每行一个时间标签
- **LRC (逐字)** - 逐字 LRC，方括号标签
- **KRA** - 卡拉OK专用格式（同 LRC 增强型，不同扩展名）
- **SRT** - 标准 SRT 字幕格式
- **TXT** - 纯文本时间标签
- **txt2ass** - 用于生成 ASS 字幕
- **ASS** - 直接导出 ASS 字幕文件
- **Nicokara规则** - 用于ニコカラメーカー

## 文档

详细设计文档请查看 [docs/](./docs/) 目录：

- [架构总览](docs/architecture.md)
- [领域层设计](docs/domain.md)
- [应用层设计](docs/application.md)
- [基础设施层设计](docs/infrastructure.md)
- [UI 层设计](docs/ui.md)
- [经验教训](docs/lessons_learned.md)
- [修复记录](docs/fixes_summary.md)
- [更新日志 v0.3.0](docs/更新日志-0.3.0.md)

## 测试

测试文件位于 `tests/` 目录，与应用代码分离：

```
tests/
└── unit/                     # 单元测试
    ├── domain/               # 领域层测试
    ├── application/          # 应用层测试
    ├── infrastructure/       # 基础设施层测试
    └── frontend/             # 前端层测试
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
pytest tests/unit/frontend/

# 生成覆盖率报告
pytest tests/ --cov=src --cov-report=html

# 运行测试并显示详细输出
pytest tests/ -v
```

### 测试统计

- **当前测试数量**：382 个
- **测试覆盖率**：运行 `pytest tests/ --cov=src` 查看

### 编写测试

测试文件命名规范：`test_*.py` 或 `*_test.py`

```python
# 示例：领域层测试
def test_character_check_count():
    char = Character(char="あ")
    char.set_check_count(2)
    assert char.check_count == 2
    assert len(char.timestamps) == 2
```

**测试原则**：
- 每个测试函数只测试一个功能点
- 使用描述性的测试函数名
- 测试应该独立，不依赖其他测试的执行顺序
- 使用 pytest fixtures 管理测试数据

## 打包发行

### 使用打包脚本（推荐）

```bash
# 安装 PyInstaller
pip install pyinstaller

# 运行打包脚本
python build.py
```

`build.py` 包含所有必要的配置：
- `hidden-imports`（numpy, sudachipy, pedalboard 等）
- 数据/二进制收集配置
- PortAudio DLL 路径检测

### 手动打包

```bash
pyinstaller --noconfirm --onedir --windowed \
  --name "StrangeUtaGame" \
  --icon="src/strange_uta_game/resource/icon.ico" \
  --add-data "src/strange_uta_game/config;strange_uta_game/config" \
  --add-data "src/strange_uta_game/resource;strange_uta_game/resource" \
  --collect-data "sudachipy" \
  --collect-data "sudachidict_core" \
  --collect-binaries "soundfile" \
  --hidden-import "numpy" \
  --hidden-import "sudachipy" \
  --hidden-import "pedalboard" \
  main.py
```

### 打包产物

- **输出目录**：`dist/StrangeUtaGame/`
- **主程序**：`StrangeUtaGame.exe`
- **体积参考**：约 410 MB（sudachidict_core 约 207 MB 占大头，属日语形态素分析词典）

## 项目信息

| 项目 | 信息 |
|------|------|
| **GitHub** | https://github.com/Xuan-cc/StrangeUtaGame |
| **许可证** | GPL-3.0 License |
| **作者** | Xuan-cc |
| **版本** | v0.3.0 |

## 依赖

### 生产依赖

主要依赖项：
- PyQt6 >= 6.6.0
- PyQt6-Fluent-Widgets >= 1.5.0
- sounddevice >= 0.4.6
- soundfile >= 0.12.1
- pedalboard >= 0.9.0
- sudachipy >= 0.6.0
- sudachidict_core
- pykakasi >= 2.2.1

完整依赖列表见 [requirements.txt](requirements.txt)

### 开发依赖

开发依赖列表见 [requirements-dev.txt](requirements-dev.txt)

## 代码规范

### 代码风格

- **格式化工具**：black
- **Lint 工具**：ruff
- **类型检查**：mypy

### 命名规范

- **类名**：PascalCase（如 `TimingService`、`Character`）
- **函数/方法**：snake_case（如 `on_key_changed`、`set_check_count`）
- **常量**：UPPER_SNAKE_CASE（如 `DEFAULT_CONFIG`）
- **私有成员**：单下划线前缀（如 `_current_position`）

### 架构规范

- **依赖方向**：始终向内（Presentation → Application → Domain）
- **领域层**：零外部依赖，纯 Python 数据类
- **命令模式**：所有编辑操作通过 Command 对象执行，支持撤销/重做

## 许可

GPL-3.0 License
