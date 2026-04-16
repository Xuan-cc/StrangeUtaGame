# StrangeUtaGame 开发计划与测试计划

**制定日期**: 2025-04-16  
**项目**: StrangeUtaGame - 歌词打轴软件  
**架构**: 四层分层架构 (Domain + Application + Infrastructure + Presentation)

---

## 📋 目录

1. [开发计划](#开发计划)
   - [Phase 1: 领域层 (Domain)](#phase-1-领域层-domain)
   - [Phase 2: 基础设施层 - 解析器](#phase-2-基础设施层---解析器)
   - [Phase 3: 应用服务层](#phase-3-应用服务层)
   - [Phase 4: 基础设施层 - 音频引擎](#phase-4-基础设施层---音频引擎)
   - [Phase 5: UI层 - StartupInterface](#phase-5-ui层---startupinterface)
   - [Phase 6: UI层 - EditorInterface核心](#phase-6-ui层---editorinterface核心)
   - [Phase 7: 导出系统](#phase-7-导出系统)
   - [Phase 8: 高级功能](#phase-8-高级功能)

2. [测试计划](#测试计划)
   - [测试金字塔](#测试金字塔)
   - [单元测试](#单元测试)
   - [集成测试](#集成测试)
   - [端到端测试](#端到端测试)
   - [性能测试](#性能测试)

3. [质量保障](#质量保障)
   - [代码规范](#代码规范)
   - [CI/CD流程](#cicd流程)

---

## 开发计划

### 开发原则

1. **从内到外**: 先实现 Domain，再 Application，最后 Infrastructure 和 UI
2. **测试驱动**: 每个模块必须有对应的单元测试
3. **小步快跑**: 每个 Phase 产出可验证的中间成果
4. **文档同步**: 代码变更必须同步更新文档

---

## Phase 1: 领域层 (Domain)

**目标**: 实现核心业务实体和值对象，建立领域模型基础
**周期**: 1-2 周
**依赖**: 无（纯 Python，零外部依赖）

### 任务列表

#### 1.1 值对象 (Value Objects)
- [ ] `Checkpoint` - 节奏点/检查点
- [ ] `CheckpointConfig` - 节奏点配置（check_count, is_line_end）
- [ ] `TimeTag` - 时间标签（timestamp_ms, singer_id）
- [ ] `Ruby` - 注音（text, start_idx, end_idx）

#### 1.2 实体 (Entities)
- [ ] `Singer` - 演唱者（id, name, color, is_default）
- [ ] `LyricLine` - 歌词行（id, singer_id, text, chars, timetags）
- [ ] `Project` - 项目聚合根（id, lines, singers, metadata）

#### 1.3 业务规则验证
- [ ] 时间标签非负验证
- [ ] 字符索引范围验证
- [ ] 演唱者至少一个验证
- [ ] 注音索引不越界验证

### 产出物
```
backend/domain/
├── __init__.py
├── models.py          # 值对象定义
├── entities.py          # 实体定义
├── project.py           # 聚合根
└── validators.py        # 业务规则验证
```

### 验收标准
- [ ] 所有值对象支持相等性比较
- [ ] 所有实体有唯一标识
- [ ] 单元测试覆盖率 100%
- [ ] 边界情况测试通过

### 测试重点
```python
# 示例测试
class TestCheckpoint:
    def test_equality(self):
        cp1 = Checkpoint(timestamp_ms=1000, char_idx=0, checkpoint_idx=0)
        cp2 = Checkpoint(timestamp_ms=1000, char_idx=0, checkpoint_idx=0)
        assert cp1 == cp2
    
    def test_inequality_different_timestamp(self):
        cp1 = Checkpoint(timestamp_ms=1000, char_idx=0, checkpoint_idx=0)
        cp2 = Checkpoint(timestamp_ms=2000, char_idx=0, checkpoint_idx=0)
        assert cp1 != cp2

class TestProject:
    def test_add_lyric_line(self):
        project = Project()
        line = LyricLine(text="测试歌词")
        project.add_line(line)
        assert len(project.lines) == 1
    
    def test_must_have_at_least_one_singer(self):
        with pytest.raises(DomainError):
            Project(singers=[])
```

---

## Phase 2: 基础设施层 - 解析器

**目标**: 实现文件解析和保存功能
**周期**: 1 周
**依赖**: Phase 1 (Domain)

### 任务列表

#### 2.1 歌词文件解析器
- [ ] `TXTLyricParser` - 纯文本歌词导入
- [ ] `LRCLyricParser` - LRC 格式解析
- [ ] `KRALyricParser` - KRA 格式解析（同 LRC）
- [ ] `TextSplitter` - 字符拆分（日文/英文/符号）

#### 2.2 项目文件解析器
- [ ] `SugProjectParser` - SUG 格式解析/保存
- [ ] `SugMigrator` - 版本迁移支持
- [ ] JSON 序列化/反序列化

#### 2.3 注音分析器
- [ ] `RubyAnalyzer` 接口
- [ ] `PykakasiRubyAnalyzer` 实现
- [ ] 注音与字符对齐

### 产出物
```
backend/infrastructure/
├── __init__.py
├── parsers/
│   ├── __init__.py
│   ├── base.py              # 解析器基类
│   ├── txt_parser.py
│   ├── lrc_parser.py
│   ├── text_splitter.py
│   └── ruby_analyzer.py
└── persistence/
    ├── __init__.py
    └── sug_parser.py        # SUG 项目文件解析
```

### 验收标准
- [ ] TXT 文件正确解析为多行
- [ ] LRC 文件正确解析并提取时间标签
- [ ] SUG 文件正确序列化/反序列化
- [ ] 字符拆分符合规则（日文/英文）
- [ ] 注音准确率测试

---

## Phase 3: 应用服务层

**目标**: 实现核心业务逻辑协调
**周期**: 2 周
**依赖**: Phase 1, Phase 2

### 任务列表

#### 3.1 命令模式基础设施
- [ ] `Command` 抽象基类
- [ ] `CommandManager` - 撤销/重做管理
- [ ] 具体命令实现

#### 3.2 核心服务
- [ ] `ProjectService` - 项目管理
  - 创建项目
  - 打开项目（SUG 文件）
  - 保存项目
  - 导入歌词
- [ ] `AutoCheckService` - 自动检查
  - 分析注音
  - 计算 check_count
  - 标记句尾
- [ ] `SingerService` - 演唱者管理
  - 添加/删除演唱者
  - 自动判断当前演唱者

### 产出物
```
backend/application/
├── __init__.py
├── commands/
│   ├── __init__.py
│   ├── base.py
│   └── concrete_commands.py   # AddTimeTagCommand, DeleteCheckpointCommand 等
├── command_manager.py
├── project_service.py
├── auto_check_service.py
└── singer_service.py
```

### 验收标准
- [ ] 项目创建流程完整
- [ ] SUG 文件正确加载
- [ ] 撤销/重做功能正常
- [ ] 自动检查生成正确配置

---

## Phase 4: 基础设施层 - 音频引擎

**目标**: 实现音频播放功能
**周期**: 1-2 周
**依赖**: Phase 1 (Domain)

### 任务列表

#### 4.1 音频引擎接口
- [ ] `IAudioEngine` 接口定义
- [ ] `SoundDeviceEngine` 实现

#### 4.2 音频功能
- [ ] 加载音频文件（MP3, WAV, FLAC）
- [ ] 播放/暂停/停止
- [ ] 位置控制（获取/设置）
- [ ] 变速播放（0.5x ~ 2.0x）
- [ ] 位置回调（60fps）

#### 4.3 音频时长检测
- [ ] 获取音频时长
- [ ] 与项目数据对比验证

### 产出物
```
backend/infrastructure/
└── audio/
    ├── __init__.py
    ├── base.py                # IAudioEngine 接口
    └── sounddevice_engine.py   # sounddevice 实现
```

### 验收标准
- [ ] 音频正常播放无卡顿
- [ ] 位置获取精度 < 20ms
- [ ] 变速播放音质可接受
- [ ] 回调频率稳定在 60fps

### 测试重点
```python
def test_audio_playback():
    engine = SoundDeviceEngine()
    engine.load("test.mp3")
    engine.play()
    time.sleep(1)
    assert engine.is_playing()
    engine.pause()
    assert not engine.is_playing()

def test_position_accuracy():
    engine = SoundDeviceEngine()
    engine.load("test.mp3")
    engine.play()
    time.sleep(0.5)
    pos = engine.get_position_ms()
    assert 450 < pos < 550  # 允许 50ms 误差
```

---

## Phase 5: UI层 - StartupInterface

**目标**: 实现启动界面，支持歌词导入和项目创建/打开
**周期**: 1-2 周
**依赖**: Phase 1-3

### 任务列表

#### 5.1 界面组件
- [ ] `LyricInputPanel` - 歌词输入区
  - 大文本编辑框
  - 粘贴/导入/清空按钮
  - 文件拖拽支持
- [ ] `ImportPreview` - 导入预览
  - 歌词结构表格
  - 字符数/节奏点显示
  - 注音预览
- [ ] `AudioSelectPanel` - 音频选择区
  - 文件选择
  - 拖拽支持
  - 音频信息显示

#### 5.2 功能实现
- [ ] 新建项目流程
- [ ] 打开现有项目（.sug）
- [ ] 歌词文件导入（TXT/LRC/KRA）
- [ ] 自动分析并显示预览
- [ ] 创建项目并进入编辑器

### 产出物
```
frontend/
├── __init__.py
├── main_window.py           # FluentWindow
├── startup/
│   ├── __init__.py
│   ├── startup_interface.py # 启动界面主容器
│   ├── lyric_input_panel.py
│   ├── import_preview.py
│   └── audio_select_panel.py
└── resources/
    └── startup.qss          # 启动界面样式
```

### 验收标准
- [ ] 界面布局合理，视觉美观
- [ ] 文件拖拽功能正常
- [ ] 自动分析显示正确
- [ ] 项目创建流程顺畅
- [ ] 键盘快捷键支持

---

## Phase 6: UI层 - EditorInterface核心

**目标**: 实现编辑器主界面，这是整个应用的核心
**周期**: 3-4 周
**依赖**: Phase 1-5

### 任务列表

#### 6.1 KaraokePreview（最复杂组件）
- [ ] 多行歌词显示（±N 行）
- [ ] 字符级ワイプ效果（渐变高亮）
- [ ] 多演唱者渲染（不同颜色）
- [ ] 60fps 刷新优化
- [ ] Alt+滚轮缩放

#### 6.2 TransportBar
- [ ] 播放/暂停/停止按钮
- [ ] 进度条（可拖拽跳转）
- [ ] 时间显示（MM:SS.CC）
- [ ] 速度控制滑块（50%~200%）

#### 6.3 TimelineWidget
- [ ] 音频波形显示
- [ ] 时间标签位置标记
- [ ] 播放位置游标
- [ ] 缩放支持

#### 6.4 键盘事件处理
- [ ] Space 按下/抬起监听
- [ ] 快捷键映射（A, S, Z, X, Q, W 等）
- [ ] +/- 增删 Checkpoint
- [ ] L 切换句尾标记

#### 6.5 TimingService 集成
- [ ] 回调接口实现
- [ ] 位置更新同步
- [ ] 时间标签添加反馈

### 产出物
```
frontend/
├── editor/
│   ├── __init__.py
│   ├── editor_interface.py    # 编辑器主界面
│   ├── karaoke_preview.py     # 核心组件
│   ├── transport_bar.py
│   ├── timeline_widget.py
│   └── key_handler.py         # 键盘事件处理
└── widgets/
    ├── __init__.py
    └── custom_widgets.py      # 自定义控件
```

### 验收标准
- [ ] ワイプ效果流畅（60fps）
- [ ] Space 打轴延迟 < 20ms
- [ ] 多演唱者渲染正确
- [ ] 快捷键响应迅速
- [ ] 界面缩放功能正常

### 测试重点
```python
# GUI 测试使用 pytest-qt
def test_space_timing(qtbot, editor):
    """测试 Space 键打轴"""
    qtbot.keyPress(editor, Qt.Key_Space)
    # 验证时间标签已添加
    assert len(editor.current_line.timetags) == 1

def test_playback_position_update(qtbot, editor):
    """测试播放位置更新"""
    editor.transport_bar.play()
    qtbot.wait(100)  # 等待 100ms
    # 验证位置已更新
    assert editor.transport_bar.position_ms > 0
```

---

## Phase 7: 导出系统

**目标**: 实现多格式导出功能
**周期**: 1 周
**依赖**: Phase 1-3

### 任务列表

#### 7.1 导出器实现
- [ ] `LRCExporter` - LRC 格式
- [ ] `KRAExporter` - KRA 格式
- [ ] `TXTExporter` - 纯文本
- [ ] `Txt2AssExporter` - txt2ass 格式
- [ ] `NicokaraExporter` - Nicokara 规则

#### 7.2 导出服务
- [ ] `ExportService` - 导出协调
- [ ] 批量导出支持
- [ ] 导出选项配置

#### 7.3 UI 集成
- [ ] `ExportInterface` - 导出界面
- [ ] 格式选择
- [ ] 演唱者选择
- [ ] 进度显示

### 产出物
```
backend/infrastructure/
└── exporters/
    ├── __init__.py
    ├── base.py                # IExporter 接口
    ├── lrc_exporter.py
    ├── kra_exporter.py
    ├── txt_exporter.py
    ├── txt2ass_exporter.py
    └── nicokara_exporter.py

frontend/
└── export/
    ├── __init__.py
    └── export_interface.py
```

### 验收标准
- [ ] 各格式导出正确
- [ ] 注音格式正确（Nicokara）
- [ ] 时间精度正确
- [ ] 批量导出功能正常

---

## Phase 8: 高级功能

**目标**: 实现高级功能，提升用户体验
**周期**: 2-3 周
**依赖**: Phase 1-7

### 任务列表

#### 8.1 时间调整服务
- [ ] `TimingAdjustmentService`
- [ ] 整体偏移调整
- [ ] 比例调整
- [ ] 平滑调整

#### 8.2 设置界面
- [ ] `SettingsInterface`
- [ ] `TimingSettingsPanel` - 打轴设置
- [ ] `DisplaySettingsPanel` - 显示设置
- [ ] `AudioSettingsPanel` - 音频设置
- [ ] `ShortcutSettingsPanel` - 快捷键设置

#### 8.3 演唱者管理界面
- [ ] `SingerManagerInterface`
- [ ] 演唱者列表
- [ ] 添加/删除演唱者
- [ ] 颜色配置

#### 8.4 注音编辑界面
- [ ] `RubyInterface`
- [ ] `RubyEditorTable`
- [ ] 批量编辑注音

### 产出物
```
frontend/
├── settings/
│   ├── __init__.py
│   ├── settings_interface.py
│   ├── timing_settings.py
│   ├── display_settings.py
│   ├── audio_settings.py
│   └── shortcut_settings.py
├── singer/
│   ├── __init__.py
│   └── singer_manager.py
└── ruby/
    ├── __init__.py
    └── ruby_editor.py

backend/application/
└── timing_adjustment_service.py
```

---

# 测试计划

## 测试金字塔

```
        /\
       /  \      End-to-End Tests (5-10%)
      /____\
     /      \    Integration Tests (15-25%)
    /________\
   /          \  Unit Tests (70-80%)
  /____________\
```

### 测试目标

| 类型 | 覆盖率目标 | 执行时间 |
|------|-----------|----------|
| 单元测试 | 80%+ | < 30 秒 |
| 集成测试 | 核心路径覆盖 | < 2 分钟 |
| E2E 测试 | 关键用户流程 | < 5 分钟 |
| 性能测试 | 定期执行 | - |

---

## 单元测试

### 领域层测试 (Phase 1)

**测试目标**: 100% 覆盖率

```python
# tests/domain/test_entities.py
class TestCheckpoint:
    """Checkpoint 值对象测试"""
    
    def test_creation(self):
        cp = Checkpoint(timestamp_ms=1000, char_idx=0, checkpoint_idx=0)
        assert cp.timestamp_ms == 1000
        assert cp.char_idx == 0
    
    def test_equality(self):
        cp1 = Checkpoint(timestamp_ms=1000, char_idx=0, checkpoint_idx=0)
        cp2 = Checkpoint(timestamp_ms=1000, char_idx=0, checkpoint_idx=0)
        assert cp1 == cp2
    
    def test_inequality_different_fields(self):
        cp1 = Checkpoint(timestamp_ms=1000, char_idx=0, checkpoint_idx=0)
        cp2 = Checkpoint(timestamp_ms=2000, char_idx=0, checkpoint_idx=0)
        assert cp1 != cp2
    
    def test_invalid_negative_timestamp(self):
        with pytest.raises(ValueError):
            Checkpoint(timestamp_ms=-100, char_idx=0)

class TestLyricLine:
    """LyricLine 实体测试"""
    
    def test_add_timetag(self):
        line = LyricLine(text="测试")
        tag = TimeTag(timestamp_ms=1000, singer_id="s1")
        line.add_timetag(tag)
        assert len(line.timetags) == 1
    
    def test_get_char_checkpoint_config(self):
        line = LyricLine(text="测试")
        line.add_checkpoint_config(CheckpointConfig(char_idx=0, check_count=2))
        config = line.get_checkpoint_config(0)
        assert config.check_count == 2

class TestProject:
    """Project 聚合根测试"""
    
    def test_must_have_default_singer(self):
        project = Project()
        assert any(s.is_default for s in project.singers)
    
    def test_add_line_with_singer(self):
        project = Project()
        singer = project.singers[0]
        line = LyricLine(text="测试", singer_id=singer.id)
        project.add_line(line)
        assert len(project.lines) == 1
        assert project.lines[0].singer_id == singer.id
    
    def test_cannot_remove_last_singer(self):
        project = Project()
        with pytest.raises(DomainError):
            project.remove_singer(project.singers[0].id)
```

### 解析器测试 (Phase 2)

```python
# tests/infrastructure/test_parsers.py
class TestTXTParser:
    """TXT 歌词解析器测试"""
    
    def test_parse_simple_text(self):
        parser = TXTLyricParser()
        text = "第一行\n第二行\n第三行"
        lines = parser.parse(text)
        assert len(lines) == 3
        assert lines[0].text == "第一行"
    
    def test_parse_empty_lines(self):
        parser = TXTLyricParser()
        text = "第一行\n\n第三行"
        lines = parser.parse(text)
        assert len(lines) == 2  # 空行被过滤

class TestLRCParser:
    """LRC 解析器测试"""
    
    def test_parse_with_timestamps(self):
        parser = LRCLyricParser()
        text = "[00:10.50]第一行\n[00:15.20]第二行"
        lines = parser.parse(text)
        assert len(lines) == 2
        assert lines[0].timetags[0].timestamp_ms == 10500
```

### 命令模式测试 (Phase 3)

```python
# tests/application/test_commands.py
class TestAddTimeTagCommand:
    """添加时间标签命令测试"""
    
    def test_execute_adds_tag(self):
        project = create_test_project()
        line = project.lines[0]
        cmd = AddTimeTagCommand(
            project, line.singer_id, 0, 0, 0, 1000
        )
        cmd.execute()
        assert len(line.timetags) == 1
    
    def test_undo_removes_tag(self):
        project = create_test_project()
        line = project.lines[0]
        cmd = AddTimeTagCommand(
            project, line.singer_id, 0, 0, 0, 1000
        )
        cmd.execute()
        cmd.undo()
        assert len(line.timetags) == 0

class TestCommandManager:
    """命令管理器测试"""
    
    def test_undo_redo(self):
        manager = CommandManager()
        project = create_test_project()
        line = project.lines[0]
        
        cmd = AddTimeTagCommand(project, line.singer_id, 0, 0, 0, 1000)
        manager.execute(cmd)
        
        assert manager.can_undo()
        manager.undo()
        assert len(line.timetags) == 0
        
        assert manager.can_redo()
        manager.redo()
        assert len(line.timetags) == 1
```

---

## 集成测试

### 项目服务集成测试

```python
# tests/integration/test_project_service.py
class TestProjectServiceIntegration:
    """ProjectService 集成测试"""
    
    def test_create_and_save_project(self, tmp_path):
        """测试创建并保存项目"""
        service = ProjectService()
        
        # 创建项目
        project = service.create_project()
        project.add_line(LyricLine(text="测试歌词"))
        
        # 保存项目
        file_path = tmp_path / "test.sug"
        service.save_project(project, str(file_path))
        
        # 验证文件存在
        assert file_path.exists()
    
    def test_load_project(self, tmp_path):
        """测试加载项目"""
        service = ProjectService()
        
        # 先创建并保存
        project = service.create_project()
        project.add_line(LyricLine(text="测试歌词"))
        file_path = tmp_path / "test.sug"
        service.save_project(project, str(file_path))
        
        # 加载
        loaded = service.load_project(str(file_path))
        assert len(loaded.lines) == 1
        assert loaded.lines[0].text == "测试歌词"
```

### 音频引擎集成测试

```python
# tests/integration/test_audio_engine.py
class TestSoundDeviceEngine:
    """音频引擎集成测试"""
    
    def test_load_and_play(self):
        engine = SoundDeviceEngine()
        engine.load("tests/fixtures/test.mp3")
        engine.play()
        
        time.sleep(0.5)
        assert engine.is_playing()
        assert engine.get_position_ms() > 0
        
        engine.stop()
        assert not engine.is_playing()
    
    def test_position_callback(self):
        positions = []
        
        def on_position_changed(pos):
            positions.append(pos)
        
        engine = SoundDeviceEngine()
        engine.set_position_callback(on_position_changed)
        engine.load("tests/fixtures/test.mp3")
        engine.play()
        
        time.sleep(1)
        engine.stop()
        
        # 验证回调被调用多次（约 60fps * 1s = 60 次）
        assert len(positions) >= 50  # 允许一些误差
```

---

## 端到端测试

使用 `pytest-qt` 进行 GUI 测试

```python
# tests/e2e/test_startup.py
class TestStartupInterface:
    """启动界面 E2E 测试"""
    
    def test_import_lyrics_and_create_project(self, qtbot):
        """测试导入歌词并创建项目"""
        from frontend.main import MainWindow
        
        window = MainWindow()
        qtbot.addWidget(window)
        window.show()
        
        # 获取启动界面组件
        startup = window.startup_interface
        
        # 输入歌词
        startup.lyric_input.setPlainText("测试歌词第一行\n测试歌词第二行")
        
        # 触发分析
        qtbot.mouseClick(startup.analyze_btn, Qt.LeftButton)
        
        # 验证预览已更新
        assert startup.import_preview.rowCount() == 2
    
    def test_open_existing_project(self, qtbot, tmp_path):
        """测试打开现有项目"""
        # 先创建一个项目文件
        service = ProjectService()
        project = service.create_project()
        project.add_line(LyricLine(text="测试"))
        sug_file = tmp_path / "test.sug"
        service.save_project(project, str(sug_file))
        
        # 打开项目
        window = MainWindow()
        qtbot.addWidget(window)
        window.show()
        
        # 模拟打开项目流程
        window.open_project(str(sug_file))
        
        # 验证已进入编辑器
        assert window.current_interface == "editor"

class TestEditorInterface:
    """编辑器界面 E2E 测试"""
    
    def test_space_timing(self, qtbot):
        """测试 Space 键打轴"""
        # 创建测试项目
        project = create_test_project()
        
        window = MainWindow()
        window.load_project(project)
        window.load_audio("tests/fixtures/test.mp3")
        qtbot.addWidget(window)
        window.show()
        
        editor = window.editor_interface
        
        # 播放音频
        editor.transport_bar.play()
        qtbot.wait(500)  # 等待 500ms
        
        # 按下 Space
        qtbot.keyPress(editor, Qt.Key_Space)
        
        # 验证时间标签已添加
        current_line = editor.get_current_line()
        assert len(current_line.timetags) == 1
        assert current_line.timetags[0].timestamp_ms > 0
```

---

## 性能测试

### 测试场景

```python
# tests/performance/test_performance.py
class TestKaraokePreviewPerformance:
    """卡拉 OK 预览性能测试"""
    
    def test_rendering_fps(self, qtbot):
        """测试渲染帧率"""
        from frontend.editor.karaoke_preview import KaraokePreview
        
        preview = KaraokePreview()
        qtbot.addWidget(preview)
        
        # 创建大项目
        project = create_large_project(lines=100, chars_per_line=50)
        preview.set_project(project)
        
        # 测量帧率
        frame_times = []
        for i in range(120):  # 测试 2 秒
            start = time.perf_counter()
            preview.update()
            qtbot.wait(16)  # 约 60fps
            end = time.perf_counter()
            frame_times.append((end - start) * 1000)
        
        avg_frame_time = sum(frame_times) / len(frame_times)
        fps = 1000 / avg_frame_time
        
        assert fps >= 55, f"帧率过低: {fps:.1f}fps"
    
    def test_timing_latency(self):
        """测试打轴延迟"""
        from backend.application.timing_service import TimingService
        
        service = TimingService()
        service.load_project(create_test_project())
        service.load_audio("tests/fixtures/test.mp3")
        
        latencies = []
        for i in range(100):
            start = time.perf_counter_ns()
            service.on_timing_key("SPACE")
            end = time.perf_counter_ns()
            latencies.append((end - start) / 1_000_000)
            time.sleep(0.05)
        
        avg_latency = sum(latencies) / len(latencies)
        assert avg_latency < 20, f"平均延迟过高: {avg_latency:.1f}ms"

class TestLargeFilePerformance:
    """大文件性能测试"""
    
    def test_load_large_project(self):
        """测试大项目加载"""
        service = ProjectService()
        
        # 创建大项目
        project = create_large_project(lines=1000, chars_per_line=50)
        file_path = "tests/fixtures/large_test.sug"
        service.save_project(project, file_path)
        
        # 测量加载时间
        start = time.time()
        loaded = service.load_project(file_path)
        load_time = time.time() - start
        
        assert load_time < 3, f"加载时间过长: {load_time:.2f}秒"
        
        # 验证内存占用
        import psutil
        process = psutil.Process()
        memory_mb = process.memory_info().rss / 1024 / 1024
        assert memory_mb < 500, f"内存占用过高: {memory_mb:.1f}MB"
```

---

# 质量保障

## 代码规范

### 1. 代码格式化

```bash
# 使用 Black 格式化
pip install black
black src/ tests/

# 使用 Ruff 检查和修复
pip install ruff
ruff check src/ tests/
ruff check --fix src/ tests/
```

### 2. 类型检查

```bash
# 使用 mypy 进行静态类型检查
pip install mypy
mypy src/backend/domain src/backend/application
```

### 3. 文档字符串

所有公共 API 必须有文档字符串：

```python
def add_timetag(self, tag: TimeTag) -> None:
    """添加时间标签到歌词行
    
    Args:
        tag: 要添加的时间标签
        
    Raises:
        DomainError: 如果时间标签无效
        
    Example:
        >>> line = LyricLine(text="测试")
        >>> tag = TimeTag(timestamp_ms=1000, singer_id="s1")
        >>> line.add_timetag(tag)
    """
    # 实现...
```

## CI/CD流程

### GitHub Actions 配置

```yaml
# .github/workflows/ci.yml
name: CI

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    
    steps:
    - uses: actions/checkout@v3
    
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'
    
    - name: Install dependencies
      run: |
        pip install -r requirements.txt
        pip install pytest pytest-cov pytest-qt black ruff mypy
    
    - name: Lint with Ruff
      run: ruff check src/ tests/
    
    - name: Format with Black
      run: black --check src/ tests/
    
    - name: Type check with mypy
      run: mypy src/backend/domain src/backend/application
    
    - name: Run unit tests
      run: pytest tests/unit -v --cov=src --cov-report=xml
    
    - name: Run integration tests
      run: pytest tests/integration -v
      env:
        QT_QPA_PLATFORM: offscreen
    
    - name: Upload coverage
      uses: codecov/codecov-action@v3
      with:
        file: ./coverage.xml
```

### 预提交钩子

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/psf/black
    rev: 23.12.1
    hooks:
      - id: black
        language_version: python3.11

  - repo: https://github.com/charliermarsh/ruff-pre-commit
    rev: v0.1.9
    hooks:
      - id: ruff
        args: [--fix, --exit-non-zero-on-fix]

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.7.1
    hooks:
      - id: mypy
        additional_dependencies: [types-all]
```

---

# 项目结构

```
strange-uta-game/
├── src/
│   └── strange_uta_game/
│       ├── backend/
│       │   ├── __init__.py
│       │   ├── domain/
│       │   │   ├── __init__.py
│       │   │   ├── models.py
│       │   │   ├── entities.py
│       │   │   ├── project.py
│       │   │   └── validators.py
│       │   ├── application/
│       │   │   ├── __init__.py
│       │   │   ├── commands/
│       │   │   ├── command_manager.py
│       │   │   ├── project_service.py
│       │   │   ├── auto_check_service.py
│       │   │   ├── singer_service.py
│       │   │   └── timing_adjustment_service.py
│       │   └── infrastructure/
│       │       ├── __init__.py
│       │       ├── parsers/
│       │       ├── audio/
│       │       ├── exporters/
│       │       └── persistence/
│       └── frontend/
│           ├── __init__.py
│           ├── main_window.py
│           ├── startup/
│           ├── editor/
│           ├── settings/
│           ├── singer/
│           ├── export/
│           └── widgets/
├── tests/
│   ├── __init__.py
│   ├── unit/
│   │   ├── domain/
│   │   ├── application/
│   │   └── infrastructure/
│   ├── integration/
│   ├── e2e/
│   ├── performance/
│   └── fixtures/
│       ├── test.mp3
│       └── test.sug
├── docs/
├── .github/
│   └── workflows/
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── pytest.ini
├── .pre-commit-config.yaml
└── README.md
```

---

# 依赖管理

## requirements.txt

```
# 核心依赖
pyqt6>=6.6.0
pyqt-fluent-widgets>=1.5.0
sounddevice>=0.4.6
soundfile>=0.12.1
pykakasi>=2.2.1
```

## requirements-dev.txt

```
# 开发依赖
-r requirements.txt

# 测试
pytest>=7.4.0
pytest-cov>=4.1.0
pytest-qt>=4.2.0
pytest-asyncio>=0.21.0

# 代码质量
black>=23.12.0
ruff>=0.1.9
mypy>=1.7.1

# 性能测试
psutil>=5.9.0
```

---

# 时间估算

| Phase | 估算时间 | 优先级 |
|-------|---------|--------|
| Phase 1: 领域层 | 1-2 周 | 🔴 高 |
| Phase 2: 解析器 | 1 周 | 🔴 高 |
| Phase 3: 应用服务 | 2 周 | 🔴 高 |
| Phase 4: 音频引擎 | 1-2 周 | 🟠 中 |
| Phase 5: StartupInterface | 1-2 周 | 🟠 中 |
| Phase 6: EditorInterface | 3-4 周 | 🔴 高 |
| Phase 7: 导出系统 | 1 周 | 🟡 低 |
| Phase 8: 高级功能 | 2-3 周 | 🟡 低 |
| **总计** | **12-17 周** | - |

**建议**: 可以先实现 MVP（Phase 1-6 核心功能），约 8-10 周，然后逐步添加高级功能。

---

**计划完成**: 本计划涵盖完整开发和测试流程，可作为项目实施的路线图。
