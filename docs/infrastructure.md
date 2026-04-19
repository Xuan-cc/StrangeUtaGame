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
    - **假名锚点分配算法**：基于分词结果，将假名智能分配至对应的汉字。`_try_distribute_kanji_block` 采用两步分发策略（优先匹配 pykakasi 参考，失败则回退至无约束分发）。`_partition_with_refs` 使用三级匹配逻辑（精确匹配→前缀匹配→无约束回退）。
    - **片假名→平假名转换修复**：所有注音分析（Sudachi/Pykakasi）均会将片假名转换为平假名作为注音（包括小写片假名如 ェ），确保注音显示的一致性。
    - **pykakasi 单字参考分配**：在无法通过上下文确定读音时，使用 pykakasi 作为单字读音参考。
    - **PykakasiAnalyzer 作为回退**：若系统未安装 SudachiPy 相关依赖，自动降级至 PykakasiAnalyzer。
    - **create_analyzer() 优先级**：SudachiPy → pykakasi → DummyAnalyzer。
    - **默认词典数据 (data/default_dictionary)**：内嵌 1757 条 RL 字典原始文本，用于在用户缺失 `dictionary.json` 时自动初始化词典数据。

### lyric_parser (歌词解析器)
支持多种原始歌词格式的导入。

- **职责**：
    - `parse_to_sentences(content, format)`：支持 TXT, LRC（逐行/逐字/增强型）, KRA, ASS, SRT 等格式。
    - 负责从原始文本中提取歌词行、注音和现有的时间标签。
    - **增强型 LRC 支持**：解析 `<mm:ss.xx>` 尖括号逐字时间标签格式。
    - **ASS 解析器**：解析 ASS 字幕的 `[Events]` Dialogue 行，提取 `\kf`/`\k`/`\ko` 卡拉OK时间标签（厘秒→毫秒转换）。
    - **SRT 解析器**：解析 SRT 字幕的块结构（序号、`HH:MM:SS,mmm --> HH:MM:SS,mmm` 时间戳、文本），自动剥离 HTML 标签。
    - **工厂模式**：`LyricParserFactory` 根据文件扩展名（.txt/.lrc/.kra/.ass/.srt）自动选择解析器。

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
        - **Nicokara Exporter**：生成符合ニコカラメーカー规范的歌词文件。使用 `ch.export_timestamps`。支持同一汉字多次出现时的独立 @Ruby 条目生成；支持单行内非行尾句尾的释放时间戳导出（句尾字符后插入额外时间戳）。
        - **演唱者过滤与标签 (Nicokara)**：
            - 无效演唱者归一化：未指定演唱者、未知（"?"、"未知"）或空值的句子在导出过滤时均被视为项目的默认演唱者。
            - 过滤逻辑使用有效演唱者（优先 per-char singer_id，其次 sentence.singer_id）。
            - 导出界面演唱者列表包含所有在 Sentence 级别或 Character 级别出现的演唱者。
            - 演唱者切换标签（【演唱者名】）实现跨行连续性：仅在演唱者实际发生变化（对比 `prev_singer_id`）时插入标签。默认演唱者也会在首次出现时插入标签。

### AudioEngine (音频引擎)
提供跨平台音频播放与实时处理。

- **职责**：
    - **Phase Vocoder 变速不变调**：采用 Phase-Locked Phase Vocoder 算法实现高质量变速播放，并保持立体声相位一致性。
        - **立体声保持**：以第 0 通道作为参考相位，其它通道通过 Phase-Locked 机制保持与参考通道的相位差，从而保留声场定位效果。
        - **原理**：STFT → 相位累积 (Phase Accumulation) → ISTFT 重叠相加 (Overlap-add)。FFT size: 2048, hop: 512。
        - **分段优先处理**：变速时采用两阶段后台处理策略：Phase 1 优先处理当前播放位置到末尾的片段，以尽快切换到变速不变调模式（大幅缩短回退期）；Phase 2 再处理完整文件以支持任意位置拖动。已播放部分使用快速重采样填充，仅作索引对齐用途。
        - **模式切换淡入**：从线性插值回退模式切换到 Phase Vocoder 模式时，施加 32ms 线性淡入以消除拼接噪声，保证音频连续性。
        - **实现细节**：纯 numpy 实现，无外部 C 库依赖。原始数据保留在 `_original_data` 中，使用 `_stretched_speed`、`_stretch_version` 和 `_switch_fade_samples` 管理处理状态与切换平滑。位置追踪始终基于原始音频时间。
    - **低延迟播放**：使用 sounddevice (PortAudio) 提供低延迟音频输出。
    - **格式支持**：通过 soundfile 支持多种音频格式解码。
