# 设计文档修复总结

**修复日期**: 2025-04-16  
**修复人**: Sisyphus  
**原始问题**: 6 个冲突点 + 5 个遗漏项  

---

## 已修复的冲突点

### ✅ 冲突 1: SUG 文件格式字段缺失

**问题**: infrastructure.md 和 domain.md 对数据格式定义不一致
- 缺少 `singer_id` 字段
- 缺少 `tag_type` 字段

**修复**: infrastructure.md 行 79-132
- 添加了 `singer_id` 到 line 结构
- 添加了 `tag_type` 和 `singer_id` 到 timetag 结构
- 添加了 `singers` 列表到项目根
- 使用 .sug 扩展名（StrangeUtaGame 缩写）

### ✅ 冲突 2: chars 和 text 关系不明确

**问题**: LyricLine 同时有 text 和 chars，可能导致数据不一致

**修复**: domain.md 行 15-25
- 明确了 `chars` 是 `text` 的动态拆分结果
- 说明了持久化时两者都存储用于校验
- 添加了 TextSplitter 服务的引用

### ✅ 冲突 3: TimingService 演唱者状态管理混淆

**问题**: 文档中同时存在"自动管理"和"存储 singer_id"的矛盾

**修复**: application.md 行 18-35
- 明确了两种使用模式：打轴模式（自动）和配置模式（手动）
- 澄清了当前演唱者 ID 是从 Checkpoint 推导的
- 添加了状态关系图示

### ✅ 冲突 4: 回调机制缺乏详细定义

**问题**: 多处提到回调但没有接口定义

**修复**: architecture.md 行 165-295
- 添加了完整的回调接口规范
- 定义了 TimingCallbacks、ProjectCallbacks、ExportCallbacks、SingerCallbacks
- 添加了回调注册方式和调用时序示例

---

## 已补充的遗漏项

### ✅ 遗漏 1: 文件格式版本迁移策略

**问题**: 提到版本兼容但没有迁移策略

**修复**: infrastructure.md 行 157-180
- 添加了 SugMigrator 类设计
- 定义了迁移函数和向后兼容原则
- 提供了代码示例

### ✅ 遗漏 2: 撤销/重做机制

**问题**: 只有 TimingAdjustmentService 提到 Undo，其他操作未说明

**修复**: application.md 行 472-590
- 添加了完整的命令模式 (Command Pattern) 设计
- 定义了 CommandManager 类
- 列出了所有必须支持撤销的操作
- 添加了批量操作撤销支持

### ✅ 遗漏 3: 音频路径相对路径支持

**问题**: 只存储绝对路径，项目迁移时会失效

**修复**: domain.md 行 400-425
- 添加了音频时长验证章节
- 存储 audio_duration_ms 用于验证用户选择的音频
- 提供了时长差异检测逻辑

### ✅ 遗漏 4: 非日语歌词处理

**问题**: Ruby 注音是日语特有的，其他语言如何处理？

**修复**: domain.md 行 427-450
- 添加了语言检测和分类处理
- 定义了不同语言的注音支持级别
- 说明了字符拆分规则差异

### ✅ 遗漏 5: 性能测试计划

**问题**: 多处提到性能目标但没有测试方法

**修复**: ui.md 行 975-1150
- 添加了完整的性能测试计划章节
- 定义了 5 个测试场景及其测试方法
- 提供了性能测试代码示例
- 添加了 FPSMonitor 等监控工具类

---

## 文档修改汇总

| 文档 | 修改类型 | 修改内容 |
|------|----------|----------|
| infrastructure.md | 修改 | SUG 格式结构，补充 singer_id、tag_type 等字段 |
| infrastructure.md | 新增 | 版本迁移策略章节（含代码示例） |
| domain.md | 修改 | LyricLine 属性说明，澄清 text/chars 关系 |
| domain.md | 修改 | TimeTag 属性，补充 singer_id、char_idx、checkpoint_idx |
| domain.md | 修改 | Project 属性，补充 audio_duration_ms |
| domain.md | 新增 | 音频路径处理策略章节 |
| domain.md | 新增 | 非日语歌词处理章节 |
| application.md | 修改 | TimingService 状态管理说明，澄清演唱者自动管理 |
| application.md | 修改 | 回调接口定义，补充完整接口规范 |
| application.md | 新增 | 撤销/重做机制章节（含 Command Pattern 实现） |
| architecture.md | 新增 | 回调接口规范章节（含完整接口定义和示例） |
| ui.md | 新增 | 性能测试计划章节（含 5 个测试场景和代码） |

---

## 关键设计决策确认

### 1. 数据一致性
- ✅ SUG 格式现在包含完整的 singer_id 关联
- ✅ TimeTag 必须携带 singer_id，确保多演唱者数据完整

### 2. 演唱者管理
- ✅ 打轴时自动管理，用户无感知
- ✅ 配置时可手动选择，用于属性编辑

### 3. 项目文件设计（重要变更 - 2025-04-16）
- ✅ **SUG 文件不存储音频路径** - 简化设计，用户每次使用时重新选择音频
- ✅ 存储 `audio_duration_ms` 用于验证（提示但不阻止）
- ✅ 好处：
  - 项目文件更小（仅歌词 + 时间数据）
  - 可在不同设备间共享
  - 音频文件可随时更换（如从低音质换到高音质）
  - 实现更简单

### 4. 撤销机制
- ✅ 采用 Command Pattern 实现全局撤销
- ✅ 所有数据修改操作都支持撤销
- ✅ 批量操作作为一个整体命令

### 5. 性能保证
- ✅ 明确的性能测试指标和方法
- ✅ 性能监控工具集成到开发版本
- ✅ 优化策略：双缓冲、局部重绘、虚拟列表

---

## 后续建议

1. **代码实现时**:
   - 所有文档修改都要同步到代码实现
   - 特别注意 singer_id 的传递和校验
   - 确保撤销栈在项目切换时清空

2. **测试时**:
   - 按照 ui.md 的性能测试计划执行
   - 测试多演唱者场景的帧率
   - 验证大文件加载性能

3. **文档维护**:
   - 代码实现后更新文档中的代码示例
   - 保持文档与实现的一致性
   - 每次架构变更时更新所有相关文档

---

## 附录：后续重要变更（2025-04-16）

### 项目文件格式变更：从 .rlf 到 .sug

**变更原因**: 
1. 避免与 RhythmicaLyrics 的 .rlf 格式混淆
2. .sug = **S**trange**U**ta**G**ame，更具辨识度

### 项目文件设计简化

**用户反馈**: SUG 文件不应该存储音频路径，让用户每次使用时拖入音频更灵活。

**变更内容**:

1. **infrastructure.md**: 
   - 移除 `audio_path` 和 `audio_path_relative` 字段
   - 添加 `audio_duration_ms` 用于验证
   - 更新关键设计说明，明确"音频不绑定"原则

2. **domain.md**:
   - 移除 Project 的 `audio_path` 和 `audio_path_relative` 属性
   - 添加 `audio_duration_ms` 属性
   - 替换"音频路径处理策略"为"音频时长验证"

3. **application.md**:
   - 更新 ProjectService 说明，明确项目加载流程
   - 添加音频选择对话框的说明
   - 添加音频时长验证逻辑

4. **ui.md**:
   - StartupInterface 添加"打开项目"功能
   - 添加音频拖拽支持说明
   - 菜单栏添加"更换音频文件"选项

**设计好处**:
- ✅ 项目文件精简，只包含核心数据
- ✅ 跨设备共享方便（无需担心路径问题）
- ✅ 音频可灵活更换（不同音质、不同版本）
- ✅ 简化实现，减少路径处理复杂性

---

**修复完成**: 所有冲突点和遗漏项已解决，文档现在一致且完整。
