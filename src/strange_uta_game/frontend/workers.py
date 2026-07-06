"""后台工作线程 — 将耗时 I/O 和 CPU 操作移出 UI 线程。

所有 Worker 均为 QObject，配合 QThread 使用 moveToThread 模式。
调用方负责创建 QThread、moveToThread、连接信号、启动。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, pyqtSignal

if TYPE_CHECKING:
    from strange_uta_game.backend.infrastructure.audio import IAudioEngine
    from strange_uta_game.backend.domain import Project


# ──────────────────────────────────────────────
# 音频 / 视频
# ──────────────────────────────────────────────


class AudioLoadWorker(QObject):
    """在后台线程加载音频文件到引擎。"""

    progress = pyqtSignal(str, float)  # (stage, 0.0~1.0)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, engine: IAudioEngine, file_path: str):
        super().__init__()
        self._engine = engine
        self._file_path = file_path

    def run(self) -> None:
        try:
            self._engine.stop()
            from strange_uta_game.backend.infrastructure.audio.video_converter import (
                clear_extracted_cache,
            )
            clear_extracted_cache()
            self._engine.load(self._file_path, progress_cb=self.progress.emit)
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


class VideoExtractWorker(QObject):
    """从视频提取音频并加载到引擎（编辑器用）。"""

    progress = pyqtSignal(str, float)
    finished = pyqtSignal(str)  # temp_path，供调用方清理
    error = pyqtSignal(str)

    def __init__(self, engine: IAudioEngine, file_path: str):
        super().__init__()
        self._engine = engine
        self._file_path = file_path

    def run(self) -> None:
        temp_path = None
        try:
            from strange_uta_game.backend.infrastructure.audio.video_converter import (
                extract_audio,
            )

            self.progress.emit(self.tr("正在提取音频..."), 0.0)
            temp_path = extract_audio(self._file_path, progress_cb=self.progress.emit)

            self._engine.stop()
            self._engine.load(temp_path, progress_cb=self.progress.emit)
            self.finished.emit(temp_path or "")
        except Exception as e:
            self.error.emit(str(e))


class VideoExtractOnlyWorker(QObject):
    """仅从视频提取音频（不加载到引擎），用于首页。"""

    progress = pyqtSignal(str, float)
    finished = pyqtSignal(str)  # extracted audio path
    error = pyqtSignal(str)

    def __init__(self, file_path: str):
        super().__init__()
        self._file_path = file_path

    def run(self) -> None:
        try:
            from strange_uta_game.backend.infrastructure.audio.video_converter import (
                extract_audio,
            )

            self.progress.emit(self.tr("正在提取音频..."), 0.0)
            temp_path = extract_audio(self._file_path, progress_cb=self.progress.emit)
            self.finished.emit(temp_path)
        except Exception as e:
            self.error.emit(str(e))


# ──────────────────────────────────────────────
# 项目
# ──────────────────────────────────────────────


class ProjectLoadWorker(QObject):
    """后台加载 .sug 项目文件。"""

    finished = pyqtSignal(object, str)  # (Project, file_path)
    error = pyqtSignal(str)

    def __init__(self, file_path: str):
        super().__init__()
        self._file_path = file_path

    def run(self) -> None:
        try:
            from strange_uta_game.backend.infrastructure.persistence.sug_io import (
                SugProjectParser,
            )

            project = SugProjectParser.load(self._file_path)
            self.finished.emit(project, self._file_path)
        except Exception as e:
            self.error.emit(str(e))


class ProjectSaveWorker(QObject):
    """后台保存项目。

    接收 Project 的深拷贝，避免保存过程中 UI 线程修改 project 导致数据竞争。
    nicokara_tags 和 media_path 在创建 worker 时从主线程读取并传入，保证线程安全。
    """

    progress = pyqtSignal(str)  # stage description
    finished = pyqtSignal(str)  # saved path
    error = pyqtSignal(str)

    def __init__(self, project: Project, file_path: str, *, nicokara_tags=None, media_path=None):
        super().__init__()
        self._project = project
        self._file_path = file_path
        self._nicokara_tags = nicokara_tags
        self._media_path = media_path

    def run(self) -> None:
        try:
            from strange_uta_game.backend.infrastructure.persistence.sug_io import (
                SugProjectParser,
            )

            SugProjectParser.save(
                self._project,
                self._file_path,
                nicokara_tags=self._nicokara_tags,
                media_path=self._media_path,
                progress_cb=self.progress.emit,
            )
            self.finished.emit(self._file_path)
        except Exception as e:
            self.error.emit(str(e))


# ──────────────────────────────────────────────
# 注音分析
# ──────────────────────────────────────────────


class RubyAnalyzeWorker(QObject):
    """在后台线程对项目副本执行注音分析。

    AutoCheckService 由调用方在主线程创建（确保 WinRT STA apartment 正确初始化），
    worker 只负责在自己线程调用 init_apartment 后执行分析计算，避免阻塞 UI。
    """

    progress = pyqtSignal(str, int, int)  # (phase, current_line, total_lines)
    llm_waiting = pyqtSignal()         # LLM 整首批量请求发出、等待返回中
    llm_progress = pyqtSignal(str)     # LLM 内部阶段文本（请求/解析/重试/轮次）
    finished = pyqtSignal(object, int) # (analyzed_project_copy, deleted_count)
    error = pyqtSignal(str)

    def __init__(
        self,
        project_copy: "Project",
        auto_check,
        only_noruby: bool,
        delete_types: list,
        llm_apply_user_dict: bool = True,
        update_checkpoints: bool = True,
    ):
        super().__init__()
        self._project = project_copy
        self._auto_check = auto_check
        self._only_noruby = only_noruby
        self._delete_types = delete_types
        # LLM 注音时是否仍应用用户词典（非 LLM 模式恒为 True，无副作用）
        self._llm_apply_user_dict = llm_apply_user_dict
        # False=只刷注音、保留现有节奏点不动
        self._update_checkpoints = update_checkpoints

    def run(self) -> None:
        try:
            # 为 worker 线程初始化 WinRT COM STA apartment，
            # 确保在非主线程调用 WinRT 静态方法时 apartment 已就绪。
            try:
                from winrt._winrt import STA, init_apartment
                init_apartment(STA)
            except Exception:
                pass

            def _progress_cb(phase: str, current: int, total: int) -> None:
                self.progress.emit(phase, current, total)

            # LLM 注音：显式预热整首批量请求，期间发「等待 LLM」信号给 UI。
            _analyzer = getattr(self._auto_check, "_analyzer", None)
            if _analyzer is not None and hasattr(_analyzer, "prewarm"):
                if hasattr(_analyzer, "set_progress_callback"):
                    _analyzer.set_progress_callback(self.llm_progress.emit)
                self.llm_waiting.emit()
                _analyzer.prewarm()

            deleted_count = self._auto_check.analyze_and_apply_pipeline(
                self._project,
                only_noruby=self._only_noruby,
                apply_user_dict=self._llm_apply_user_dict,
                delete_types=self._delete_types or None,
                update_checkpoints=self._update_checkpoints,
                progress_callback=_progress_cb,
            )

            self.finished.emit(self._project, deleted_count)
        except Exception as e:
            self.error.emit(str(e))


# ──────────────────────────────────────────────
# 注音分析（局部）
# ──────────────────────────────────────────────


class RubySubsetAnalyzeWorker(QObject):
    """对指定行/字符范围列表执行注音分析，避免 UI 阻塞。

    specs: list of (line_idx: int, restrict_indices: set | None)
        restrict_indices=None 表示整行分析。
    """

    progress = pyqtSignal(str, int, int)  # (phase, current, total)
    llm_waiting = pyqtSignal()     # LLM 整首批量请求发出、等待返回中
    llm_progress = pyqtSignal(str) # LLM 内部阶段文本（请求/解析/重试/轮次）
    finished = pyqtSignal(object)  # analyzed project copy
    error = pyqtSignal(str)

    def __init__(
        self,
        project_copy: "Project",
        auto_check,
        specs: list,
        apply_user_dict: bool = True,
        update_checkpoints: bool = True,
    ):
        super().__init__()
        self._project = project_copy
        self._auto_check = auto_check
        self._specs = specs
        self._apply_user_dict = apply_user_dict
        # False=只刷注音、保留现有节奏点不动
        self._update_checkpoints = update_checkpoints

    def run(self) -> None:
        try:
            try:
                from winrt._winrt import STA, init_apartment
                init_apartment(STA)
            except Exception:
                pass

            # LLM 注音：显式预热整首批量请求，期间发「等待 LLM」信号给 UI。
            _analyzer = getattr(self._auto_check, "_analyzer", None)
            if _analyzer is not None and hasattr(_analyzer, "prewarm"):
                if hasattr(_analyzer, "set_progress_callback"):
                    _analyzer.set_progress_callback(self.llm_progress.emit)
                self.llm_waiting.emit()
                _analyzer.prewarm()

            total = len(self._specs)
            for idx, (line_idx, restrict_indices) in enumerate(self._specs):
                if total > 1:
                    self.progress.emit(self.tr("注音分析"), idx + 1, total)
                sentence = self._project.sentences[line_idx]
                self._auto_check.analyze_and_apply_sentence_pipeline(
                    sentence,
                    only_noruby=False,
                    restrict_indices=restrict_indices,
                    apply_user_dict=self._apply_user_dict,
                    update_checkpoints=self._update_checkpoints,
                )

            self.finished.emit(self._project)
        except Exception as e:
            self.error.emit(str(e))


# ──────────────────────────────────────────────
# LLM 注音连通性测试
# ──────────────────────────────────────────────


class LLMTestWorker(QObject):
    """后台测试 LLM 注音连通性，避免阻塞设置页 UI。"""

    finished = pyqtSignal(bool, str)  # (ok, message)

    def __init__(self, config, proxies=None):
        super().__init__()
        self._config = config
        self._proxies = proxies

    def run(self) -> None:
        try:
            from strange_uta_game.backend.infrastructure.parsers.llm_ruby import (
                LLMRubyClient,
            )

            client = LLMRubyClient(self._config, proxies=self._proxies)
            ok, msg = client.test_connection()
            self.finished.emit(ok, msg)
        except Exception as e:  # noqa: BLE001
            self.finished.emit(False, f"{type(e).__name__}: {e}")


# ──────────────────────────────────────────────
# 歌词
# ──────────────────────────────────────────────


class LyricReadWorker(QObject):
    """后台读取歌词文件原始内容。"""

    finished = pyqtSignal(str)  # raw text content
    error = pyqtSignal(str)

    def __init__(self, file_path: str):
        super().__init__()
        self._file_path = file_path

    def run(self) -> None:
        try:
            path = Path(self._file_path)
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = path.read_text(encoding="shift_jis")
            self.finished.emit(content)
        except Exception as e:
            self.error.emit(str(e))


class LyricParseWorker(QObject):
    """后台读取并解析歌词文件，避免大文件解析时阻塞 UI 主线程。

    调用方在主线程预先读取 settings 值并传入，worker 内部不接触任何 Qt/UI 对象。
    Nicokara 元数据同步（_sync_nicokara_metadata_to_settings）延迟到主线程回调中执行。
    """

    progress = pyqtSignal(str)    # 阶段描述文字，用于更新 StateToolTip
    finished = pyqtSignal(object) # dict: {sentences, is_nicokara, new_singers, parse_meta}
    error = pyqtSignal(str)

    def __init__(
        self,
        file_path: str,
        default_singer_id: str,
        project_singers: list,
        software_compensation_ms: int,
        auto_check_flags: dict,
        user_dict: list,
        annotate_katakana_with_english: bool,
        *,
        content: str | None = None,
    ):
        super().__init__()
        self._file_path = file_path
        self._content = content  # 非 None 时直接用此内容，跳过文件读取
        self._default_singer_id = default_singer_id
        self._project_singers = project_singers
        self._software_compensation_ms = software_compensation_ms
        self._auto_check_flags = auto_check_flags
        self._user_dict = user_dict
        self._annotate_katakana_with_english = annotate_katakana_with_english

    def run(self) -> None:
        try:
            # Utaten 对齐阶段调用 AutoCheckService 可能依赖 WinRT，需初始化 STA apartment。
            try:
                from winrt._winrt import STA, init_apartment
                init_apartment(STA)
            except Exception:
                pass

            if self._content is not None:
                content = self._content
            else:
                self.progress.emit(self.tr("正在读取文件..."))
                path = Path(self._file_path)
                try:
                    content = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    content = path.read_text(encoding="shift_jis")

            from strange_uta_game.frontend.editor.timing.lyric_loader import (
                parse_lyric_content,
            )

            sentences, is_nicokara, new_singers, parse_meta = parse_lyric_content(
                content,
                self._default_singer_id,
                self._project_singers,
                software_compensation_ms=self._software_compensation_ms,
                auto_check_flags=self._auto_check_flags,
                user_dict=self._user_dict,
                annotate_katakana_with_english=self._annotate_katakana_with_english,
                skip_settings_sync=True,
                progress_cb=self.progress.emit,
            )
            self.finished.emit({
                "sentences": sentences,
                "is_nicokara": is_nicokara,
                "new_singers": new_singers,
                "parse_meta": parse_meta,
            })
        except Exception as e:
            self.error.emit(str(e))
