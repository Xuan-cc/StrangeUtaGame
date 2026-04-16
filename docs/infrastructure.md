# 基础设施层设计

## 概述

基础设施层提供具体的技术实现，包括音频播放、文件读写、日语分析等。通过**接口/抽象基类**与上层解耦，实现细节可被替换而不影响业务逻辑。

## 音频引擎

### IAudioEngine 接口

**职责**：抽象音频播放能力，屏蔽具体音频库差异

**能力**：
- 加载音频文件（MP3、WAV、FLAC 等）
- 播放控制（播放、暂停、停止）
- 位置控制（获取、设置当前位置）
- 播放参数（速度、音量）
- 位置变化通知（回调机制）

**关键设计**：
- 异步播放，非阻塞 API
- 位置回调频率：~60fps
- 时间精度：毫秒
- 变速范围：0.5x ~ 2.0x

### SoundDeviceEngine 实现

**技术选型**：sounddevice + soundfile

**特点**：
- 基于 PortAudio，跨平台
- 低延迟音频回调
- 支持 NumPy 数组操作

**实现要点**：
- 音频数据加载为 NumPy 数组
- 独立播放线程管理音频流
- 变速采用重采样或时间拉伸算法
- 位置回调在播放循环中触发

**性能目标**：
- 启动延迟 < 50ms
- 位置回调延迟 < 20ms
- CPU 占用 < 5%（正常播放时）

## 文件解析器

### ILyricParser 接口

**职责**：解析歌词文件（LRC、TXT、KRA）

**输入**：文件路径
**输出**：歌词文本列表（可选时间标签）

**支持的格式**：

| 格式 | 特点 |
|------|------|
| LRC | `[mm:ss.xx]歌词` 格式 |
| TXT | 纯文本，无时间标签 |
| KRA | 同 LRC，卡拉OK专用 |

**错误处理**：
- 文件不存在
- 编码错误（支持 UTF-8、Shift-JIS）
- 格式解析错误

### IProjectParser 接口

**职责**：解析和保存项目文件（SUG）

**SUG 格式设计**：
- 基于 JSON
- 包含完整的项目数据（歌词、时间标签、节奏点配置、注音）
- 版本控制，支持向后兼容
- 文件扩展名：.sug（StrangeUtaGame 的缩写）

**数据结构**：
```json
{
  "version": "1.0",
  "id": "项目UUID",
  "metadata": {
    "title": "歌曲标题",
    "artist": "艺术家",
    "album": "专辑",
    "created_at": "创建时间",
    "updated_at": "更新时间",
    "language": "ja"
  },
  "singers": [
    {
      "id": "演唱者UUID",
      "name": "演唱者1",
      "color": "#FF6B6B",
      "is_default": true,
      "display_priority": 0,
      "enabled": true
    }
  ],
  "lines": [
    {
      "id": "行UUID",
      "singer_id": "演唱者UUID",
      "text": "歌词文本",
      "chars": ["字", "符", "列", "表"],
      "checkpoints": [
        {
          "char_idx": 0,
          "check_count": 2,
          "is_line_end": false,
          "is_rest": false
        },
        {
          "char_idx": 1,
          "check_count": 1,
          "is_line_end": false
        },
        {
          "char_idx": 2,
          "check_count": 2,
          "is_line_end": false
        },
        {
          "char_idx": 3,
          "check_count": 1,
          "is_line_end": true
        }
      ],
      "timetags": [
        {"timestamp_ms": 12340, "char_idx": 0, "checkpoint_idx": 0, "tag_type": "char_start", "singer_id": "演唱者UUID"},
        {"timestamp_ms": 12560, "char_idx": 0, "checkpoint_idx": 1, "tag_type": "char_middle", "singer_id": "演唱者UUID"},
        {"timestamp_ms": 12800, "char_idx": 1, "checkpoint_idx": 0, "tag_type": "char_start", "singer_id": "演唱者UUID"}
      ],
      "rubies": [
        {
          "text": "あか",
          "start": 0,
          "end": 1
        }
      ]
    }
  ]
}
```

**关键设计说明**：

1. **项目文件设计原则（音频不绑定）**：
   - **RLF 文件不存储音频路径** - 用户每次使用时重新选择音频
   - **好处**：
     - 项目文件更小（只存歌词和时间数据）
     - 音频文件可随时更换（如从低音质换到高音质）
     - 项目文件可在不同设备间共享
     - 实现更简单，无需处理路径解析

2. **演唱者列表（singers）**：
   - 必须至少有一个演唱者（default = true）
   - 每个歌词行通过 `singer_id` 关联到演唱者
   - 颜色用于 UI 区分不同演唱者
   - `enabled` 控制是否参与打轴和导出

3. **歌词行（lines）**：
   - `singer_id`: 必填，标识该行属于哪个演唱者
   - `text` 和 `chars`: `chars` 是 `text` 拆分后的字符列表，运行时动态生成，持久化时两者都存储用于校验

4. **节奏点配置（checkpoints）**：
   - 与 `chars` 一一对应，描述每个字符的节奏属性
   - `check_count`: 该字符需要击打几次（如「赤」(あか)=2）
   - `is_line_end`: 标记行末字符（通常有长音或休止）
   - `is_rest`: 标记休止符（非歌词字符的停顿）

5. **时间标签（timetags）**：
   - 记录实际打轴的时间戳
   - `char_idx`: 对应字符索引
   - `checkpoint_idx`: 该字符的第几个节奏点（支持连打）
   - `tag_type`: 标签类型（`char_start`, `char_middle`, `line_end`, `rest`）
   - `singer_id`: 所属演唱者 ID（用于多演唱者场景）
   - 通过 char_idx + checkpoint_idx + singer_id 可以精确定位

6. **注音（rubies）**：
   - 描述注音文本及其覆盖范围
   - 支持多对一（一个注音对应多个字符）
   - 非日语项目可省略此字段

**版本兼容**：
- 版本号遵循语义化版本（Semantic Versioning）
- 读取时检查版本，向后兼容旧版本
- 写入时总是使用最新版本

**版本迁移策略**：

当加载旧版本 SUG 文件时，自动执行迁移：

```python
# 迁移器示例
class SugMigrator:
    MIGRATIONS = {
        "1.0": None,  # 基础版本，无需迁移
        # 未来版本添加迁移函数
        # "1.1": migrate_v1_to_v1_1,
    }
    
    @staticmethod
    def migrate(data: dict, from_version: str) -> dict:
        """将旧版本数据迁移到最新版本"""
        # 迁移逻辑
        return migrated_data
```

**命名说明**：
- **SUG** = **S**trange**U**ta**G**ame
- 避免与 RhythmicaLyrics 的 .rlf 格式混淆

**向后兼容原则**：
1. 新增字段：提供默认值
2. 删除字段：忽略旧数据
3. 修改结构：提供转换函数
4. 所有迁移操作保持数据完整性

## 导出器

### IExporter 接口

**职责**：将 Project 导出为特定格式文件

**能力**：
- 格式名称和扩展名
- 导出方法
- 可选的配置选项

**内置导出器**：

#### LRCExporter
- 输出标准 LRC 格式
- 支持元数据标签（ti/ar/al）
- 行级时间标签

#### KRAExporter
- 同 LRC，使用 .kra 扩展名

#### TXTExporter
- 简化文本格式
- 可配置时间精度

#### Txt2AssExporter
- 用于 txt2ass 工具
- 包含注音信息
- 支持多种效果配置

#### NicokaraExporter
- **Nicokara规则**格式
- 字符级时间标签
- 格式：`[mm:ss.xx]歌词|注音`
- 每字符一行

**Nicokara规则详细规范**：

1. 时间格式：`[分:秒.厘秒]`，厘秒为 2 位
2. 每行一个字符
3. 注音可选，用 `|` 分隔
4. 行间空一行
5. 示例：
   ```
   [00:12.34]赤|あか
   [00:13.50]い
   [00:14.20]花|はな
   ```

### 导出器注册机制

ExportService 维护导出器集合：
- 内置导出器自动注册
- 支持第三方导出器插件
- 通过名称查找对应导出器

## 日语处理

### RubyAnalyzer 接口

**职责**：分析日语文本，提供汉字注音

**能力**：
- 获取文本的假名读音
- 字符与注音对齐
- 支持长文本批量处理

**技术选型**：pykakasi

**特点**：
- 纯 Python 实现
- 支持汉字转平假名/片假名/罗马音
- 可处理人名、地名等特殊读音

**输出格式**：
- 完整读音字符串
- 字符级对齐信息（原文字符、注音、起止位置）

**限制**：
- 依赖内置字典，可能有不认识的汉字
- 多音字根据上下文判断，可能不准确
- 人名可能需要手动校正

## 错误定义

### 基础设施错误类型

**AudioError**：音频相关错误
- AudioLoadError：加载失败（文件不存在、格式不支持）
- AudioPlaybackError：播放错误（设备占用、驱动问题）

**ParseError**：解析相关错误
- FileNotFound：文件不存在
- EncodingError：编码错误
- FormatError：格式不符合规范

**ExportError**：导出相关错误
- PermissionError：权限不足
- PathError：路径无效
- WriteError：写入失败

### 错误处理原则

1. **精确类型**：使用具体错误类型，便于上层处理
2. **保留上下文**：包装错误时保留原始异常
3. **友好消息**：错误信息用户可读
4. **日志记录**：系统错误记录详细信息

## 扩展点

### 添加新音频引擎

**场景**：替换 sounddevice 为其他音频库

**步骤**：
1. 实现 IAudioEngine 接口
2. 处理平台差异（Windows/macOS/Linux）
3. 注入到 TimingService
4. 保持 API 兼容

**候选方案**：
- PyAudio：更底层控制
- miniaudio：跨平台、轻量
- Pygame.mixer：简单场景

### 添加新导出格式

**步骤**：
1. 实现 IExporter 接口
2. 处理格式特定逻辑
3. 在 ExportService 注册
4. 在 UI 添加选项

**示例**：导出为 MIDI 歌词、导出为 SRT 字幕

### 替换注音引擎

**场景**：使用更准确的日语分析

**候选方案**：
- Janome：形态素分析器
- MeCab：高性能但配置复杂
- Sudachi：现代日语分析器

## 性能考虑

### 音频处理

- 预加载音频数据到内存
- 使用环形缓冲区减少内存分配
- 异步文件 I/O（大文件场景）

### 文件解析

- 流式解析大文件
- 缓存解析结果
- 支持增量加载

### 日语分析

- 缓存常见文本的注音结果
- 批量处理优于单次调用
- 异步分析避免阻塞 UI

## 配置与初始化

### 音频引擎配置

- 默认采样率：44100Hz
- 默认缓冲区：1024 样本
- 设备选择：系统默认或用户指定

### 解析器配置

- 默认编码：UTF-8
- 备用编码：Shift-JIS
- 编码检测：自动或手动指定

### 导出器配置

- 默认输出目录：用户文档
- 文件名模板：{标题}_{艺术家}
- 编码选项：UTF-8 带/不带 BOM
