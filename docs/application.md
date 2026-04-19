# 应用服务层设计

应用服务层负责协调各组件，执行业务逻辑，并管理系统的状态。

## 核心服务

### TimingService (打轴服务)
处理打轴过程中的核心逻辑。

- **职责**：
    - 管理播放器的当前播放位置。
    - 处理 Checkpoint 的导航和跳转逻辑。
    - 记录和更新 Character 的时间标签。打轴时始终覆盖现有时间戳，不再提示时间倒退警告。
    - 控制播放状态（播放、暂停、停止、调速）。

### AutoCheckService (自动分析服务)
基于文本和节奏规则自动配置节奏点。

- **职责**：
    - `analyze_sentence`：分析句子结构，为所有字符类型生成注音（包括汉字、平假名、片假名、英文、数字、符号、空格）。
    - `apply_to_sentence`：将分析结果应用到 Sentence 实体，设置 check_count 和 Ruby。句尾字符允许 check_count=0，此时句尾节奏点以前一个字符的 checkpoint 为开始。
    - `update_checkpoints_from_rubies`：根据用户调整后的注音数据和 auto_check_flags 规则重新计算节奏点，不重新分析注音。调用前需检测并处理逗号分隔的注音字典项。
    - **用户字典优先级**：用户词典优先于库函数分析。词典按从上到下顺序排列，后添加的在顶部（最高优先级）。最长匹配优先确定分词。
    - **单字拆分决策（`_try_split_to_chars`）**：锁定词语读法后，采用三步走策略：Pass 1（约束回溯搜索，优先匹配用户字典与库函数常见度，并加入 pykakasi 候选词）；Pass 2（基于 pykakasi 参考的分词，使用「精确匹配→前缀匹配→无约束回退」三级匹配逻辑）；Pass 3（无约束分区）。配合 `_partition_reading` 辅助函数实现智能读音分配。验证通过则各字独立分配节奏点，否则作为连词（第一字承载全部读音，其余 check_count=0）。
    - **小写假名 check flag**：提供 `small_kana` 开关，控制是否自动为小写假名分配节奏点，默认为 OFF。
    - **句尾判定**：提供 `check_space_as_line_end` 开关（默认 ON），当字符后面紧跟空格时，或者作为 Sentence 的最后一个字符时，将其 `is_sentence_end` 标记设为 True。句尾字符允许 check_count=0（无普通节奏点），此时仅有句尾释放点。注意「句尾」（sentence end）与「行尾」（line end）不同，一行内可以有多个句尾，但只有一个行尾。
    - **全类型 auto_check_flags**：支持按字符类型过滤节奏点（hiragana、katakana、kanji、alphabet、digit、symbol、space 等）。

### ProjectService (项目服务)
管理项目的生命周期和持久化逻辑。

- **职责**：
    - 处理项目的创建 (create)、加载 (load) 和保存 (save)。
    - 提供项目验证 (validate) 和数据统计 (statistics) 功能。

### ExportService (导出服务)
将项目数据转换为不同的外部格式。

- **职责**：
    - 调用基础设施层的导出器生成 LRC（增强型/逐行/逐字）, KRA, TXT, SRT, ASS, Nicokara 等格式。
    - 处理导出时的各种参数设置（如偏移值、演唱者过滤等）。导出器统一使用 `ch.export_timestamps`，故 `ExportService` 内部将导出器的 `_offset_ms` 设为 0。

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

## 数据管理

### ProjectStore (数据中心)
前端统一数据中心，管理项目数据和自动保存。

- **职责**：
    - 集中管理项目数据（Project、音频路径、保存路径）。
    - 通过 `data_changed` 信号统一广播数据变更，各界面按 change_type 刷新。
    - **防抖自动保存**：数据变更后 2 秒无操作时保存到 `.autosave.sug`（仅在有保存路径时）。程序退出时将清理此文件。
    - **定时自动保存**：可配置的周期性保存（默认 5 分钟）。保存到 `{save_path}.temp` 或程序所在目录下的 `untitled.sug.temp`（无保存路径时），每次覆盖。若程序目录不可写，则回退到 `~/.strange_uta_game/`。
    - **配置管理**：默认配置文件存放于程序目录下。支持通过程序目录下的 `.config_redirect` 文件重定向配置位置。About 界面提供「打开目录」和「更改位置」功能以便管理。
    - **独立字典/演唱者存储**：用户字典条目存储在 `dictionary.json`，演唱者预设存储在 `singers.json`，与主配置文件 `config.json` 分离。若不存在 `dictionary.json`，首次启动时自动使用内置 RL 字典（1757 条）进行初始化。重置配置不影响字典和演唱者数据。提供 `load_dictionary()`、`save_dictionary()`、`load_singer_presets()`、`save_singer_presets()` 方法。首次启动时自动迁移旧配置中的字典/演唱者数据到独立文件。
    - **设置自动保存**：所有设置项操作后自动保存（500ms 防抖），无需手动点击保存按钮。使用 `_loading_settings` 标志防止加载时误触发保存。
    - **配置自动重载**：切换标签页至设置界面时，自动从磁盘重新加载配置（`AppSettings.reload()`），确保外部修改（如通过字典功能新增的读音）立即可见。
    - **闪退恢复**：启动时检查 `untitled.sug.temp`，若存在则提示用户恢复上次未保存的工作。用户主动退出时清理 temp 文件。
