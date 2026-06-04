# Karaoke Helper（卡拉OK工作台）

当前版本：`2.1.0`

`Karaoke Helper` 是一个面向卡拉 OK / B 站投稿制作流程的 Windows 桌面工具。
界面以一条「6 步工作流」串联整个制作链路，目前已经实现 4 个功能模块，另外 2 个为占位（规划中）。

已实现模块：

- `视频下载`：从 YouTube / Bilibili 下载素材
- `波形对齐`：把字幕视频与原唱音源在时间轴上对齐
- `歌词检索`：多来源搜索、预览、复制歌词
- `Hi-Res 混流`：生成可投稿的 `on vocal / off vocal` 成品 `mkv`

规划中（界面已占位，暂未实现）：

- `歌词打轴`：逐句 / 逐字打轴
- `字幕视频生成`：渲染字幕样式并输出字幕视频

> 说明：本工具同时提供图形界面（默认）与命令行模式。命令行目前只覆盖 `Hi-Res 混流` 流程。

## 功能清单

### 1. 视频下载

- 支持来源：`YouTube` / `Bilibili`（也会尝试识别其他 `yt-dlp` 可解析的链接）
- 解析后端：优先使用 Python 包 `yt_dlp`，找不到时回退到 `yt-dlp` 命令行
- 支持一次粘贴多个链接批量解析
- 清晰度 / 格式选择：
  - 自动给出「最佳质量」推荐项
  - 列出各分辨率 / 帧率 / 编码 / 体积，纯视频流会自动配最佳音频流并标记「需要合并」
- Bilibili 登录：
  - 扫码登录（二维码 + 轮询），登录后展示昵称与头像
  - Cookie 持久化、登录状态检查、刷新与退出登录
  - 也可手动指定 Cookie 文件路径
- 下载选项：
  - 命名规则：`使用标题` / `标题 + 作者` / `自定义模板`（占位符 `{title}`、`{uploader}`/`{author}`）
  - 是否合并音视频（合并输出 `mp4`）
  - 是否下载封面、是否下载字幕
  - 并发数（1–5）、超时（5 / 10 / 15 秒）、重试次数（1–5）
- 下载进度展示（速度、ETA、已下载 / 总大小、分片进度），支持取消
- YouTube 触发「机器人校验」时自动切换兼容 `player_client` 重试
- 文件名自动做 Windows 非法字符清理
- 记住偏好：来源、保存目录、命名规则、自定义模板、是否合并 / 封面 / 字幕、并发 / 超时 / 重试、Cookie 路径

### 2. 波形对齐

- 将字幕视频音轨与原唱音源分别绘制为双波形（8kHz 采样、80 峰值/秒）
- 自动对齐：基于互相关在 ±6 秒范围内搜索最佳偏移，并给出置信度 / 得分
- 手动微调：拖动调整偏移，支持可配置步长的微调（nudge）
- 时间轴交互：点击设置 / 跳转播放头、滚轮缩放、复位视图、跳到末尾
- 播放预览：`ffmpeg` 推流给 `ffplay` 混音试听，支持音量调节
- 两种对齐目标：
  - 调整字幕视频
  - 调整原唱音源

当目标是「调整字幕视频」时：

- 负偏移：裁掉字幕视频开头
- 正偏移：前导画面补黑 / 补白 / 首帧定格
- 视频尾部裁剪（限制输出时长）
- 可选强制重编码为 `1920x1080 / 60fps`
- 编码模式：软编（`libx264/265` 等）或硬编（NVENC），硬编失败自动回退软编
- 导出对齐后视频（`.mp4`）
- 音轨可选：替换为原唱音源，或保留字幕视频自带音轨
- 可选额外导出一份对齐后的 `WAV`

当目标是「调整原唱音源」时：

- 正偏移：开头补静音
- 负偏移：裁掉开头
- 导出修正后的无损 `WAV`（PCM，按源位深选择 16/24/32/浮点）

快捷键（仅在波形对齐模块内生效，且焦点不在文本输入框时）：

- `Space`：生成波形后切换播放 / 停止
- `Alt+V`：切换拖动模式
- `Ctrl+D`：自动对齐
- `Ctrl+S`：导出当前对齐目标

导出会在中断 / 取消时清理未完成的输出文件。

### 3. 歌词检索

- 支持多来源：`QQ 音乐` / `酷狗音乐` / `网易云音乐` / `LRCLIB`
- 支持单源搜索，也支持聚合搜索（多源并发，再统一排序）
- 聚合排序综合：歌名匹配档位、各源原始顺位、来源优先级、歌名 / 歌手 / 专辑 / 歌词片段匹配度
- 搜索结果表格支持继续下滑加载更多
- 歌词预览两种格式：
  - `按行 LRC`
  - `按字 LRC`（逐字；若源只有逐行时间轴，会按字估算时间）
- 可选「省略歌曲介绍」：自动省略开头的歌名 / 词曲 / 编曲 / 制作人等介绍行
- 一键复制当前预览歌词
- 歌词清理：
  - 时间戳统一到百分之一秒
  - 删除纯时间戳空行
  - 清理酷狗 `KRC` / 网易 `YRC` 等逐字格式，转成标准 LRC
  - 去掉时间戳后的多余空格
  - 全角空格替换为半角空格
- 记住偏好：上次的歌词来源、LRC 显示格式、是否省略歌曲介绍

### 4. Hi-Res 混流

- 输入：
  - 字幕视频
  - 原唱音频（可选）
  - 伴奏音频（可选）
  - 原唱 / 伴奏至少提供一个
- 自动标准化外部音频为 `Hi-Res FLAC 32bit / 2ch`
- 采样率低于 `48kHz` 时自动提升到 `48kHz`
- 自动保留原视频中的非音频流（视频 / 字幕 / 数据 / 附件），并移除原音轨
- 输出（按需生成）：
  - `on_vocal.mkv`
  - `off_vocal.mkv`
- 命名模式：`fixed`（固定名）/ `template`（自定义模板）/ `video_name`
- 输出写入 `+faststart`

### 规划中模块

- `歌词打轴`：根据歌词与音频节奏进行逐句 / 逐字打轴（界面占位）
- `字幕视频生成`：将时间轴与样式渲染为字幕视频（界面占位）

## 项目结构

```text
krok-helper/
├─ app.py                     # 兼容入口
├─ README.md
├─ docs/                      # 设计 / 重构参考文档（默认被 .gitignore 忽略）
├─ krok_helper/
│  ├─ __init__.py
│  ├─ __main__.py             # python -m krok_helper 入口
│  ├─ cli.py                  # 命令行 / GUI 启动分发
│  ├─ config.py               # 应用常量（名称、版本、窗口尺寸等）
│  ├─ errors.py
│  ├─ types.py
│  ├─ models.py               # MediaInfo
│  ├─ ffmpeg.py               # ffmpeg/ffprobe 定位与执行
│  ├─ pipeline.py             # Hi-Res 混流流程
│  ├─ audio_alignment.py      # 波形提取 / 自动对齐 / 导出
│  ├─ lyrics.py               # 多来源歌词搜索与解析
│  ├─ settings.py             # 本地设置读写
│  ├─ windows.py              # Windows High-DPI / AppUserModelID
│  ├─ gui_qt.py               # 桌面主界面（PyQt6 + qfluentwidgets）
│  ├─ video_download/         # 视频下载模块
│  │  ├─ download_task.py     # 数据结构与常量
│  │  ├─ ytdlp_service.py     # yt-dlp 解析 / 下载封装
│  │  ├─ format_parser.py     # 清晰度 / 格式解析与排序
│  │  ├─ cookie_manager.py    # Cookie 持久化与登录状态
│  │  ├─ bilibili_auth.py     # Bilibili 扫码登录
│  │  └─ video_download_page.py  # 下载模块界面
│  └─ assets/                 # 图标与平台 logo
├─ scripts/
│  ├─ build_windows.bat
│  └─ build_macos.command
├─ 启动桌面版.bat
└─ 一键HiRes（mkv）.bat        # 旧版纯批处理脚本（默认被 .gitignore 忽略）
```

## 启动方式

双击：

```text
启动桌面版.bat
```

或在当前目录运行：

```powershell
python -m krok_helper
```

兼容旧入口：

```powershell
python app.py
```

## 桌面版说明

顶部为 6 步工作流导航，可在各模块之间切换；右上角的设置按钮会打开「当前模块」对应的设置。

### 视频下载

- 粘贴一个或多个链接后点击解析
- 选择清晰度 / 格式，配置命名与下载选项
- Bilibili 资源建议先扫码登录
- 开始下载后可查看进度并随时取消

### 波形对齐

- 先放入字幕视频与原唱音源
- 生成波形后再进行自动对齐或手动微调
- 支持播放预览与导出

### 歌词检索

- 先选择来源，再输入关键词搜索
- 结果按表格显示，可继续滚动加载更多
- 预览区可切换 `按行 LRC / 按字 LRC`，可选是否省略歌曲介绍，并直接复制

### Hi-Res 混流

- 选择字幕视频、原唱音频、伴奏音频（后两者至少一个）
- 自动完成音频标准化与封装
- 输出最终 `mkv` 文件

## 输出命名

Hi-Res 混流默认输出名称：

- `on_vocal.mkv`
- `off_vocal.mkv`

切换到自定义模板后，可分别设置原唱 / 伴奏模板，支持占位符：

- `{video_name}`：字幕视频文件名，不含扩展名

示例：

```text
原唱模板: {video_name}_orig_master
伴奏模板: {video_name}_inst_master
```

最终输出：

```text
你的视频名_orig_master.mkv
你的视频名_inst_master.mkv
```

注意：

- 模板里不需要写 `.mkv`
- 模板中不能包含路径分隔符

波形对齐的命名模板同理：

- 对齐后视频模板支持 `{video_name}`
- 对齐后音频模板支持 `{audio_name}` 和 `{video_name}`

## 命令行用法

> 命令行目前只覆盖 `Hi-Res 混流` 流程。其余模块请使用图形界面。

最基本用法：

```powershell
python -m krok_helper `
  --video "D:\path\subtitle_video.mkv" `
  --on-audio "D:\path\song.flac" `
  --off-audio "D:\path\inst.flac"
```

如果系统 `PATH` 中没有 `ffmpeg` / `ffprobe`，可以额外指定目录：

```powershell
python -m krok_helper `
  --video "D:\path\subtitle_video.mkv" `
  --on-audio "D:\path\song.flac" `
  --off-audio "D:\path\inst.flac" `
  --ffmpeg-dir "D:\tools\ffmpeg\bin"
```

如需通过命令行指定自定义命名：

```powershell
python -m krok_helper `
  --video "D:\path\subtitle_video.mkv" `
  --on-audio "D:\path\song.flac" `
  --off-audio "D:\path\inst.flac" `
  --output-name-mode template `
  --on-name-template "{video_name}_orig_master" `
  --off-name-template "{video_name}_inst_master"
```

支持的命名模式：

- `fixed`
- `template`
- `video_name`

命令行至少需要 `--video`，以及 `--on-audio` / `--off-audio` 中的一个。
如果没有显式传入命名参数或 `ffmpeg` 目录参数，命令行会优先读取本地保存的设置。
加上 `--gui` 可在带参数时强制启动图形界面。

## FFmpeg / FFprobe / FFplay 目录

程序查找外部工具的顺序：

1. 系统环境变量 `PATH`
2. 你在设置窗口或命令行中指定的 `ffmpeg` 目录（会同时在该目录与其 `bin` 子目录下查找）

推荐直接选择：

```text
...\ffmpeg\bin
```

说明：

- `Hi-Res 混流` 需要 `ffmpeg` / `ffprobe`
- `波形对齐` 的播放预览还需要 `ffplay`

## 本地设置保存位置

Windows 默认保存在：

```text
%APPDATA%\Karaoke Helper\settings.json
```

当前会保存：

- 输出命名模式、原唱 / 伴奏模板
- 波形对齐的视频 / 音频命名模板、是否保留视频自带音轨
- `ffmpeg` 目录
- 歌词来源选择、LRC 显示格式、是否省略歌曲介绍
- 视频下载的来源、保存目录、命名规则与模板、合并 / 封面 / 字幕开关、并发 / 超时 / 重试、Cookie 路径

Bilibili 登录 Cookie 默认保存在：

```text
%APPDATA%\Karaoke Helper\video_download\bilibili_cookies.txt
```

## 打包

项目使用 `PyInstaller` 打包。

### Windows

```powershell
.\scripts\build_windows.bat
```

输出目录：

```text
dist\windows\Karaoke Helper\
```

主程序：

```text
dist\windows\Karaoke Helper\Karaoke Helper.exe
```

### macOS

先给脚本执行权限：

```bash
chmod +x ./scripts/build_macos.command
```

然后运行：

```bash
./scripts/build_macos.command
```

输出目录：

```text
dist/macos/Karaoke Helper.app
```

> 注意：当前打包脚本安装并裁剪的是 `PySide6`，而源码实际依赖 `PyQt6` + `PyQt6-Fluent-Widgets`，两者并不一致，打包前请先核对（见下文「已知问题」）。

## 依赖

运行时：

- Python 3.10+
- `ffmpeg` / `ffprobe`（`波形对齐` 预览还需要 `ffplay`）
- `PyQt6`
- `PyQt6-Fluent-Widgets`（即 `qfluentwidgets`）
- `yt-dlp`（视频下载；Python 包或命令行均可）

打包时：

- `PyInstaller`

> 项目目前没有 `requirements.txt` / `pyproject.toml`，依赖未锁定版本。

## 说明

- 如果字幕视频和音频时长差异较大，程序会给出警告，但仍会继续处理
- Hi-Res 流程会先标准化输入音频，再封装进最终 `mkv`
- B 站是否最终显示 `Hi-Res`，上传时仍需要你在投稿页手动勾选对应选项
- 各在线接口（歌词来源、yt-dlp 解析、B 站登录）依赖第三方服务，可能随对方变动而失效
</content>
