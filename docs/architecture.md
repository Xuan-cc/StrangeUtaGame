# 架构总览

StrangeUtaGame 采用分层架构设计，确保核心业务逻辑与用户界面、外部依赖解耦。项目分为四层：

## 4层架构模型

### 1. 表示层 (Presentation Layer)
- **位置**：`src/strange_uta_game/frontend/`
- **职责**：负责 UI 展示和用户交互。使用 PyQt6 和 PyQt-Fluent-Widgets 构建。
- **核心组件**：
    - `EditorInterface`：打轴主界面。
    - `EditInterface`：列表编辑界面。
    - `RubyInterface`：注音编辑界面。
    - `resource/`：应用资源文件（图标等）。
    - `config/`：内嵌默认配置文件（config.json、dictionary.json、singers.json）。

### 2. 应用服务层 (Application Layer)
- **位置**：`src/strange_uta_game/backend/application/`
- **职责**：协调业务流程，作为表示层和领域层之间的中介。不包含具体的业务规则，而是调用领域层和基础设施层来完成任务。
- **核心服务**：`TimingService`, `AutoCheckService`, `ProjectService`, `ExportService`, `SingerService`, `CommandManager`。

### 3. 领域层 (Domain Layer)
- **位置**：`src/strange_uta_game/backend/domain/`
- **职责**：包含核心业务实体和逻辑。这是最内层，不依赖任何外部库或框架。
- **层次结构**：Ruby → Character → Word → Sentence → Project。

### 4. 基础设施层 (Infrastructure Layer)
- **位置**：`src/strange_uta_game/backend/infrastructure/`
- **职责**：提供具体的技术实现。包括文件解析、导出器、音频处理等。
- **核心功能**：`SugProjectParser`, `lyric_parser`, 各类导出器 (LRC, KRA, ASS 等)。

## 依赖规则
- 依赖方向始终向内。表示层可以调用应用层，应用层可以调用领域层和基础设施层（通常通过接口或抽象），领域层不依赖任何层。
- 基础设施层实现领域层或应用层定义的抽象接口。
