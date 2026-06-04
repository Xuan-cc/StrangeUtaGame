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

            self.progress.emit("正在提取音频...", 0.0)
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

            self.progress.emit("正在提取音频...", 0.0)
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

    progress = pyqtSignal(int, int)    # (current_line, total_lines)
    llm_waiting = pyqtSignal()         # LLM 整首批量请求发出、等待返回中
    finished = pyqtSignal(object, int) # (analyzed_project_copy, deleted_count)
    error = pyqtSignal(str)

    def __init__(
        self,
        project_copy: "Project",
        auto_check,
        only_noruby: bool,
        delete_types: list,
        llm_apply_user_dict: bool = True,
    ):
        super().__init__()
        self._project = project_copy
        self._auto_check = auto_check
        self._only_noruby = only_noruby
        self._delete_types = delete_types
        # LLM 注音时是否仍应用用户词典（非 LLM 模式恒为 True，无副作用）
        self._llm_apply_user_dict = llm_apply_user_dict

    def run(self) -> None:
        try:
            # 为 worker 线程初始化 WinRT COM STA apartment，
            # 确保在非主线程调用 WinRT 静态方法时 apartment 已就绪。
            try:
                from winrt._winrt import STA, init_apartment
                init_apartment(STA)
            except Exception:
                pass

            def _progress_cb(current: int, total: int) -> None:
                self.progress.emit(current, total)

            # LLM 注音：显式预热整首批量请求，期间发「等待 LLM」信号给 UI。
            _analyzer = getattr(self._auto_check, "_analyzer", None)
            if _analyzer is not None and hasattr(_analyzer, "prewarm"):
                self.llm_waiting.emit()
                _analyzer.prewarm()

            # Step 1: 生成假名注音（延迟 romaji，delete 之后再转）
            self._auto_check.apply_to_project(
                self._project,
                only_noruby=self._only_noruby,
                apply_user_dict=(not bool(self._delete_types)) and self._llm_apply_user_dict,
                progress_callback=_progress_cb,
                skip_romanize=True,
            )
            self._auto_check.update_checkpoints_for_project(self._project)

            # Step 2: 按类型删除注音
            deleted_count = 0
            if self._delete_types:
                from strange_uta_game.backend.application.auto_check_service import (
                    delete_rubies_by_type_names,
                )
                deleted_count = delete_rubies_by_type_names(
                    self._project, self._delete_types
                )
                if self._llm_apply_user_dict:
                    self._auto_check.apply_user_dict_to_project(self._project, skip_romanize=True)

            # Step 3: 罗马音转换（走在 delete 之后，只转换剩余的假名注音）
            self._auto_check.romanize_project_rubies(self._project)

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

    llm_waiting = pyqtSignal()     # LLM 整首批量请求发出、等待返回中
    finished = pyqtSignal(object)  # analyzed project copy
    error = pyqtSignal(str)

    def __init__(
        self,
        project_copy: "Project",
        auto_check,
        specs: list,
        apply_user_dict: bool = True,
    ):
        super().__init__()
        self._project = project_copy
        self._auto_check = auto_check
        self._specs = specs
        self._apply_user_dict = apply_user_dict

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
                self.llm_waiting.emit()
                _analyzer.prewarm()

            for line_idx, restrict_indices in self._specs:
                sentence = self._project.sentences[line_idx]
                self._auto_check.apply_to_sentence(
                    sentence, only_noruby=False, restrict_indices=restrict_indices,
                    apply_user_dict=self._apply_user_dict,
                )
                self._auto_check.update_checkpoints_from_rubies(sentence)

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
