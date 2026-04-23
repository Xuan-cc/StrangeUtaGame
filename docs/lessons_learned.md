# 经验教训沉淀

记录开发过程中踩过的坑、确立的不变量、以及通用约束。新经验追加到对应分类下，已过时的条目可删除。

---

## 编码（Windows / PowerShell）

- **绝对禁止** 用 PowerShell 的 `Set-Content` / `>` / `Out-File` 写包含中文的文件——宿主默认 GBK 会污染 UTF-8 内容。永远走 write/edit 工具。
- pytest 前必须 `$env:PYTHONIOENCODING="utf-8"`。
- `Get-Content` / `Select-String` 终端显示中文乱码不代表文件本身有问题，验证文件编码请用 read 工具。

## Git 工作流

- **绝对禁止** `git stash`（曾因 stash drop 丢失过 ~30 分钟的 auto_check_service.py 改动）。
- 长任务拆分多个 checkpoint commit，保持工作树清晰可回滚。
- `git add` 显式指定文件路径，避免 `git add -A` 误带备份/临时文件。

## Ruby 数据结构

- Ruby 的小单元是 `RubyPart`，存储原始文本；checkpoint 按演唱字母分组。
- 不再支持原 `#` 字符串 inline 语法（无意义且未对外发布过含 `#` 的版本）。
- `len(parts) == character.check_count` 是 Ruby 与 checkpoint 的对应不变量。
- Ruby 内英文字符不参与 mora 计算。

## AutoCheckService

- "注音"和"打节奏点"是两个过程：注音流程不含标点，节奏点流程含标点（开关由用户控制）。
- `linked_to_next=True` 时，下一个字符也视为"已注音"（only_noruby 判定要传递）。
- F2 单字编辑界面**不应**触发自动注音（用户手动注音场景）。
- `apply_to_project` / `update_checkpoints_from_rubies` 改 `check_count` 后必须调用 `Project.shift_selected_checkpoint_if_lost()`。
- startup / home 自动注音用 `only_noruby=True`，不弹窗——避免覆盖用户已带注音的导入文本。
- ruby_editor 的"自动分析全部注音"用三选项弹窗：全部重新分析 / 仅未注音 / 取消。
- **英文词条的词边界必须把 apostrophe（`'` U+0027 和 `’` U+2019）视为词内字符**，否则 `what` 会命中 `what's` 中部，导致 `'s` 被占用冲突、裸露无注音。用户词典子串匹配的边界检查不能只看 `isalpha()`。
- 用户词典在 `analyze_sentence` 中**先于** e2k 执行（dict_covered → e2k_covered → fallback）。这与直觉"英文优先 e2k"相反，是有意的覆盖优先级设计（允许用户词典强制改写英文读音）。影响：用户词典的英文词条边界必须严格，否则污染下游 e2k。
- `update_checkpoints_from_rubies` 的节奏点覆写要在"标点/flag 过滤之后"插入英文词组规则（首=1/中=0/末=句尾），否则会被 flag 过滤抹掉。英文句尾判定合并进 `is_sentence_end` 逻辑，不单独一段。

## TimingService / Checkpoint

- TimingService 是节奏点时间戳的**唯一**写入入口（domain 层只读取）。
- 按键事件统一走 `on_key_changed(timestamp_ms, key_type)`：按下/抬起都推送给当前选中 cp，由 cp 角色过滤——普通 cp 仅响应 `pressed`，句尾末尾 cp 仅响应 `released`。写入后单次推进。
- 句尾末尾 cp 判定：`Character.is_sentence_end_tail_cp(cp_idx)`，即 `is_sentence_end and cp_idx == check_count`。
- 打轴游标 = `Project.selected_cp` = TimingService 的 `_current_position`，三者必须同步，是同一个概念。
- 选中 cp（selected_checkpoint_idx）全局唯一不变量：增删对称，每次 set 必先 clear。
- 标点 cp 默认 0；启用 `checkpoint_on_punctuation` 时 max(1)。
- 选中 cp 因 check_count 缩减而越界时，按"同字截断 → 同行后字 → 跨行首字 → 全部失效则清除"顺延。

## Domain 层约束

- domain/ 目录零框架依赖（不引 PyQt6）。
- 字符级演唱者用 `singer_id`（int）字段，颜色由 SingerService 维护。
- 句尾时间戳用 `Character.sentence_end_ts`，与 `is_sentence_end` 配套；非句尾自动 `clear_sentence_end_ts()`。

## UI

- 中文文案 + Microsoft YaHei 字体。
- 设置 SwitchSettingCard 模式：DEFAULT_SETTINGS 加 key → init UI → load (`_load_settings_to_ui`) → save (`_save_ui_to_settings`)。
- 快捷键冲突时直接弹 warning + 恢复原键，不允许冲突共存。
- **ShortcutSettingCard 的 `value_changed` 信号必须在 `_init_shortcut_group` 创建卡片时立即 connect**（非 `_connect_auto_save_signals` 里统一 connect）——后者路径会漏掉部分 scope，导致冲突检测与即时保存都失效。
- 字符级编辑对话框用字符行布局（`[字][ruby][check]`）比"整句输入框 + # 分隔"更直观；RubyPart 分组用逗号 `,`，与词典条目惯例一致，避免歧义。
- 字符数相同的修改走 `push_to_ruby` 原地改、保留 timestamps；字符数变化走 slice 替换（新建 Character），这是"修改节奏点无效"的根因修复模式。

## 导入导出

- 空行处理的原则：**导入剥除 + 导出原样**。不要在导出阶段根据时间间隔自动补空行——这会与导入剥除形成双重失配（导入少一行、导出多一行）。
- LRC/TXT/Nicokara 三个 exporter 的空 sentence 都应 append `""`（保留用户原始段落分隔），而不是过滤掉。
