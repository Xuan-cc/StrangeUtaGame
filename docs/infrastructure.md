# 基础设施层设计

基础设施层提供具体的外部系统交互实现，包括项目持久化、歌词解析、多格式导出和音频处理。

## 核心实现

### SugProjectParser (v2.0 项目解析器)
基于 JSON 格式的项目文件解析与序列化，版本 2.0 对应新的分层模型。

- **职责**：
    - `save(project, path)`：将 Project 对象（包含 sentences, characters, ruby）序列化为 JSON 字符串并保存。
    - `load(path)`：从文件读取 JSON 并反序列化为 Project 实体树。序列化/反序列化 `sentence_end_ts` 字段。
    - **向后兼容迁移**：加载旧版 v2.0 文件（无 `sentence_end_ts` 字段）时，自动将句尾字符（is_sentence_end=True）的最后一个时间戳提取为 `sentence_end_ts`。
    - **格式特点**：按 Sentence → Character → Ruby 结构存储，不保存音频物理路径。

### inline_format (内联文本处理)
提供一种便于在文本编辑器中阅读和修改带时间标签的格式。

- **职责**：
    - `to_inline_text(sentence)`：将带时间标签的句子转换为带有 `[timestamp]` 标记的文本字符串。
    - `from_inline_text(text)`：解析内联格式文本并重建 Sentence 和时间标签。

### SudachiAnalyzer (SudachiPy 分析器)
提供高精度的日语分词与注音分析实现。

- **职责**：
    - **SudachiPy Mode C 上下文感知**：长单位分词识别复合词；假名锚点分配采用两步策略（优先匹配 pykakasi 参考，失败则无约束分发）。
    - **假名统一**：所有注音输出均转为平假名（含小写片假名如 ェ）。
    - **回退链**：`create_analyzer()` 优先级 SudachiPy → pykakasi → DummyAnalyzer。
    - **内置词典**：缺失 `dictionary.json` 时自动从内嵌的 1757 条 RL 词典初始化。

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
    - `parse_rl_dictionary(text) -> List[Dict]`：解析 `原文\t注音1,注音2,...` 格式，全角 `＋`（U+FF0B）作为连词占位符剥离；仅含 `＋` 的尾部读音项与空串一并去除；读音全空则丢弃整条。
    - 返回 `[{"enabled": True, "word": str, "reading": str}, ...]`，顺序与输入一致。
    - 前端 `frontend/settings/app_settings._parse_rl_dictionary` 改为薄包装，历史导入路径保留。

### annotated_text (带注音行级文本格式)
服务于全文本编辑界面（`frontend/editor/fulltext_interface`）的 parse/serialize。

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
    - **TXT Exporter**：纯文本打轴数据。使用 `ch.export_timestamps`。
    - **txt2ass Exporter**：兼容特定 ASS 生成工具的中间格式。使用 `ch.export_timestamps`。
    - **ASS Exporter**：直接生成包含 Ruby 支持和样式信息的 ASS 字幕。
        - **Nicokara Exporter**：符合ニコカラメーカー规范。使用 `ch.export_timestamps`，支持句尾释放时间戳（非行尾句尾字符后插入额外时间戳）。
            - **@Ruby 拡張規格**：格式 `@RubyN=亲,注音,开始时间,结束时间`，N 从 1 连续编号。注音含内嵌时间戳（`つ[00:00:20]ば[00:00:60]さ`）。同 (亲, 读音) 组 reading_with_ts 全相同 → 单条全局；有差异 → 按子组输出带时间范围（首省略开始、末省略结束）。所有条目按首字符时间戳全局排序，跨字交错。分隔符半角逗号；含逗号用 `&#44;` 转义。
            - **演唱者标签**：无效演唱者（空/"?"/"未知"）归一化为默认演唱者；标签 `【名】` 跨行连续，仅在演唱者实际变化（对比 `prev_singer_id`）时插入；默认演唱者首次出现也插入。

### AudioEngine (音频引擎)
提供跨平台音频播放与实时处理。

- **职责**：
    - **WSOLA 流式变速不变调**：基于 audiotsm 的 WSOLA 算法按需生成变速音频，立体声相位一致，支持实时速度切换（50%-200%）。位置追踪始终基于原始音频时间。
    - **低延迟播放**：sounddevice (PortAudio) 输出，soundfile 解码多格式。
