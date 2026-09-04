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
卡拉OK打轴的最小单位。包含注音、时间戳、演唱者、停顿点标记。

```python
@dataclass
class Character:
    char: str
    ruby: Optional[Ruby] = None
    check_count: int = 1              # 普通节奏点数量，不含停顿点
    timestamps: List[int] = field(default_factory=list)
    sentence_end_ts: Optional[int] = None   # 停顿点时间戳
    linked_to_next: bool = False
    is_line_end: bool = False
    is_sentence_end: bool = False
    is_rest: bool = False
    singer_id: str = ""
    selected_checkpoint_idx: Optional[int] = None  # 选中 cp（UI 态，不序列化）
    needs_guide: bool = False                       # 导唱待办视觉提示（不参与不变式）
    # 内部：全局偏移预计算的派生时间戳（渲染与导出共用同一套）
    _global_offset_ms: int          # 全局偏移量
    global_timestamps / global_sentence_end_ts
```

核心方法：`push_to_ruby()`（写入 Ruby.timestamps + RubyPart.offset_ms）、`add_timestamp`、`remove_timestamp_at`、`set_sentence_end_ts`、`set_check_count`（维护 check_count / timestamps / ruby.parts 不变式）、`set_offset`、`_update_offset_timestamps`（按 `_global_offset_ms` 重算 `global_timestamps`）。所有时间戳写入均由 TimingService 独家调用。

> ⚠️ 偏移模型已统一：早期版本曾区分 `render_timestamps` 与 `export_timestamps` 两套派生时间戳，现已合并为**单套 `global_timestamps`**（渲染与导出共用），由 `set_offset()` 写入。

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

id、name、color、complement_color（自动算补色，选中高亮用）、backend_number、display_priority、enabled、is_default、is_placeholder、group 等字段。支持**分色**：`color_mode`（`"solid"` / `"split"`）+ `split_colors`（split 模式额外颜色，总数 ≤ 5）。颜色与补色由 SingerService / 实体方法（`change_color` 等）维护。

### Project (项目根实体)

聚合所有句子、演唱者和元数据。保存选中 checkpoint 的 cursor（`selected_checkpoint_*`）。

```python
@dataclass
class Project:
    id: str
    sentences: List[Sentence]
    singers: List[Singer]
    metadata: ProjectMetadata           # title/artist/album/language/created_at/updated_at
    audio_duration_ms: int = 0
    global_offset_ms: Optional[int] = None   # 每项目全局偏移（None=用全局默认）
```

> ⚠️ 选中态不变量（I1 全局单选 / I2 默认选中 / I3 增删对称）由 Project 层维护：`set_selected_checkpoint` / `clear_selected_checkpoint` / `get_selected_checkpoint` / `select_default_checkpoint` / `shift_selected_checkpoint_if_lost`。

核心辅助方法：

- `find_prev_line_with_checkpoints(current_idx) -> int`：从 `current_idx - 1` 起向上查找首个"有 checkpoint"的句子（任一字符 `check_count > 0` 或 `is_sentence_end`）；找不到返回 `-1`。供打轴界面「上一行」导航使用。
- `collect_all_timestamp_ms() -> List[int]`：按原始顺序展平收集所有字符的 `ch.all_timestamps`（毫秒，未排序、未去重），供前端时间轴显示等只需毫秒值的场景使用。
- `get_all_timestamps()`（既有）返回五元组集合（含位置信息），与上述两个辅助方法互补，不冲突。

## 数据层级关系

Ruby（由 RubyPart 组成） ⊂ Character ⊂ Sentence ⊂ Project；Character 按 `linked_to_next` 连成 Word（逻辑分组）；Singer 属 Project，Character 持有 singer_id 引用。
