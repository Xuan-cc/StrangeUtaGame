# 架构总览

## 设计目标

1. **低延迟**：打轴操作延迟 < 20ms
2. **可测试**：核心业务可独立单元测试
3. **可扩展**：支持新导出格式、新音频后端
4. **可维护**：分层清晰，职责单一

## 分层架构

本项目采用**四层分层架构**：

```
┌─────────────────────────────────────┐
│  表示层 (Presentation)               │
│  职责：UI 展示与用户输入              │
│  技术：PyQt6 + PyQt-Fluent-Widgets    │
└─────────────┬───────────────────────┘
              │ 信号/回调
┌─────────────▼───────────────────────┐
│  应用服务层 (Application)            │
│  职责：协调业务逻辑，编排用例流程      │
│  关键服务：Timing/Project/Export     │
└─────────────┬───────────────────────┘
              │ 方法调用
┌─────────────▼───────────────────────┐
│  领域层 (Domain)                     │
│  职责：核心业务实体与规则              │
│  关键实体：Project/LyricLine/TimeTag  │
└─────────────┬───────────────────────┘
              │ 依赖注入
┌─────────────▼───────────────────────┐
│  基础设施层 (Infrastructure)         │
│  职责：技术实现（音频、文件、网络）     │
│  技术：sounddevice/pykakasi          │
└─────────────────────────────────────┘
```

## 依赖规则

**只允许上层调用下层，禁止反向依赖**：

- ✅ Presentation → Application
- ✅ Application → Domain
- ✅ Application → Infrastructure
- ✅ Infrastructure 实现 Domain 定义的接口
- ❌ Domain → 任何其他层
- ❌ Application → Presentation

## 数据流向

### 用户操作流向

```
用户输入（键盘/鼠标）
    ↓
表示层捕获事件
    ↓
调用应用服务层方法
    ↓
应用服务协调领域对象
    ↓
调用基础设施（音频播放等）
    ↓
状态变更通过回调通知上层
    ↓
表示层更新界面
```

### 文件操作流向

```
用户选择文件
    ↓
应用服务层接收请求
    ↓
调用基础设施层解析器
    ↓
解析器创建领域对象
    ↓
领域对象返回给应用服务
    ↓
应用服务通知表示层更新
```

## 核心用例

### 1. 打轴（核心用例）

**参与者**：用户
**触发条件**：用户按空格键或 F1-F9
**基本流程**：
1. 表示层捕获键盘事件
2. TimingService 获取当前音频时间
3. 向当前 LyricLine 添加 TimeTag
4. TimingService 触发回调
5. 表示层更新 KaraokePreview 显示
6. 光标自动前进到下一位置

**扩展流程**：
- 行尾自动跳转到下一行
- 支持连打（一个字符多个时间标签）

### 2. 导出文件

**参与者**：用户
**触发条件**：用户选择导出格式并确认
**基本流程**：
1. ExportService 根据格式名查找 Exporter
2. Exporter 遍历 Project 数据
3. 按格式规范生成文本
4. 写入指定路径文件

**支持的格式**：
- LRC：通用歌词，行级时间标签
- KRA：卡拉OK专用
- TXT：纯文本时间标签
- txt2ass：ASS字幕生成器的输入格式
- Nicokara规则：ニコカラメーカー专用格式

### 3. 自动检查（チェック付加）

**参与者**：用户
**触发条件**：用户触发自动检查功能
**基本流程**：
1. AutoCheckService 分析歌词文本
2. 对每个字符调用 RubyAnalyzer 获取注音
3. 根据注音假名数量计算检查数
4. 生成检查数分布
5. 应用到 LyricLine

**规则**：
- 汉字：根据注音假名数量决定
- 假名：通常 1 个
- 促音「っ」：可配置 0 或 1
- 长音「ー」：可配置是否计数

## 扩展点

### 添加新导出格式

1. 在 Infrastructure 层实现 Exporter 接口
2. 在 ExportService 注册新 Exporter
3. 在 UI 层添加对应选项

**Exporter 接口契约**：
- 输入：Project 对象、输出路径、选项字典
- 输出：文件写入结果（成功/失败）
- 异常：文件权限、格式错误等

### 替换音频引擎

1. 实现 IAudioEngine 接口
2. 注入到 TimingService
3. 保持上层代码不变

**IAudioEngine 接口契约**：
- 加载文件、播放/暂停/停止
- 获取/设置播放位置
- 变速播放（0.5x ~ 2.0x）
- 位置变化回调（~60fps）

## 回调接口规范

### 设计原则

- 上层通过**回调函数**或**接口对象**与下层通信
- 回调中**不包含 UI 对象**，只传递数据
- 回调调用时机：状态变更完成后立即调用

### 核心回调接口定义

```python
from typing import Protocol, Dict, List, Optional
from dataclasses import dataclass

# ========== 数据对象（用于回调参数） ==========

@dataclass
class TimeTagInfo:
    """时间标签信息"""
    singer_id: str
    line_idx: int
    char_idx: int
    checkpoint_idx: int
    timestamp_ms: int
    tag_type: str

@dataclass
class SingerPosition:
    """演唱者位置信息"""
    singer_id: str
    line_idx: int
    char_idx: int
    checkpoint_idx: int
    color: str

@dataclass
class PlaybackPosition:
    """播放位置信息"""
    position_ms: int
    duration_ms: int
    is_playing: bool

# ========== TimingService 回调 ==========

class TimingCallbacks(Protocol):
    """打轴服务回调接口"""
    
    def on_timetag_added(self, info: TimeTagInfo) -> None:
        """时间标签添加时"""
        ...
    
    def on_timetag_removed(self, info: TimeTagInfo) -> None:
        """时间标签删除时"""
        ...
    
    def on_position_changed(self, position: PlaybackPosition,
                           singer_positions: Dict[str, SingerPosition]) -> None:
        """
        播放位置变化时（约 60fps）
        singer_positions: {singer_id: SingerPosition}
        """
        ...
    
    def on_singer_changed(self, new_singer_id: str, 
                         prev_singer_id: str) -> None:
        """演唱者切换时（自动管理触发）"""
        ...
    
    def on_checkpoint_moved(self, singer_id: str,
                           line_idx: int, char_idx: int, 
                           checkpoint_idx: int) -> None:
        """Checkpoint 位置移动时"""
        ...
    
    def on_timing_warning(self, warning_type: str, 
                         message: str, details: dict) -> None:
        """打轴警告（如时间倒退）"""
        ...

# ========== ProjectService 回调 ==========

class ProjectCallbacks(Protocol):
    """项目管理服务回调接口"""
    
    def on_project_loaded(self, project_id: str, 
                         metadata: dict) -> None:
        """项目加载完成时"""
        ...
    
    def on_project_saved(self, project_id: str, 
                        file_path: str) -> None:
        """项目保存完成时"""
        ...
    
    def on_project_error(self, error_type: str, 
                        message: str) -> None:
        """项目操作错误时"""
        ...

# ========== ExportService 回调 ==========

class ExportCallbacks(Protocol):
    """导出服务回调接口"""
    
    def on_export_started(self, format_name: str, 
                         output_path: str) -> None:
        """导出开始时"""
        ...
    
    def on_export_progress(self, current: int, total: int) -> None:
        """导出进度更新"""
        ...
    
    def on_export_completed(self, format_name: str, 
                           output_path: str, success: bool) -> None:
        """导出完成时"""
        ...

# ========== SingerService 回调 ==========

class SingerCallbacks(Protocol):
    """演唱者服务回调接口"""
    
    def on_singer_added(self, singer_id: str, 
                       name: str, color: str) -> None:
        """演唱者添加时"""
        ...
    
    def on_singer_removed(self, singer_id: str) -> None:
        """演唱者删除时"""
        ...
    
    def on_singer_updated(self, singer_id: str, 
                         updates: dict) -> None:
        """演唱者属性更新时"""
        ...

# ========== CommandManager 回调（撤销/重做） ==========

class CommandCallbacks(Protocol):
    """命令管理器回调接口"""
    
    def on_undo_state_changed(self, can_undo: bool, 
                             undo_desc: Optional[str]) -> None:
        """撤销状态变化时"""
        ...
    
    def on_redo_state_changed(self, can_redo: bool, 
                             redo_desc: Optional[str]) -> None:
        """重做状态变化时"""
        ...
```

### 回调注册方式

```python
class TimingService:
    def __init__(self):
        self.callbacks: Optional[TimingCallbacks] = None
    
    def register_callbacks(self, callbacks: TimingCallbacks) -> None:
        """注册回调接口"""
        self.callbacks = callbacks
    
    def _notify_timetag_added(self, info: TimeTagInfo) -> None:
        """内部通知方法"""
        if self.callbacks:
            self.callbacks.on_timetag_added(info)
```

### 回调调用时序示例

```
用户按下 Space 键
    ↓
TimingService.on_timing_key()
    ↓
添加时间标签到领域对象
    ↓
调用 callbacks.on_timetag_added()  ← UI 更新显示
    ↓
移动到下一个 Checkpoint
    ↓
调用 callbacks.on_checkpoint_moved()  ← UI 更新光标位置
```

## 错误处理策略

### 分层错误类型

- **InfrastructureError**：音频加载失败、文件读写错误
- **ApplicationError**：业务规则违反（如时间倒退）
- **DomainError**：领域约束违反（如无效的时间标签）

### 处理原则

1. **基础设施错误**：向上传播，由 UI 层提示用户
2. **业务错误**：在应用层捕获，返回错误信息给 UI
3. **系统错误**：记录日志，友好提示

## 性能考虑

### 低延迟打轴

- 音频位置获取采用轮询 + 回调混合策略
- 目标精度：10ms
- 键盘事件直接处理，不经过消息队列缓冲

### 卡拉OK预览

- 使用双缓冲绘制避免闪烁
- 60fps 刷新率
- 仅重绘变化的字符

### 大文件处理

- 音频波形按需生成/缓存
- 歌词行采用虚拟列表（仅渲染可见行）

## 项目目录结构

```
strange-uta-game/
├── backend/
│   ├── domain/                 # 领域层
│   │   ├── models.py           # 实体与值对象
│   │   └── project.py          # 聚合根
│   │
│   ├── application/            # 应用服务层
│   │   ├── timing_service.py
│   │   ├── project_service.py
│   │   └── export_service.py
│   │
│   └── infrastructure/         # 基础设施层
│       ├── audio/
│       ├── parsers/
│       └── exporters/
│
├── frontend/                   # 表示层
│   ├── widgets/               # UI 组件
│   ├── components/            # 自定义控件
│   └── main.py
│
├── docs/                       # 设计文档
└── tests/                      # 测试
```
