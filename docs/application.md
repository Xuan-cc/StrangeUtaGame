# 应用服务层设计

应用服务层负责协调各组件，执行业务逻辑，并管理系统的状态。

## 核心服务

### TimingService (打轴服务)
处理打轴过程中的核心逻辑。

- **职责**：
    - 管理播放器的当前播放位置。
    - 处理 Checkpoint 的导航和跳转逻辑。
    - 记录和更新 Character 的时间标签。
    - 控制播放状态（播放、暂停、停止、调速）。

### AutoCheckService (自动分析服务)
基于文本和节奏规则自动配置节奏点。

- **职责**：
    - `analyze_sentence`：分析句子结构，识别汉字等需要多节奏点的字符。
    - `apply_to_sentence`：将分析结果应用到 Sentence 实体，设置 check_count。

### ProjectService (项目服务)
管理项目的生命周期和持久化逻辑。

- **职责**：
    - 处理项目的创建 (create)、加载 (load) 和保存 (save)。
    - 提供项目验证 (validate) 和数据统计 (statistics) 功能。

### ExportService (导出服务)
将项目数据转换为不同的外部格式。

- **职责**：
    - 调用基础设施层的导出器生成 LRC, KRA, TXT, ASS, Nicokara 等格式。
    - 处理导出时的各种参数设置（如偏移值、演唱者过滤等）。

### SingerService (演唱者管理服务)
管理项目中的演唱者信息。

- **职责**：
    - 管理演唱者的增删改查。
    - 处理演唱者颜色的设置和启用/禁用状态。
    - 同步 Character 级别的 singer_id。

### CommandManager (命令管理器)
实现撤销/重做功能。

- **职责**：
    - 使用命令模式封装所有的修改操作。
    - 维护撤销栈和重做栈，提供 `undo` 和 `redo` 方法。
