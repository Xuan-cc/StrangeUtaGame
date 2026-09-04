# 基础设施层设计

基础设施层提供具体的外部系统交互实现，包括项目持久化、歌词解析、多格式导出和音频处理。

## 核心实现

### SugProjectParser / SugMigrator (.sug 项目读写)
基于 JSON 格式的项目文件解析与序列化。当前格式版本 `SugMigrator.CURRENT_VERSION = "0.3.0"`（独立于应用版本号；实现见 `persistence/sug_io.py`）。

- **职责**：
    - `save(project, path)`：将 Project 对象（包含 sentences, characters, ruby）序列化为 JSON 字符串并保存，写入 `version` 字段。
    - `load(path)`：从文件读取 JSON 并反序列化为 Project 实体树（含 `sentence_end_ts`）；`version` 不等于 `CURRENT_VERSION` 时先经 `SugMigrator.migrate` 升级。
    - **向后兼容迁移**：旧版文件按 `1.0 → 2.0 → 0.3.0` 链式升级（line→sentence 结构、Ruby 分组 / RubyPart 模型重建）。
    - **格式特点**：按 Sentence → Character → Ruby 结构存储，不保存音频物理路径。

### inline_format (内联文本处理)
提供一种便于在文本编辑器中阅读和修改带时间标签的格式。

- **职责**：
    - `to_inline_text(sentence)`：将带时间标签的句子转换为带有 `[timestamp]` 标记的文本字符串。
    - `from_inline_text(text)`：解析内联格式文本并重建 Sentence 和时间标签。

### 注音分析器（WinRT / Sudachi 双引擎）
提供高精度的日语分词与注音分析实现。两种分词器均继承分词器无关基类 `KanaDistributingAnalyzer`（假名锚点 + pykakasi 参考的两步分配策略），仅"分词 + 读音获取"实现不同。

- **WinRTAnalyzer（main 变体主引擎）**：
    - **WinRT IME 上下文感知**：基于 `Windows.Globalization.JapanesePhoneticAnalyzer`，按整段输入做上下文消歧分词。
    - **运行时依赖**：注音引擎来自系统日语功能 `Language.Basic~~~ja-JP`（日语 IME）。应用内 `winrt_japanese_status()` 探测、`install_winrt_japanese()`（UAC 提权）/`winrt_install_guidance()` 引导安装。
    - **输入约束**：`GetWords` 单次上限 100 字符（按 ≤100 字切块）；surface 取原文切片（display_text 会半角→全角归一，不可信）。
    - **假名统一**：所有注音输出均为平假名（`yomi_text`）。
- **SudachiAnalyzer（noWinIME / mac 变体主引擎）**：基于 `sudachipy` + `sudachidict_small`（控制打包体积），分词粒度 Mode A（最短形态素，与 WinRT 默认粒度相近）。
- **回退链**：`create_analyzer()` 顺序为 **WinRTAnalyzer → SudachiAnalyzer → PykakasiAnalyzer → DummyAnalyzer**。main 变体优先 WinRT（缺日语 IME 由 UI 引导安装）；noWinIME/mac 变体无 winrt 包，直接落到 Sudachi。
- **内置词典**：缺失 `dictionary.json` 时自动从内嵌的 RL 词典初始化。
- **`_is_kanji`**：判定范围含 CJK Unified Ideographs (U+4E00-U+9FFF)、CJK Extension A (U+3400-U+4DBF)、CJK Compatibility (U+F900-U+FAFF)、以及迭字记号 `々` (U+3005)。

### kanji_readings (单字音读字典)
基于 KANJIDIC2 项目提取的全量汉字读音数据，用于复合词读音拆分。

- **数据源**：KANJIDIC2 XML（Jim Breen / EDRDG 维护），包含 12000+ 汉字的音读（on）和训读（kun）。
- **文件**：`src/strange_uta_game/config/kanji_readings.json`（作为 package data 打包），格式 `{字: {on: [...], kun: [...]}}`；拆分逻辑在 `infrastructure/parsers/kanji_reading_split.py`。
- **职责**：
    - 为 `_try_split_to_chars` 的 Pass 2（音读字典组合匹配）提供单字候选读音。
    - 不包含连浊、缩读、特殊读法——这些属于用户词典 `dictionary.json` 的范畴。

### lyric_parser (歌词解析器)
支持多种原始歌词格式的导入。

- **职责**：
    - `parse_to_sentences(content, format)`：支持 TXT, LRC（逐行/逐字/增强型）, KRA, ASS, SRT 等格式。
    - 负责从原始文本中提取歌词行、注音和现有的时间标签。
    - **增强型 LRC 支持**：解析 `<mm:ss.xx>` 尖括号逐字时间标签格式。
    - **ASS 解析器**：解析 ASS 字幕的 `[Events]` Dialogue 行，提取 `\kf`/`\k`/`\ko` 卡拉OK时间标签（厘秒→毫秒转换）。
    - **SRT 解析器**：解析 SRT 字幕的块结构（序号、`HH:MM:SS,mmm --> HH:MM:SS,mmm` 时间戳、文本），自动剥离 HTML 标签。
    - **工厂模式**：`LyricParserFactory` 根据文件扩展名（.txt/.lrc/.kra/.ass/.srt）自动选择解析器。

### rl_dictionary (RhythmicaLyrics 词典解析器)
用户字典导入的纯文本解析。

- **职责**：
    - `parse_rl_dictionary(text) -> List[Dict]`：自动识别多种 RL 文本格式 → annotated 条目列表。
        - **输入格式识别**（HSP 字面体 → INI ``[AutoCheckDefine]`` 段 → tab 行 → 成对行兜底）。
        - **piece 语义**（按 RL 源码 ``@RhythmicaLyrics.hsp:12636+`` 应用路径还原）：
            * 末尾全角 `＋`（U+FF0B）→ 该字符与下一字符**连词**（同 annotated 块）；
            * 末尾 `/<N>` → 强制 cp 数（注音格式不承载，下游按 RubyPart 派生）；
            * 整段为半角数字 → 该字符**无 ruby + 显式 cp**（注音格式不承载）；
            * 多 mora ruby → 按 mora（小假名 / ``ー`` 附属前拍）拆分为 ``|`` 段（复用 `inline_format.split_into_moras`）；
            * ruby == 字符（kata→hira 归一化）→ 字面输出（不包 ``{...||...}``）。
        - **整体规则**：尾部 `@<digit>` 标志位（a_chk_kakute_flg 字段）剥离；空 / 仅 `＋` 的尾部 piece 剥离；piece 数与 word 字符数对齐（超出合并到末字符、不足补空）；含 ASCII 字母的 word 丢弃；ruby 全空的条目丢弃。
    - `read_rl_dictionary_file(path) -> str`：utf-8-sig → utf-8 → cp932 → shift_jis 顺序解码。
    - 返回 `[{"enabled": True, "word": str, "reading": str}, ...]`，顺序与输入一致。
    - 前端 `frontend/settings/app_settings._parse_rl_dictionary` 改为薄包装，历史导入路径保留。

### network_dictionary (网络读音词典)
独立于本地词典的多源网络词典容器，与 RhythmicaLyrics 服务端协议兼容。

- **存储**：**meta 与 cache 分离**。
    - **meta**（用户设置）放在 `config.json["network_dictionary"]`：
        ```json
        {"enabled": false,
         "source_order": ["local", "rl_official", ...],
         "sources": [{"id": "rl_official", "name": "...", "url": "...",
                      "builtin": true, "enabled": true}, ...]}
        ```
        `source_order` 中 sentinel `"local"` 代表本地 `dictionary.json`。
    - **cache**（抓取到的 entries + last_fetched）放在 `network_dictionary.json`（与 config.json 同目录）：
        ```json
        {"rl_official": {"entries": [...], "last_fetched": 1745000000}, ...}
        ```
        UI 中的"条目缓存文件"路径栏明示。
    - 旧版一体式 `network_dictionary.json` 会在首次 `load_network_dictionary` 时自动迁移：拆分 meta → config.json，cache 留下。
- **优先级模型**：两层 —— 源列表序（`source_order`） + 各源内 entries 自顶向下序。
- **职责**：
    - `fetch_source_entries(url)`：HTTP GET `<url>?req=get&dummy=<ms>` → `[success]` + tab 行体 → 复用 `parse_rl_dictionary` 解析。与 RL `kakuteiyominet.php` 协议一致（来源：`routin_func.hsp:6876`）。
    - `import_file_to_entries(path)`：本地文件 → entries（utf-8 / cp932 自动识别，多格式自适应）。
    - `flatten_effective_dictionary(local_entries, net_doc)`：按 `source_order` 拼接本地 + 启用网络源 → 全局 entries，供 `analyze_sentence` 消费。`enabled=False` 退化为仅本地。
    - `ensure_builtin_sources(doc)`：补齐缺失的内置预设（向前兼容）。
    - `split_meta_and_cache(doc)` / `merge_meta_and_cache(meta, cache)`：统一 doc ↔ 分离存储的双向转换。
- **内置预设**（packaged `src/strange_uta_game/config/config.json` 即含）：
    - `rl_official` — `http://timetag.main.jp/RhythmicaLyrics/kakuteiyominet.php`
    - 不可删除（仅可禁用 / 改 URL）。用户可任意添加自定义 URL 源。
- **AppSettings 接口**：`load_network_dictionary()` / `save_network_dictionary()` 自动桥接 meta/cache 双文件；`load_effective_dictionary()` 是注音管线（`AutoCheckService` 各调用点）的统一入口；编辑场景用 `load_dictionary()` 仅取本地。
- **UI**：设置页"读音词典"组中：
    - SwitchSettingCard "启用网络词典" 直接落 `network_dictionary.enabled`（即时保存）。
    - "管理网络词典" 按钮打开管理对话框；对话框只编辑源列表 + 条目缓存，**不再承载总开关与优先级**。
    - "字典源优先级"按钮卡片：点击 "编辑优先级" 打开 `PriorityOrderDialog`，列表式 + 上下移；每次打开都重新 `load_network_dictionary()`，故管理对话框中刚添加的源能立刻在此调整。
    - 管理对话框内按钮"刷新所有启用源"批量 HTTP 拉取（不依赖单击选中行）；"查看/编辑条目" 打开 `NetworkSourceEntriesDialog` 对所选源的 entries 进行表格 CRUD。
- **HTTPS 证书**：`fetch_source_entries` 默认用 `certifi.where()` 根证书包；遇到 `CERTIFICATE_VERIFY_FAILED` 自动回退一次无验证上下文重试（Windows 系统证书链缺失场景常见），`allow_insecure_fallback=False` 可关闭该兜底。
- **自动更新**（``config.json["network_dictionary"]["auto_update"]``）：
    - 字段：``enabled``（默认 false）、``interval_value``（默认 1）、``interval_unit``（``week`` / ``day`` / ``hour``，默认 ``week``）。
    - 时间戳：``network_dictionary.last_auto_update_at``（Unix 秒）。
    - 触发：`main.py` 启动后 500ms 内 `QTimer.singleShot` 调度，在后台线程调 `AppSettings.maybe_auto_update_network_dictionary()`。
    - 行为：仅在 `enabled=True` 且 `is_auto_update_due(last_at, value, unit)` 时遍历所有 `enabled=True` 的源批量 HTTP 拉取（复用 `auto_update_enabled_sources`），落盘并更新时间戳；`force=True` 可强制（未来"立即同步"按钮可用）。
    - UI（读音字典子页面）：SwitchSettingCard "启用网络源自动更新" + SettingCard "网络源自动更新间隔"（**LineEdit** + QIntValidator(1-9999) + ComboBox 周/天/小时）。控件改动即时落 `config.json`。
- **UI 非阻塞**：所有 HTTP 拉取均在工作线程执行：
    - 启动自动更新：`main.py` 用 `threading.Thread(daemon=True)` 后台跑 `maybe_auto_update_network_dictionary()`。
    - "管理网络词典"对话框的"刷新所有启用源"：`QThread + _FetchWorker` 工作线程跑 `fetch_source_entries`，`finished` 信号通过 queued connection 回主线程更新表格；操作期间按钮禁用防重入，重入再次点击会被 ``isRunning()`` 拦截。
    - 文件 IO / JSON 读写 / 字典操作均为本地内存 / 小文件级别，无需后台。

### annotated_text (带注音行级文本格式)
服务于全文本编辑（已废弃不建议使用）界面（`frontend/editor/fulltext_interface`）的 parse/serialize。

- **职责**：
    - `parse_annotated_line(line_text) -> (raw_text, raw_chars, ruby_map)`：解析 `{大冒険||だ|い,ぼ|う,け|ん}` 主格式及兼容格式（`{漢|か|ん|じ}` 单字多 mora、`{赤|あか}` 单字单段、`{text}` 纯文本），ruby_map 仅收录有读音的字符索引。
    - `sentence_to_annotated_line(characters) -> str`：按 `linked_to_next` 链合并连词组为一个 `{...||...}` 块，非连词带 ruby 字符输出单字块，无 ruby 字符原样输出。
    - 前端 `_parse_annotated_line` 与 `_lines_to_text` 改为薄委托；parse ↔ serialize 在典型输入上可往返保形。

### Exporters (导出器集合)
为不同的播放器和编辑软件提供兼容的数据格式。

- **职责**：
    - **LRC Exporter（三种子格式）**：
        - LRC (增强型)：`[mm:ss.xx]<mm:ss.xx>字<mm:ss.xx>字...` 尖括号逐字标签。
        - LRC (逐行)：`[mm:ss.xx]歌词文本` 每行一个时间标签。
        - LRC (逐字)：`[mm:ss.xx]字[mm:ss.xx]字...` 方括号逐字标签。
    - **KRA Exporter**：同 LRC 增强型，不同扩展名。
    - **SRT Exporter**：标准 SRT 字幕格式（序号 + 时间戳 + 文本）。
    - **TXT Exporter**：纯文本打轴数据。使用 `ch.global_timestamps`。
    - **txt2ass Exporter**：兼容特定 ASS 生成工具的中间格式。使用 `ch.global_timestamps`。
    - **ASS Exporter**：直接生成包含 Ruby 支持和样式信息的 ASS 字幕。
        - **Nicokara Exporter（含带注音变体）**：符合ニコカラメーカー规范。使用 `ch.global_timestamps`，支持停顿点时间戳（非行尾停顿点字符后插入额外时间戳）。
            - **@Ruby 拡張規格**：格式 `@RubyN=亲,注音,开始时间,结束时间`，N 从 1 连续编号。注音含内嵌时间戳（`つ[00:00:20]ば[00:00:60]さ`）。按 `linked_to_next` 连词切段（2026-05-13 用户修订），每段独立条目、按文本顺序输出。作用域按文本顺序在本行内优先闭段：pos1 = 段首字首 ts（缺失向上回溯，跨行回退钳制不超过本行内其后首个 ts）；pos2 = 段尾停顿释放 ts / 本句下一字起始 ts / 本句后续释放 ts（如行尾 `[>..]`），跨行借用下一行首 ts 仅为最后手段且钳制 pos2 ≥ pos1——重叠歌词（下一行先于本行结束开始）时直接借用会把作用域压垮（ココ☆ナツ案例）。分隔符半角逗号；含逗号用 `&#44;` 转义。
            - **演唱者标签**：无效演唱者（空/"?"/"未知"）归一化为默认演唱者；标签 `【名】` 跨行连续，仅在演唱者实际变化（对比 `prev_singer_id`）时插入；默认演唱者首次出现也插入。

### AudioEngine (音频引擎)
统一接口 `IAudioEngine`（`audio/base.py`），多实现按平台 / 设置切换。引擎在 `MainWindow._make_audio_engine()` 中选定。

- **实现与选择策略**：
    - **BassEngine**（`bass_engine.py`，Windows 默认引擎）：基于 **BASS** 音频库，支持 FLAC/OPUS/MP3/WMA/WV/DSD/ALAC/AAC/AC3/WebM 等格式原生解码与低延迟回放。
    - **BassTsmEngine**（`bass_tsm_engine.py`）：在 BASS 之上做**离线 TSM 预渲染**变速（变速不变调、无爆音）。「设置 → 启用高质量音频变速」开启（默认）时用它，关闭时退回 `BassEngine`（原版 BASS 实时变速，零缓存但可能爆音）。
    - **SoundDeviceEngine**（`sounddevice_engine.py`）：sounddevice (PortAudio) 输出 + soundfile 解码。**BASS 不可用的 macOS** 始终用它，并固定走离线 TSM（HQ 开关无效）。
- **支撑组件**：
    - **SPSC RingBuffer**（`ring_buffer.py`）：单生产者单消费者环形缓冲，零分配音频回调，立体声相位一致。
    - **TSMCache**（`tsm_cache.py`）+ **pedalboard**：高质量离线变速预渲染缓存。切换速度时播控栏实时显示后台渲染进度；位置追踪始终基于原始音频时间轴。
    - **video_converter**：从视频文件抽取音轨。
    - 变速范围 0.2×–2.0×（20%–200%）。
