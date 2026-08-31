# 波形图升级：声谱图模式 + 齿轮高级设置 + BPM 网格 — 方案书

> 目标：为时间轴波形区（`TimelineWidget` / `WaveformDisplay`）新增**声谱图（spectrogram）显示模式**，
> 与波形图互斥切换；在波形开关左侧新增**齿轮按钮**打开高级设置对话框（显示模式、BPM 网格、频谱参数）；
> 支持 BPM 自动识别（numpy 实现，无新依赖）。

状态：已实施并通过全量测试（2026-08-27）。实施记录：
`spectrum_core.py`（纯 numpy 算法）、`workers.py`（后台 worker）、
`timeline_widget.py`（渲染/交互）、`waveform_advanced_dialog.py`（齿轮对话框）、
`timing_interface.py` + `app_settings.py` + `config.json`（持久化）、
`tests/unit/frontend/test_spectrum_core.py` + `test_spectrum_mode.py`（32 项新增测试）。

---

## 1. 性能调研结论（本机实测：Python 3.14 + numpy 2.4.4，与项目 requirements 一致）

| 环节 | 实测数据 | 结论 |
|---|---|---|
| 全文件 STFT（hop=fft/4） | **≈140ms / 每分钟音频**（1min≈143ms、3min≈410ms、5min≈0.7s、10min≈1.45s），与 FFT 窗口大小基本无关 | 必须放后台线程，不能阻塞 UI |
| numpy rfft 与 GIL | 后台跑 1M×1024 rfft 时，主线程纯 Python 吞吐保留 **80%** | rfft 释放 GIL，QThread 后台计算可行，UI 不卡 |
| 内存（uint8 量化 dB 矩阵） | **≈5.3MB / 每分钟音频**（5min=26MB、10min=53MB）；float32 是 4 倍，弃用 | 缓存量化为 uint8（0.5dB/步，固定 -128~0dB 量程），切歌/换文件时释放 |
| 金字塔 | **在 worker 线程构建**（10min 音频 ≈35ms，不占 UI）；完整金字塔 ≈2×矩阵，超 `PYRAMID_BUDGET_BYTES`(256MB) 时只保留原始矩阵，渲染侧按需惰性构建**单个**所需层（缓存 ≈1.5×矩阵） | 兼顾长音频内存与缩放性能 |
| 视图重渲染（缩放/滚动时静态层重建） | 金字塔分层后：W=800→8.7ms、W=1600→17.6ms、W=2560→31.5ms；10s 缩放窗口 8.2ms。**不建金字塔直接扫全矩阵要 50~170ms（会卡）** | 与现有 `_waveform_peak_levels` 同思路：帧数按 2^k 分组求 max 建金字塔，选「行数 ≥ 2×列宽」的最粗层做 `np.maximum.reduceat` |
| 播放期间逐帧绘制 | **0 额外开销**——频谱画进静态 QPixmap 层，逐帧只 blit 图层 + 播放头，与现状一致 | 60fps 播放刷新不受影响 |
| 动态范围/频率刻度调整 | 只改颜色 LUT 映射 / 行分组边界，**不重算 STFT**（<5ms） | 即时生效；LUT 阈值按 `(128-R)·255/128` 换算并把可见段拉伸到全色带 |
| FFT 窗口大小调整 | 需重算 STFT（后台，可取消） | 计算期间显示「计算中 x%」占位 |
| 对照：现有波形峰值整文件归约 | 4.7~10.6ms | 频谱视图渲染与现有波形同量级 |

**线程生命周期（owner 销毁安全）**：线程不 parent 到任何 UI 控件，由
`task_runner` 模块级注册表持引用并自回收（`finished → thread.quit →
thread.finished → deleteLater`）；UI 槽经 `TaskRelay`（owner 子对象，每任务一个，
`is_current` 闭包过滤过期结果）连接——owner 销毁时中继随之销毁、排队信号被 Qt
丢弃，**槽内不用 `sender()`**（worker 的同线程直连 `deleteLater` 可能在排队
信号送达前析构 sender，实测会悬垂）。owner 销毁自动请求取消；应用退出由
`aboutToQuit → task_runner.shutdown` 收尾（唯一允许 wait 的路径）。
正常 UI 路径全程无 `wait()`。子进程探针测试覆盖「计算中销毁时间轴」「检测中
销毁弹窗」两个崩溃场景。

**资源策略**：切回波形模式 / 隐藏波形区时取消在途声谱计算（保留已完成缓存
便于快速切回）；音频切换立即释放旧缓存；金字塔受 `PYRAMID_BUDGET_BYTES`
预算管理，超预算时长音频由 worker 后台构建受预算限制的中间粗层金字塔，
深缩放用可见切片归约——**UI 绘制路径不同步构建任何完整层**。

**dB 标尺**：单边谱幅度按 `2·|X|/Σwindow` 归一化——加窗正弦的峰 bin 幅值
≈ a·Σwindow/2，故该式 ≅ 信号幅度 a（满幅正弦 ≈ 0dB），与量化量程对齐。

### BPM 自动识别可行性

可行，采用业界标准管线（纯 numpy 复刻，零新增依赖）：

- **算法出处**：Ellis 2007《Beat Tracking by Dynamic Programming》三段式的前两段，
  即 librosa 的实现路线（librosa.onset.onset_strength + librosa.feature.tempo）。
- **onset 强度包络**：mel 功率谱（128 mel、n_fft=2048、hop=512，Slaney 滤波器组）→
  dB（top_db=80 相对地板）→ 一阶差分 → 半波整流 → 跨 mel 频带取均值。
- **tempo 评分**：onset 包络的 Fourier tempogram（包络 DFT 在 BPM 频率处连续取样，
  无整数滞后量化误差）× Ellis 梳状谐波支持（周期 2/3/4 倍加权）× librosa 默认
  对数正态先验（center=120 BPM、std=1 个八度），`log1p(1e6·x)+logprior` 后 argmax。
  （实测注意：150 BPM 在 hop=512 下周期 34.45 帧，整数 lag 自相关只能对齐一半节拍，
  会错检成 75 BPM——这是 librosa 默认自相关 tempogram 也存在的量化，Fourier tempogram 规避之。）
- 耗时 <0.3s（后台 worker）。对节奏稳定的流行/动画歌曲（mp3/mp4 分离出的音频）通常可靠；
  纯人声清唱、自由节奏（rubato）会误判。做成「自动检测」按钮，
  **结果填回 BPM 输入框供确认，不静默生效**。

---

## 2. UI 设计

### 2.1 底部控制栏（`timeline_widget.py` `_init_ui`）

顺序变为：`zoom_slider` → `zoom_label` → `scroll_bar`(stretch) → `lbl_audio_name`
→ **[新增齿轮按钮]** → `switch_waveform`。

- 齿轮按钮：`TransparentToolButton(FIF.SETTING)`（先例：`sug_concat_dialog.py:206`），
  tooltip「波形图高级设置」，点击弹出高级设置对话框；`changeEvent` 补充重译。

### 2.2 高级设置对话框（`frontend/editor/timing/waveform_advanced_dialog.py`）

**普通非模态 Fluent 顶层窗口**（非遮罩式 MessageBoxBase）：`show()` 展示、
无遮罩不拦截主程序其他区域；以主窗口为定位锚点，由 `TimelineWidget` 持有
强引用直到销毁（重复点击齿轮复用同一实例并刷新音源）。改动即时生效：

1. **显示模式**（药丸三选）：波形图 / 声谱图（互斥）/ 双谱模式（上下并排，
   双方参数同页可调，见 §6）。
2. **网格**（两种模式通用）：时间网格 / BPM 网格单选；BPM 数值输入（30.0~300.0，步进 0.1）
   + 「自动检测」按钮（无音频禁用；检测中显示进度百分比；成功填回数值并给置信度提示）。
3. **频谱参数**（仅声谱图模式启用）：FFT 窗口（512/1024/2048/4096/8192，默认 2048≈21.5Hz 分辨率）、
   频率刻度（对数/线性，默认对数）、动态范围（40~120dB 滑条，默认 90）、
   频谱高度（120~400px 滑条，默认 120 = 旧版不可调时的波形窗口高度）。
4. **时间标签**（面板置于声谱参数之后）：标签拖拽 / 播放头居中 / 标签显示字符 /
   标签显示注音四个开关，与设置页「打轴 → 波形时间标签」组**共用同一组
   `timing.waveform_tag_*` 键**：弹窗改动经
   `applied → _apply_display_settings → display_settings_changed` 持久化；
   设置页改动经设置级联回到 `_apply_display_settings`，并经
   `sync_tag_settings()`（blockSignals 防回环）刷新打开中的弹窗。字符关→注音
   开关禁用，与设置页联动一致。

### 2.3 高度与布局协商

「显示高度」是**期望高度**（120~400px，波形/声谱公共属性，持久化保存）：
经 `sizeHint()`（Preferred）表达而非硬 `minimumHeight`——显示区硬下限恒为
80px，空间不足时布局压缩显示区，**顶层窗口绝不被撑出请求尺寸**（曾实测
请求 713px 得到 855px）。`TimelineWidget.sizeHint()` 跟随「显示区 hint +
底栏」；预览（Expanding）吸收剩余。歌词预览（固有 min 400）在声谱模式或
**窗口空间不足**（总 min > 窗口高，singleShot 延后查询防布局重入）时让位
到 160。测试断言真实 show 后的 geometry 无重叠 + 窗口尺寸不超请求。

频率轴 gutter（40px）：声谱模式左侧为独立频率轴区（背景 + 分隔线 + 刻度），
热图/网格/tag/播放头统一从轴区右侧开始；`_x_to_time`/把手命中/拖动/平移/
Ctrl+滚轮锚点全部按 `_plot_width()` 换算——频率刻度与视口左缘的 tag 物理
分离，互不覆盖。

### 2.4 波形/声谱分辨率（对齐 Sonic Visualiser）

- **窗口重叠**（声谱，SV 的 Window Overlap 口径）：50% / 75% / 87.5% / 93.75%，
  帧距 hop = FFT÷{2,4,8,16}。大 FFT 窗口损失的时间分辨率靠更大重叠补偿
  （2048 点 93.75% ≈ 2.9ms/帧）；重叠越大计算量与缓存内存同比例增长。
  波形与 SV 一致：每像素 min/max 峰值，分辨率由缩放决定（无独立粒度概念）。
- **双层波形**（SV / Audacity 口径）：外层 min/max 峰值轮廓（半透明 fill，
  展示瞬态极值）+ 内层 RMS 均方根核心带（亮色 line，展示持续响度）。
- **缩放上限** 100x → **1000x**（滑条对数映射与 Ctrl+滚轮同步 1~1000，
  3 分钟歌 1000x 可见窗 ≈0.18s）。
- **网格线宽**（时间/BPM 共用，0~100px；0 = 不绘制网格）：半拍/拍线/小节线
  按 基础-1 / 基础 / 基础+1 层级递进。
- **BPM 网格偏移**（±600000ms，毫秒输入，失焦/回车提交）：拍时刻 =
  偏移 + b·拍长（b 为整数，可为负）。节拍通常不从 0ms 开始——正值把
  网格后移（延迟）、负值前移，用于把拍线相位对齐到歌曲实际节拍。半拍
  细分与小节号随相位联动；b=0 恒为第 1 小节第 1 拍，负数拍仍画线但
  不标注小节号。持久化键 `timing.waveform_grid_offset_ms`。
- **显示高度**（波形/声谱公共属性，120~400px，位于「网格与节拍」Panel）：
  对两种模式同时生效。
- 输入框提交：editingFinished（失焦/回车——按钮 autoDefault 已关闭，Enter
  不再被「自动检测」吞掉）即时提交 + 输入停顿 400ms 防抖兜底。
- 「实际 N px」提示实时同步：显示区高度变化（eventFilter Resize）经
  actual_spectrum_height_changed 信号推送弹窗。
- **内存预算**：基础声谱矩阵（frames×bins）在 worker 启动前预估，超
  `SPECTRUM_MATRIX_BUDGET_BYTES`(384MB) 自动逐级降重叠（93.75→87.5→75→50），
  最低档仍超则拒绝并提示（1 小时 @93.75% 未设门禁约 1.18GiB，有 OOM 风险）；
  金字塔预算 256MB 不变。波形峰值层缓存为 LRU + 96MB 预算。
- **后台预热**：加载音频后经 task_runner 在后台线程预计算 min/max/RMS
  峰值层（按预算从粗到细），首次缩放/滚动命中缓存零等待；预热失败静默
  （渲染时同步兜底）。
- **活动门禁**：`_spectrum_active=False`（隐藏/后台）时任何路径（换音频/
  改参数/切模式）都不得重启声谱计算；恢复可见时按需启动。
- 输入框严格失焦语义：只 editingFinished（失焦/回车，按钮 autoDefault 已关）
  提交；点击面板空白经 mousePressEvent 显式清焦触发提交。

### 2.5 后台资源策略

- 切回波形模式 / 关闭波形显示开关：取消在途声谱计算（保留已完成缓存便于快速切回）。
- 应用最小化 / 切后台（`background_throttle.visibility_maybe_changed`）：暂停在途
  计算，回前台且仍处声谱模式时按需恢复。
- 音频切换立即释放旧缓存；金字塔受 `PYRAMID_BUDGET_BYTES` 预算管理。

---

## 3. 架构与数据流

```
加载音频（float32 np 数组，已有链路）
  └→ 声谱模式激活 / 音频变更 / FFT 变更
      └→ 后台 SpectrogramWorker（QThread + moveToThread，遵循 frontend/workers.py 约定）
          分块 STFT（每块≈30s，发 progress，可 request_cancel）
          → uint8 量化的 (frames × bins) 矩阵（-128~0dB，0.5dB/步）
          → worker 线程内预建金字塔（每层 = 上一层 2 帧分组合并取 max）；
            总内存超 PYRAMID_BUDGET_BYTES(256MB) 时不建，渲染侧按需惰性构建单层
      └→ 视图渲染：按可见窗口/缩放选金字塔层 → reduceat 列/行 → LUT 上色 → QImage → 静态图层
```

- 世代计数（generation）作废过期结果（换音频 / 改 FFT 时旧任务作废）。
- 切回波形模式瞬时（峰值缓存未清）。
- 播放头、时间标签拖拽、A/B 区间、seek/缩放/滚动交互对两种模式完全一致——只替换静态层里的
  「波形层」部分，网格/标签/播放头绘制不变。

### 文件改动

1. **`frontend/editor/timing/timeline_widget.py`**（主要改动）
   - `WaveformDisplay`：`_display_mode` / `_grid_mode` / `_grid_bpm` / 频谱参数状态；
     `_spectrogram` 缓存（矩阵 + 金字塔 + hop/fft 元数据）；计算状态机（idle/computing/ready + 进度）；
     `_render_static_layer` 分支到 `_draw_spectrogram`（含计算中占位）；
     `_compute_spectrogram_view()` 金字塔选层 + reduceat + LUT → QImage；
     `_draw_time_grid` 扩展 BPM 网格（拍线、每 4 拍小节线加重 + 小节号、高倍缩放半拍细分；
     拍号固定 4/4）与频谱模式频率标签；`set_audio_data` 联动重算。
   - `TimelineWidget`：齿轮按钮、`set_display_mode` / `set_grid_*` / `set_spectrum_*` 透传、
     `display_settings_changed(dict)` 信号、`changeEvent` 重译。
2. **`frontend/workers.py`**：`SpectrogramWorker`（progress/finished/error + request_cancel）、
   `BpmDetectWorker`（自带 fft=1024/hop=512 轻量 STFT → onset 包络 → 自相关，返回 `{bpm, confidence}`）。
3. **`frontend/settings/app_settings.py`**：`DEFAULT_SETTINGS["timing"]` 新增
   `waveform_display_mode`("waveform")、`waveform_visible`(true，顺手持久化现有开关)、
   `waveform_grid_mode`("time")、`waveform_grid_bpm`(120.0)、`spectrum_fft_size`(2048)、
   `spectrum_freq_scale`("log")、`spectrum_dyn_range_db`(90)、`spectrum_height`(120)；
   镜像到 `src/strange_uta_game/config/config.json`。
4. **`frontend/editor/timing_interface.py`**：`_apply_settings` 推送新键；连接
   `display_settings_changed` → 写回 AppSettings；启动时应用持久化的显示开关状态。
5. **翻译**：按 `frontend/localization/WORKFLOW.md` 跑全流程
   （extract_ts → 补 ja/en JSON → apply_translations → build_zh_CN → pyside6-lrelease）。

---

## 4. 测试（tests/unit/frontend/）

- STFT 正确性：440Hz 正弦峰值落在正确频 bin；uint8 量化往返；金字塔构建/选层规则；
  视图渲染形状与 dtype；对数/线性行边界单调。
- BPM 检测：合成 128 BPM 脉冲曲 → 128±1；90 BPM 慢速用例；含噪声稳健性。
- 设置默认值与读写往返；模式切换最小高度变化。

## 5. 约束

- 不引入新依赖：scipy 不用，全部 numpy（`numpy.fft` 已在 build.py hidden-import 中）。
- 遵循现有模式：workers 全部 QObject + moveToThread；设置走 AppSettings `timing.*` 键。

## 6. 双谱模式（display_mode = "dual"）

同一时间轴上**上下并排**显示波形图 lane（上）与声谱图 lane（下），
两条 lane 共用同一时间轴/滚动/缩放/播放头。持久化键仍为
`timing.waveform_display_mode`（枚举扩展为 `waveform / spectrum / dual`）。

### 6.1 布局与高度

- 显示区 `minimumHeight` = `waveform_display_height + spectrum_display_height`
  （两 lane 各按期望高度领取空间，240~800px）。
- 空间不足被布局压缩时按两期望高度的**比例**分摊（`_dual_lane_heights`），
  相对占比与用户设置一致。
- 频率轴 gutter 与声谱模式同宽（50px），只沿下半声谱 lane 绘制；
  lane 边界画 1px 分隔线。

### 6.2 参数同时可调（齿轮弹窗）

双谱页**同时显示**波形设置面板与声谱设置面板（`_update_params_enabled`
对 dual 路由同时放行两页）：RMS 开关、波形高度、FFT/重叠/刻度/配色/
动态范围/频率范围、声谱高度在同一页一起可调，各自独立生效与持久化。
双谱下两个高度滑条标签区分为**「波形高度」「声谱高度」**（单模式沿用
「显示高度」不变，`_update_height_labels` 随模式/重译切换）；高度上限
提示在双谱下约束的是**两 lane 之和**（超出时显示「实际合计 N px」）。

### 6.3 tag 标记同步

`_time_tags` / `_warning_time_tags` 是唯一数据源：双谱渲染时
`_draw_time_tags` 逐 lane 画一遍竖线与把手（`_tag_lanes` 返回两条 lane，
把手在各 lane 竖直中央，密度门控逐 lane 独立）；顶部标签轨道整体只有
一条，只画一份。拖拽提交走既有 `tags_drag_committed → 模型写回 →
set_time_tags 重灌` 链路——任一条 lane 上的增/删/拖都同步反映到另一条。
命中测试（`_hit_test_handle`）改为按 `_hit_boxes` 里各把手自带的 lane
中心 y 比对，两条 lane 的把手都可命中。

### 6.4 预览压缩（仅双谱允许单行）

双谱时间轴占用约两倍纵向空间，`_apply_preview_spectrum_yield` 在 dual
模式下把歌词预览的让位下限降到**单行行高**（`KaraokePreview.
single_line_height()`），并把可见行数下限经 `set_min_visible_lines(1)`
放宽到 1——预览可以压缩到只剩一句。其余模式恒为 160px / 3 行下限，
隐藏时间轴时恢复固有 400px / 3 行。

### 6.5 快捷键

「切换波形/声谱/双谱」快捷键为**三模式循环**：波形图 → 声谱图 →
双谱 → 波形图（`_DISPLAY_MODE_CYCLE`），走既有 `display_settings`
持久化链，打开中的齿轮弹窗药丸经 `sync_display_mode` 同步。

