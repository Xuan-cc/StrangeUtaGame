"""文件加载管理器。

处理项目、音频、歌词文件的加载逻辑，包括拖拽和菜单触发。
从 EditorInterface 中提取，保持主界面代码简洁。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QThread
from PyQt6.QtWidgets import QFileDialog
from strange_uta_game.frontend.fluent_widgets import message_choice
from qfluentwidgets import InfoBar, InfoBarPosition, StateToolTip

from strange_uta_game.backend.infrastructure.audio.video_converter import (
    VIDEO_EXTENSIONS,
    is_ffmpeg_available,
    is_video_file,
)
from strange_uta_game.frontend.settings.app_settings import AppSettings

from .lyric_loader import parse_lyric_content

if TYPE_CHECKING:
    from ..timing_interface import EditorInterface


class FileLoader:
    """文件加载管理器 — 处理项目/音频/歌词的加载"""

    _AUDIO_EXTENSIONS = {
        ".mp3", ".wav", ".flac", ".ogg",
        # 由 BASS 插件直接解码（无需 FFmpeg）
        ".m4a", ".m4b", ".aac", ".wma", ".opus", ".ape", ".ac3", ".wv",
        ".dsf", ".dff",
    }
    _LYRIC_EXTENSIONS = {".lrc", ".txt", ".kra"}
    _PROJECT_EXTENSIONS = {".sug"}

    def __init__(self, editor: EditorInterface):
        self._editor = editor
        # 异步加载相关（项目/视频）
        self._loading_thread: QThread | None = None
        self._loading_worker = None
        self._state_tooltip = None
        self._project_on_success = None  # 可选的加载成功额外回调 (project, file_path)
        # 异步歌词解析相关
        self._lyric_thread: QThread | None = None
        self._lyric_worker = None
        self._lyric_tooltip = None

    @property
    def _project(self):
        return self._editor._project

    @property
    def _store(self):
        return self._editor._store

    @property
    def _timing_service(self):
        return self._editor._timing_service

    # ── 拖拽 ──

    def can_accept_drop(self, file_path: str) -> bool:
        """判断文件是否可接受拖拽"""
        ext = Path(file_path).suffix.lower()
        return ext in (self._AUDIO_EXTENSIONS | VIDEO_EXTENSIONS | self._LYRIC_EXTENSIONS | self._PROJECT_EXTENSIONS)

    def handle_drop(self, file_path: str):
        """处理拖拽文件"""
        ext = Path(file_path).suffix.lower()
        if ext in self._AUDIO_EXTENSIONS:
            self._editor.load_audio(file_path)
            self._save_last_dir(file_path)
        elif ext in VIDEO_EXTENSIONS:
            self._load_video_as_audio(file_path)
        elif ext in self._LYRIC_EXTENSIONS:
            self.load_lyrics(file_path)
            self._save_last_dir(file_path)
        elif ext in self._PROJECT_EXTENSIONS:
            self._save_last_dir(file_path)
            self.load_project(file_path)

    # ── 菜单/按钮触发 ──

    def _save_last_dir(self, file_path: str):
        """保存文件所在目录到 store + config（统一入口）。"""
        store = self._store
        if store:
            store.set_working_dir(file_path)
            return
        # 退化路径：无 store 时直接写 settings
        parent_dir = str(Path(file_path).parent)
        settings = AppSettings()
        settings.set("export.last_export_dir", parent_dir)
        settings.save()

    def prompt_load_project(self):
        """弹出文件选择框加载项目"""
        if not self.check_unsaved_changes():
            return
        init_dir = self._store.working_dir if self._store else ""
        path, _ = QFileDialog.getOpenFileName(
            self._editor, self._editor.tr("打开项目"), init_dir,
            self._editor.tr("StrangeUtaGame 项目 (*.sug);;所有文件 (*.*)"),
        )
        if path:
            self._save_last_dir(path)
            self.load_project(path, check_unsaved=False)

    def prompt_load_audio(self):
        """弹出文件选择框加载音频或视频"""
        init_dir = self._store.working_dir if self._store else ""
        path, _ = QFileDialog.getOpenFileName(
            self._editor, self._editor.tr("选择音频或视频文件"), init_dir,
            self._editor.tr("音频/视频文件 (*.mp3 *.wav *.flac *.ogg *.mp4 *.mkv *.m4a *.avi *.mov *.wmv *.flv *.webm *.m4v *.mpg *.mpeg *.ts *.3gp *.vob *.mts *.m2ts *.rm *.rmvb *.asf *.f4v *.ogv *.m4b *.aac *.wma *.opus *.ape *.ac3 *.dts);;所有文件 (*.*)"),
        )
        if path:
            if is_video_file(path):
                self._load_video_as_audio(path)
            else:
                self._editor.load_audio(path)
                self._save_last_dir(path)
            self._notify_main_window_frameless_refresh()

    def prompt_load_lyrics(self):
        """弹出文件选择框加载歌词（等同「新建项目 + 加载歌词」）。

        与 prompt_load_project 一致：先做未保存检测再弹文件框，避免用户选完
        文件后才被要求保存；选定文件后以全新项目装入歌词（check_unsaved=False
        避免二次弹窗）。无项目时也可加载——会自动创建项目，与拖拽路径一致。
        """
        if not self.check_unsaved_changes():
            return
        init_dir = self._store.working_dir if self._store else ""
        path, _ = QFileDialog.getOpenFileName(
            self._editor, self._editor.tr("选择歌词文件"), init_dir,
            self._editor.tr("歌词文件 (*.lrc *.txt *.kra);;所有文件 (*.*)"),
        )
        if path:
            self.load_lyrics(path, check_unsaved=False)
            self._save_last_dir(path)
            self._notify_main_window_frameless_refresh()

    def _load_video_as_audio(self, file_path: str):
        """加载视频文件，提取音频并加载（异步）"""
        from strange_uta_game.frontend.theme import theme

        # 检查 FFmpeg 是否可用
        if not is_ffmpeg_available():
            InfoBar.error(
                title=self._editor.tr("无法读取视频文件"),
                content=self._editor.tr("未检测到 FFmpeg，请在「设置 → 关于/语言 → 工具配置」中浏览并设置 FFmpeg 路径。"),
                orient=Qt.Orientation.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=7000,
                parent=self._editor,
            )
            return

        # 创建状态提示
        self._state_tooltip = StateToolTip(self._editor.tr("正在处理视频"), self._editor.tr("正在检查 FFmpeg 环境..."), self._editor)
        green = theme.status_complete.name()
        self._state_tooltip.setStyleSheet(f"""
            StateToolTip {{
                background-color: {green};
                border: 1px solid {green};
                border-radius: 8px;
            }}
            StateToolTip QLabel {{
                color: white;
            }}
        """)
        self._state_tooltip.move(self._state_tooltip.getSuitablePos())
        self._state_tooltip.show()

        # 创建后台线程
        from strange_uta_game.frontend.workers import VideoExtractWorker

        engine = self._timing_service._audio_engine if self._timing_service else None
        self._loading_thread = QThread(self._editor)
        self._loading_worker = VideoExtractWorker(engine, file_path)
        self._loading_worker.moveToThread(self._loading_thread)

        # 连接信号
        self._loading_thread.started.connect(self._loading_worker.run)
        self._loading_worker.progress.connect(self._on_video_progress)
        self._loading_worker.finished.connect(lambda temp: self._on_video_loaded(temp, file_path))
        self._loading_worker.error.connect(self._on_video_error)
        self._loading_worker.finished.connect(self._cleanup_video_thread)
        self._loading_worker.error.connect(self._cleanup_video_thread)

        # 启动线程
        self._loading_thread.start()

    def _on_video_progress(self, stage: str, value: float) -> None:
        """更新视频处理进度"""
        if self._state_tooltip:
            self._state_tooltip.setContent(stage)

    def _on_video_loaded(self, temp_path: str, original_path: str) -> None:
        """视频提取+加载完成的回调"""
        if self._state_tooltip:
            self._state_tooltip.setState(True)
            self._state_tooltip.setContent(self._editor.tr("加载完成"))
            self._state_tooltip.close()
            self._state_tooltip = None

        # 设置音频文件路径（用于波形显示等）
        self._editor._audio_file_path = temp_path
        self._editor.timeline.set_audio_name(Path(original_path).name)

        # 更新 UI（音频已在后台线程加载到引擎）
        if self._timing_service:
            info = self._timing_service.get_audio_info()
            if info:
                self._editor.transport.set_duration(info.duration_ms)
                self._editor.timeline.set_duration(info.duration_ms)
                self._editor.preview.set_duration(info.duration_ms)
                self._editor.transport.set_position(0)
                self._editor.timeline.set_position(0)

                samples = self._timing_service.get_original_samples()
                if samples is not None:
                    self._editor.timeline.set_audio_data(
                        samples, info.sample_rate, info.channels
                    )

        # 应用设置中的默认音量和速度
        if self._timing_service:
            setting_iface = self._editor._get_setting_interface()
            if setting_iface is not None:
                settings = setting_iface.get_settings()
                default_volume = int(settings.get("audio.default_volume", 80))
                self._editor.transport.slider_volume.setValue(default_volume)
                self._editor.transport.set_default_volume(default_volume)
                speed_min = settings.get("audio.speed_slider_min", 0.5)
                speed_max = settings.get("audio.speed_slider_max", 1.0)
                self._editor.transport.set_speed_range(
                    speed_min,
                    speed_max,
                    emit_signal=False,
                )
                default_speed = settings.get("audio.default_speed", 1.0)
                speed_pct = self._editor.transport.set_speed_value(
                    int(default_speed * 100), emit_signal=False
                )
                self._editor.transport.set_default_speed(speed_pct)
                self._timing_service.set_speed(speed_pct / 100.0)
                self._timing_service.prewarm_speeds(
                    speed_min=speed_min,
                    speed_max=speed_max,
                )

        # 通知 store：先设 original_media_path（可能标 dirty），再 emit "audio"
        if self._store:
            self._store.set_original_media_path(original_path)
            self._store.set_audio_path(temp_path)

        self._save_last_dir(original_path)

        # 视频提取后的音频加载同样会(重)初始化 BASS 设备，使按键音样本失效；
        # 与 _on_audio_loaded 对称地重载，确保导入新视频后即有按键音。
        self._editor._reload_keysound_after_audio()

        InfoBar.success(
            title=self._editor.tr("音频已加载"),
            content=Path(original_path).name,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self._editor,
        )

        # 记录临时文件路径以便后续清理
        self._temp_audio_path = temp_path

        self._notify_main_window_frameless_refresh()

    def _on_video_error(self, error_msg: str) -> None:
        """视频处理失败的回调"""
        if self._state_tooltip:
            self._state_tooltip.close()
            self._state_tooltip = None

        InfoBar.error(
            title=self._editor.tr("视频处理失败"),
            content=error_msg,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP, duration=5000,
            parent=self._editor,
        )

    def _cleanup_video_thread(self) -> None:
        """清理视频处理线程"""
        if self._loading_thread:
            self._loading_thread.quit()
            self._loading_thread.wait()
            self._loading_thread = None
        if self._loading_worker:
            self._loading_worker.deleteLater()
            self._loading_worker = None

    # ── 实际加载逻辑 ──

    def check_unsaved_changes(self) -> bool:
        """检查当前项目是否有未保存内容，提示用户保存。

        Returns:
            True: 可以继续加载新项目
            False: 用户取消了操作
        """
        if not self._project:
            return True

        store = self._store
        # 检查是否有未保存的更改
        if store and store.dirty:
            choice = message_choice(
                self._editor,
                self._editor.tr("保存当前项目"),
                self._editor.tr("当前项目有未保存的更改，是否保存？"),
                [
                    self._editor.tr("保存"),
                    self._editor.tr("放弃"),
                    self._editor.tr("取消"),
                ],
                default=0,
            )
            if choice == 0:  # 保存
                # 保存被取消（如「另存为」对话框点了取消）→ 整个流程中止，
                # 不应继续加载/新建，避免丢失未保存内容。
                return bool(self._editor._on_save())
            elif choice == 1:  # 放弃
                return True
            else:  # 取消 / 关闭
                return False

        return True

    def load_project(self, file_path: str, check_unsaved: bool = True, on_success=None):
        """加载 .sug 项目文件（异步）"""
        if check_unsaved and not self.check_unsaved_changes():
            return

        from strange_uta_game.frontend.theme import theme

        # 创建状态提示
        self._state_tooltip = StateToolTip(self._editor.tr("正在加载项目"), self._editor.tr("正在解析项目数据..."), self._editor)
        green = theme.status_complete.name()
        self._state_tooltip.setStyleSheet(f"""
            StateToolTip {{
                background-color: {green};
                border: 1px solid {green};
                border-radius: 8px;
            }}
            StateToolTip QLabel {{
                color: white;
            }}
        """)
        self._state_tooltip.move(self._state_tooltip.getSuitablePos())
        self._state_tooltip.show()

        self._project_on_success = on_success

        # 创建后台线程
        from strange_uta_game.frontend.workers import ProjectLoadWorker

        self._loading_thread = QThread(self._editor)
        self._loading_worker = ProjectLoadWorker(file_path)
        self._loading_worker.moveToThread(self._loading_thread)

        # 连接信号
        self._loading_thread.started.connect(self._loading_worker.run)
        self._loading_worker.finished.connect(self._on_project_loaded)
        self._loading_worker.error.connect(self._on_project_load_error)
        self._loading_worker.finished.connect(self._cleanup_loading_thread)
        self._loading_worker.error.connect(self._cleanup_loading_thread)

        # 启动线程
        self._loading_thread.start()

    def _on_project_loaded(self, project, file_path: str) -> None:
        """项目加载完成的回调"""
        if self._state_tooltip:
            self._state_tooltip.setState(True)
            self._state_tooltip.setContent(self._editor.tr("加载完成"))
            self._state_tooltip.close()
            self._state_tooltip = None

        if self._store:
            self._store.load_project(project, save_path=file_path)
            self._store.set_working_dir(file_path)
        else:
            self._editor.set_project(project)

        self._apply_project_extras(file_path)

        if self._project_on_success:
            cb = self._project_on_success
            self._project_on_success = None
            cb(project, file_path)

        self._notify_main_window_frameless_refresh()

    def _apply_project_extras(self, file_path: str) -> None:
        """读取并应用 .sug 的附加字段（nicokara_tags、media_path）。"""
        from strange_uta_game.backend.infrastructure.persistence.sug_io import (
            SugProjectParser,
        )

        extras = SugProjectParser.load_extras(file_path)
        # extras 可能为空（旧版 sug 无 extras 字段），但仍需重置 nicokara_tags，
        # 否则上一个项目残留的 tags 会在保存时回写到当前 sug，造成跨项目污染。

        # nicokara_tags：始终覆盖到 AppSettings；sug 内缺失则 reset 为默认值。
        # 必须写到 SettingsInterface 共享的 _settings 实例（而非新建 AppSettings()），
        # 否则共享实例内存中的旧值会在后续任何 self._settings.save() 时回滚磁盘。
        nicokara_tags = extras.get("nicokara_tags")
        if nicokara_tags is None:
            nicokara_tags = AppSettings.DEFAULT_SETTINGS.get("nicokara_tags", {})
        try:
            setting_iface = self._editor._get_setting_interface()
            settings = setting_iface.get_settings() if setting_iface else AppSettings()
            settings.set("nicokara_tags", nicokara_tags)
            settings.save()
        except Exception:
            pass

        # 加载媒体文件
        media_path = extras.get("media_path", "")
        if not media_path:
            return

        if not Path(media_path).exists():
            InfoBar.warning(
                title=self._editor.tr("媒体文件未找到"),
                content=self._editor.tr("上次关联的媒体文件不存在：{name}").format(name=Path(media_path).name),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self._editor,
            )
            return

        # 自动恢复：静默预填路径，使加载完成时 set_original_media_path() 值相同
        # → 判定为 no-op → 不触发 dirty
        if self._store:
            self._store.restore_media_path(media_path)

        if is_video_file(media_path):
            self._load_video_as_audio(media_path)
        else:
            self._editor.load_audio(media_path)

    def _apply_nicokara_tags_from_data(self, data: dict) -> None:
        """从已解析的 SUG dict 同步 nicokara_tags 到 AppSettings。

        与 _apply_project_extras 中的应用逻辑保持一致：缺失字段时 reset 为默认值，
        避免上一个项目残留的 tags 污染当前 sug。剪贴板粘贴等无文件路径的入口使用。
        """
        nicokara_tags = data.get("nicokara_tags")
        if nicokara_tags is None:
            nicokara_tags = AppSettings.DEFAULT_SETTINGS.get("nicokara_tags", {})
        try:
            setting_iface = self._editor._get_setting_interface()
            settings = setting_iface.get_settings() if setting_iface else AppSettings()
            settings.set("nicokara_tags", nicokara_tags)
            settings.save()
        except Exception:
            pass

    def _reset_nicokara_tags_to_defaults(self) -> None:
        """新建项目时重置全局 nicokara_tags 为默认值，避免上一项目残留。"""
        try:
            setting_iface = self._editor._get_setting_interface()
            if setting_iface is not None:
                from strange_uta_game.frontend.settings.app_settings import AppSettings
                settings = setting_iface.get_settings()
                settings.set(
                    "nicokara_tags",
                    dict(AppSettings.DEFAULT_SETTINGS.get("nicokara_tags", {})),
                )
                settings.save()
        except Exception:
            pass

    def _on_project_load_error(self, error_msg: str) -> None:
        """项目加载失败的回调"""
        if self._state_tooltip:
            self._state_tooltip.close()
            self._state_tooltip = None

        InfoBar.error(
            title=self._editor.tr("打开失败"), content=error_msg,
            orient=Qt.Orientation.Horizontal, isClosable=True,
            position=InfoBarPosition.TOP, duration=5000,
            parent=self._editor,
        )

    def _cleanup_loading_thread(self) -> None:
        """清理加载线程"""
        if self._loading_thread:
            self._loading_thread.quit()
            self._loading_thread.wait()
            self._loading_thread = None
        if self._loading_worker:
            self._loading_worker.deleteLater()
            self._loading_worker = None

    def _notify_main_window_frameless_refresh(self) -> None:
        """通知主窗口刷新无边框状态。

        macOS 上 QFileDialog（原生 NSOpenPanel）关闭后，NSWindow 的
        styleMask 可能丢失 NSResizableWindowMask，导致窗口边缘无法拖拽调整大小。
        本项目导入完成后调用，委托 MainWindow._refresh_frameless 恢复。
        """
        try:
            win = self._editor.window()
            if hasattr(win, '_refresh_frameless'):
                win._refresh_frameless()
        except Exception:
            pass

    def _on_lyric_progress(self, stage: str) -> None:
        """更新歌词解析进度提示。"""
        if self._lyric_tooltip:
            self._lyric_tooltip.setContent(stage)

    def _on_lyrics_parsed(self, result: dict) -> None:
        """歌词解析完成的回调（主线程）。"""
        if self._lyric_tooltip:
            self._lyric_tooltip.setState(True)
            self._lyric_tooltip.setContent(self._editor.tr("解析完成"))
            self._lyric_tooltip.close()
            self._lyric_tooltip = None

        sentences = result["sentences"]
        is_nicokara = result["is_nicokara"]
        new_singers = result["new_singers"]
        parse_meta = result["parse_meta"]

        # Nicokara 元数据延迟到主线程同步，确保写入共享 settings 实例
        nicokara_raw_meta = parse_meta.pop("_nicokara_raw_meta", None)
        if nicokara_raw_meta is not None:
            from .lyric_loader import _sync_nicokara_metadata_to_settings
            _sync_nicokara_metadata_to_settings(
                nicokara_raw_meta,
                setting_iface=self._editor._get_setting_interface(),
            )

        self._apply_lyrics_result(sentences, is_nicokara, new_singers, parse_meta)

        self._notify_main_window_frameless_refresh()

    def _on_lyrics_parse_error(self, error_msg: str) -> None:
        """歌词解析失败的回调。"""
        if self._lyric_tooltip:
            self._lyric_tooltip.close()
            self._lyric_tooltip = None

        InfoBar.error(
            title=self._editor.tr("加载失败"), content=error_msg,
            orient=Qt.Orientation.Horizontal, isClosable=True,
            position=InfoBarPosition.TOP, duration=5000,
            parent=self._editor,
        )

    def _cleanup_lyric_thread(self) -> None:
        """清理歌词解析线程。"""
        if self._lyric_thread:
            self._lyric_thread.quit()
            self._lyric_thread.wait()
            self._lyric_thread = None
        if self._lyric_worker:
            self._lyric_worker.deleteLater()
            self._lyric_worker = None

    def _apply_lyrics_result(
        self,
        sentences: list,
        is_nicokara: bool,
        new_singers: list,
        parse_meta: dict,
    ) -> None:
        """将解析结果应用到项目并刷新 UI（同步、主线程执行）。"""
        # 添加新演唱者
        for singer in new_singers:
            self._project.add_singer(singer)

        # ASS Title → project.metadata.title（仅当项目无标题或为默认时覆盖）
        ass_title = parse_meta.get("title") if parse_meta else None
        if ass_title and self._project.metadata is not None:
            cur = (self._project.metadata.title or "").strip()
            if not cur or cur in ("Untitled", "未命名"):
                self._project.metadata.title = ass_title

        if new_singers and self._store:
            self._store.notify("singers")

        if not sentences:
            InfoBar.warning(
                title=self._editor.tr("解析结果为空"), content=self._editor.tr("歌词文件未解析出有效内容"),
                orient=Qt.Orientation.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=3000,
                parent=self._editor,
            )
            return

        self._project.sentences.clear()
        for s in sentences:
            self._project.sentences.append(s)

        self._editor._reapply_global_offset()

        if self._timing_service:
            self._timing_service.set_project(self._project)
        if self._store:
            self._store.notify("lyrics")

        self._editor.refresh_lyric_display()

        InfoBar.success(
            title=self._editor.tr("歌词已加载"),
            content=self._editor.tr("已加载 {n} 行歌词").format(n=len(sentences)),
            orient=Qt.Orientation.Horizontal, isClosable=True,
            position=InfoBarPosition.TOP, duration=3000,
            parent=self._editor,
        )

        if is_nicokara:
            self._prompt_nicokara_ruby_choice()
        elif parse_meta.get("format") == "utaten":
            self._update_utaten_checkpoints_as_imported()
        else:
            self._editor._auto_analyze_rubies(only_noruby=True, auto_detect_chinese=True)

    def can_load_from_clipboard(self) -> bool:
        """判断是否可以从剪贴板加载歌词。

        仅在未创建项目或项目内不存在任何歌词行时返回 True。
        """
        if not self._project:
            return True
        return len(self._project.sentences) == 0

    def load_lyrics_from_text(self, content: str):
        """从文本内容加载歌词（用于剪贴板粘贴），大文件异步解析避免 UI 阻塞。

        SUG 项目格式（JSON）解析极快，保持同步；其余格式走后台线程。
        """
        if not content or not content.strip():
            InfoBar.warning(
                title=self._editor.tr("剪贴板为空"), content=self._editor.tr("剪贴板中没有文本内容"),
                orient=Qt.Orientation.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=3000,
                parent=self._editor,
            )
            return

        # SUG 项目格式：JSON 解析毫秒级，且需要走 load_project 流程，保持同步
        from .lyric_loader import detect_lyric_format
        if detect_lyric_format(content) == "sug":
            self._load_sug_from_text(content)
            return

        # 若已有解析正在进行，忽略本次请求
        if self._lyric_thread is not None:
            return

        from strange_uta_game.frontend.theme import theme
        from strange_uta_game.frontend.workers import LyricParseWorker

        # 若没有项目先创建
        if not self._project:
            if self._store:
                from strange_uta_game.backend.application import ProjectService
                project = ProjectService().create_project()
                self._store._project = project
                self._store.notify("project")
                self._reset_nicokara_tags_to_defaults()
            else:
                InfoBar.warning(
                    title=self._editor.tr("无法加载"), content=self._editor.tr("请先创建或打开一个项目"),
                    orient=Qt.Orientation.Horizontal, isClosable=True,
                    position=InfoBarPosition.TOP, duration=3000,
                    parent=self._editor,
                )
                return

        # 在主线程预读 settings
        from strange_uta_game.frontend.settings.app_settings import AppSettings
        settings = AppSettings()
        auto_check_flags = settings.get_all().get("auto_check", {})
        user_dict = settings.load_effective_dictionary()
        annotate_katakana_with_english = settings.get(
            "ruby_dictionary.annotate_katakana_with_english", False
        )
        software_compensation_ms = settings.get("export.software_compensation_ms", 0)

        default_singer_id = self._project.get_default_singer().id
        project_singers = list(self._project.singers)

        self._start_lyric_worker(
            "", content=content, tooltip_hint=self._editor.tr("正在解析内容..."),
            default_singer_id=default_singer_id,
            project_singers=project_singers,
            software_compensation_ms=software_compensation_ms,
            auto_check_flags=auto_check_flags,
            user_dict=user_dict,
            annotate_katakana_with_english=annotate_katakana_with_english,
        )

    def _prepare_fresh_project_for_lyrics(self, check_unsaved: bool = True) -> bool:
        """为加载歌词准备一个全新空项目（等同「新建项目」）。

        与 _on_new_project 语义一致：替换当前项目前先做未保存检测，随后以
        ProjectService().create_project() 的全新项目替换，使后续歌词装入纯净
        项目（重置演唱者/音频/metadata/nicokara_tags）。这样「拖入歌词」「按
        快捷键/工具栏加载歌词」都不会再静默覆盖已有项目的未保存歌词。

        Args:
            check_unsaved: 是否在替换前做未保存检测。调用方若已在更早阶段
                （如弹文件框前）检测过，可传 False 避免二次弹窗。

        Returns:
            True  — 已就绪，可继续加载歌词；
            False — 用户取消，或无 store 无法创建项目（已提示）。
        """
        # 退化路径：无 store（测试等）无法走 load_project 全量替换流程。
        # 仅在完全无项目时报错；已有项目则沿用旧行为（后续仅替换歌词行）。
        if not self._store:
            if not self._project:
                InfoBar.warning(
                    title=self._editor.tr("无法加载"),
                    content=self._editor.tr("请先创建或打开一个项目"),
                    orient=Qt.Orientation.Horizontal, isClosable=True,
                    position=InfoBarPosition.TOP, duration=3000,
                    parent=self._editor,
                )
                return False
            return True

        # 已有项目：先做未保存检测（用户取消则中止，旧项目原样保留）。
        if check_unsaved and self._project and not self.check_unsaved_changes():
            return False

        from strange_uta_game.backend.application import ProjectService
        project = ProjectService().create_project()
        self._store.load_project(project)
        self._reset_nicokara_tags_to_defaults()
        return True

    def load_lyrics(self, path: str, check_unsaved: bool = True):
        """加载歌词文件到全新项目（等同「新建项目 + 加载歌词」，异步解析）。

        无论当前是否已有项目，都会先（在有 store 时）以一个全新空项目替换当前
        项目，再装入解析出的歌词，与「新建项目」语义一致；替换前若已有项目会
        进行未保存检测，用户取消则中止。``check_unsaved=False`` 用于调用方
        （如 prompt_load_lyrics）已在弹文件框前完成检测的场景，避免二次弹窗。
        """
        # 准备全新项目（含未保存检测）。需要 default_singer_id，必须在启动
        # worker 前完成。
        if not self._prepare_fresh_project_for_lyrics(check_unsaved=check_unsaved):
            return

        # 在主线程预读 settings，worker 内不访问任何 Qt 对象
        from strange_uta_game.frontend.settings.app_settings import AppSettings
        settings = AppSettings()
        auto_check_flags = settings.get_all().get("auto_check", {})
        user_dict = settings.load_effective_dictionary()
        annotate_katakana_with_english = settings.get(
            "ruby_dictionary.annotate_katakana_with_english", False
        )
        software_compensation_ms = settings.get("export.software_compensation_ms", 0)

        default_singer_id = self._project.get_default_singer().id
        project_singers = list(self._project.singers)

        self._start_lyric_worker(
            path, tooltip_hint=self._editor.tr("正在读取文件..."),
            default_singer_id=default_singer_id,
            project_singers=project_singers,
            software_compensation_ms=software_compensation_ms,
            auto_check_flags=auto_check_flags,
            user_dict=user_dict,
            annotate_katakana_with_english=annotate_katakana_with_english,
        )

    def _start_lyric_worker(
        self,
        file_path: str,
        *,
        content: str | None = None,
        tooltip_hint: str = "正在读取文件...",
        default_singer_id: str,
        project_singers: list,
        software_compensation_ms: int,
        auto_check_flags: dict,
        user_dict: list,
        annotate_katakana_with_english: bool,
    ) -> None:
        """创建并启动 LyricParseWorker（文件和剪贴板共用入口）。"""
        from strange_uta_game.frontend.theme import theme
        from strange_uta_game.frontend.workers import LyricParseWorker

        self._lyric_tooltip = StateToolTip(self._editor.tr("正在解析歌词"), tooltip_hint, self._editor)
        green = theme.status_complete.name()
        self._lyric_tooltip.setStyleSheet(f"""
            StateToolTip {{
                background-color: {green};
                border: 1px solid {green};
                border-radius: 8px;
            }}
            StateToolTip QLabel {{
                color: white;
            }}
        """)
        self._lyric_tooltip.move(self._lyric_tooltip.getSuitablePos())
        self._lyric_tooltip.show()

        self._lyric_thread = QThread(self._editor)
        self._lyric_worker = LyricParseWorker(
            file_path, default_singer_id, project_singers,
            software_compensation_ms, auto_check_flags,
            user_dict, annotate_katakana_with_english,
            content=content,
        )
        self._lyric_worker.moveToThread(self._lyric_thread)
        self._lyric_thread.started.connect(self._lyric_worker.run)
        self._lyric_worker.progress.connect(self._on_lyric_progress)
        self._lyric_worker.finished.connect(self._on_lyrics_parsed)
        self._lyric_worker.error.connect(self._on_lyrics_parse_error)
        self._lyric_worker.finished.connect(self._cleanup_lyric_thread)
        self._lyric_worker.error.connect(self._cleanup_lyric_thread)
        self._lyric_thread.start()

    def _do_load_lyrics(self, content: str):
        """歌词加载的核心逻辑（文件和剪贴板共用）"""
        try:
            from strange_uta_game.backend.application import ProjectService

            # 如果没有项目，自动创建
            if not self._project:
                if self._store:
                    project_service = ProjectService()
                    project = project_service.create_project()
                    self._store._project = project
                    self._store.notify("project")
                    self._reset_nicokara_tags_to_defaults()
                else:
                    InfoBar.warning(
                        title=self._editor.tr("无法加载"), content=self._editor.tr("请先创建或打开一个项目"),
                        orient=Qt.Orientation.Horizontal, isClosable=True,
                        position=InfoBarPosition.TOP, duration=3000,
                        parent=self._editor,
                    )
                    return

            default_singer = self._project.get_default_singer()

            # 读取软件导出补偿配置
            from strange_uta_game.frontend.settings.app_settings import AppSettings
            settings = AppSettings()
            software_compensation_ms = settings.get("export.software_compensation_ms", 0)

            # 解析歌词
            sentences, is_nicokara, new_singers, parse_meta = parse_lyric_content(
                content, default_singer.id, self._project.singers,
                software_compensation_ms=software_compensation_ms,
                setting_iface=self._editor._get_setting_interface(),
            )

            # 添加新演唱者
            for singer in new_singers:
                self._project.add_singer(singer)

            # ASS Title → project.metadata.title（仅当项目无标题或为默认时覆盖）
            ass_title = parse_meta.get("title") if parse_meta else None
            if ass_title and self._project.metadata is not None:
                cur = (self._project.metadata.title or "").strip()
                if not cur or cur in ("Untitled", "未命名"):
                    self._project.metadata.title = ass_title
            # 通知演唱者面板刷新（即使没有新增也要刷新一次，避免遗漏复用场景）
            if new_singers and self._store:
                self._store.notify("singers")

            if not sentences:
                InfoBar.warning(
                    title=self._editor.tr("解析结果为空"),
                    content=self._editor.tr("歌词文件未解析出有效内容"),
                    orient=Qt.Orientation.Horizontal, isClosable=True,
                    position=InfoBarPosition.TOP, duration=3000,
                    parent=self._editor,
                )
                return

            # 替换项目歌词
            self._project.sentences.clear()
            for s in sentences:
                self._project.sentences.append(s)

            # 应用全局偏移到新添加的字符
            self._editor._reapply_global_offset()

            # 重建引擎状态
            if self._timing_service:
                self._timing_service.set_project(self._project)
            if self._store:
                self._store.notify("lyrics")

            self._editor.refresh_lyric_display()

            InfoBar.success(
                title=self._editor.tr("歌词已加载"),
            content=self._editor.tr("已加载 {n} 行歌词").format(n=len(sentences)),
                orient=Qt.Orientation.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=3000,
                parent=self._editor,
            )

            # Nicokara 格式弹窗；非 nicokara 格式自动跑一轮保持原有注音的注音分析
            if is_nicokara:
                self._prompt_nicokara_ruby_choice()
            elif parse_meta.get("format") == "utaten":
                self._update_utaten_checkpoints_as_imported()
            else:
                self._editor._auto_analyze_rubies(only_noruby=True, auto_detect_chinese=True)

        except ValueError as e:
            # SUG 项目文件：直接加载为项目
            if str(e) == "__SUG_PROJECT__":
                self._load_sug_from_text(content)
            else:
                InfoBar.error(
                    title=self._editor.tr("加载失败"), content=str(e),
                    orient=Qt.Orientation.Horizontal, isClosable=True,
                    position=InfoBarPosition.TOP, duration=5000,
                    parent=self._editor,
                )
        except Exception as e:
            InfoBar.error(
                title=self._editor.tr("加载失败"), content=str(e),
                orient=Qt.Orientation.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=5000,
                parent=self._editor,
            )

    def _load_sug_from_text(self, content: str):
        """从文本内容加载 SUG 项目（用于剪贴板粘贴）。

        检查未保存更改后，解析 SUG JSON 内容并加载为新项目。
        由于没有文件路径，保存时需要用户选择路径。
        """
        # 检查未保存更改
        if not self.check_unsaved_changes():
            return

        try:
            import json

            from strange_uta_game.backend.infrastructure.persistence.sug_io import (
                SugMigrator,
                SugProjectParser,
            )

            data = json.loads(content.strip())

            # 版本迁移
            version = data.get("version", "1.0")
            if version != SugMigrator.CURRENT_VERSION:
                data = SugMigrator.migrate(data, version)

            project = SugProjectParser._dict_to_project(data)

            # 加载项目（无文件路径，保存时需用户选择）
            if self._store:
                self._store.load_project(project)
            else:
                self._editor.set_project(project)

            # 同步 SUG 中的 nicokara_tags 到 AppSettings（无字段则重置为默认）。
            # 与磁盘加载路径 _apply_project_extras 行为一致，避免跨项目污染。
            self._apply_nicokara_tags_from_data(data)

            InfoBar.success(
                title=self._editor.tr("项目已加载"),
                content=self._editor.tr("从剪贴板加载了 SUG 项目（保存时需选择路径）"),
                orient=Qt.Orientation.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=3000,
                parent=self._editor,
            )

            self._notify_main_window_frameless_refresh()
        except Exception as e:
            InfoBar.error(
                title=self._editor.tr("加载失败"),
                content=self._editor.tr("解析 SUG 项目失败: {err}").format(err=e),
                orient=Qt.Orientation.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=5000,
                parent=self._editor,
            )

    def _prompt_nicokara_ruby_choice(self):
        """Nicokara 格式注音处理弹窗（三选一）"""
        choice = message_choice(
            self._editor,
            self._editor.tr("Nicokara 格式检测"),
            self._editor.tr("检测到 Nicokara 格式歌词（已包含注音）。")
            + "\n\n"
            + self._editor.tr(
                "「保留原有注音」使用文件中的 @Ruby 注音。\n"
                "「全部重新分析」清除原有注音，使用自动分析。\n"
                "「仅分析未注音字符」保留已有注音，补充缺失的。"
            ),
            [
                self._editor.tr("保留原有注音"),
                self._editor.tr("全部重新分析"),
                self._editor.tr("仅分析未注音字符"),
            ],
            default=0,
        )
        if choice == 1:  # 全部重新分析
            self._editor._auto_analyze_rubies(only_noruby=False, auto_detect_chinese=True)
        elif choice == 2:  # 仅分析未注音字符
            self._editor._auto_analyze_rubies(only_noruby=True, auto_detect_chinese=True)
        elif choice == 0:  # 保留原有注音
            self._keep_nicokara_as_imported()

    def _keep_nicokara_as_imported(self):
        """完全按文件导入（纯文件信任路径）。

        Nicokara 的 body + @Ruby 已无歧义地编码了每个字符的节奏点数量
        (check_count)、句尾/演唱停顿释放 (is_sentence_end/sentence_end_ts)、
        行尾 (is_line_end) 与连词 (linked_to_next)，解析器
        `nicokara_result_to_sentences` 已将其全部还原为终态。

        因此这里**不**调用 AutoCheckService 的 flag 驱动节奏点重算，
        也不跑注音分析——避免用户的 auto_check 开关（check_n / 标点 /
        空格 / 行尾等）覆盖文件里的事实，凭空增删节奏点与句尾。
        解析即终态，这里仅确保 UI 与模型同步。
        """
        if not self._project:
            return
        self._editor.refresh_lyric_display()
        if hasattr(self._editor, "_store") and self._editor._store:
            self._editor._store.notify("checkpoints")

    def _update_utaten_checkpoints_as_imported(self):
        """UtaTen 导入：只根据文件自带 ruby 更新节奏点，不重新注音。"""
        if not self._project:
            return
        try:
            from strange_uta_game.backend.application import AutoCheckService
            from strange_uta_game.frontend.settings.settings_interface import AppSettings

            app_settings = AppSettings()
            auto_check = AutoCheckService(
                ruby_analyzer=object(),
                auto_check_flags=app_settings.get_all().get("auto_check", {}),
                user_dictionary=[],
            )
            auto_check.update_checkpoints_for_project(self._project)
        except Exception:
            # UtaTen ruby 本身已导入；节奏点更新失败不应阻断歌词加载。
            pass
        self._editor.refresh_lyric_display()
        if hasattr(self._editor, "_store") and self._editor._store:
            self._editor._store.notify("checkpoints")
