# 应用服务层设计

协调 UI 与领域层，执行业务流程。不写业务规则，仅调用 domain / infrastructure。

## 核心服务

### TimingService（打轴服务）

节奏点时间戳的**唯一**写入入口（AGENTS.md 不变量）。

- `on_key_changed(timestamp_ms, key_type)`：统一按键入口，pressed/released 全推给当前选中 cp，由 cp 角色过滤——普通 cp 仅响应 `pressed`，句尾末尾 cp（`is_sentence_end_tail_cp`）仅响应 `released`。写入后单次推进。
- `adjust_current_timestamp(delta_ms)`（批 18 #8）：微调当前 cp 时间戳。普通 cp 分支显式调 `_update_offset_timestamps()` + `push_to_ruby()`，避免 render/export 失同步。
- 播放位置与选中 cp（`Project.selected_checkpoint_*` / `_current_position`）同步维护。
- Offset 校准基于按键按下。打轴时覆盖现有时间戳，不提示时间倒退。

### AutoCheckService（自动分析服务）

- `analyze_sentence`：为所有字符类型生成注音（汉字、假名、英文、数字、符号、空格）。执行链路：用户词典 → e2k 引擎 → 用户词典整词回退 → e2k 静态词表 → 英文 fallback。
- `apply_to_project`：将分析结果写入 Sentence（设置 check_count 和 Ruby）。
- `update_checkpoints_from_rubies`：基于当前注音数据和 auto_check_flags 重算节奏点，不重新分析。批 18 #9 起覆写英文词组节奏点（首=1 cp，中=0，末字标句尾）。
- **节奏点 vs 注音**：两个独立过程。注音流程不含标点；节奏点流程含标点（`checkpoint_on_punctuation` 控制，默认关）。
- **单字拆分（`_try_split_to_chars`）**：Pass 1 约束回溯（用户字典 + 分析器 + pykakasi 候选）→ Pass 2 pykakasi 参考分区（精确→前缀→无约束三级匹配）→ Pass 3 无约束分区。失败则连词（首字承载全部读音，其余 check_count=0）。
- **auto_check_flags**：按字符类型控制节奏点（hiragana / katakana / kanji / alphabet / digit / symbol / space / small_kana / check_n / check_sokuon / check_space_as_line_end / check_parentheses / check_line_start 等）。
- **only_noruby 模式**：`apply_to_project(only_noruby=True)` 跳过已注音字符，用于启动/主页自动注音避免覆盖用户导入。
- 句尾字符允许 check_count=0（无普通 cp，仅靠 sentence_end_ts 结束句子）。

### ProjectService（项目服务）

项目生命周期：create / load / save / validate / statistics。

### ExportService（导出服务）

调用 infrastructure 导出器生成 LRC（增强型/逐行/逐字）/ KRA / TXT / SRT / ASS / Nicokara。导出器统一使用 `ch.export_timestamps`，ExportService 将导出器 `_offset_ms` 强制设为 0 避免双重 offset。

### SingerService（演唱者管理）

演唱者增删改查、颜色、启用状态；同步 Character 级 singer_id。

### CommandManager（撤销/重做）

命令模式封装修改操作，维护 undo/redo 栈。所有破坏性 domain 操作走 CommandManager.execute。

`SentenceSnapshotCommand`（`backend.application.commands.sentence_snapshot`）用于把整句快照式 diff（如 `BulkChangeDialog` 批量字符替换、`fulltext_interface` 同步文本编辑）接入 undo/redo 栈；前端 `frontend/editor/timing/commands._SentenceSnapshotCommand` 是其历史路径别名，仅作兼容。

### ProjectImportService（项目导入辅助）

`backend.application.project_import_service` — 静态服务：

- `load_lyrics_from_file(path, singer_id) -> List[Sentence]`：根据扩展名分派 `LyricParserFactory`，统一把 parser 底层异常包装为 `ProjectImportError`（含原异常 chain）。
- 前端 `timing_interface._load_lyrics_from_path` 改为薄委托，不再自行处理 IO/parser 异常分支。

### CalibrationService（Offset 校准算式）

`backend.application.calibration_service` — 无状态纯函数（不是类，避免伪 OO）：

- `compute_tap_offset_ms(tap_time, metronome_start, beat_interval_ms) -> int`：相位折叠到 `[-beat/2, beat/2)` 返回签名偏移。
- `filtered_average_offset_ms(offsets: Sequence[int]) -> Optional[int]`：`|offset| ≥ beat/2` 剔除（防相位跳点），样本不足返回 `None`。
- `frontend/settings/calibration_dialog` 两个同名私有方法改为薄委托。

## 数据管理

### ProjectStore（前端数据中心）

- 集中管理 Project、音频路径、保存路径；`data_changed(change_type)` 信号统一广播。
- **定时自动保存**：可配置周期（默认 5 分钟），保存到 `{save_path}.temp` 或程序目录 `untitled.sug.temp`。退出清理。
- **防抖设置保存**：设置项变更 500ms 后写盘。`_loading_settings` 标志防止加载触发写入。
- **闪退恢复**：启动检查 `untitled.sug.temp` 提示恢复。
- **配置位置**：默认位于程序目录，通过 `.config_redirect` 重定向到用户自定义目录。配置文件分离为 `config.json`（主配置）、`dictionary.json`（用户词典，首次启动自动种入 1757 条 RL 内置词典）、`singers.json`（演唱者预设）。重置配置不影响字典和演唱者。
- **配置自动重载**：切换到设置标签页时 `AppSettings.reload()`，确保外部修改（字典添加等）立即可见。
