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

    def __init__(self, embedded: bool = False, settings_provider=None):
        # ⚠ _embedded / _settings_provider 必须在 super().__init__() 之前赋值。
        #   MSFluentWindow init 会触发 resizeEvent/changeEvent，那些 handler
        #   读 self._embedded；未赋值会 AttributeError，Qt C++ 事件分发无法
        #   捕获 Python 异常，进程直接 0xC0000409 崩溃。
        self._embedded = embedded
        self._settings_provider = settings_provider
        super().__init__()

    @staticmethod
    def for_embedding(parent=None, settings_provider=None) -> "MainWindow":
        # 构造 embedded 实例，剥离顶层窗口装饰，挂到 parent。宿主拿去 addWidget。
```

**契约**：宿主用 `for_embedding(parent, settings_provider)` 创建，得到一个可直接 `addWidget` 到任意 layout 的 widget。

## 2. 宿主调用的公开方法

| 方法 | 用途 |
|---|---|
| `trigger_save()` | 宿主把自己顶层的 Ctrl+S 转发到这里 |
| `has_unsaved_changes() -> bool` | 宿主 closeEvent 用，判断是否有脏数据 |
| `flush_unsaved()` | 宿主销毁 widget 前调用，把脏数据兜底写到崩溃恢复临时文件 |

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
- 启动期定时器：崩溃恢复弹窗、应用 updater 自检、网络词典自动更新
- `_init_window` 的全局主题 / 标题 / 尺寸 / 居中（只保留 widget 本地背景兜底）
- `closeEvent` 的 `QApplication.quit()`（embedded 下会杀掉宿主进程）

**隐藏 UI**（`frontend/settings/sub_interfaces/about.py`，`provider is not None` 时）：
- `tools_group`：ffmpeg 路径选择入口（宿主统一管理 ffmpeg）
- `_path_card`：配置文件位置卡片（embedded 下配置走宿主，无文件目录概念）

**noop / 空返回**（provider 模式）：
- `load/save_dictionary`、`load/save_singer_presets`、`load/save_network_dictionary` 的文件部分（走 `load_extra/save_extra`）
- `maybe_auto_update_network_dictionary`

## 6. 宿主侧职责（工作台，分离后**不**跟 SUG 走）

以下在宿主仓库实现，仅列出供理解契约全貌：
- `KrokHelperSettingsBridge`（实现 `SettingsProvider`，桥到工作台 settings.json 的 `lyrics_timing*` 字段）
- `_sync_lyrics_timing_host_paths`（注入 `SUG_CACHE_DIR` + `tools.ffmpeg_path`）
- 启动时一次性迁移老 SUG 配置（`migrate_strange_uta_game_settings`）

## 7. 契约稳定性约定

改动 §1–§5 的任何签名 / 行为，视为**破坏性变更**，需：
1. 先更新本文档
2. 跑 `tests/unit/test_embedded_contract.py` 确认（或同步更新测试）
3. 通知宿主维护方

standalone 行为（`embedded=False` 路径）**绝不能**因 embedded 改动而回退 —— 这是 SUG 独立分发的红线。
