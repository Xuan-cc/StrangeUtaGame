# 领域层设计

StrangeUtaGame 的领域模型采用分层级联结构，所有数据交互严格遵循此体系。domain/ 目录零框架依赖。

## 核心数据结构

### RubyPart / Ruby (注音实体)

Ruby 由多个 RubyPart 组成，每个 RubyPart 对应一个 checkpoint 的演唱字母段。`len(ruby.parts) == character.check_count` 是核心不变量。注音数据从 Character 同步推送（`push_to_ruby`），不独立维护。

```python
@dataclass
class RubyPart:
    text: str       # 该 checkpoint 对应的演唱片段
    offset_ms: int = 0   # 相对 Character 首时间戳的偏移

@dataclass
class Ruby:
    parts: List[RubyPart] = field(default_factory=list)
    timestamps: List[int] = field(default_factory=list)  # 从 Character.all_timestamps 推送
    singer_id: str = ""                                    # 从 Character.singer_id 推送
```

### Character (字符实体)
卡拉OK打轴的最小单位。包含注音、时间戳、演唱者、句尾标记。

```python
@dataclass
class Character:
    char: str
    ruby: Optional[Ruby] = None
    check_count: int = 1              # 普通节奏点数量，不含句尾释放点
    timestamps: List[int] = field(default_factory=list)
    sentence_end_ts: Optional[int] = None   # 句尾释放时间戳
    linked_to_next: bool = False
    is_line_end: bool = False
    is_sentence_end: bool = False
    is_rest: bool = False
    singer_id: str = ""
    # 内部：offset 预计算的渲染/导出时间戳
    render_timestamps / render_sentence_end_ts
    export_timestamps / export_sentence_end_ts
```

核心方法：`push_to_ruby()`（写入 Ruby.timestamps + RubyPart.offset_ms）、`add_timestamp`、`remove_timestamp_at`、`set_sentence_end_ts`、`set_offsets`、`_update_offset_timestamps`（重算 render/export）。所有时间戳写入均由 TimingService 独家调用。

### Word (词组实体)

由 `linked_to_next` 链接的 Character 序列，用于连词渲染和逻辑分组。

### Sentence (句子实体)

Character 列表组成一行歌词。`sentence.text` 是 `@property`，从 characters 实时拼接。

```python
@dataclass
class Sentence:
    singer_id: str
    id: str = field(default_factory=lambda: str(uuid4()))
    characters: List[Character] = field(default_factory=list)
    # properties: text, words, timing_start_ms, timing_end_ms, ...
```

### Singer (演唱者实体)

id、name、color、backend_number、display_priority、enabled 等字段。颜色由 SingerService 维护。

### Project (项目根实体)

聚合所有句子、演唱者和元数据。保存选中 checkpoint 的 cursor（`selected_checkpoint_*`）。

```python
@dataclass
class Project:
    id: str
    sentences: List[Sentence]
    singers: List[Singer]
    metadata: ProjectMetadata
    audio_duration_ms: int = 0
```

## 数据层级关系

Ruby（由 RubyPart 组成） ⊂ Character ⊂ Sentence ⊂ Project；Character 按 `linked_to_next` 连成 Word（逻辑分组）；Singer 属 Project，Character 持有 singer_id 引用。
