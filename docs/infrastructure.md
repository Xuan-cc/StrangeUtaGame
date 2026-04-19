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
    - **SudachiPy Mode C 上下文感知分析器**：利用 SudachiPy 进行长单位（Mode C）分词，能够准确识别复合词。
    - **假名锚点分配算法**：基于分词结果，将假名智能分配至对应的汉字。
    - **片假名→平假名转换修复**：所有注音分析（Sudachi/Pykakasi）均会将片假名转换为平假名作为注音（包括小写片假名如 ェ），确保注音显示的一致性。
    - **pykakasi 单字参考分配**：在无法通过上下文确定读音时，使用 pykakasi 作为单字读音参考。
    - **PykakasiAnalyzer 作为回退**：若系统未安装 SudachiPy 相关依赖，自动降级至 PykakasiAnalyzer。
    - **create_analyzer() 优先级**：SudachiPy → pykakasi → DummyAnalyzer。

### lyric_parser (歌词解析器)
支持多种原始歌词格式的导入。

- **职责**：
    - `parse_to_sentences(content, format)`：支持 TXT, LRC, KRA 等格式。
    - 负责从原始文本中提取歌词行、注音和现有的时间标签。

### Exporters (导出器集合)
为不同的播放器和编辑软件提供兼容的数据格式。

- **职责**：
    - **LRC/KRA Exporter**：通用时间标签格式。使用 `ch.export_timestamps` 和 `sentence.export_timing_start_ms`。
    - **TXT Exporter**：纯文本打轴数据。使用 `ch.export_timestamps`。
    - **txt2ass Exporter**：兼容特定 ASS 生成工具的中间格式。使用 `ch.export_timestamps`。
    - **ASS Exporter**：直接生成包含 Ruby 支持和样式信息的 ASS 字幕。
    - **Nicokara Exporter**：生成符合ニコカラメーカー规范的歌词文件。使用 `ch.export_timestamps`。
