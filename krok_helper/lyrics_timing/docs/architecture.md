# 架构总览

StrangeUtaGame 采用分层架构设计，确保核心业务逻辑与用户界面、外部依赖解耦。项目分为四层：

## 4层架构模型

### 1. 表示层 (Presentation Layer)
- **位置**：`src/strange_uta_game/frontend/`
- **职责**：负责 UI 展示和用户交互。使用 PyQt6 和 PyQt-Fluent-Widgets 构建。
- **核心组件**：
    - `MainWindow`：主窗口框架 + 侧边栏导航
    - `EditorInterface`：打轴主界面（timing_interface.py）
    - `EditInterface`：行编辑界面（edit_interface.py）
    - `FulltextInterface`：全文本编辑（已废弃不建议使用）界面（fulltext_interface.py）
    - `ExportInterface`：导出界面
    - `SingerInterface`：演唱者管理界面
    - `SettingsInterface`：设置界面
    - `HomeInterface`：主页界面
    - `resource/`：应用资源文件（图标等）
    - `config/`：内嵌默认配置文件（config.json、dictionary.json、singers.json）

### 2. 应用服务层 (Application Layer)
- **位置**：`src/strange_uta_game/backend/application/`
- **职责**：协调业务流程，作为表示层和领域层之间的中介。不包含具体的业务规则，而是调用领域层和基础设施层来完成任务。
- **核心服务**：
    - `TimingService`：打轴核心服务（节奏点时间戳唯一写入入口）
    - `AutoCheckService`：自动注音与节奏点分配服务
    - `ProjectService`：项目生命周期管理
    - `ExportService`：导出服务
    - `SingerService`：演唱者管理服务
    - `CommandManager`：撤销/重做命令管理
    - `CalibrationService`：Offset 校准算式
    - `ProjectImportService`：项目导入辅助

### 3. 领域层 (Domain Layer)
- **位置**：`src/strange_uta_game/backend/domain/`
- **职责**：包含核心业务实体和逻辑。这是最内层，不依赖任何外部库或框架。
- **核心实体**：
    - `Character`：字符实体（卡拉OK打轴最小单位）
    - `Ruby` / `RubyPart`：注音实体
    - `Word`：词组实体（由 linked_to_next 链接的 Character 序列）
    - `Sentence`：句子实体（Character 列表）
    - `Singer`：演唱者实体
    - `Project`：项目根实体（聚合所有句子、演唱者和元数据）
- **层次结构**：Ruby → Character → Word → Sentence → Project

### 4. 基础设施层 (Infrastructure Layer)
- **位置**：`src/strange_uta_game/backend/infrastructure/`
- **职责**：文件解析、导出器、音频处理等外部系统实现。
- **核心功能**：
    - **音频引擎**：`SoundDeviceEngine`、`TSMRenderCache`、`SPSC RingBuffer`
    - **解析器**：`LyricParserFactory`、`lyric_parser`、`ass_parser`、`srt_parser`、`ruby_analyzer`、`e2k_engine`
    - **导出器**：`LrcExporter`、`KraExporter`、`SrtExporter`、`TxtExporter`、`Txt2AssExporter`、`AssExporter`、`NicokaraExporter`
    - **持久化**：`SugProjectParser`（.sug 文件读写）
    - **数据**：`default_dictionary`（内嵌 1757 条词典）、`kanji_readings`（汉字音读字典）

## 依赖规则
- 依赖方向始终向内。表示层可以调用应用层，应用层可以调用领域层和基础设施层（通常通过接口或抽象），领域层不依赖任何层。
- 基础设施层实现领域层或应用层定义的抽象接口。
