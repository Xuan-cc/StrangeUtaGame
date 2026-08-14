# 嵌入契约（Embedding Contract）

本文件描述 StrangeUtaGame（以下简称 SUG）作为**子模块嵌入宿主程序**（当前为 karaoke-studio 工作台）时的接口契约。

> **为什么有这份文档**：SUG 既能 standalone 独立运行，也能被宿主嵌入。嵌入相关的代码（"embedded hook"）住在 SUG 自己的源文件里。当 SUG 未来分离成独立仓库 / submodule 时，**这份文档就是 SUG 与宿主之间的边界合同** —— 改动这些接口前，双方都应知道契约，避免破坏嵌入。
>
> 配套的回归测试见 [`tests/unit/test_embedded_contract.py`](../tests/unit/test_embedded_contract.py)。改 embedded 代码后跑它，确认契约没破。

---

## 两种运行模式

| | standalone（默认） | embedded |
|---|---|---|
| 触发 | `MainWindow()` / `python main.py` | `MainWindow(embedded=True)` / `MainWindow.for_embedding(...)` |
| 窗口 | 顶层 `MSFluentWindow` | 降级为子 widget（`Qt.WindowType.Widget`），由宿主放进自己的 layout |
| 配置 | 文件（`config.json` 等） | 宿主注入的 `SettingsProvider` |
| 缓存 | `程序目录/.cache` | `SUG_CACHE_DIR` 环境变量指向的目录 |
| 顶层行为 | 全部自管 | 跳过（见下），由宿主管 |

**核心不变量：embedded 的一切惰性化 —— 当 `embedded=False` 且 provider 为 None 且 `SUG_CACHE_DIR` 未设时，SUG 行为必须跟没有嵌入支持时逐字节一致。** 这是 SUG 能独立分发的前提。

---

## 1. 构造接口

`frontend/main_window.py`：

```python
class MainWindow(MSFluentWindow):
    _embedded: bool = False  # 类级 fallback

    def __init__(self, embedded: bool = False, settings_provider=None, ai_timing_host=None):
        # ⚠ _embedded / _settings_provider 必须在 super().__init__() 之前赋值。
        #   MSFluentWindow init 会触发 resizeEvent/changeEvent，那些 handler
        #   读 self._embedded；未赋值会 AttributeError，Qt C++ 事件分发无法
        #   捕获 Python 异常，进程直接 0xC0000409 崩溃。
        self._embedded = embedded
        self._settings_provider = settings_provider
        super().__init__()

    @staticmethod
    def for_embedding(parent=None, settings_provider=None, ai_timing_host=None) -> "MainWindow":
        # 构造 embedded 实例，剥离顶层窗口装饰，挂到 parent。宿主拿去 addWidget。
```

**契约**：宿主用 `for_embedding(parent, settings_provider, ai_timing_host=...)` 创建，得到一个可直接 `addWidget` 到任意 layout 的 widget。

## 2. 宿主调用的公开方法

| 方法 | 用途 |
|---|---|
| `trigger_save() -> bool` | 宿主把自己顶层的 Ctrl+S 转发到这里；返回是否成功发起异步保存 |
| `has_unsaved_changes() -> bool` | 宿主 closeEvent 用，判断是否有脏数据 |
| `flush_unsaved()` | 宿主销毁 widget 前调用，把脏数据兜底写到崩溃恢复临时文件 |
| `export_to_next_payload() -> dict \| None` | 获取项目、角色、Nicokara 标签和媒体路径的隔离快照，供宿主送往下一模块 |
| `on_host_visibility_changed(visible: bool)` | 宿主切入/切出整个 SUG 控件时调用；按设置暂停播放并从音频实际状态同步快捷键模式 |

embedded 实例还公开 `export_to_next_requested` 信号。SUG 的导出页仅在
embedded 模式显示“导出到下一步”按钮；点击后发出该信号，宿主收到后调用
`export_to_next_payload()`。standalone 模式不创建这个按钮，原导出流程不变。

异步保存完成后发出 `project_save_finished(str)`，失败时发出
`project_save_failed(str)`。宿主需要“保存后继续”的流程时，应等待对应信号，
不能把 `trigger_save()` 返回 `True` 当作已经写入磁盘。

宿主的外层页面切换不会进入 SUG 的 `switchTo()`。因此宿主应在隐藏 SUG 前调用
`on_host_visibility_changed(False)`，并在重新显示后调用
`on_host_visibility_changed(True)`。SUG 的 embedded `hideEvent/showEvent` 也会执行相同
同步作为兜底；接口是幂等的，显式通知与 Qt 事件重复到达不会重复暂停。

## 3. 设置后端：SettingsProvider

`frontend/settings/app_settings.py`：

```python
@runtime_checkable
class SettingsProvider(Protocol):
    def load(self) -> dict: ...                       # 主 config（config.json 等价）
    def save(self, data: dict) -> None: ...
    def load_extra(self, key: str, default): ...      # key ∈ {"dictionary","singers","network"}
    def save_extra(self, key: str, data) -> None: ...
```

- `AppSettings(provider=<obj>)`：provider 模式 —— 不碰文件系统，主 config 走 `provider.load/save`，词典/演唱者/网络走 `provider.load_extra/save_extra`。
- `AppSettings.set_default_provider(provider)`：**进程级全局默认**。SUG 代码里散落大量裸 `AppSettings()` 调用，靠这个让它们自动走宿主存储。`for_embedding` 内部会调它。优先级：显式 `provider=` 参数 > `_default_provider` > 文件模式。
- **边界 deepcopy**：进出 provider 的数据都 deepcopy，防止宿主与 SUG 共享嵌套引用导致互相污染。

## 4. 运行时路径注入

| 注入项 | 形式 | SUG 侧读取点 |
|---|---|---|
| 缓存目录 | `SUG_CACHE_DIR` **环境变量** | 三处 `_get_cache_dir()`（`frontend/project_store.py`、`backend/infrastructure/audio/tsm_cache.py`、`.../video_converter.py`）优先读它。`project_store` 的缓存路径已**惰性化**（`_cache_dir()`/`_untitled_temp_path()` 函数，非 import 期常量），避免 import 时机固化错路径。 |
| ffmpeg 路径 | 配置键 `tools.ffmpeg_path`（主 config namespace） | `video_converter.get_ffmpeg_path()` |

**注意**：`SUG_CACHE_DIR` 必须在 import SUG 任何模块**之前**设置（虽已惰性化降低风险，但宿主仍应尽早设）。

## 5. embedded 模式下 SUG 内部的行为契约

`embedded=True` 时，SUG **跳过 / 隐藏 / noop** 以下（全部由宿主接管）：

**跳过**（`if not self._embedded`）：
- 窗口几何持久化（`_win_settings`、几何定时器、resize/change 存盘）
- 全局 Ctrl+S 快捷键注册（改由宿主转发 `trigger_save`）
- 启动期定时器：崩溃恢复弹窗、应用 updater 自检
- `_init_window` 的全局主题 / 标题 / 尺寸 / 居中（只保留 widget 本地背景兜底）
- `closeEvent` 的 `QApplication.quit()`（embedded 下会杀掉宿主进程）
- **全局主题写入**：`SettingsInterface._apply_theme_setting` 在 embedded 下直接
  return —— 不能 set `theme.mode`，否则会通过 `_sync_app_palette()` 掀翻
  宿主 `QApplication.palette()` 并调 `qfluentwidgets.setTheme`，导致工作台
  出现"半亮半暗"崩坏画面。主题归宿主独占（host 通过 `theme_workbench`
  adapter 驱动同一个 SUG `theme` 单例）。

**隐藏 UI**（`frontend/settings/sub_interfaces/about.py` / `ui_settings.py`，`provider is not None` 时）：
- `tools_group`：ffmpeg 路径选择入口（宿主统一管理 ffmpeg）
- `_path_card`：配置文件位置卡片（embedded 下配置走宿主，无文件目录概念）
- `card_theme`：主题选择卡（embedded 下主题归宿主"界面"设置独占）

**改走 provider**（不访问 standalone 文件）：
- `load/save_dictionary`、`load/save_singer_presets`、`load/save_network_dictionary`（走 `load_extra/save_extra`）
- 网络词典启动自动更新仍会调度；`maybe_auto_update_network_dictionary` 通过
  provider 读取源配置并写回 cache namespace 与更新时间戳。

## 6. AI 打轴宿主能力：ai_timing_host（可选注入）

`for_embedding(..., ai_timing_host=...)` 接受一个满足
`backend/application/ai_timing/host.py::AiTimingHost` 协议的对象（鸭子类型，
宿主不导入 SUG 代码）。传入后 SUG 的「AI 打轴」使用宿主能力；传 `None`
（或省略）时回落 standalone 默认配置，两种模式共用同一弹窗与核心逻辑。

| 方法 | 语义 |
|---|---|
| `separation_status() -> dict` | 工作台分离环境状态 `{available, model, message}` |
| `effective_identity() -> dict` | 当前生效分离身份 `{model, stem, params}`（人声缓存键组成） |
| `find_session_vocal(source_path, media_sha256) -> Path \| None` | 本次会话已分离、与原音频匹配的人声（零分离复用） |
| `separate_vocal(source_path, on_progress, is_cancelled) -> Path` | 阻塞执行一次工作台人声分离，返回产物路径；取消/失败抛中文异常 |
| `ai_cache_dir() -> Path` | SUG AI 缓存根目录（宿主 `.cache` 范围，§7.2） |
| `model_root() -> Path`（可选） | 统一 AI 模型根目录（对齐模型与分离模型同源管理，嵌入模式复用工作台目录）；缺省 = SUG 自身默认目录 |
| `http_proxy() -> str`（可选） | 当前生效的下载代理 URL；空串/缺省 = 不显式代理（SUG 模型下载默认跟随宿主网络设置，standalone 则用 SUG 自身的「网络与代理」设置） |
| `runtime_python() -> str \| None`（可选，方案 B） | 宿主托管 PyMSS Runtime 的 `python.exe`。路径存在时：SUG 的「安装/修复」向该解释器**增量**安装 AI 依赖（`install_shared`：不建 venv、不重装 torch，torchaudio 按其 torch 版本/变体自动配对），embedded 的对齐 worker 与分离共享同一份 torch（含 CUDA）；返回 None / 路径不存在 = SUG 回落自建 venv 路径。能力发现用 `getattr`，不实现不影响其余协议 |

embedded 语义（对应主仓库 AI 打轴计划 §6.2）：跟随工作台当前「分离人声」
设置，不安装第二份 PyMSS Runtime、不复制模型、不在 SUG 设置中保存另一套
分离参数。宿主实现见工作台
`krok_helper/audio_processing/separation/ai_timing_host.py`。

## 7. 宿主侧职责（工作台，分离后**不**跟 SUG 走）

以下在宿主仓库实现，仅列出供理解契约全貌：
- `KrokHelperSettingsBridge`（实现 `SettingsProvider`，桥到工作台 settings.json 的 `lyrics_timing*` 字段）
- `_sync_lyrics_timing_host_paths`（注入 `SUG_CACHE_DIR` + `tools.ffmpeg_path`）
- 启动时一次性迁移老 SUG 配置（`migrate_strange_uta_game_settings`）
- `KaraokeAiTimingHost`（实现 §6 的 `AiTimingHost` 协议，注入给 SUG AI 打轴）

## 8. 契约稳定性约定

改动 §1–§6 的任何签名 / 行为，视为**破坏性变更**，需：
1. 先更新本文档
2. 跑 `tests/unit/test_embedded_contract.py` 确认（或同步更新测试）
3. 通知宿主维护方

standalone 行为（`embedded=False` 路径）**绝不能**因 embedded 改动而回退 —— 这是 SUG 独立分发的红线。
