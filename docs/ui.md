# UI 层设计

StrangeUtaGame 提供了三套主要的编辑界面，分别针对打轴、行编辑和全文本编辑任务。

## 核心界面

### 1. EditorInterface (打轴界面)
这是主要的时间标签编辑界面。

- **职责**：
    - 类似节奏游戏的打轴体验，全键盘驱动；实时预览卡拉OK走字 (KaraokePreview)。
    - **工具栏**：「修改所选字符」(ModifyCharacterDialog) 修改文本/注音/节奏点数量，可注册到用户字典，保持句尾标记与演唱者继承；「插入导唱符」(InsertGuideSymbolDialog) 在选中字符前批量插入导唱字符（如 `>`），自动计算时间戳并设置 `linked_to_next`。撤销/重做保留 Ctrl+Z/Y 快捷键。
    - **变速控制**：Transport Bar QLineEdit 直接输入 50-200% 值，Q/W 快捷键 ±10% 步进。
    - **全局 Offset**：工具栏 QLineEdit 实时调整 -2000~2000ms，联动 Character `render_timestamps`。
    - **单击跳转**：单击字符跳到其第一个 checkpoint；无 checkpoint 的字符（check_count=0 且非句尾）视觉焦点保持该字符（便于 F4 添加），内部位置仍移到最近有效 checkpoint。
    - **渲染**：逐字高亮同步 Ruby；无时间戳字符按邻近组时间连读；连词组合并渲染。

### 2. EditInterface (行编辑界面)
提供基于表格的项目全局视图。

- **职责**：
    - 展示所有歌词行及其元数据（行号、演唱者、连词标记等）。
    - **行级操作**：添加行、删除行、复制行、插入行，支持多行选择。
    - **键盘快捷键**：Ctrl+C 复制行、Ctrl+V 粘贴行、Delete 删除行。
    - **LineDetailDialog**：双击行打开详细编辑对话框，可逐个 Character 编辑注音、节奏点、句尾标记（is_sentence_end）和演唱者。
    - **批量变更对话框**：支持多字符划选自动填充注音；移除"将匹配词设为连词"复选框，变更为继承原字符的连词属性。
    - **字符级操作**：在行详情中支持添加、删除、复制、插入字符，Ctrl+C/V/Delete 快捷键。
    - 操作后自动维护领域不变量（is_line_end、is_sentence_end、check_count、linked_to_next）。句尾字符允许 check_count=0（无普通节奏点时以前一个字符的 checkpoint 为开始）。

### 3. RubyInterface (全文本编辑界面)
专注于注音编辑和文本调整。

- **职责**：
    - **全类型自动注音**：汉字、平假名、片假名、英文、数字、符号、空格全部生成注音；「按类型删除注音」可按字符类型批量清除（含長音ー/～、♪、小假名 ぁぃぅぇぉゃゅょゎっ 等）。
    - **连词格式**：`{text|r1,r2,...}` 按 `linked_to_next` 链条分组各字独立读音（如 `{大冒険|だい,ぼう,けん}`、`{One|ワン,,}`）。向后兼容旧版 `漢字{かんじ}`。
    - **自由文本编辑**：直接修改歌词文本，文本未变更时仅更新标注注音不清除未标注；文本变更时通过 SequenceMatcher 保留匹配字符的注音。
    - **自动应用**：切换标签页离开时自动应用修改回项目数据。
    - **更新节奏点**：根据注音与 AutoCheck 规则重新计算（更新前自动应用文本框内容）。

### 4. SettingsInterface (设置界面)
管理全局配置与校准工具。

- **职责**：
    - **Offset 校准（可视化弹窗）**：深色画布上 2 个白色滑块按 BPM 匀速滑过中央红色判定线（接近时放大），`sd.OutputStream` 实时生成节拍器点击音，spin-wait 亚毫秒级定时；空格敲击记录偏移，左上角实时显示最近/平均偏移；BPM 60-240 可调；窗口关闭调 `_stop_metronome()` 清理。
    - **打轴设定**：默认打轴偏移 -130ms；Karaoke 渲染/导出偏移 -100ms；快捷键配置（`add_checkpoint` F4、`remove_checkpoint` F5、`toggle_line_end` F6、`toggle_word_join` F3 等）。
    - **Auto Check 设置**：小写假名 check 开关、句尾判定开关。
    - **定时自动保存**：启用开关 + 间隔 1~60 分钟。
    - **用户字典管理**：条目按优先级排列（新增置顶），上移/下移调整；支持导入 RL 字典（`原文\t注音1,注音2...`，`＋` 代表连词）；独立存储于 `dictionary.json`，首次启动自动从内嵌 1757 条初始化，重置配置不影响字典。
    - **配置管理（About 界面）**：「打开目录」「更改位置」(通过 `.config_redirect`)；`config/` 作为 package data 嵌入，缺失时回退内嵌版本。

### 5. KaraokePreview (卡拉OK预览)
打轴界面内嵌的实时走字预览组件。

- **职责**：
    - 逐字高亮渲染 + Ruby 同步走字；无时间戳字符按邻近组时间均分连读；连词组合并渲染 Ruby 与连词框。
    - 使用 `ch.render_timestamps` 和 `ch.render_sentence_end_ts`（预计算偏移），消除双重偏移逻辑。
    - **渲染缓存**：每帧复用逐句渲染数据（字符宽度、分组、wipe 时间、连词组），仅在数据变更时重算，降低 60fps CPU 开销。

## 前端目录结构

| 路径 | 职责 |
|------|------|
| `frontend/editor/timing_interface.py` | `EditorInterface` 主类（打轴主控） |
| `frontend/editor/timing/` | 从 `timing_interface` 拆出的子组件：`TransportBar` / `EditorToolBar` / `KaraokePreview` / `TimelineWidget` / `ModifyCharacterDialog` / `InsertGuideSymbolDialog` / `CharEditDialog` / `BulkChangeDialog` / `_SentenceSnapshotCommand` |
| `frontend/editor/edit_interface.py` | `EditInterface` 行表格编辑 |
| `frontend/editor/ruby_interface.py` | `RubyInterface` 全文本/注音编辑 |
| `frontend/editor/timing/bulk_change_dialog.py` | 批量字符替换对话框（通过 `_SentenceSnapshotCommand` 支持撤销） |
| `frontend/settings/settings_interface.py` | `SettingsInterface` 主界面 |
| `frontend/settings/{app_settings,cards,dictionary_dialog,nicokara_dialog,calibration_dialog}.py` | 设置界面的配置模型、卡片控件、字典/导出/校准对话框 |
| `frontend/singer/singer_interface.py` | `SingerInterface` 演唱者管理 |
| `frontend/startup/`, `frontend/online/` | 启动向导、在线查询（占位） |

为保留历史 `from ...editor.timing_interface import X` 和 `from ...settings.settings_interface import X` 路径，主模块对拆出的符号进行了 re-export。

## 辅助界面
- **MainWindow**：FluentWindow 主框架 + 侧边栏导航。图标 `resource/icon.ico`（开发/PyInstaller 双兼容）；全局 Ctrl+S 保存（无路径弹另存为）；切换标签页时自动应用全文本编辑修改；启动时询问闪退恢复；未保存关闭弹中文按钮（保存/放弃/取消）。
- **StartupInterface / HomeInterface**：项目创建入口（向导 + 主页）。
    - **导入流程**：拖入/选择文件时仅读原始内容到文本框；格式解析延迟到「创建项目」时执行。
    - **格式检测**：自动识别 ASS / SRT / LRC（逐行/逐字/增强型）/ Nicokara / 内联 / 纯文本；支持 .lrc/.txt/.kra/.ass/.srt 歌词与 .mp3/.wav/.flac 音频拖拽。
    - **演唱者传播**：创建项目时默认演唱者 ID 传播到 sentence / character / ruby 三层，避免 singer_id 留空；Nicokara 通过 singer_key 映射达到同效果。
- **ExportInterface**：导出配置界面。演唱者过滤自动仅列当前实际使用者（QScrollArea 可滚动）；「插入【演唱者名】标签」作为独立设置项，与过滤无关。
