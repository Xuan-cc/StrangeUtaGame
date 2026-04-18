# 领域层设计

StrangeUtaGame 的领域模型采用分层级联结构，所有数据交互严格遵循此体系。

## 核心数据结构

### Ruby (注音实体)
表示字符上方的注音。注音数据通常从 Character 同步推送，而非独立存在。

```python
@dataclass
class Ruby:
    text: str  # 注音文本
    timestamps: List[int] = field(default_factory=list)  # 从Character推送
    singer_id: str = ""  # 从Character推送
```

### Character (字符实体)
卡拉OK打轴的最小单位。包含注音信息和自身的时间标签。

```python
@dataclass
class Character:
    char: str  # 单个字符
    ruby: Optional[Ruby] = None
    check_count: int = 1  # 节奏点数量
    timestamps: List[int] = field(default_factory=list)
    linked_to_next: bool = False  # 连词标记
    is_line_end: bool = False
    is_rest: bool = False
    singer_id: str = ""

    def push_to_ruby(self): ...
    def add_timestamp(self, timestamp: int): ...
    def remove_timestamp_at(self, index: int): ...
    def clear_timestamps(self): ...
    def set_ruby(self, ruby: Optional[Ruby]): ...
```

### Word (词组实体)
由一个或多个 Character 组成，通常用于逻辑上的注音划分和文本处理。

```python
@dataclass
class Word:
    characters: List[Character]
    
    @property
    def text(self) -> str: ...
    @property
    def ruby_parts(self) -> List[str]: ...
    @property
    def has_ruby(self) -> bool: ...
```

### Sentence (句子实体)
由 Character 列表组成的逻辑行，对应显示中的歌词行。

```python
@dataclass
class Sentence:
    singer_id: str
    id: str = field(default_factory=lambda: str(uuid4()))
    characters: List[Character] = field(default_factory=list)

    @property
    def words(self) -> List[Word]: ...
    @property
    def timing_start_ms(self) -> int: ...
    @property
    def timing_end_ms(self) -> int: ...
    
    def get_character(self, index: int) -> Character: ...
    def add_ruby_to_char(self, char_idx: int, ruby_text: str): ...
    def clear_all_timestamps(self): ...
    @classmethod
    def from_text(cls, text: str, singer_id: str, id=None): ...
```

### Singer (演唱者实体)
用于定义演唱者信息，影响渲染颜色和导出标记。

```python
@dataclass
class Singer:
    id: str
    name: str
    color: str
    backend_number: int
    is_default: bool
    display_priority: int
    enabled: bool
```

### Project (项目根实体)
聚合所有句子、演唱者和元数据。

```python
@dataclass
class Project:
    id: str
    sentences: List[Sentence]
    singers: List[Singer]
    metadata: ProjectMetadata
    audio_duration_ms: int = 0
    
    def add_singer(self, singer: Singer): ...
    def get_default_singer(self) -> Singer: ...
    def add_sentence(self, sentence: Sentence): ...
    def get_timing_statistics(self) -> dict: ...
```

## 数据层级关系
1. **Ruby** 归属于 **Character**。
2. **Character** 组成 **Word**（逻辑分组）。
3. **Character** 序列构成 **Sentence**。
4. **Sentence** 和 **Singer** 构成 **Project**。
