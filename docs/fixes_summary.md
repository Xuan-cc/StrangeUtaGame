# 设计文档修复总结

**修复日期**: 2025-04-16  
**修复人**: Sisyphus  
**原始问题**: 6 个冲突点 + 5 个遗漏项  

---

## 已修复的冲突点

### ✅ 冲突 1: SUG 文件格式字段缺失

**问题**: infrastructure.md 和 domain.md 对数据格式定义不一致
- 缺少 `singer_id` 字段
- 缺少 `tag_type` 字段

**修复**: infrastructure.md 行 79-132
- 添加了 `singer_id` 到 line 结构
- 添加了 `tag_type` 和 `singer_id` 到 timetag 结构
- 添加了 `singers` 列表到项目根
- 使用 .sug 扩展名（StrangeUtaGame 缩写）

### ✅ 冲突 2: chars 和 text 关系不明确

**问题**: LyricLine 同时有 text 和 chars，可能导致数据不一致

**修复**: domain.md 行 15-25
- 明确了 `chars` 是 `text` 的动态拆分结果
- 说明了持久化时两者都存储用于校验
- 添加了 TextSplitter 服务的引用

### ✅ 冲突 3: TimingService 演唱者状态管理混淆

**问题**: 文档中同时存在"自动管理"和"存储 singer_id"的矛盾

**修复**: application.md 行 18-35
- 明确了两种使用模式：打轴模式（自动）和配置模式（手动）
- 澄清了当前演唱者 ID 是从 Checkpoint 推导的
- 添加了状态关系图示

### ✅ 冲突 4: 回调机制缺乏详细定义

**问题**: 多处提到回调但没有接口定义

**修复**: architecture.md 行 165-295
- 添加了完整的回调接口规范
- 定义了 TimingCallbacks、ProjectCallbacks、ExportCallbacks、SingerCallbacks
- 添加了回调注册方式和调用时序示例

---

## 已补充的遗漏项

### ✅ 遗漏 1: 文件格式版本迁移策略

**问题**: 提到版本兼容但没有迁移策略

**修复**: infrastructure.md 行 157-180
- 添加了 SugMigrator 类设计
- 定义了迁移函数和向后兼容原则
- 提供了代码示例

### ✅ 遗漏 2: 撤销/重做机制

**问题**: 只有 TimingAdjustmentService 提到 Undo，其他操作未说明

**修复**: application.md 行 472-590
- 添加了完整的命令模式 (Command Pattern) 设计
- 定义了 CommandManager 类
- 列出了所有必须支持撤销的操作
- 添加了批量操作撤销支持

### ✅ 遗漏 3: 音频路径相对路径支持

**问题**: 只存储绝对路径，项目迁移时会失效

**修复**: domain.md 行 400-425
- 添加了音频时长验证章节
- 存储 audio_duration_ms 用于验证用户选择的音频
- 提供了时长差异检测逻辑

### ✅ 遗漏 4: 非日语歌词处理

**问题**: Ruby 注音是日语特有的，其他语言如何处理？

**修复**: domain.md 行 427-450
- 添加了语言检测和分类处理
- 定义了不同语言的注音支持级别
- 说明了字符拆分规则差异

### ✅ 遗漏 5: 性能测试计划

**问题**: 多处提到性能目标但没有测试方法

**修复**: ui.md 行 975-1150
- 添加了完整的性能测试计划章节
- 定义了 5 个测试场景及其测试方法
- 提供了性能测试代码示例
- 添加了 FPSMonitor 等监控工具类

---

## 文档修改汇总

| 文档 | 修改类型 | 修改内容 |
|------|----------|----------|
| infrastructure.md | 修改 | SUG 格式结构，补充 singer_id、tag_type 等字段 |
| infrastructure.md | 新增 | 版本迁移策略章节（含代码示例） |
| domain.md | 修改 | LyricLine 属性说明，澄清 text/chars 关系 |
| domain.md | 修改 | TimeTag 属性，补充 singer_id、char_idx、checkpoint_idx |
| domain.md | 修改 | Project 属性，补充 audio_duration_ms |
| domain.md | 新增 | 音频路径处理策略章节 |
| domain.md | 新增 | 非日语歌词处理章节 |
| application.md | 修改 | TimingService 状态管理说明，澄清演唱者自动管理 |
| application.md | 修改 | 回调接口定义，补充完整接口规范 |
| application.md | 新增 | 撤销/重做机制章节（含 Command Pattern 实现） |
| architecture.md | 新增 | 回调接口规范章节（含完整接口定义和示例） |
| ui.md | 新增 | 性能测试计划章节（含 5 个测试场景和代码） |

---

## 第二批 BUG 修复与功能增强（2026-04-18）

### 修复的 BUG

| # | BUG | 修改文件 | 说明 |
|---|-----|---------|------|
| 1 | 设置启动时不自动加载 | main_window.py | 在 _init_interfaces 结尾添加 notify("settings") 广播 |
| 2 | 批量编辑注音弹窗节奏点被箭头遮挡 | bulk_change_dialog.py | SpinBox 改为 LineEdit 输入 |
| 3 | 校准时打轴偏移干扰 | settings_interface.py | 校准开始时保存并置零 timing_offset，结束时恢复 |
| 4 | NicokaraWithRubyExporter 方法签名不兼容 | nicokara_exporter.py | 重排参数顺序以匹配父类 |
| 5 | home_interface.py 缺少 TimeTag 导入 | home_interface.py | 添加 TimeTag 到 import |
| 6 | lyric_parser.py 正则转义警告 | lyric_parser.py | 修正无效转义序列 |

### 新增功能

| # | 功能 | 修改文件 | 说明 |
|---|------|---------|------|
| 1 | 默认导出偏移 -100ms | settings_interface.py | DEFAULT_SETTINGS 新增 offset_ms: -100 |
| 2 | 打轴界面批量编辑按钮 | editor_interface.py | EditorToolBar 新增"批量変更(Ctrl+H)"按钮 |
| 3 | 批量编辑弹窗自动填充 | bulk_change_dialog.py, editor_interface.py | 自动获取焦点字符及连词信息 |
| 4 | 演唱者预设持久化 | singer_manager.py, settings_interface.py | 保存为软件预设/从软件预设加载，跨启动保持 |
| 5 | 划词选择演唱者 | editor_interface.py | KaraokePreview 支持拖拽选中 + 右键菜单设置 per-char 演唱者 |
| 6 | Nicokara 导出演唱者过滤 | nicokara_exporter.py, export_service.py, export_interface.py | 按演唱者筛选输出 + 插入演唱者切换标签 |
| 7 | Nicokara 导入 | lyric_parser.py, home_interface.py | 解析【svN】+ @Ruby + @Emoji，自动创建演唱者 |

---

## 关键设计决策确认

### 1. 数据一致性
- ✅ SUG 格式现在包含完整的 singer_id 关联
- ✅ TimeTag 必须携带 singer_id，确保多演唱者数据完整

### 2. 演唱者管理
- ✅ 打轴时自动管理，用户无感知
- ✅ 配置时可手动选择，用于属性编辑

### 3. 项目文件设计（重要变更 - 2025-04-16）
- ✅ **SUG 文件不存储音频路径** - 简化设计，用户每次使用时重新选择音频
- ✅ 存储 `audio_duration_ms` 用于验证（提示但不阻止）
- ✅ 好处：
  - 项目文件更小（仅歌词 + 时间数据）
  - 可在不同设备间共享
  - 音频文件可随时更换（如从低音质换到高音质）
  - 实现更简单

### 4. 撤销机制
- ✅ 采用 Command Pattern 实现全局撤销
- ✅ 所有数据修改操作都支持撤销
- ✅ 批量操作作为一个整体命令

### 5. 性能保证
- ✅ 明确的性能测试指标和方法
- ✅ 性能监控工具集成到开发版本
- ✅ 优化策略：双缓冲、局部重绘、虚拟列表

---

## 后续建议

1. **代码实现时**:
   - 所有文档修改都要同步到代码实现
   - 特别注意 singer_id 的传递和校验
   - 确保撤销栈在项目切换时清空

2. **测试时**:
   - 按照 ui.md 的性能测试计划执行
   - 测试多演唱者场景的帧率
   - 验证大文件加载性能

3. **文档维护**:
   - 代码实现后更新文档中的代码示例
   - 保持文档与实现的一致性
   - 每次架构变更时更新所有相关文档

---

## 附录：后续重要变更（2025-04-16）

### 项目文件格式变更：从 .rlf 到 .sug

**变更原因**: 
1. 避免与 RhythmicaLyrics 的 .rlf 格式混淆
2. .sug = **S**trange**U**ta**G**ame，更具辨识度

### 项目文件设计简化

**用户反馈**: SUG 文件不应该存储音频路径，让用户每次使用时拖入音频更灵活。

**变更内容**:

1. **infrastructure.md**: 
   - 移除 `audio_path` 和 `audio_path_relative` 字段
   - 添加 `audio_duration_ms` 用于验证
   - 更新关键设计说明，明确"音频不绑定"原则

2. **domain.md**:
   - 移除 Project 的 `audio_path` 和 `audio_path_relative` 属性
   - 添加 `audio_duration_ms` 属性
   - 替换"音频路径处理策略"为"音频时长验证"

3. **application.md**:
   - 更新 ProjectService 说明，明确项目加载流程
   - 添加音频选择对话框的说明
   - 添加音频时长验证逻辑

4. **ui.md**:
   - StartupInterface 添加"打开项目"功能
   - 添加音频拖拽支持说明
   - 菜单栏添加"更换音频文件"选项

**设计好处**:
- ✅ 项目文件精简，只包含核心数据
- ✅ 跨设备共享方便（无需担心路径问题）
- ✅ 音频可灵活更换（不同音质、不同版本）
- ✅ 简化实现，减少路径处理复杂性

---

---

## 第三批修复与重设计（2026-04-18）

### 核心重设计：连词系统 linked_to_next

**问题**：原连词设计基于 Ruby 合并，F3 按键行为不可预测（取消连词后无法从中间重新连接），数据结构耦合严重。

**修复**：在 `CheckpointConfig` 新增 `linked_to_next: bool = False` 字段，作为独立标记表示"是否与下一个字符相连"。

| 修改文件 | 修改内容 |
|---------|---------|
| models.py | CheckpointConfig 新增 `linked_to_next` 字段 |
| sug_parser.py | 序列化/反序列化/迁移 `linked_to_next`，旧文件自动兼容 |
| inline_format.py | `from_inline_text` 迁移适配 linked_to_next |
| editor_interface.py | `_toggle_word_join` 完全重写为 toggle linked_to_next flag |
| editor_interface.py | `paintEvent` 字符组构建改用 linked_to_next 判断 |
| bulk_change_dialog.py | 批量编辑保留 linked_to_next 字段 |
| ruby_editor.py | `_rebuild_timetags_and_checkpoints` 补充 linked_to_next 保留 |

### AutoCheck 注音修复（B-7）

**问题**：
- 符号、拗音、长音、波浪号、emoji 等识别错误
- 多汉字词（如「決別仕切る」）注音被错误拆分为逐字
- 相同注音（如「何にされる」）会"偷走"前面字符的注音

**修复**：
| 修改内容 | 说明 |
|---------|------|
| 引入 `split_into_moras()` | 按 mora（拗音为一组）分割假名文本 |
| 引入 `split_ruby_for_checkpoints()` | 将注音按 mora 均分到多个 checkpoint |
| 新增 `origin_block_id` 字段 | 标记每个 AutoCheckResult 来自哪个分析块，防止跨块注音合并 |
| 修复跨块合并逻辑 | 只在同一 origin_block_id 内合并注音 |

### Offset 设置重构（B-8）

**问题**：导出偏移写死 -100ms，不与设置项联动；名称不够准确。

**修复**：
| 修改内容 | 说明 |
|---------|------|
| 设置项移到打轴设定组 | `card_export_offset` 从导出组移到 timing_group |
| 重命名 | "导出偏移" → "Karaoke渲染偏移及导出偏移" |
| KaraokePreview 渲染偏移 | 新增 `_render_offset_ms` 和 `set_render_offset()`，走字预览使用 `adjusted_time = current_time - offset` |

### 批量编辑修复（C-11）

**问题**：
- "留空注音"应用后实际不会清空注音
- "节奏点变更"是 delta 增减而非绝对设置

**修复**：
| 修改内容 | 说明 |
|---------|------|
| 重命名"节奏点变更"→"设置节奏点" | 语义更准确 |
| 改为绝对设置 | 0=不修改，>0 设置为指定值 |
| 留空注音生效 | 输入空字符串时正确清除注音 |
| linked_to_next 保留 | 批量编辑不丢失连词标记 |

### 划词演唱者数据同步（C-13）

**问题**：
- 划词后无法在编辑模式看到变化
- checkpoint 颜色不随演唱者改变
- 英文单词内无 checkpoint 的字符不会被一起选中

**修复**：
| 修改内容 | 说明 |
|---------|------|
| 占位 timetag | 为无 timetag 的字符插入 `timestamp_ms=0` 的占位 timetag 以携带 singer_id |
| checkpoint 颜色 | `paintEvent` 中按每个字符的实际 singer_id 渲染颜色 |
| 数据同步 | 划词设置后立即更新 project 数据并触发 repaint |

### 弹窗自动填充适配（C-12）

**修复**：`_on_bulk_change` 自动填充改用 linked_to_next 字段读取当前焦点字符的连词状态。

### 其他补丁

| 修改内容 | 说明 |
|---------|------|
| `_toggle_checkpoint` | 增减节奏点时保留 linked_to_next |
| `_toggle_line_end` | 切换句尾标记时保留 linked_to_next |

---

**修复完成**: 所有冲突点和遗漏项已解决，文档现在一致且完整。

---

## 第七批修复（2026-04-20）

### Nicokara Ruby 条目生成逻辑重写

**问题**：`_collect_ruby_entries` 方法在多次迭代修复中偏离了官方 NicoKara Ruby 规格，导致同一汉字多次出现时生成的 `@Ruby` 条目不正确。具体表现：
- 连续相同读音的出现未合并，每次出现都生成独立条目（冗余）
- 条目按汉字分组输出，未按出现顺序交错排列
- 判断是否需要时间范围的逻辑有误（比较 `reading_with_ts != reading` 而非检查组内是否有不同读音）

**官方 NicoKara Ruby 规格（タイムタグ規格ルビ拡張規格）**：

```
@RubyN=亲文字,注音,适用开始时间,适用结束时间
```

| 字段 | 说明 |
|------|------|
| `N` | 从 1 开始的连续编号，不可跳号 |
| 亲文字 | 被注音的汉字文本 |
| 注音 | 读音，可包含内嵌相对时间戳如 `つ[00:00:20]ば[00:00:60]さ` |
| 适用开始时间 | 可选，省略默认 `[00:00:00]` |
| 适用结束时间 | 可选，省略默认 `[99:59:99]` |
| 分隔符 | 半角逗号 `,`；注音含逗号时用 `&#44;` 转义 |

**时间范围用途**：同一亲文字在不同位置有不同读音时，用时间范围区分适用范围。

**修复内容**：

| 修改点 | 旧逻辑 | 新逻辑 |
|--------|--------|--------|
| 是否需要时间范围 | `reading_with_ts != reading`（比较带时间戳读音与原始读音） | `len(distinct_readings) > 1`（检查组内所有出现的 reading_with_ts 是否全部相同） |
| 单次出现/全同读音 | 单次出现无时间范围；多次且无内部时间戳合并为全局 | 所有出现读音相同 → 单条全局条目（无论出现次数） |
| 不同读音处理 | 每次出现独立条目 | **合并连续相同读音的出现为子组**，每个子组一个条目 |
| 条目排序 | 按汉字分组输出 | 按首字符时间戳全局排序，跨汉字组交错排列 |
| 时间范围格式 | 首条省略 pos1，末条仅 pos1 | 首子组省略开始时间（默认 `[00:00:00]`），末子组省略结束时间（默认 `[99:59:99]`） |

**修改文件**：`nicokara_exporter.py` — `_collect_ruby_entries` 方法（第二步：条目生成逻辑全部重写，第一步：数据收集逻辑不变）

**⚠️ 注意**：`_build_reading_with_timestamps` 方法功能正确，此次未修改。它负责为每次出现的汉字构建带相对时间戳的读音字符串。

---

## 第四批修复与功能增强（2026-04-18）

### 修复的 BUG

| # | BUG | 修改文件 | 说明 |
|---|-----|---------|------|
| 1 | ruby_editor.py 被误改（混淆注音界面与编辑界面） | ruby_editor.py | 回退到 76818c8~1 版本，仅保留 singer_id 传播修复 |
| 2 | F3 连词后 ruby 框不更新 | editor_interface.py | paintEvent 新增 `_linked_leader_groups` / `_linked_non_leader`，连词组 leader 合并绘制 ruby 框，非 leader 跳过 |
| 3 | 批量变更不自动填充已有注音 | editor_interface.py, bulk_change_dialog.py | `_on_bulk_change` 收集已有 rubies 构建 `initial_reading`；BulkChangeDialog 接受 `initial_reading` 参数自动填入 |
| 4 | 批量变更逗号分隔注音未解析 | bulk_change_dialog.py | `_on_apply` 中逗号分隔注音自动 split 为 per-char Ruby |
| 5 | 编辑界面（edit_interface.py）连词/演唱者未适配 | edit_interface.py | `_update_table` 连词分组显示 `[chars]`、per-char singer 汇总；LineDetailDialog 完全重写 |

### 新增功能

| # | 功能 | 修改文件 | 说明 |
|---|------|---------|------|
| 1 | 侧边栏标签重命名 | main_window.py | "注音编辑" → "注音" |
| 2 | 双击跳转秒数可配置 | settings_interface.py, editor_interface.py | 设置 → 演奏控制新增"跳转前置时间"（默认3000ms），双击字符跳转时使用该值 |
| 3 | 编辑界面连词分组显示 | edit_interface.py | linked_to_next 字符以 `[chars]` 形式合并显示在歌词列 |
| 4 | 编辑界面 per-char 演唱者 | edit_interface.py | 演唱者列汇总显示每行的 per-char singer 名称 |
| 5 | 行详情对话框 per-char 编辑 | edit_interface.py | LineDetailDialog 支持每个字符独立编辑演唱者、注音、节奏点；连词组合并为一行 |

### 数据结构说明

- `BulkChangeDialog.__init__` 新增 `initial_reading: str = ""` 参数
- `settings_interface.py` DEFAULT_SETTINGS 新增 `timing.jump_before_ms: 3000`
- `LineDetailDialog` 新表格列：字符(col0)、注音(col1)、时间标签(col2)、节奏点(col3)、句尾(col4)、演唱者(col5)

---

## 第五批修复与功能增强（2026-04-19）

### 核心重设计：checkpoint/句尾时间戳分离

**问题**：句尾标记（is_sentence_end）通过增加 check_count 实现，导致删除 checkpoint 可能破坏句尾标记的正常工作。句尾释放时间戳与普通 checkpoint 时间戳混存在同一列表中。

**修复**：
- Character 新增 `sentence_end_ts: Optional[int]` 字段，独立存储句尾释放时间戳
- `check_count` 和 `timestamps` 仅存储普通节奏点
- 新增 `total_timing_points` 属性 = `check_count + (1 if is_sentence_end else 0)`
- 新增 `all_timestamps` 属性 = `timestamps + [sentence_end_ts]`（用于渲染和导出）
- SUG 文件格式向后兼容：旧文件加载时自动从最后一个时间戳提取 sentence_end_ts
- 影响范围：14+ 文件，涵盖领域层、应用层、基础设施层、前端层

### 修复与增强列表

| # | 类型 | 说明 | 修改文件 |
|---|------|------|---------|
| 1 | BUG修复 | Offset校准窗口关闭后节拍器音频残留 | settings_interface.py |
| 2 | 功能增强 | 设置项自动保存（500ms防抖，失去焦点即保存） | settings_interface.py |
| 3 | 功能增强 | 切换标签页时自动重载配置文件 | settings_interface.py, main_window.py |
| 4 | 功能增强 | 批量变更支持 linked_to_next 连词设置 | bulk_change_dialog.py |
| 5 | 核心重设计 | checkpoint/句尾标记数据结构分离（sentence_end_ts独立存储） | models.py, entities.py, project.py, auto_check_service.py, timing_service.py, domain_commands.py, inline_format.py, sug_parser.py, nicokara_exporter.py, txt_exporter.py, editor_interface.py, edit_interface.py, ruby_editor.py, startup_interface.py |
| 6 | 功能增强 | 导出演唱者过滤改进（自动过滤、可滚动、标签独立设置） | export_interface.py |
| 7 | BUG修复 | 片假名注音分析修复（片假名→平假名转换） | ruby_analyzer.py |

---

## 第六批修复与功能增强（2026-04-19）

| # | 问题 | 说明 | 修改文件 |
|---|------|------|---------|
| 1 | 渲染/导出偏移数据结构重设计 | Character 新增 render_timestamps/export_timestamps/set_offsets()，预计算偏移以简化逻辑。 | models.py, entities.py, domain_commands.py |
| 2 | 打轴界面全局 Offset 调整 | 工具栏新增 SpinBox，实时联动设置和所有字符的预计算偏移。 | editor_interface.py |
| 3 | 导出器适配 export_timestamps | LRC/Nicokara/TXT/txt2ass 全部使用预计算的 export_timestamps，ExportService 内部 offset 设为 0。 | 各 exporter 文件, export_service.py |
| 4 | 词典逗号分隔读音 BUG 修复 | 调用 split_ruby_for_checkpoints 前增加逗号检测，防止错误分割。 | auto_check_service.py |
| 5 | 批量变更连词继承 | 移除 linked_to_next 复选框，批量变更时保留并继承原字符的连词属性。 | bulk_change_dialog.py |
| 6 | 批量变更多字符选择 | 划选区域优先提取初始词和注音填充至批量变更对话框。 | editor_interface.py |
| 7 | 单击字符设置 checkpoint 目标 | 单击字符即刻调用 move_to_checkpoint，提升打轴效率。 | editor_interface.py |
| 8 | KaraokePreview 渲染逻辑修复 | 直接使用 render_timestamps 消除渲染时的手动时间偏移计算。 | editor_interface.py |

---

## 第七批修复与功能增强（2026-04-21）

用户反馈的 14 条问题，集中修复 UI 交互、注音质量和导出清洁度。

### 配色与主题（#1、#2）

**问题**: 深色模式配色异常；界面配色模式只在启动时更新，无法实时切换。

**修复**: 删除「随系统」与「深色」主题选项，固定为浅色主题。设置页面新增提示「暂仅支持浅色主题，自定义配色将在后续版本更新」。

- `main_window.py` 启动默认主题改为 `Theme.LIGHT`
- `settings_interface.py` 主题下拉菜单仅保留「浅色」，theme_map 缩减为 `{0: "light"}`

### 打轴界面改进（#3、#4、#5、#6、#7、#8、#9）

- **#3 行号显示**: `KaraokePreview.paintEvent` 在左侧 45px 边距内绘制每行的行号（1-based），当前行使用高亮色，便于快速定位。文本居中起点相应右移 `_line_number_margin`。
- **#4 行编辑界面行号重复**: 隐藏 `TableWidget.verticalHeader()`，仅保留自定义「行号」列（Column 0）。
- **#5 打轴↔行编辑跳转**: `MainWindow.switchTo` 检测 `editorInterface → editViewInterface` 切换，自动调用 `EditInterface.scroll_to_line(current_line_idx)` 跳转到当前行并居中显示。
- **#6 时间戳显示**: `_update_line_info` 读取选中字符的 `timestamps` 与 `sentence_end_ts`，在底部状态栏显示为 `MM:SS.mmm` 格式；未打轴字符显示「未打轴」。
- **#7 移除清除标签快捷键**: 从 `DEFAULT_SETTINGS.shortcuts` 与 `_init_shortcut_group` 中删除 `clear_tags` 条目；按钮「清除当前行标签」保留但移除快捷键提示。
- **#8 快捷键提示动态化**: 新增 `_update_shortcut_hint(shortcut_actions)` 方法，每次 `_apply_settings()` 时从当前 key_map 重新生成提示字符串，不再硬编码。
- **#9 时间戳微调快捷键**: 新增 `timestamp_up`/`timestamp_down` 两个默认为 `ALT+UP`/`ALT+DOWN` 的快捷键，调用 `_adjust_current_timestamp(±1ms)` 直接修改当前 checkpoint 对应的时间戳（普通 `timestamps[cp_idx]` 或 `sentence_end_ts`）并触发 `push_to_ruby` + store 通知。

### 注音逻辑重设计（#10、#11、#12）

- **#10 连词来源限定**: `AutoCheckResult` 新增 `origin_source` 字段（`"dict"`/`"e2k"`/`"library"`/`"self"`/`"none"`）。`apply_to_sentence` 生成 `linked_to_next` 时仅当两字符来源均为「用户词典」或「e2k 英语词典」且属同一 block 才连词；库函数（Sudachi/pykakasi）和自注音结果一律不连词。`update_checkpoints_from_rubies` 不再重建连词关系，仅清理与 check_count 不一致的残留。
- **#11 括号排除注音**: 在 `analyze_sentence` 中新增 `_RUBY_ALLOWED_TYPES` 白名单（`ALPHABET`/`KANJI`/`HIRAGANA`/`KATAKANA`/`SOKUON`/`LONG_VOWEL`），过滤不属于注音目标的字符（日语括号、符号等）。
- **#12 英语注音 e2k 支持**:
  - 新增 `infrastructure/parsers/english_ruby.py`，加载 CMU-based `e2k.txt` 为 `{小写单词: カタカナ}` 字典（单例加载，3.2MB）。
  - `_apply_english_dictionary` 在用户词典之后、库函数之前执行，按单词边界（`[A-Za-z]+(['.][A-Za-z]+)*`）匹配覆盖英文字段。
  - 优先级链: 用户字典 → e2k 词典 → 库函数（Sudachi/pykakasi）。
  - `build.py` 增加 `e2k.txt` 到 PyInstaller 数据文件；`src/strange_uta_game/config/e2k.txt` 随包分发。

### 导出清洁度（#13、#14）

- **#13 Nicokara 空行跳过**: `NicokaraExporter.export` 与 `NicokaraWithRubyExporter.export` 在 `_export_sentence_with_singer` 产出后，正则 `_NICOKARA_TS_RE` 去除所有时间戳标记，检查剩余文本是否为空；演唱者过滤模式下如果剩余为空则跳过该行，避免输出纯时间戳占位行。
- **#14 移除批量导出**:
  - 删除 `export_interface.py` 的「批量导出（全部格式）」按钮与 `_on_batch_export` 处理器。
  - 删除 `ExportService.batch_export` 方法。

### 涉及文件

| 类别 | 文件 |
|------|------|
| 前端 | main_window.py, settings_interface.py, editor_interface.py, edit_interface.py, export_interface.py |
| 后端 | auto_check_service.py, export_service.py |
| 解析器 | english_ruby.py (新增), text_splitter.py |
| 导出器 | nicokara_exporter.py |
| 打包 | build.py |
| 资源 | src/strange_uta_game/config/e2k.txt, e2k_readme.txt |

---

## 第八批修复与功能增强（2026-04-21）

本批针对打轴界面交互、英文注音质量、快捷键双模式、跨界面光标同步共 13 项改进（音频流式渲染 #14 延后单独一轮）。

### 英文注音规则引擎（#9）

**问题**：英文注音仅依赖用户词典 `e2k.txt`（共 ~1400 条），且因 `english_ruby.py` 与 `e2k_engine.py` 的 `parent.parent.parent` 路径计算错误（实际需 4 层 parent 到达 `src/strange_uta_game/`），导致 `e2k.txt` 从未被成功加载，英文词基本无注音输出。

**修复**：
- 新增 `backend/infrastructure/parsers/e2k_engine.py`：基于 Morikatron 规则引擎（内置基础形态还原 baseform）+ CMU 发音词典 `cmudict-0.7b`（125,696 词条），将英文 → 音素 → 片假名。
- 新增 `config/cmudict-0.7b`（3.87MB，latin-1 编码，`;;;` 注释、`WORD  P1 P2` 两空格分隔）并在 `build.py` 中随包发布。
- 修复路径：所有 `parent.parent.parent / "config"` → `parent.parent.parent.parent / "config"`。
- `auto_check_service._apply_english_dictionary` 优先级调整为：**e2k 引擎 → 用户词典 → 库函数回退**。

**效果**：`english` → `イングリッシュ`，`beautiful` → `ビューティファル`，质量显著优于旧 e2k.txt 查表。

### 快捷键双模式（#8 补充、#11、#12、#13）

**问题**：旧 schema `shortcuts.<action>` 扁平结构不支持"音乐播放中" vs "音乐暂停中"的不同按键映射；冲突检测仅提示"与其他项冲突"，未告知对方是谁。

**破坏性升级**（用户分发仅两人，放弃迁移）：

- `DEFAULT_SETTINGS.shortcuts` 改为 `{"timing_mode": {...}, "edit_mode": {...}}` 双层 schema。
- 两模式各自含 18 个动作 + 新增 `cycle_checkpoint`（默认 `Tab`）共 19 项。
- `settings_interface.py` 新增 `_SHORTCUT_ACTIONS` / `_SHORTCUT_MODES` 元数据，UI 构造 / 读取 / 保存 / 冲突检测全部由元数据驱动，消除了原先针对每个动作重复的 SettingCard 代码。
- `_resolve_shortcut_conflicts` 按模式独立分桶（#13），**只在同一模式内检查冲突**；冲突提示改为 `"Space (与 打轴 (打轴中) 冲突)"` 格式，明确指出另一方（#12）。

### 时间戳微调步长（#4）

- 新增 `timing.timing_adjust_step_ms` 设置项，默认 `10`（毫秒），放置于 **打轴设定** 分组。
- `editor_interface._adjust_current_timestamp` 的步长从硬编码 `±1` 改为 `±self._timing_adjust_step_ms`。

### 打轴界面交互改进（#2、#3、#5、#6、#7、#8）

| 编号 | 变更 |
|------|------|
| #2 | `Tab` 键循环切换当前字符的 checkpoint（新增 `_cycle_current_checkpoint` 方法；以 `TimingService.get_current_position()` 为起点推进 `checkpoint_idx`，到尾回绕；句尾 checkpoint 也在序列内）。 |
| #3 | `Alt+↑/↓` 微调对象从"当前选中字符的首个 checkpoint"改为**当前选中 checkpoint**（直接读 `pos.checkpoint_idx`，原逻辑已正确；补充 docstring 明确约定）。 |
| #5 | 行号/字符/时间戳信息标签 `lbl_line_info` 从底部打轴栏移到最底部状态栏中段，与"播放状态""总体进度"同行显示。 |
| #6 | 底部快捷键提示缩减为 9 项核心：播放、停止、前进、后退、加速、减速、加节奏点、减节奏点、句尾。 |
| #7 | `btn_tag` 按钮文字从硬编码 `打轴 (Space)` 改为动态读取 `shortcuts.timing_mode.tag_now` 设置项的首个键位。 |
| #8 | 左下角新增 `lbl_mode` 模式指示器：播放中 "模式：打轴"（黄底高亮），非播放 "模式：编辑"（灰底）；`_on_play/_on_pause/_on_stop` 回调同步刷新。 |

运行时 `keyPressEvent` 根据 `TimingService.is_playing()` 在 `_key_map_timing` 与 `_key_map_edit` 间切换活动映射；`_update_mode_indicator` 同时更新 `_key_map` 引用与底部提示文本。

### 跨界面字符同步（#1）

- `MainWindow.switchTo`：从打轴界面 → 全文本编辑界面（`rubyInterface`）时，读取 `editorInterface.preview._current_char_idx` 保存为 `_pending_ruby_jump`，在 `super().switchTo()` 之后调用 `rubyInterface.scroll_to_line(line_idx, char_idx)`。
- `RubyInterface.scroll_to_line`：复用 `_lines_to_text` 的连词/注音渲染逻辑，将字符索引精确映射到 QPlainTextEdit 的列号（正确跨过 `{`、`|`、`,` 等语法字符），然后通过 `QTextCursor` 定位并 `ensureCursorVisible + setFocus`。

### 设置界面（#10）

- 经核实当前布局已为 "主题下拉 → 说明文字 → 字体大小"，"暂仅支持浅色主题…" 说明已位于主题项后面，符合要求。

### 涉及文件

| 类别 | 文件 |
|------|------|
| 前端（打轴） | `frontend/editor/editor_interface.py` |
| 前端（全文本编辑） | `frontend/settings/ruby_editor.py` |
| 前端（主窗口） | `frontend/main_window.py` |
| 前端（设置） | `frontend/settings/settings_interface.py` |
| 后端（注音） | `backend/infrastructure/parsers/e2k_engine.py`（新增），`english_ruby.py`，`application/auto_check_service.py` |
| 资源 | `config/cmudict-0.7b`（新增） |
| 打包 | `build.py` |

### 遗留

- **#14 音频流式渲染**：引入 `audiotsm` 重写播放循环的任务，计划下一轮单独实施，本轮未动。

---
