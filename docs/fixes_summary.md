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

- **#14 音频流式渲染**：已在后续批次实施完成，详见「第九批」。

---

## 第九批修复与功能增强（2026-04-21，#14 音频流式渲染）

### 背景

此前 `SoundDeviceEngine` 采用「Phase Vocoder 后台预渲染 + 线性插值回退 + 32ms 淡入拼接」
的架构。实测表明：在动态拖动倍速滑块（尤其 0.5~0.75 区间）时，即便有淡入过渡，
仍会听到短促的"采样碎裂声"。根因是 Phase Vocoder 在变速瞬间切换数据源时，
预渲染数据与回退插值之间存在相位不连续。

用户与作者协同调研（见 `音频播放优化问答.md`）后确认：在纯 Python 生态中，
`audiotsm` 的 WSOLA 算法是这一场景的最佳方案——它是**状态保持（stateful）**
的流式 TSM 对象，内部维护 overlap-add 缓冲区，可在 `set_speed()` 调用时
基于上一周期相位平滑衔接下一周期。

### 重构方案

以 `audiotsm.wsola` 完全替换原 Phase Vocoder 路径。架构三要素：

| 组件 | 作用 |
|------|------|
| `self._original_data` | 预加载的原始 PCM（不再保留 `self._data` 变速副本） |
| `self._tsm` | 整个生命周期复用的 WSOLA 状态机 |
| `self._reader` | 从当前虚拟播放头切片出的 `ArrayReader` |
| `self._reader_pos_samples` | 以原始采样为基准的权威播放头 |

### 关键设计点

1. **回调内同步拉取**：`_render_frames(outdata, frames)` 循环调用
   `tsm.read_from(reader)` + `tsm.write_to(writer)` 直到拿够 frames 或 EOF，
   不再依赖后台预处理线程。
2. **速度切换无重建**：`set_speed` 仅调 `self._tsm.set_speed(speed)`，
   不销毁 TSM，不清空缓冲区；WSOLA 自行平滑过渡，彻底消除"碎裂感"。
3. **Seek 触发重建**：`set_position_ms` 调 `_rebuild_tsm_and_reader` 重新
   切片 + 新建 WSOLA（因为跨越非连续位置时旧相位不再有效）。
4. **位置推进基于消耗**：`read_from` 返回实际消耗的原始采样数，据此推进
   `_reader_pos_samples`，再折算为毫秒。位置始终以**原始音频时间**表达，
   不受倍速影响（便于与时间戳标签对齐）。
5. **防死循环护栏**：`stuck_counter > 3` 无进展即退出渲染循环，避免
   `read_from` 偶发返回 0 时挂死音频线程。

### 代码变更

- 完全重写 `src/strange_uta_game/backend/infrastructure/audio/sounddevice_engine.py`：
  - 删除 `_apply_speed_stretch` / `_time_stretch`（Phase Vocoder）
  - 删除 `_callback_stretched` / `_callback_interp`（双模式回调分支）
  - 删除 `_stretched_speed` / `_stretch_version` / `_switch_fade_samples`
    （Phase Vocoder 状态）
  - 新增 `_rebuild_tsm_and_reader(start_sample)`
  - 新增 `_render_frames(outdata, frames)`
  - 保留 `IAudioEngine` 接口契约（外部调用方无需改动）
- `requirements.txt`：新增 `audiotsm>=0.1.2`
- `build.py`：新增 `audiotsm` 依赖探测、`--hidden-import=audiotsm` 和
  `--collect-all=audiotsm`
- `README.md`：变速功能描述由 "Phase Vocoder" 改为 "WSOLA 流式变速不变调，
  实时拖动无爆音"；FAQ 改写；技术栈补充 `audiotsm`

### 验证（独立冒烟测试）

```
loaded. duration_ms=3000 channels=2 sr=44100
speed=1.0: nonzero=8818/8820, max=0.3000, pos_ms=150
speed=0.75: nonzero=8819/8820, max=0.3000, pos_ms=232  # ΔMs=82（比 1.0 少，符合慢速）
speed=1.5:  nonzero=8820/8820, max=0.3000, pos_ms=380  # ΔMs=148（比 1.0 多，符合快速）
after seek 1500ms → 1708ms（继续推进正常）
```

立体声振幅未爆音（峰值严格 ≤ 0.3 源信号），seek 后继续渲染正常，
连续 set_speed 无爆音无 underflow。

### 遗留 / 未来优化

- `sd.OutputStream` 回调频率较高（~23ms @ 44.1kHz / 4410 blocksize），
  若用户 CPU 较弱且音频极长（>10 分钟），单次 WSOLA 分析可能偶发抖动。
  必要时可将 blocksize 从 100ms 增大到 200ms 缓冲更厚。
- 当前 WSOLA 默认参数（帧长、分析 hop）为 audiotsm 默认值。若未来遇到
  极端素材（大量打击乐瞬态），可尝试 `audiotsm.phasevocoder` 对比。

---

## 第十批改动（2026-04-21）

本批改动覆盖 11 项用户反馈（#1~#11），主要修复快捷键/注音/右键菜单/字典/
结构化编辑等交互问题，并引入 Domain 层结构化编辑 API，保持分层架构零
跨层污染。

### 功能改动

#### #1 Checkpoint 循环键与高亮补色
- `cycle_checkpoint` 默认键由 `Tab` 改为 `ALT+RIGHT`（对 Tab 焦点切换更友好）
- 当前 checkpoint marker 使用**演唱者颜色的 HSV 补色**高亮，保持
  marker 形状/大小/描边不变，仅换色，便于识别当前选中节奏点
- `KaraokePreview` 新增 `_current_checkpoint_idx` 跟踪字内选中 cp
- 预览底部快捷键提示追加 "Alt+→ 切换字内节奏点"

#### #2 Alt+→ 提示
- 同 #1，编辑器底部提示栏已加入按键说明
- 设置界面同步更新 `cycle_checkpoint` 描述文本

#### #3 主题说明合并
- 删除独立 `theme_hint` QLabel
- 把 "暂仅支持浅色主题，自定义配色将在后续版本更新" 拼接到
  `card_theme` 的 content 参数末尾，保持单一 card 展示

#### #4 快捷键冲突明确提示
- `_resolve_shortcut_conflicts()` 返回 `list[str]` 冲突描述
- `_do_auto_save` 调用后用 `InfoBar.warning` 逐条弹出冲突项
  （包含具体冲突的两个动作名称）

#### #5 英文注音管线重写
- `auto_check_service.py` 注音优先级严格为：
  1. 英文 e2k（`_has_latin`）
  2. 用户字典（`_dict_map` 按词条长度倒序匹配）
  3. 库函数（SudachiPy）
- 参与过 e2k 或用户字典的字符标记后**不再进入下一轮**
- 注音对象白名单：英文字符、英文词语、汉字、日汉字、平假名、片假名、
  阿拉伯数字（`CharType.NUMBER`）
- 英文单引号（`who's`、`what's`、`don't`）由现有正则
  `[A-Za-z]+(?:['.][A-Za-z]+)*` 覆盖，作为英文单词内部字符；
  非英文上下文的单引号不受影响

#### #6 RL 字典逆序覆盖
- `AppSettings.register_dictionary_word(word, reading)` 新增：
  去重 + 插入到最高优先级
- `AppSettings.import_rl_dictionary(text)` 新增：逆序遍历导入内容，
  相同 word 覆盖旧条目并移到最高优先级，返回 `(added, updated)`
- `DictionaryEditDialog._on_import_rl` 按新逻辑在表格内逆序覆盖
- `ModifyCharacterDialog._register_to_dictionary` 改用
  `register_dictionary_word`

#### #7 编辑模式键位（音乐暂停时）
- `Space` → 当前字符加 checkpoint
- `Backspace` → 当前字符减 checkpoint（无 checkpoint 则 no-op）
- `.` / `。` → 切换 sentence_end
- `Enter/Return` → 在此处断行（当前字成为新的行尾，后续字移到新行）
- `Delete` → 删除当前字符，含 is_line_end 提升 / 空行清理规则
- 编辑模式下原 `Space/Z/X` 打轴/跳转行为变为 no-op

#### #8 打轴模式键位（音乐播放时）
- `Space` → tag_now（保持）
- `Z` / `X` → seek ±5s（保持）
- `F4` → 切换 sentence_end
- `F5` → 加 checkpoint
- `F6` → 减 checkpoint
- `Enter/Return` → 断行（同 #7）
- `Delete` → 删除选择或当前字符

#### #9 右键菜单重设计
`KaraokePreview._show_context_menu` 改用 `RoundMenu + Action`：

1. 删除字符 → `delete_chars_requested(line_idx, start, end)`
2. 在此插入空格 → `insert_space_after_requested(line_idx, char_idx)`
   [分割线]
3. 合并上一行 → `merge_line_up_requested(line_idx)`（行 0 禁用）
4. 删除本行 → `delete_line_requested(line_idx)`
5. 在此插入空行 → `insert_blank_line_requested(line_idx)`
   [分割线]
6. 增加节奏点 → `add_checkpoint_requested(line_idx, char_idx)`
7. 减少节奏点 → `remove_checkpoint_requested(line_idx, char_idx)`
8. 设置/取消句尾 → `toggle_sentence_end_requested(line_idx, char_idx)`
   [分割线]
9. 设置演唱者 ▶（子菜单，复用 `singer_selected` 信号）

选区命中时 "删除字符" 使用选区范围，未命中时按 `[char_idx, char_idx+1)`。

#### #10 快捷键 UI 合并（按 scope 语义）
`_SHORTCUT_ACTIONS` 改为 7 元组：
`(action_key, icon, title, content, default_timing, default_edit, scope)`
- `scope="both"` → 单卡片，两模式共享
- `scope="timing_only"` / `"edit_only"` → 单模式出现
- `scope="split"` → 标题追加【打轴模式】/【编辑模式】两卡片

**后台仍保留 `_key_map_timing` / `_key_map_edit` 双模式 dict，config 结构
`shortcuts.{mode}.{action}` 不变**（用户要求：不改 config）。
冲突检测用 `id(card)` 去重，避免 both 卡片自冲突。

#### #11 设置切页即保存
- `hideEvent` 检测 `_auto_save_timer.isActive()`，active 时立即 stop +
  `_do_auto_save()` flush，与其他设置保持一致

### Domain 层新 API（保持零框架导入）

#### `backend/domain/entities.py::Sentence`（+94 LOC）
- `insert_character(idx, ch)`
- `delete_character(idx) -> bool`（返回 True 若该行变空；内部处理
  is_line_end 提升、链尾修正）
- `toggle_sentence_end(idx)`
- `add_checkpoint(idx)` / `remove_checkpoint(idx)`（min 0）
- `split_at(idx) -> Sentence`（返回 idx+1 起的新 Sentence；
  当前 Sentence 保留 [0..idx] 并把 idx 设为 line_end）

#### `backend/domain/project.py::Project`（+68 LOC）
- `merge_line_into_previous(line_idx) -> bool`
- `delete_line(line_idx)`
- `insert_blank_line(after_line_idx) -> int`（零字符空 Sentence）
- `insert_line_break(line_idx, char_idx)`

#### `backend/application/timing_service.py`（+4 LOC）
- 公开封装 `rebuild_global_checkpoints()` 调用已有
  `_rebuild_global_checkpoints()`，让 presentation 层无需访问私有成员

### 架构合规性
- Domain 层零 PyQt 导入（仅 `dataclasses` / `typing` / 相对 domain 导入）
- Presentation 层通过 `TimingService.rebuild_global_checkpoints()` 访问
  引擎，不直接访问 engines 子模块
- 所有结构化编辑走 `CommandManager` + `ProjectSnapshotCommand`
  （deepcopy sentences），保留撤销/重做链
- 结构变更统一链：mutate → `rebuild_global_checkpoints()` →
  `_refresh_preview()` → `preview.update()`

### 验证
- `lsp_diagnostics(severity="error")` 对 4 个修改文件：无新增错误
  （预存在 PyQt5/PyQt6 stub 不匹配噪音保持，不属于本批改动）
- 运行时冒烟测试：
  ```
  from strange_uta_game.frontend.editor.editor_interface import EditorInterface, KaraokePreview
  from strange_uta_game.backend.domain.entities import Sentence
  from strange_uta_game.backend.domain.project import Project
  from strange_uta_game.backend.application.timing_service import TimingService
  # 所有 import 成功，新 API 全部存在
  hasattr(TimingService, 'rebuild_global_checkpoints') == True
  hasattr(Sentence, 'split_at') == True
  hasattr(Project, 'insert_line_break') == True
  ```
- `AppSettings` 字典 API：`register_dictionary_word` /
  `import_rl_dictionary` 皆 callable
- `_SHORTCUT_ACTIONS` 加载 21 项，包含新增 `break_line_here` /
  `delete_char` 两个 action

### 改动文件一览

| 文件 | 负责范围 | 说明 |
|---|---|---|
| `backend/domain/entities.py` | #7 #8 #9 | Sentence 结构化 API |
| `backend/domain/project.py` | #7 #8 #9 | Project 行级结构化 API |
| `backend/application/timing_service.py` | #1 #7 #8 #9 | 公开 rebuild 包装 |
| `backend/application/auto_check_service.py` | #5 | 注音管线重写 |
| `frontend/editor/editor_interface.py` | #1 #2 #6 #7 #8 #9 | 键位 / 右键菜单 / 预览补色 |
| `frontend/settings/settings_interface.py` | #2 #3 #4 #6 #10 #11 | 快捷键合并 / 切页即保存 / 字典 API |

### 遗留 / 未来优化
- "插入空格" 当前插入半角空格 `" "`；如需与日文排版一致可改为全角
  空格 `"　"`（用户可选）
- 空行在引擎渲染时以零字符 Sentence 存在，若后续发现引擎对空 Sentence
  的边缘 case 处理不一致，可改为插入单个全角空格占位
- `_SHORTCUT_ACTIONS` 的 scope 字段未来如果要支持更细粒度的 "某键仅某
  模式禁用"，可扩展为枚举

---

## 第十一批改动（2026-04-21）

本批覆盖 10 项用户反馈（#1~#10），包含 UX 修复、英文注音改进、打轴预览渲染
竞态根治，以及 **.sug 文件格式破坏性升级至 v0.2.0**（Ruby 分组）。

### #1 默认快捷键描述对齐
- `README.md` / `settings_interface.py` 默认快捷键表与实际 `DEFAULT_SETTINGS`
  一致化，修复先前描述与默认值不同步的问题

### #2 按键冲突弹窗后还原
- `SettingsInterface._on_shortcut_changed`：检测到冲突时先 `InfoBar.warning`
  提示，然后将 SettingCard 的显示值恢复为**修改前的上一次值**（通过
  `_last_valid_shortcuts` 快照），不再停留在冲突状态

### #3 冲突检测时机前移
- 快捷键 SettingCard 的 `valueChanged` 信号回调中**立即**跑冲突检测，不再
  等到 `_do_auto_save`。保存按钮消失后也能即时反馈

### #4 特殊字符键支持
- `settings_interface.py` 对 `,`、`.`、`/`、`;`、`'`、`[`、`]`、`、`、`-`、`=`
  等 OEM 键位在 `_sequence_to_readable` / `_readable_to_sequence` 双向
  转换表中加入显式映射，避免 PyQt6 sequence 解析失败
- `editor_interface.py` keyPressEvent 相应映射补全

### #5 三动作 content 描述修复
- `increase_checkpoint` / `decrease_checkpoint` / `toggle_sentence_end`
  三个 action 的 content 字符串修正为正确释义（此前存在 copy-paste 错误）

### #6 模式描述字体颜色区分
- 快捷键卡片"【打轴模式】"标签使用橙色，"【编辑模式】"使用蓝色，富文本
  `<span style="color:...">` 注入到 title，提升模式可辨识度

### #7 英文连词切分修复
- `backend/infrastructure/parsers/english_ruby.py`：修复原 tokenizer 对
  `what's` 之类缩写词错误切成 `{what|...}{'|'}{s|s}` 的问题
- 现使用 `[A-Za-z]+(?:['.][A-Za-z]+)*` 完整匹配，将缩写视为单一 token

### #8 e2k 注音分组修复
- `auto_check_service.py::_apply_english_dictionary`：e2k 返回单片假名
  串（如 `ヘロー`）时，按**字符数等分**到原英文单词的字母上，避免出现
  `{hello|ヘロー,e,l,l,o}` 这样的错误分组
- 统一走 `_group_reading_for_character` 聚合

### #9 打轴预览字符选中渲染竞态（核心修复）

**问题**：选中任意一行任意字符后，附近几行出现不规则空白/错位。

**初判（已被否决）**：曾怀疑是补色高亮 `QColor.fromHsv` 在 paintEvent 中
副作用；经 `explore` agent 审查确认该假设不成立——paintEvent 纯读状态、
不触发 update/repaint。

**真实根因**：`EditorInterface._on_char_selected` 在一次点击中对
`preview.set_current_position` 进行了**三次竞态写入**：
1. L2598 直接 set（本地 line_idx）
2. L2610 `timing_service.move_to_checkpoint()` 的异步回调
   `_apply_checkpoint_position` 再次 set（可能跳到最近有效 cp 的不同
   line_idx）
3. L2614 再手动 set 回原 line_idx

三次写入之间 `_scroll_center_line` 被反复覆盖，导致 `_update_display`
在中间帧读到错误的 scroll 基准。

**修复**：
- `KaraokePreview.set_current_position` 加幂等 guard：当 `line_idx`
  未变时仅更新 `_current_char_idx`，不再重置
  `_scroll_center_line`，避免多次无害重调把 scroll 打乱
- `EditorInterface._on_char_selected` 重写：
  - 若目标字符**有 checkpoint** → 通过 `timing_service.move_to_checkpoint()`
    单一路径设置，由 `_apply_checkpoint_position` 负责最终写入
    （single source of truth）
  - 若目标字符**无 checkpoint** → 本地 `preview.set_current_position`
    直接写，不再叠加 move_to_checkpoint
- 回滚此前 3 处诊断日志（`set_project` / `wheelEvent` / `set_current_position`）

### #10 Ruby 分组（破坏性升级 v0.1.0 → v0.2.0）

**设计目标**：Ruby 从「整串字符时间戳」升级为「带分组信息的字符序列」，
一组对应一个 checkpoint，用于支持 Ruby 内部精细打轴（而非整个词同一时间）。

**用户澄清的语义（方案 B，双分隔符）**：
- `,`（逗号）= 词内**字符边界**（哪几个读音属于哪个汉字）
- `#`（井号）= 单个字符内的 **checkpoint 分组边界**
- 示例：
  - `私 わたし` → `わ#た#し`（单字三拍三 cp）
  - `大冒険 だいぼうけん` → `だ#い,ぼ#う,けん`（三字，每字内再按 cp 分组）
  - `赤い あかい` → `あ,か#い`（两字，"い" 字内两 cp）

**数据模型变更**：
- `backend/domain/models.py::Ruby`：
  - 单字段 `text: str` 保持不变（破坏在于 text 的语义）
  - `text` 只含 `#`，**不含 `,`**（因为 Ruby 挂在 Character 上，每字
    一个 Ruby 对象，字符边界天然由 Character 承担）
  - 新增 `groups() -> list[str]` 按 `#` 切分返回各 cp 分组
  - 新增 `group_count() -> int`
  - `__post_init__` 校验无空组

**破坏性格式升级**：
- `sug_parser.py::CURRENT_VERSION = "0.2.0"`
- 新增 `_migrate_v2_to_v0_2_0`：对 v0.1.0 老文件，将原始完整读音重新
  跑一遍 `analyze_sentence_ruby` 生成带 `#` 分组的新 text，然后保存
- 用户分发仅两人，直接破坏升级无需兼容运行

**涉及文件全链路**：
- `models.py`：Ruby.groups / group_count / 校验
- `inline_format.py`：内联 `{汉字|か#ん#じ}` 解析 `#`
- `lyric_parser.py`：导入 `#` 透传
- `ruby_analyzer.py`：新增 `_group_reading_for_character`，Ruby 聚合时
  按 checkpoint 数切分
- `sug_parser.py`：版本号 + 迁移
- `nicokara_exporter.py`：导出时用 `ruby.groups()` 对齐时间戳
- `auto_check_service.py`：自动注音产出带 `#` 的 Ruby；修复
  `char_to_ruby` stale reference

### 字典迁移脚本

**新增** `scripts/migrate_dict_to_ruby_groups.py`：
- 双分隔符语义迁移：`config/dictionary.json`（1891 条）
- 识别 5 类：
  - `unchanged`（已是正确格式）
  - `comma_cp_split`（原 `,` 分隔 mora → 转为 `,` + `#`）
  - `mora_split` / `char_split` / `char_split_flat`
  - `manual_review`（英文带多余逗号等需人工审核的 411 条）
- 支持 `--dry-run`（默认）和 `--apply`
- Dry-run 实测 1891 条全部分类成功无崩溃

### 验证

- `lsp_diagnostics(severity="error")` 对 11 个改动文件：无本批新增错误
  （pre-existing PyQt5/6 stub 噪音不变）
- 迁移脚本 dry-run：1891 条字典条目全部归类成功
- #9 修复后 `set_current_position` 语义简化，竞态路径消除

### 涉及文件

| 文件 | 批次项 |
|---|---|
| `README.md` | #1 |
| `frontend/settings/settings_interface.py` | #1~#6 |
| `frontend/editor/editor_interface.py` | #4 #9 |
| `backend/application/auto_check_service.py` | #7 #8 #10 |
| `backend/infrastructure/parsers/english_ruby.py` | #7 |
| `backend/domain/models.py` | #10 |
| `backend/infrastructure/parsers/inline_format.py` | #10 |
| `backend/infrastructure/parsers/lyric_parser.py` | #10 |
| `backend/infrastructure/parsers/ruby_analyzer.py` | #10 |
| `backend/infrastructure/persistence/sug_parser.py` | #10 |
| `backend/infrastructure/exporters/nicokara_exporter.py` | #10 |
| `scripts/migrate_dict_to_ruby_groups.py`（新增） | #10 |

### 遗留 / 未来优化
- 字典 411 条 `manual_review` 待人工逐条校对（主要是英文词含多余逗号）
- 用户本地需手动执行迁移脚本：
  `python scripts/migrate_dict_to_ruby_groups.py --apply`
- .sug v0.1.0 老项目首次打开自动升级至 v0.2.0，无需用户操作

---

## 第十二批修复（2026-04-21，v0.2.0 UI 层适配补丁）

### 背景
用户质疑 v0.2.0 破坏性升级后三个前端 UI 文件（`editor_interface.py` /
`edit_interface.py` / `ruby_editor.py`）是否适配方案 B。审计结论：三个 UI
层对 `,` 边界解析、Ruby.text 透传的工作方式，意外地**透明兼容**方案 B
（UI 只处理 `,` 切字符，`#` 作为 Ruby.text 内原子内容流过），但发现一处
真实 bug 和两处文案需更新。

### 1. 修复 `split_ruby_for_checkpoints` fallback 路径 bug（`inline_format.py`）
- **问题**：当 ruby_text 含 `#` 但组数与 cp 数不匹配时，回退到 mora/字符
  均分会把 `#` 当成普通字符切出，产出 `['da', '#', 'i']` 之类。调用方
  `Ruby(text='#')` 直接抛 `ValidationError: Ruby 分组存在空组`。
- **修复**：fallback 前先 `replace("#", "")`，`#` 仅作分组标记，不作为
  内容字符参与均分。
- **影响面**：`ruby_editor._parse_annotated_line` 旧格式路径、
  `editor_interface` 连词组编辑 fallback。
- **回归测试**：`split_ruby_for_checkpoints('da#i', 3) == ['d','a','i']`
  （以前是 `['da','#','i']`），所有 part 可安全传入 `Ruby(text=...)`。

### 2. 更新 `ruby_editor` 界面文案（新格式提示）
- **问题**：全文本编辑界面说明只提旧格式 `赤{あか}い花{はな}`，用户无从
  得知新格式 `{text|r1,r2}` 和 `#` 的含义。
- **修复**：说明文字与 placeholder 补充新格式示例
  `{大冒険|だい,ぼう,けん}` 和多 checkpoint 单字 `{私|わ#た#し}`。

### 3. 三个 UI 文件兼容性审计结论

- `editor_interface.py` `ModifyCharacterDialog`（L1184-1268）：
  - 显示：`",".join(c.ruby.text)` — Ruby.text 含 `#` 时原样透传 ✅
  - 解析：`ruby_text.split(",")` per-char，每段经 `Ruby(text=...)` 校验 ✅
- `edit_interface.py` `LineDetailDialog`（L219-458）：
  - 显示：连词组 `,` 连接，单字符原样 ✅
  - 解析：`"," in raw` → split；否则整体作 `Ruby.text` ✅
- `ruby_editor.py` 全文本（L135-215, L466-497）：
  - 序列化：`{text|r1,r2}` 格式 ✅
  - 解析新格式：`readings_part.split(",")` ✅
  - 解析旧格式：依赖已修复的 `split_ruby_for_checkpoints` ✅

### 遗留：v0.2.0 commit 自带测试回归（非本批引入）
以下 5 个 `test_inline_format.py` 测试在 HEAD=75d5d4e 就已失败，反映
`to_inline_text` / `from_inline_text` 对"单字符多 cp + mora 级 ruby"
的对齐尚未完全适配方案 B：

- `TestToInlineText::test_ruby_single_char` — 输出 `{柔|[2|t1]やわ[t2]}`
  但期望 `{柔|[2|t1]や[t2]わ}`（cp timestamp 需夹在 mora 边界）
- `TestFromInlineText::test_ruby_group` / `test_multi_char_ruby` /
  `test_mixed_line` — 解析后 `sentence.rubies[i].text` 含 mojibake，
  连词组 ruby 合并逻辑需复核
- `TestRoundtrip::test_ruby_roundtrip` — `Ruby(text='やわ#')` 末尾空组

这些问题与本次用户询问的"三 UI 文件适配"相对独立，作为 v0.2.0 后续
补丁（第十三批）处理。

---

## 第十三批修复 (2026-04-22) - Ruby `#` 语义收敛 + cp 主导对齐 + 字典英文保留

**背景**：第十一批引入的"方案 B"把 Ruby 分组标记 `#` 直接塞进了
`Ruby.text`，但渲染/导出/字典/UI 对话框并未全链路适配，导致：
(a) 卡拉 OK 预览与 TXT 导出里 `#` 直接漏出；(b) 三个编辑对话框没有
`#` 说明也不处理 cp 与分组数失配；(c) 迁移脚本对 "ruby 含英文字母"
的条目一律尝试切分，产生 349 条假阳性 manual_review；(d) #9 选中
字符后出现异常大空行的渲染 bug。

### 1. 新增 `Ruby.display_text()` + `align_ruby_to_checkpoints()`

- `backend/domain/models.py` `Ruby.display_text()`：返回
  `self.text.replace("#", "")`，供渲染/导出使用。`text` 保留
  `#` 用于内部分组持久化，`display_text()` 用于所有用户可见路径。
- `backend/infrastructure/parsers/inline_format.py`
  `align_ruby_to_checkpoints(ruby_text, check_count, is_sentence_end)`：
  - `cp < 2` 或 `is_sentence_end=True` → 剥 `#`（单 cp 不分组）
  - `cp >= 2` → 按 `#` 分组后：
    - 组数 == cp：空组补空格（避免 ValidationError）
    - 组数 > cp：多余组合并到末组
    - 组数 < cp：末尾补空格占位
  - 规则严格按用户要求："以 checkpoint 为准，多余合一、缺失补空格，
    句尾 cp 不算"

### 2. 三个编辑对话框：`#` 提示 + cp 主导对齐

- **`editor_interface.py`** `ModifyCharacterDialog`（L1184-1280）：
  - 显示初值 `c.ruby.display_text()`（不再漏 `#`）
  - placeholder 加 `#` 分组说明
  - 保存时走 `align_ruby_to_checkpoints(raw, cc, is_se)`
- **`editor/bulk_change_dialog.py`**（L25-230）：
  - placeholder 加 `#` 说明
  - 两处 `Ruby(text=...)` 构造走 `align_ruby_to_checkpoints`
- **`editor/edit_interface.py`** `LineDetailDialog`（L28-478）：
  - Hint 文案补 `#` 说明
  - 三处 `Ruby(text=...)` 构造走 `align_ruby_to_checkpoints`

结论：单 cp 或句尾 cp 自动剥 `#`；多 cp 失配自动归并/补齐，
**永不抛 ValidationError**。

### 3. 全文本编辑界面（`ruby_editor.py`）以 `#` 数反推 cp

用户规则："全文本编辑界面下必须以 `#` 分组为准更新 checkpoint，
因为这里会自动生成节奏点。"

- `settings/ruby_editor.py` `_rebuild_characters`（L76-111）：
  解析 `{字|ruby}` 时 `check_count = ruby.count("#") + 1`，
  没有 `#` 则 cp=1；覆盖旧的"cp 固定为 1"逻辑。

### 4. 渲染 / 导出路径统一走 `display_text()`

- `editor_interface.py` L924、L984、L989、L1001、L1021：
  所有预览渲染 `r.text` → `r.display_text()`（新增局部变量
  `_ruby_disp` 避免重复调用）
- `startup_interface.py` L384：字符列表显示改用 `display_text()`
- `txt_exporter.py` L109：TXT 导出用 `display_text()`
- **无需改**：`nicokara_exporter.py` 走 `ruby_parts`（已经经过
  `c.ruby.groups()`，天然 #-free）；`inline_exporter.py` 保留 `#`
  是 inline 格式规范要求。

### 5. 修复 #9：选中字符后出现异常大空行

**根因**：`editor_interface.paintEvent` 对选中字符的补色高亮
使用了 `max(line_box_top, font_ascent_top)` 的混用逻辑，而
非选中字符用的是 `y_center - rect_height / 2`。当 line_height
与 font metrics 不一致（字体 fallback、ruby 导致行高变化）时，
选中态的矩形顶端会跳到行盒子上沿，视觉上像多出一条空行。

**修复**：L870-876 统一锚点为
`_rect_top = int(round(y_center_f - _rect_height / 2))`，
选中/非选中一致，差异只在背景色 alpha。

### 6. 字典迁移：跳过 "ruby 含英文字母" 的条目

用户规则："字典里 ruby 含有英文的场景不要处理，用 `,` 分组足够了；
如果原文是英文而 ruby 是假名，还是要处理。"

- `scripts/migrate_dict_to_ruby_groups.py`：
  - 新增 `_has_ascii_letter(s)`
  - `_migrate_reading` 开头检测 **reading**（非 lemma）含 ASCII
    字母时直接返回 `unchanged`
- **Dry-run 统计（1891 条）**：
  ```
  unchanged      121 → 132 (+11, 纯英文 reading)
  manual_review  411 → 349 (-62)
  comma_cp_split 1304
  mora_split     13
  char_split     26
  char_split_flat 78
  ```
- **已 `--apply` 写入**：
  - `src/strange_uta_game/config/dictionary.json`（168208→170396 bytes）
  - `dictionary.json`（程序目录覆盖）
  - 两份 `.bak.20260422_00xxxx` 备份已保留

### 7. 测试 & LSP

- `domain/` + `inline_format` 单元测试：96/96 通过
- `align_ruby_to_checkpoints` 行为手工验证：cp<2 剥 #、cp>=2
  归并/补齐均符合预期
- LSP 诊断：所修文件无新增错误（`edit_interface.py` 剩余错误为
  PyQt5/PyQt6 stub 冲突的 pre-existing 问题）

### 遗留（独立批次处理）

- `tests/unit/infrastructure/test_inline_format.py` 中
  `test_ruby_single_char` / `test_ruby_group` / `test_multi_char_ruby` /
  `test_mixed_line` / `test_ruby_roundtrip` 5 个失败，属第十一批
  引入的 `to_inline_text` / `from_inline_text` 方案 B 对齐逻辑，
  与本批"UI/渲染/字典适配"正交，留作第十四批。

---

## 第十四批修复 (2026-04-22) - inline_format 方案 B 回归测试收尾

**背景**：第十一批引入方案 B 后，`tests/unit/infrastructure/test_inline_format.py`
有 5 个测试失败。失败分两类：(a) `to_inline_text` 对"无 `#` 的
legacy 多 cp ruby"没有 mora 级切分，导致所有假名挤在首个 cp 时间戳
前；(b) 测试仍按旧模型断言 `ruby.text == "やわ"`，未适配方案 B 的
`ruby.text == "や#わ"` 内部分组存储。

### 1. `to_inline_text` 适配 legacy 无 `#` 多 cp ruby

- **问题**：`Ruby(text="やわ")` + `check_count=2` 调用
  `c.ruby.groups()` 返回 `["やわ"]`（1 组），序列化时只有 cp_idx=0
  附上 `"やわ"`，cp_idx=1 的时间戳后没有 ruby 片段 → 输出
  `[2|ts1]やわ[ts2]` 而非期望的 `[2|ts1]や[ts2]わ`。
- **修复**：`inline_format.py` L223-229 改用
  `split_ruby_for_checkpoints(c.ruby.text, c.check_count)`，
  该函数优先按 `#` 切分，否则按 mora / 字符均分。这样 legacy 数据
  （无 `#` 多 cp）也能正确序列化。

### 2. 测试按方案 B 更新断言：用 `display_text()` / `groups()`

- 方案 B 后，`from_inline_text` 解析多 cp ruby 时会产生
  `Ruby(text="や#わ")`（存储含 `#` 内部分组标记）。旧测试仍断言
  `ruby.text == "やわ"`，失败。
- **修复**：`tests/unit/infrastructure/test_inline_format.py`
  - `test_ruby_group`: `ruby.text == "やわ"` →
    `ruby.display_text() == "やわ"` + `ruby.groups() == ["や","わ"]`
  - `test_multi_char_ruby`: cc=1 (`"しゃ"`) 保持 text 断言，
    cc=2 (`"てい"`) 改用 `display_text()` + `groups()`
  - `test_mixed_line`: 三处 ruby 断言全改 `display_text()`
  - `test_ruby_roundtrip`: roundtrip 后改断言 `display_text()` +
    `groups()`，显式注释"ruby.text 从 `やわ` 变为 `や#わ`，
    但 display_text() 保持不变"

### 3. 测试 & 验证

- `tests/unit/infrastructure/test_inline_format.py`：**45/45 通过**
- `tests/unit/`（全量）：209 通过；2 pre-existing 失败与本批无关：
  - `test_batch_export` — `batch_export` 功能已于 commit 0217443 移除
  - `test_export_ruby_relative_timestamps` — nicokara_exporter
    中 legacy `#` 遗留，非本批引入
- 验证方式：`git stash` + run tests + `git stash pop`，确认两失败
  在 HEAD=c901138 未改动状态下同样失败

---

## 第十五批修复：#9 选中 checkpoint 产生"大白框"根治（2026-04-22）

### 0. 症状与用户诊断

用户报告（原话）："有一个修了好几批次的问题，就是那个选中后大空行问题。
经我观测，其实不是大空行而是大白框。应该就是选中后高亮引起的问题……
只要是在这个checkpoint渲染的情况下，该checkpoint下方所有的内容都会变白。
并且这个白框会以着这个补色后的checkpoint的底部为分界逐渐往上。所谓加一
个空行就恢复，其实本质上是因为加一次空行会重新渲染一次，但是不渲染高亮。
所以我建议你仔细检查以下checkpoint高亮的绘图逻辑，他是不是越界了，他应
该绘图框限定在字符和▮▶。这三个符号内吧。"

### 1. 前几批的误诊

- 第十批"已修复 #9"按 race condition + 垂直锚点两处调整 → 用户实测未修
- 第十三批 `_rect_top = y_center - _rect_height/2` 统一垂直锚点 → 方向
  对但未根治；症状仍在
- 根因不在 line-height 数学，而在 **Windows 平台 QPainter.drawText**
  渲染 `▶/▮/▷/▯/。` 等符号 glyph 时的 text-run 背景清除行为。

### 2. 根因

Oracle 诊断结论（session `ses_24f0281e8ffeApj5W442HXNxg7`）：

1. `editor_interface.py` paintEvent 起始 L762 有唯一一次
   `painter.fillRect(self.rect(), QColor("#FFFFFF"))` → 整个可见区域白
2. checkpoint 标记块 L1083-1142 仅用 `drawText` 绘制，**表面上不可能产
   生大矩形**
3. 但 Windows 下 Qt 对 `▶/▮` 等特殊 Unicode 符号 glyph 会走 fallback
   symbol-font 渲染路径；该路径的 text-run 有时会清除一个远大于 glyph
   ink 的 background 矩形，**露出 L762 填的纯白背景**
4. 另外该路径对 **前序未清理的 setClipRect 状态** 更敏感——若 ruby/
   主文字 wipe clip 因任何原因泄漏到 checkpoint 绘制，后续 draw 会被
   意外裁剪，视觉表现同样是"大白框"
5. 选中（补色）分支仅是 **触发这条路径更频繁的条件**（每帧必走），
   所以用户观察到"只要 checkpoint 渲染就白"

### 3. 修复方案

对每个 checkpoint marker 的 `drawText` 做防御性隔离
（`editor_interface.py` L1115-1154）：

```python
draw_rect = QRect(
    int(mx) - 1,
    marker_y - fm_checkpoint.ascent() - 1,
    int(mw) + 2,
    fm_checkpoint.height() + 2,
)
painter.save()
painter.setClipping(False)                             # 清除任何残留 clip
painter.setBackgroundMode(Qt.BGMode.TransparentMode)   # 强制透明 text run
painter.setClipRect(draw_rect)                         # 本地严格 clip 到 glyph 框
painter.setPen(color)
painter.drawText(int(mx), marker_y, marker_char)
painter.restore()
```

三条防线同时生效：

- **`setClipping(False)`**：清除任何从前序 ruby/主文字 wipe 泄漏的 clip
  状态，排除假设 H2
- **`setBackgroundMode(TransparentMode)`**：强制 drawText 不做 background
  fill，直接排除 H1 的 over-clear 机制
- **`setClipRect(draw_rect)`**：即使前两条都失效，本地 clip 也保证
  drawText 不能影响 glyph 框 `[mx-1, marker_y-ascent-1, mw+2, height+2]`
  之外的任何像素——**严格满足用户"绘图框限定在字符和▮▶三个符号内"
  的不变量**

### 4. 验证

- `lsp_diagnostics`：无错误
- `tests/unit/` 全量：209 passed；2 pre-existing 失败（`test_batch_export`、
  `test_export_ruby_relative_timestamps`）与本批无关，前批已记录
- 手工复现指引：选中任意带 checkpoint 的字符，移动
  `_current_checkpoint_idx`；在修复前当前 checkpoint 补色渲染时该字符
  下方区域应出现向上延展的白框；修复后白框消失

### 5. 影响面

- 单一修改点：`editor_interface.py` checkpoint marker 绘制块
- 不改动任何 domain/application/infrastructure 层
- 不引入新依赖、不改 Qt 配置、不改字体声明
- AGENTS.md 约束全部保持（Qt 字体 YaHei、PyQt6、中文文案、PYTHONIOENCODING）

---

## 第十六批修复（2026-04-22）：Issue #9 架构性根治选中高亮大白框

**问题**：第十至十五批所有防御性修复（本地 clip、透明 BGMode、
setClipping(False)、双管线隔离）均被用户实测"未修复"。第十五批的
`save → setClipping(False) → BGMode.TransparentMode → setClipRect →
drawText → restore` 三重防御在 Windows 符号字体 fallback 下仍无法消除
"选中后 checkpoint 下方至行末大面积变白"现象。

**用户诊断与方案（R2+R3+R4）**：

> "能否不额外渲染，通过在 cp 上绑定一个是否被选中的状态，
> 如果被选中，渲染时就调用使用演唱者的补色。"
> "演唱者的补色新增在演唱者所属的数据结构里。"
> "cp 是否被选中的状态传递就绑在各种 cp 切换事件上。"
> "除了 F5/F6 增减 cp，其他都是。"
> "全局只能选中一个 cp。添加一个必删除一个。"

**根因重判**：不是绘制隔离不彻底，而是**渲染有"选中专属分支"**。只要
paintEvent 里存在第二条代码路径（`if is_selected: color = HSV_shift(...)`），
Windows 文本子系统就有机会在这条路径上触发 text-run 的 over-clear。
真正的根治是**消灭分支**，让所有 checkpoint 走同一条 `setPen → drawText`
管道。颜色选择提前到一张 LUT（演唱者基色 / 演唱者补色），选中态由数据
承载（`Character.selected_checkpoint_idx`），不再参与运行时渲染分支。

### 1. Domain 层改动

**`Singer.complement_color: str`（新字段，持久化）**
- `entities.py`：新增 `_compute_complement_color` 纯函数（HSV h+180°，
  保持 S/V；灰度退化返回原色）
- `__post_init__` 自动补算（向后兼容：旧 .sug 加载时字段为空→自动补）
- `change_color` 同步更新

**`Character.selected_checkpoint_idx: Optional[int] = None`（新字段，
不持久化）**
- `compare=False` 避免 dataclass 等值比较副作用
- 不进 .sug：选中态是 UI 瞬态，不跨会话

**`Project` 选中 API（维持三条不变量）**
- I1 **全局单选**：`set_selected_checkpoint` 先扫全局清旧再设新
- I2 **默认选中**：`select_default_checkpoint` 选 (0,0,0)
- I3 **增删对称**：每次 set 必先清旧
- 不做运行时越界校验，由调用方保证

### 2. Application 层改动

- `ProjectService.create_project` / `open_project` / `import_lyrics`
  打开/加载/首次有字符时调用 `select_default_checkpoint` 维持 I2

### 3. sug_parser 改动

- 序列化：写入 `"complement_color"` 字段
- 反序列化：读 `singer_data.get("complement_color", "")`——旧文件
  返回 `""`，由 `Singer.__post_init__` 自动补算

### 4. Frontend 层改动

**`EditorInterface._update_selected_checkpoint(line_idx, char_idx, cp_idx)`**

统一入口，同时更新两处状态：

1. `preview._current_checkpoint_idx`（UI 侧，兼容旧路径）
2. `project.set_selected_checkpoint(...)`（domain 侧，渲染真实读取源）

覆盖三条 canonical cp 切换通路：
- `_apply_checkpoint_position`（TimingService 回调主通路）
- `_sync_after_structure_change`（结构编辑后 clamp）
- `_on_char_selected` 间接通过 `_timing_service.move_to_checkpoint`
  回灌（下游调 `_apply_checkpoint_position`）

按用户约定，F5/F6 增减 cp 事件不触发选中变更。

**`KaraokePreview.paintEvent` checkpoint 渲染（单管道）**

```python
# 第十五批（已作废）：
painter.save()
painter.setClipping(False)
painter.setBackgroundMode(Qt.BGMode.TransparentMode)
painter.setClipRect(draw_rect)
painter.setPen(color)
painter.drawText(int(mx), marker_y, marker_char)
painter.restore()

# 第十六批（单管道）：
is_selected = (ch_obj.selected_checkpoint_idx == cp_idx)
if not has_timed:
    color = QColor("#ccc")
elif is_selected:
    color = _char_complement_colors.get(char_pos, ...)
else:
    color = _char_singer_colors.get(char_pos, ...)
painter.setPen(color)
painter.drawText(int(mx), marker_y, marker_char)
```

- 所有 cp 走**同一**条 `setPen → drawText`
- 颜色在循环外已经查好（LUT：`_char_singer_colors` + `_char_complement_colors`）
- 无 `save/restore`、无 `setClipping` / `setBackgroundMode` / `setClipRect`
  切换、无运行时 HSV 计算、无第二次 drawText

### 5. 验证

- `lsp_diagnostics`：所有改动文件无 error
- `pytest tests/unit/domain tests/unit/infrastructure`：
  - 新增 23 tests 全部 PASSED（补色算法 6 + Singer dataclass 5 +
    Project 选中不变量 10 + sug 向后兼容 2）
  - 197 pre-existing tests PASSED
  - 2 pre-existing FAILED（`test_batch_export`、
    `test_export_ruby_relative_timestamps`）与本批无关，前批已记录
- 手工复现指引：选中任意带 checkpoint 的字符，切换 cp 位置；
  预期：选中 cp 以演唱者补色绘制，其他 cp 基色，**不再出现任何白框**。
  切换演唱者颜色（change_color）时，补色自动跟随，选中色实时更新。

### 6. 影响面

- Domain：`entities.Singer` 增字段+自动补算；`models.Character` 增字段；
  `project.Project` 增 4 个方法
- Application：`project_service` 3 个入口补 `select_default_checkpoint`
- Infrastructure：`sug_parser` 序列化 +1 字段、反序列化 +1 字段（向后兼容）
- Frontend：`editor_interface`
  - 新增 `_update_selected_checkpoint` 私有方法
  - 替换 `_apply_checkpoint_position` / `_sync_after_structure_change`
    里直接赋值 `preview._current_checkpoint_idx` 的三个点
  - paintEvent 预计算 `_char_complement_colors` LUT
  - paintEvent checkpoint drawText 块从 45 行（含注释）压缩为 10 行单管道
- 废弃：第十五批三重防御、第十至十四批所有渲染分支
- 不改：Qt 字体声明、AGENTS.md 约束、PyQt6 版本、事件接线（F5/F6 除外约定）

---


