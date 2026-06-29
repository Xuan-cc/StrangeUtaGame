# 波形时间标签可拖拽编辑 — 方案书

> 目标：把波形页（`TimelineWidget` / `WaveformDisplay`）上的时间标签（timetag）从纯绘制对象，
> 升级为**可单击选中、可拖拽改时间、可 Ctrl 多选批量平移**的交互对象，并提供总开关回退旧模式。

状态：设计定稿，待实现。

---

## 1. 背景与现状

- 波形区是一个手绘 `QWidget`（`WaveformDisplay`），`paintEvent` 里依次画网格 / 波形 / 时间标签 / 播放头。
  时间标签当前是**纯绘制竖线**，没有对象身份、几何或事件。
- 数据单向、扁平、无回指：
  - 数据源 `Project.collect_all_global_timestamp_ms_with_chars()`（`backend/domain/project.py:441`）
    返回 `(ts, char, id(ch), ruby_text)`，`id(ch)` 仅用于"同字首个 checkpoint 去重显示"。
  - `WaveformDisplay.set_time_tags()`（`frontend/editor/timing/timeline_widget.py:137`）消费 `char_id`
    后丢弃，最终存为 `_time_tags: List[(ts, label, ruby)]`，**无法反查到 `(line, char, cp)`**。
- 鼠标语义已占满：左键单击=seek，左键拖动=pan，滚轮=zoom
  （`timeline_widget.py:397-465`）。
- 显示值 ≠ 存储值：波形显示的是 `global = timestamps[i] + _global_offset_ms`；
  存储/导出用的是 raw `Character.timestamps[]`。`_global_offset_ms` 是**项目级单一值**，
  通过对每个 Character 调 `ch.set_offset(同一值)` 统一施加（`timing_interface.py:1035` 等）。

### 关键不变式 / 写入链路

- `Character.push_to_ruby()`（`models.py:255`）做三件事：
  1. `ruby.timestamps = self.all_timestamps`（= `timestamps[]` + 句尾点，绝对时间戳）
  2. `ruby.singer_id = self.singer_id`
  3. 每个 part：`part.offset_ms = timestamps[i] - timestamps[0]`（**相对该字符第一个 cp**）
- 改时间戳的标准副作用：写 raw → `_update_offset_timestamps()` → `push_to_ruby()`
  （见参考实现 `TimingService.adjust_current_timestamp`，`timing_service.py:784`）。
- 句尾呼吸点走 `Character.set_sentence_end_ts()`（内部已含上面两步），它进 `ruby.timestamps`
  末位但**不是 part**（无 `part.offset_ms`）。
- `rebuild_global_checkpoints()` 只按 `check_count` 重建 cp **位置索引**，与时间戳值无关 →
  **拖拽改值不需要 rebuild**。

---

## 2. 已确认的设计决策

| # | 决策 | 结论 |
|---|---|---|
| D1 | 命中方式 | 顶端**把手块**命中，不命中整条竖线；只扫可见窗 + 二分 |
| D2 | 密度门控 | 相邻把手间距 < ~8px 时不进入拖拽，hover 提示"放大以编辑" |
| D3 | 选择 | 单击把手 = seek + 选中；Ctrl+单击 = 多选切换；拖已选把手 = 改时间 |
| D4 | 批量拖动 | 多选 = 刚性平移，共享单一 `delta_ms` |
| D5 | 越界 | **允许**越过相邻点，提交后自动重判为既有紫色非单调警告线 |
| D6 | 句尾呼吸点 | **可拖**（走 `set_sentence_end_ts` 分支） |
| D7 | 吸附 | **不做** |
| D8 | 撤销 | ~~拖拽不接入撤销命令~~ → **修订：拖拽接入撤销**（实测发现拖拽是大幅手势、无简单反向，缺撤销痛点明显）。复用 `_register_timestamp_undo` + `SentenceSnapshotCommand`，Ctrl+Z/Ctrl+Y 经既有 `_on_undo`/`_on_redo` 还原。不改 Alt+↑/↓ 现有逻辑 |
| D9 | 拖拽反馈 | 跟随光标的**偏差值徽标**（主），锚点绝对时间（辅） |
| D10 | preview 同步 | 单击 tag 与拖拽提交（锚点字符）都把 preview 选中同步到该字符 |
| D11 | 总开关 | 打轴设定加 `timing.waveform_tag_edit_enabled`（默认 True），关闭逐像素回退旧模式 |

---

## 3. 详细方案

### 3.1 数据层：补回指映射

- `Project.collect_all_global_timestamp_ms_with_chars()`：元组追加
  `(line_idx, char_idx, cp_idx, is_sentence_end)`。
- `WaveformDisplay.set_time_tags()`：保留该模型句柄（当前丢弃），`_time_tags` /
  `_warning_time_tags` 每项可反查模型。

### 3.2 交互层（`WaveformDisplay`）

新增状态字段：
- `_tag_edit_enabled: bool`（总开关）
- `_selected_handles: set[(line, char, cp, is_end)]`（**用模型句柄存，不用列表下标**）
- `_is_dragging_tags: bool`、`_drag_anchor_handle`、`_drag_anchor_base_ms`、`_drag_delta_ms`

把手与命中：
- 每个 tag 顶端画把手块；命中测试**只判把手矩形**，仅遍历可见窗内 tag + 按 ts 二分。
- 密度门控（D2）：相邻把手 < ~8px 不进入拖拽，hover 提示放大。

鼠标状态机分叉（在现有 pan/seek 之上）：
- `mousePressEvent`：先做把手命中。命中**已选**把手 → 进入拖拽态（`_is_dragging_tags=True`）+
  `_suspend_auto_scroll()`；否则维持原 pan 预备态。
- `mouseMoveEvent`：拖拽态 → 计算 `delta_global_ms`，组级 0 夹紧后写 `_drag_delta_ms`，`update()`；
  否则维持原 pan。
- `mouseReleaseEvent`：
  - 拖拽态 → 发 `timetags_drag_committed(handles, delta_ms)`，清拖拽态；
  - 命中把手但未拖动 → 发 `tag_clicked(line, char, cp, is_end)` + 选中（Ctrl 则多选切换）+
    `seek_requested(ts)`；
  - 未命中把手且未平移 → 原 seek。
- 拖拽期间临时忽略 wheel-zoom（避免映射漂移）。

选中态样式（见 §4）；选中集在外部数据变更时清空（见 §3.5）。

### 3.3 写入层（`timing_interface` 提交槽）

收到 `timetags_drag_committed(handles, delta_ms)`：

1. **按 Character 分组**应用：
   - 普通 cp：`char.timestamps[cp_idx] += delta_ms`
   - 句尾点：`char.set_sentence_end_ts(raw + delta_ms)`
   - **组级 0 夹紧**：`delta = max(delta, -min(本组选中 raw_ts))`，保证整体刚性、无 cp 跌破 0。
   - 每个 Character 全部写完后 **只调一次** `_update_offset_timestamps()` + `push_to_ruby()`
     （句尾分支已内含，不重复）。
2. **不**调 `rebuild_global_checkpoints`。
3. **delta 域恒等**：因 offset 是项目级统一值，`delta_raw == delta_global`，写入直接用 `delta_ms`。

> 备注：拖 cp0 会使 `base_ts` 变、同字符兄弟 part 的 `offset_ms` 连带重算，这是相对偏移的
> 正确语义，`push_to_ruby` 每次重算全部 part，自洽。

### 3.4 提交副作用（完整清单，notify 不会代劳的要显式调）

`notify("timetags")` 自动覆盖：脏标记 + 防抖 auto-save（`project_store.py:399`）、
状态栏进度（经 `_on_data_changed("timetags")` → `_update_status`）、波形刷新
（`_schedule_time_tags_update`）。

**必须显式补调**（`_on_data_changed("timetags")` 不刷 preview）：
- `_update_time_tags_display()`
- `refresh_lyric_display()` —— KaraokePreview 经 `line_interface.py:78` 直接渲染
  `global_timestamps`，不刷会滞后
- `_update_line_info()`
- `_store.notify("timetags")`

（即完整复刻 `_adjust_current_timestamp` 的四件套，`timing_interface.py:4024`。）

### 3.5 preview 选中同步（D10）

复用 `_on_checkpoint_clicked`（`timing_interface.py:4005`）逻辑：

```python
self._suppress_cp_cursor_move = True
try:
    self._timing_service.move_to_checkpoint(line, char, cp)  # 句尾点 cp=check_count 亦可解析
finally:
    self._suppress_cp_cursor_move = False
self.preview.set_current_position(line, char)
self.preview.set_focus_position(line, char)
self._update_line_info()
```

- **单击 tag**：`tag_clicked(line,char,cp,is_end)` → 上述逻辑；seek 仍走 `seek_requested → _on_seek`。
- **拖拽提交后**：对**锚点字符**（被按住拖动的 handle）再调一次同步逻辑。
- 复用此路径自动继承 `disable_click_recenter` 等既有点击语义。

### 3.6 外部变更时清空选中集

在 `_on_data_changed` 的 `project` / `lyrics` / `checkpoints` / `rubies` 分支里清空波形选中集，
避免撤销 / 注音分析 / 增减节奏点后句柄越界悬空被下次拖拽误写。

### 3.7 总开关（D11）

- 设置 key：`timing.waveform_tag_edit_enabled`，**默认 True**（交互叠加式、低风险）。
- UI：`settings/sub_interfaces/timing.py` 打轴设定组内加 `SwitchSettingCard`
  （仿 `card_disable_click_recenter`），并补 `connect_signals` / `load_settings` /
  `collect_settings` 三处。
- 运行时下发：`_apply_settings_inner`（`timing_interface.py:592`）读取后调
  `timeline.waveform_display.set_tag_edit_enabled(bool)`；经 `data_changed("settings")` 即时生效。
- **回退语义（关闭时）**：`WaveformDisplay` 把所有新行为 gate 在 `_tag_edit_enabled`：
  - `paintEvent`：不画把手、不画选中高亮，竖线维持当前纯绘制（逐像素等同今天）；
  - 鼠标事件：跳过把手命中 / 拖拽分支，左键=seek、拖动=pan（等同今天）；
  - 关闭时清空选中集。

---

## 4. 选中态与拖拽反馈样式

现有配色（避让）：普通 tag = 红 `#FF6B6B`（`accent_warning`，2px）；非单调 = 紫
`#CC44FF`/`#9922CC`（`timetag_nonmonotonic`，3px）；播放头 = 青绿 `#4ECDC4`（`accent_primary`）。

**选中态用把手块表达，竖线保留语义色**（让"选中"与"单调状态"两维度互不遮挡）：
- 未选中把手：空心描边，用该 tag 自身的红 / 紫色。
- 选中把手：**实心填充 `accent_secondary` 蓝 `#5B9BD5` + 放大约 1.5×**；竖线保持红/紫语义色，
  额外 +1px 加粗。
- 三色互不冲突：选中(蓝) / 单调状态(红·紫) / 播放头(青绿)。

**标签防遮挡**：顶部把手块（半宽至多 `_HANDLE_SEL_HALF_W`）会压住贴顶绘制的字符/注音标签。
编辑开启时标签整体右移 `_HANDLE_SEL_HALF_W + 3` px 让开把手；关闭时维持原 `x+2`（旧模式逐像素一致）。

**标签显示开关（字符 / 注音分离）**：两个独立设置——
`timing.waveform_tag_char_enabled` 控制本体字符、`timing.waveform_tag_ruby_enabled` 控制注音
(ruby)，默认均 true，四种组合任意搭配。与拖拽总开关 `waveform_tag_edit_enabled` 也互相独立
（可只拖不显字、只显字不拖、只显注音等）。下发经 `_apply_settings_inner` →
`set_tag_char_enabled` / `set_tag_ruby_enabled`；绘制时按两个 flag 拼接 `display_text`。

**拖拽反馈徽标（D9）**：拖拽态下 `paintEvent` 末尾（播放头之后）画跟随光标的浮动徽标：
- 第一行（核心）：有符号偏差 `Δ +120 ms` / `Δ −85 ms`（`+`=延后/右移）。多选共享此值。
  显示的是**夹紧后的有效 delta**。
- 第二行（辅助，仅锚点）：锚点拖到的绝对时间 `→ m:ss.mmm`（沿用 `timing_interface.py:6320`
  的 `{m:02d}:{s:02d}.{ms:03d}`）。
- 半透明底 + 文字保证压在波形上可读；贴边夹到 widget 内。
- 选中集横跨 `_time_tags`(红) 与 `_warning_time_tags`(紫) 时，两个绘制循环都要判选中并施加
  `_drag_delta_ms`，避免"一半动一半不动"。

---

## 5. 性能

- 绘制：本就是 O(N) 全量 + 可见窗过滤；加把手 / 高亮是常数倍，非数量级变化。
- 命中测试：**限定可见窗内 + 二分**（业界 canvas 时间轴通行做法：area virtualization）。
- 拖拽：过程**纯屏幕预览**（只移动选中把手几何 + 重绘），**松手才提交一次**；避免每帧
  O(N log N) 重排 + notify 全链路。
- 波形峰值缓存不受影响（缓存键 = `(width, zoom, scroll, samples_id)`，拖 tag 不动这些）。

---

## 6. 影响文件清单

| 层 | 文件 | 改动 |
|---|---|---|
| 数据 | `backend/domain/project.py` | `collect_..._with_chars` 元组加 `(line,char,cp,is_end)` |
| 控件 | `frontend/editor/timing/timeline_widget.py` | 把手命中 / 选中集 / 拖拽状态机 / 徽标 / `set_tag_edit_enabled` / 新信号 `tag_clicked`、`timetags_drag_committed` |
| 编辑器 | `frontend/editor/timing_interface.py` | 连接新信号；提交槽（分组写入 + 副作用四件套）；preview 同步复用 `_on_checkpoint_clicked`；`_apply_settings_inner` 下发开关；`_on_data_changed` 清选中集 |
| 设置 | `frontend/settings/sub_interfaces/timing.py` | 新增 SwitchSettingCard + connect/load/collect |
| i18n | `scripts/translations_*.json` + 刷新流水线 | 新增 tr 串（开关标题/描述、徽标如有文案） |

---

## 7. 残留风险（已决策，备案）

- ~~无撤销~~ → 已修订为支持撤销（见 D8）：提交槽先 `deepcopy` 快照，写入后
  `_register_timestamp_undo` 注册 `SentenceSnapshotCommand`；Ctrl+Z 经 `_on_undo`
  还原（preview wipe 缓存是时间戳无关的版本化布局，repaint 即反映还原值）。
- preview 同步已纳入（D10），不再是风险。

---

## 8. 实现状态与校验点

实现分 5 批提交完成（分支 `feat/waveform-timetag-drag`）：数据层句柄 → WaveformDisplay
交互 → timing_interface 接入 → 设置开关 → i18n。单元测试 +7（test_project 句柄契约、
test_timeline_tag_drag 提交槽 6 项不变式），全量 `tests/unit` 相对 base 零回归。

自检（已通过 headless 单元 / 控件 / 编辑器构造 smoke 验证）：

- [x] 单击把手：seek + 选中蓝把手 + preview 选中同步（`tag_clicked` → `_sync_preview_to_handle`）。
- [x] Ctrl 多选：多选切换；刚性拖动共享 delta（`tags_drag_committed` 携带 handles+delta）。
- [x] 拖 cp0 / 拖句尾点：ruby.timestamps 与 part.offset_ms 正确（test_timeline_tag_drag 覆盖）。
- [x] 越界：提交后由 `set_time_tags` 按 running_max 自动重判为紫色非单调线。
- [x] 组级 0 夹紧：`_clamp_drag_delta`（widget 预览）+ 提交槽逐 cp `max(0)` 安全网。
- [x] preview / 行字信息 / 状态栏 / 脏标记 + auto-save：提交槽四件套 + `notify('timetags')`。
- [x] 关闭开关：`set_tag_edit_enabled(False)` 不画把手 / 不命中 / 清选中，回退 seek/pan。
- [x] 新 tr 串：en/ja/zh `.ts` 已译且 finished，`.qm` 编译 0 unfinished。
- [x] 撤销/重做：拖拽后 Ctrl+Z 还原、Ctrl+Y 重做（端到端 CommandManager 往返测试）。

**i18n 实现说明（偏离原流水线）**：`extract_ts.py` 输出为字母序排序，与当前 committed `.ts`
的 lupdate 源位置序不一致，整体重生成会churn ~1800 行。为保持本特性 diff 干净，改为
**直接在 3 份 `.ts` 的 `TimingSubInterface` 上下文内插入 2 条 message** 再 `pyside6-lrelease`
编译 `.qm`（每文件 +10 行）。translations_*.json 仍按规范补录，供后续全量 regen 使用。

**残留待人工验证（GUI 交互，headless 无法覆盖）**：真实鼠标拖拽手感、徽标视觉位置 /
可读性、密度门控阈值在实际缩放下的体感、切换语言后设置卡文案刷新。
