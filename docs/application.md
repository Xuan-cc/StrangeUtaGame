# 应用服务层设计

## 概述

应用服务层负责**编排业务用例**，协调领域对象和基础设施完成具体业务流程。它是领域层和表示层之间的桥梁，保持无状态或仅有流程状态。

## 核心服务

### TimingService（打轴服务）

**职责**：协调音频播放、用户输入、歌词数据，实现打轴核心流程

**依赖**：
- AudioEngine（音频引擎）
- Project（当前项目数据）
- SingerService（获取当前演唱者）

**状态**：
- 当前 Checkpoint 位置（全局索引）
- 当前演唱者 ID（从 Checkpoint 自动推导）
- 当前行索引（在演唱者内的索引）
- 当前字符索引
- 当前节奏点索引（checkpoint_idx）
- 录制状态（是否正在打轴）
- 显示模式（单演唱者/全部演唱者）

**关于演唱者管理的说明**：

**核心原则**：用户不需要手动切换演唱者，系统后台自动管理。

**两种使用模式**：

1. **打轴模式（自动管理）**：
   - 系统维护全局 Checkpoint 序列（跨所有演唱者）
   - 用户按顺序打轴，系统自动识别当前 Checkpoint 所属演唱者
   - UI 自动切换到对应演唱者的歌词显示
   - 用户感知为"连续往下打"，无感知切换

2. **配置模式（手动选择）**：
   - 仅在 SingerManagerInterface 中使用
   - 用户手动选择演唱者进行属性配置（名称、颜色）
   - 不涉及打轴流程

**状态关系**：
```
全局 Checkpoint 索引 → 查询 Checkpoint 所属 singer_id → 当前演唱者 ID
```
- 当前演唱者 ID 是从 Checkpoint 位置推导的，不是独立状态
- 移动 Checkpoint 时自动更新当前演唱者

**主要用例**：

1. **开始/停止录制**
   - 启动/暂停音频播放
   - 绑定/解绑快捷键
   - 启动/停止位置更新

2. **打轴按键处理（核心逻辑 - 支持多演唱者）**
   
   **正确的打轴流程**：
   1. 获取当前演唱者 ID（从 SingerService）
   2. 获取当前选中的 Checkpoint（行索引 + 字符索引 + 节奏点索引）
   3. 获取当前音频时间 + 打轴偏移量（补偿反应延迟）
   4. **将该时间记录到当前演唱者 + Checkpoint 对应的时间标签**（带上 singer_id）
   5. 自动移动到下一个 Checkpoint（可能还是同演唱者，或用户切换到其他演唱者）
   6. 触发回调通知 UI 更新（高亮新位置，按演唱者颜色显示）
   
   **注意**：不是"生成新轴点"，而是"给当前演唱者的已选中 checkpoint 打上时间"
   
    **节奏点导航逻辑**：
    - 一个字符可能有多个节奏点（连打场景）
    - 打完当前节奏点后，优先移动到同一字符的下一个节奏点
    - 当前字符的节奏点全部打完，移动到下一个字符的第一个节奏点
    - 当前行的字符全部打完，移动到下一行的第一个字符
    - **跨演唱者自动处理**：系统后台自动管理演唱者切换，用户无感知

3. **句尾字符长按打轴（特殊逻辑）**
   
   **触发条件**：当前字符标记为句尾（is_line_end = true）
   
   **处理流程**：
   1. **KeyDown（按下 Space）**：
      - 检测当前字符是否为句尾
      - 如果是，记录**开始时间**到第一个 Checkpoint
      - 设置标志位：`line_end_recording = true`
      - 触发 UI 更新：句尾字符开始持续高亮
   
   2. **KeyUp（抬起 Space）**：
      - 检测 `line_end_recording` 标志位
      - 如果是，记录**结束时间**到第二个 Checkpoint
      - 计算拖音时长：结束时间 - 开始时间
      - 清除标志位
      - 触发 UI 更新：句尾字符完全高亮
      - 自动移动到下一行的第一个字符
   
   3. **超时处理**：
      - 如果 KeyDown 超过 5 秒未 KeyUp：自动视为结束
      - 防止用户忘记抬起按键
   
   4. **短按处理（普通字符逻辑）**：
      - 如果 KeyDown 到 KeyUp 间隔 < 100ms
      - 视为普通点击，只记录一个时间点（不按句尾处理）
   
   **与普通字符的区别**：
   - 普通字符：按一下 Space = 一个时间点，立即跳到下一个
   - 句尾字符：按下 = 开始时间，抬起 = 结束时间，然后跳到下一行
   
   **状态管理**：
   - `line_end_recording`: 是否正在记录句尾拖音
   - `line_end_start_time`: 句尾开始时间（用于计算拖音时长）

5. **演唱者自动管理（后台逻辑）**
   
   **核心设计原则**：用户不需要手动切换演唱者，系统自动处理
   
   **Checkpoint 全局序列**：
   - 系统维护一个全局的 Checkpoint 序列（跨所有演唱者）
   - 按歌词文本的自然顺序排列（不是按时间顺序）
   - 示例：演唱者1行1 → 演唱者1行2 → 演唱者2行1 → 演唱者1行3...
   
   **用户操作流程**：
   1. 用户按顺序打轴（空格/F1-F9）
   2. 系统自动识别当前 Checkpoint 属于哪个演唱者
   3. 为该演唱者的该 Checkpoint 打上时间标签
   4. 自动移动到下一个 Checkpoint（可能是另一个演唱者）
   5. UI 自动渲染对应演唱者的歌词
   
   **渲染自动切换**：
   - 当前 Checkpoint 属于演唱者1 → 显示演唱者1的歌词
   - 用户打完，下一个 Checkpoint 属于演唱者2 → 自动切换到演唱者2的歌词
   - 切换平滑，用户感知为"连续往下打"

6. **多演唱者同时预览（后台处理）**
   - 系统同时跟踪所有演唱者的打轴进度
   - 根据当前 Checkpoint 的位置，自动显示对应演唱者
   - 其他演唱者的歌词在后台更新（不打断当前流程）
   - 播放时：同时渲染所有演唱者（和声场景）
   
   **自动合并时间线**：
   - 虽然用户按文本顺序打轴
   - 但系统按时间顺序组织和渲染
   - 时间重叠自动处理和声

3. **位置跳转**
   - 音频跳转
   - 更新光标位置
   - 同步 UI 显示

4. **变速播放**
   - 调整音频播放速度
   - 范围：0.5x ~ 2.0x

**回调接口**：

```python
class TimingCallbacks:
    """TimingService 回调接口定义"""
    
    def on_timetag_added(self, 
                        singer_id: str, 
                        line_idx: int, 
                        char_idx: int, 
                        checkpoint_idx: int, 
                        timestamp_ms: int) -> None:
        """时间标签添加时回调"""
        pass
    
    def on_position_changed(self, 
                           position_ms: int,
                           singer_positions: Dict[str, int]) -> None:
        """
        播放位置变化时回调
        singer_positions: {singer_id: line_idx} 各演唱者的当前行索引
        """
        pass
    
    def on_singer_changed(self, 
                         new_singer_id: str, 
                         prev_singer_id: str) -> None:
        """演唱者切换时回调（自动管理触发）"""
        pass
    
    def on_display_mode_changed(self, 
                               mode: str) -> None:
        """显示模式切换时回调 (single/all)"""
        pass
    
    def on_checkpoint_moved(self,
                           line_idx: int,
                           char_idx: int,
                           checkpoint_idx: int,
                           singer_id: str) -> None:
        """Checkpoint 位置移动时回调"""
        pass
    
    def on_timing_error(self, 
                       error_type: str, 
                       message: str) -> None:
        """打轴错误回调（如时间倒退警告）"""
        pass
```

**配置参数**（从 SettingsService 获取）：
- `timing_offset_ms`: 打轴偏移量（默认 -20ms，补偿反应延迟）
- `zx_step_ms`: Z/X 键快进/快退步长（默认 5000ms）
- `fine_step_ms`: Shift+Z/X 微调步长（默认 100ms）

### TimingAdjustmentService（时间调整服务）

**职责**：提供时间标签批量调整功能

**主要用例**：

1. **整体偏移调整**
   - **场景**：发现所有时间标签整体偏早/偏晚
   - **操作**：增加或减少固定毫秒数
   - **范围**：-60000ms ~ +60000ms（±1分钟）
   - **应用**：选择范围（全部/从当前位置开始）

2. **比例调整**
   - **场景**：音频变速后时间标签需要同步缩放
   - **操作**：乘以比例系数（如 0.5x、1.5x）
   - **基准点**：可选固定某时间点不变

3. **平滑调整**
   - **场景**：某些时间标签间隔不合理，需要平滑处理
   - **操作**：自动调整使间隔均匀
   - **约束**：保持相对顺序

4. **批量移动**
   - **场景**：某一行或某一段需要整体移动
   - **操作**：选择行范围，调整偏移量

**Undo 支持**：
- 所有调整操作支持撤销
- 记录调整前的完整状态

### SingerService（演唱者管理服务）

**职责**：管理演唱者的配置（名称、颜色），后台自动处理演唱者分配

**依赖**：
- Project（当前项目）
- TimingService（获取当前 Checkpoint）

**主要用例**：

1. **添加演唱者（配置性质）**
   - 输入：名称、颜色（可选，自动分配）
   - 创建 Singer 对象
   - 添加到 Project.singers
   - **使用场景**：添加和声部分，为该演唱者分配歌词段落
   - 系统自动分配歌词到新演唱者

2. **删除演唱者**
   - **选项A**：级联删除该演唱者的所有歌词
   - **选项B**：将其歌词合并到其他演唱者
   - **约束**：至少保留一个演唱者

3. **查询当前演唱者（自动判断）**
   - **输入**：当前 Checkpoint 位置
   - **输出**：该 Checkpoint 所属的 Singer
   - **用途**：TimingService 自动判断当前为哪个演唱者打轴
   - **用户无感知**：后台自动处理，用户不需要手动切换

4. **修改演唱者属性（配置性质）**
   - 重命名
   - 修改显示颜色（用于区分和声）
   - 启用/禁用（禁用的演唱者不参与全局序列）

5. **获取演唱者列表（仅配置用）**
   - 用于配置界面显示演唱者信息
   - 支持过滤（仅启用/全部）

**回调接口**：
- `on_singer_added(singer)`：新演唱者添加（用于配置界面刷新）
- `on_singer_removed(singer_id)`：演唱者删除
- `on_singer_updated(singer)`：演唱者属性修改（颜色、名称变更时渲染更新）

**后台管理逻辑**：
- 维护全局 Checkpoint 序列（跨所有演唱者）
- 根据当前位置自动识别所属演唱者
- 渲染时自动显示对应演唱者
- 用户始终按"下一个"操作，无需关心演唱者切换

### SettingsService（设置服务）

**职责**：管理应用配置，提供配置持久化

**配置分类**：

1. **打轴配置（TimingConfig）**
   - 打轴偏移量（timing_offset_ms）
   - 快进/快退步长（zx_step_ms, fine_step_ms）
   - 自动检查选项（促音/长音/行首/行尾）

2. **显示配置（DisplayConfig）**
   - 主题/颜色
   - 字体设置
   - 预览效果参数

3. **音频配置（AudioConfig）**
   - 输出设备
   - 缓冲区大小
   - 默认播放速度

4. **快捷键配置（ShortcutConfig）**
   - 自定义键位映射

**持久化**：
- 存储格式：JSON
- 存储位置：用户配置目录
- 自动加载/保存

### ProjectService（项目管理服务）

**职责**：管理项目生命周期（创建、打开、保存、导入）

**依赖**：
- FileParsers（文件解析器）
- 持久化存储（文件系统）

**主要用例**：

1. **创建新项目**
   - 创建 Project 对象（仅包含歌词数据）
   - 音频文件在后续由用户单独选择
   - 触发项目加载回调

2. **打开项目（加载 SUG 文件）**
   - 解析 SUG 文件，重建 Project 对象（仅歌词和时间数据）
   - **弹出音频选择对话框**，让用户选择/拖入音频文件
   - 验证音频时长与项目数据是否匹配（可选，提示用户）
   - 触发项目加载回调

3. **保存项目**
   - 序列化 Project 到 SUG（只包含歌词、时间标签、演唱者）
   - **不保存音频路径**
   - 写入文件系统
   - 触发保存完成回调

4. **导入歌词**
   - 根据文件扩展名选择解析器
   - 解析歌词文本
   - 创建 LyricLine 并添加到 Project

**项目加载流程（重要）**：

```
用户打开 .sug 文件
    ↓
ProjectService 解析 SUG，重建 Project（歌词 + 时间数据）
    ↓
弹出音频选择对话框
    ↓
用户选择/拖入音频文件
    ↓
验证音频（时长匹配提示，不匹配时警告但不阻止）
    ↓
加载完成，进入 EditorInterface
```

**错误处理**：
- SUG 文件不存在或损坏
- 格式解析错误
- 权限错误
- 音频文件验证警告（不阻止）

### ExportService（导出服务）

**职责**：管理导出器，协调导出流程

**依赖**：
- Exporters（导出器集合）

**主要用例**：

1. **单格式导出**
   - 根据格式名查找 Exporter
   - 调用 Exporter 生成文件
   - 处理导出选项

2. **批量导出**
   - 遍历导出配置列表
   - 执行每个导出任务
   - 汇总导出结果

3. **导出器注册**
   - 内置导出器自动注册
   - 支持运行时注册新导出器

**支持的导出格式**：

| 格式 | 用途 | 特性 |
|------|------|------|
| LRC | 通用歌词 | 行级时间标签 |
| KRA | 卡拉OK | 同 LRC，不同扩展名 |
| TXT | 纯文本 | 简化格式 |
| txt2ass | ASS字幕 | 带注音的卡拉OK格式 |
| Nicokara规则 | ニコカラメーカー | 字符级时间标签 + 注音 |

### AutoCheckService（自动检查服务）

**职责**：分析歌词，计算每个字符的节奏点数量（check_count）和注音

**依赖**：
- RubyAnalyzer（注音分析器）
- SettingsService（获取自动检查配置）

**主要用例**：

1. **完整分析（在 StartupInterface 使用）**
   - **触发方式**：用户点击"自动分析"按钮
   - **输入**：纯文本歌词
   - **处理**：
     - 逐字符分析注音
     - 根据注音计算节奏点数量
     - 标记句尾字符
   - **输出**：带有 checkpoints 配置和注音的 LyricLine 列表
   - **预览**：在 ImportPreview 中显示分析结果（字符 + 节奏点数 + 注音）

2. **重新分析（在 EditorInterface 使用）**
   - **触发方式**：
     - 菜单：工具 > 自动检查
     - 快捷键：可配置（如 Ctrl+Shift+A）
   - **处理范围**：
     - 全部重新分析
     - 或仅选中行重新分析
   - **影响**：更新 checkpoints 配置，保留已有的时间标签（仅调整数量）

3. **注音单独分析**
   - **触发方式**：菜单 > 工具 > 注音分析
   - **用途**：只更新注音，不修改节奏点数量

4. **配置选项**（从 SettingsService 获取）
   - 促音「っ」是否计数（check_count 为 0 或 1）
   - 长音「ー」是否计数
   - 行首/行尾特殊处理
   - 英文单词节奏点模式

**算法逻辑**：

1. 对于每个字符：
   - 调用 RubyAnalyzer 获取注音
   - 根据注音假名数量计算 check_count
   - 应用配置选项调整（促音/长音特殊处理）
   - 标记行末字符 is_line_end = true

2. 特殊处理：
   - 汉字：注音假名数量 = 节奏点数量
   - 假名：通常 1 个
   - 促音/长音：根据配置
   - 英文单词：可配置为单字或整词

**用户交互**：
- 分析前：显示预估的节奏点总数
- 分析后：在预览中高亮显示差异（如新增加的 rhythm points）
- 支持撤销分析结果

## 撤销/重做机制 (Undo/Redo)

### 全局命令模式

采用**命令模式 (Command Pattern)** 实现全局撤销/重做：

```python
from abc import ABC, abstractmethod
from typing import List, Optional

class Command(ABC):
    """命令基类"""
    
    @abstractmethod
    def execute(self) -> None:
        """执行命令"""
        pass
    
    @abstractmethod
    def undo(self) -> None:
        """撤销命令"""
        pass
    
    @abstractmethod
    def redo(self) -> None:
        """重做命令（默认与 execute 相同）"""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """命令描述（用于 UI 显示，如"添加时间标签"）"""
        pass

# 具体命令示例
class AddTimeTagCommand(Command):
    """添加时间标签命令"""
    
    def __init__(self, project: Project, singer_id: str, 
                 line_idx: int, char_idx: int, timestamp_ms: int):
        self.project = project
        self.singer_id = singer_id
        self.line_idx = line_idx
        self.char_idx = char_idx
        self.timestamp_ms = timestamp_ms
        self.added_tag: Optional[TimeTag] = None
    
    def execute(self) -> None:
        line = self.project.get_line(self.line_idx)
        self.added_tag = TimeTag(
            timestamp_ms=self.timestamp_ms,
            singer_id=self.singer_id,
            char_idx=self.char_idx
        )
        line.add_timetag(self.added_tag)
    
    def undo(self) -> None:
        line = self.project.get_line(self.line_idx)
        line.remove_timetag(self.added_tag)
    
    @property
    def description(self) -> str:
        return f"添加时间标签 [{self.timestamp_ms}ms]"

class DeleteCheckpointCommand(Command):
    """删除节奏点命令"""
    
    def execute(self) -> None:
        # 保存删除前的状态
        # 执行删除
        pass
    
    def undo(self) -> None:
        # 恢复删除前的状态
        pass
```

### 命令管理器

```python
class CommandManager:
    """命令管理器 - 维护撤销/重做栈"""
    
    def __init__(self, max_history: int = 100):
        self.undo_stack: List[Command] = []
        self.redo_stack: List[Command] = []
        self.max_history = max_history
    
    def execute(self, command: Command) -> None:
        """执行命令并加入撤销栈"""
        command.execute()
        self.undo_stack.append(command)
        self.redo_stack.clear()  # 新操作后清空重做栈
        
        # 限制历史记录数量
        if len(self.undo_stack) > self.max_history:
            self.undo_stack.pop(0)
    
    def undo(self) -> Optional[str]:
        """撤销上一个命令，返回命令描述"""
        if not self.undo_stack:
            return None
        
        command = self.undo_stack.pop()
        command.undo()
        self.redo_stack.append(command)
        return command.description
    
    def redo(self) -> Optional[str]:
        """重做下一个命令，返回命令描述"""
        if not self.redo_stack:
            return None
        
        command = self.redo_stack.pop()
        command.redo()
        self.undo_stack.append(command)
        return command.description
    
    def can_undo(self) -> bool:
        return len(self.undo_stack) > 0
    
    def can_redo(self) -> bool:
        return len(self.redo_stack) > 0
    
    def get_undo_description(self) -> Optional[str]:
        """获取下一个可撤销命令的描述"""
        if self.undo_stack:
            return self.undo_stack[-1].description
        return None
    
    def get_redo_description(self) -> Optional[str]:
        """获取下一个可重做命令的描述"""
        if self.redo_stack:
            return self.redo_stack[-1].description
        return None
```

### 支持撤销的操作

**必须支持撤销**：
1. 添加/删除时间标签
2. 添加/删除 Checkpoint（+/- 键）
3. 修改句尾标记（L 键）
4. 时间调整（批量偏移、比例调整）
5. 添加/删除歌词行
6. 添加/删除演唱者
7. 修改注音
8. 自动检查结果应用

**不支持撤销**：
1. 音频播放控制（播放/暂停/跳转）- 纯 UI 操作
2. 界面缩放（Alt+滚轮）- 纯 UI 设置
3. 设置更改 - 单独管理

### 快捷键绑定

| 快捷键 | 功能 |
|--------|------|
| `Ctrl+Z` | 撤销 |
| `Ctrl+Y` 或 `Ctrl+Shift+Z` | 重做 |

### 批量操作的撤销

批量操作（如自动检查、批量时间调整）作为一个整体命令：

```python
class BatchCommand(Command):
    """批量命令 - 包含多个子命令"""
    
    def __init__(self, commands: List[Command], description: str):
        self.commands = commands
        self._description = description
    
    def execute(self) -> None:
        for cmd in self.commands:
            cmd.execute()
    
    def undo(self) -> None:
        # 逆向撤销
        for cmd in reversed(self.commands):
            cmd.undo()
    
    @property
    def description(self) -> str:
        return self._description
```

### 与保存的集成

- 撤销栈不影响"是否已保存"状态
- 保存操作不清空撤销栈
- 关闭项目时清空撤销栈

## 服务间协作

### 打轴场景协作

```
用户打开音频文件
    ↓
ProjectService 创建项目
    ↓
TimingService 初始化（绑定音频和项目）
    ↓
用户按空格键
    ↓
TimingService 获取音频时间
    ↓
向 LyricLine 添加 TimeTag
    ↓
回调通知 UI 更新显示
```

### 导出场景协作

```
用户选择导出格式
    ↓
ExportService 查找对应 Exporter
    ↓
Exporter 读取 Project 数据
    ↓
按格式规范生成文本
    ↓
写入输出文件
    ↓
返回导出结果
```

## 与表示层交互

### 回调机制

服务层通过**回调接口**与表示层通信，避免直接依赖 Qt：

- **回调类型**：函数引用或接口对象
- **调用时机**：状态变更后
- **参数**：变更的数据（不包含 UI 对象）

### 示例回调

- `on_timetag_added(line_idx, char_idx, timestamp_ms)`
- `on_position_changed(position_ms)`
- `on_project_loaded(project)`
- `on_error(error_message)`

## 事务性操作

### 保存操作

1. 验证数据有效性
2. 序列化到临时文件
3. 原子性替换原文件
4. 触发保存成功回调

### 导出操作

1. 验证输出路径可写
2. 生成导出内容
3. 写入文件
4. 返回成功/失败状态

## 错误处理

### 错误分类

1. **用户错误**：文件不存在、格式错误
   - 处理：友好提示，允许重试

2. **系统错误**：权限不足、磁盘满
   - 处理：记录日志，提示用户

3. **业务错误**：时间倒退、数据不一致
   - 处理：验证失败，拒绝操作

### 错误传播

- 底层错误向上包装
- 保留原始错误信息
- UI 层决定如何展示

## 状态管理

### 无状态服务

- ExportService：纯协调逻辑
- AutoCheckService：纯计算逻辑

### 有状态服务

- TimingService：维护打轴状态
- ProjectService：维护当前项目

**状态持久化**：
- 服务重启后状态丢失
- 重要状态通过 Project 持久化

## 扩展设计

### 添加新服务

1. 定义服务职责和接口
2. 在 Application 层初始化
3. 注入依赖的其他服务或基础设施
4. 提供回调接口供 UI 层使用

### 服务替换

- 保持接口不变
- 通过依赖注入替换实现
- 不影响上层代码
