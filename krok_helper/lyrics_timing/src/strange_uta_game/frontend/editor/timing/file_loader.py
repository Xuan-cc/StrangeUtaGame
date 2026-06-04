"""文件加载管理器。

处理项目、音频、歌词文件的加载逻辑，包括拖拽和菜单触发。
从 EditorInterface 中提取，保持主界面代码简洁。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QThread
from PyQt6.QtWidgets import QFileDialog, QMessageBox
from qfluentwidgets import InfoBar, InfoBarPosition, StateToolTip

from strange_uta_game.backend.infrastructure.audio.video_converter import (
    VIDEO_EXTENSIONS,
    is_ffmpeg_available,
    is_video_file,
)
from strange_uta_game.frontend.settings.app_settings import AppSettings

from .lyric_loader import parse_lyric_content, read_lyric_file

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
        # 异步加载相关
        self._loading_thread: QThread | None = None
        self._loading_worker = None
        self._state_tooltip = None
        self._project_on_success = None  # 可选的加载成功额外回调 (project, file_path)

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
            self._editor, "打开项目", init_dir,
            "StrangeUtaGame 项目 (*.sug);;所有文件 (*.*)",
        )
        if path:
            self._save_last_dir(path)
            self.load_project(path, check_unsaved=False)

    def prompt_load_audio(self):
        """弹出文件选择框加载音频或视频"""
        init_dir = self._store.working_dir if self._store else ""
        path, _ = QFileDialog.getOpenFileName(
            self._editor, "选择音频或视频文件", init_dir,
            "音频/视频文件 (*.mp3 *.wav *.flac *.ogg *.mp4 *.mkv *.m4a *.avi *.mov *.wmv *.flv *.webm *.m4v *.mpg *.mpeg *.ts *.3gp *.vob *.mts *.m2ts *.rm *.rmvb *.asf *.f4v *.ogv *.m4b *.aac *.wma *.opus *.ape *.ac3 *.dts);;所有文件 (*.*)",
        )
        if path:
            if is_video_file(path):
                self._load_video_as_audio(path)
            else:
                self._editor.load_audio(path)
                self._save_last_dir(path)

    def prompt_load_lyrics(self):
        """弹出文件选择框加载歌词"""
        if not self._project:
            InfoBar.warning(
                title="无法加载", content="请先创建或打开一个项目",
                orient=Qt.Orientation.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=3000,
                parent=self._editor,
            )
            return
        init_dir = self._store.working_dir if self._store else ""
        path, _ = QFileDialog.getOpenFileName(
            self._editor, "选择歌词文件", init_dir,
            "歌词文件 (*.lrc *.txt *.kra);;所有文件 (*.*)",
        )
        if path:
            self.load_lyrics(path)
            self._save_last_dir(path)

    def _load_video_as_audio(self, file_path: str):
        """加载视频文件，提取音频并加载（异步）"""
        from strange_uta_game.frontend.theme import theme

        # 检查 FFmpeg 是否可用
        if not is_ffmpeg_available():
            InfoBar.error(
                title="无法读取视频文件",
                content="未检测到 FFmpeg，请在「设置 → 关于 → 工具配置」中浏览并设置 FFmpeg 路径。",
                orient=Qt.Orientation.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=7000,
                parent=self._editor,
            )
            return

        # 创建状态提示
        self._state_tooltip = StateToolTip("正在处理视频", "正在检查 FFmpeg 环境...", self._editor)
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
            self._state_tooltip.setContent("加载完成")
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

        InfoBar.success(
            title="音频已加载",
            content=Path(original_path).name,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self._editor,
        )

        # 记录临时文件路径以便后续清理
        self._temp_audio_path = temp_path

    def _on_video_error(self, error_msg: str) -> None:
        """视频处理失败的回调"""
        if self._state_tooltip:
            self._state_tooltip.close()
            self._state_tooltip = None

        InfoBar.error(
            title="视频处理失败",
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
            msg = QMessageBox(self._editor)
            msg.setWindowTitle("保存当前项目")
            msg.setText("当前项目有未保存的更改，是否保存？")
            btn_save = msg.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
            btn_discard = msg.addButton("放弃", QMessageBox.ButtonRole.DestructiveRole)
            msg.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            msg.setDefaultButton(btn_save)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked is btn_save:
                self._editor._on_save()
                return True
            elif clicked is btn_discard:
                return True
            else:  # Cancel
                return False

        return True

    def load_project(self, file_path: str, check_unsaved: bool = True, on_success=None):
        """加载 .sug 项目文件（异步）"""
        if check_unsaved and not self.check_unsaved_changes():
            return

        from strange_uta_game.frontend.theme import theme

        # 创建状态提示
        self._state_tooltip = StateToolTip("正在加载项目", "正在解析项目数据...", self._editor)
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
            self._state_tooltip.setContent("加载完成")
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

    def _apply_project_extras(self, file_path: str) -> None:
        """读取并应用 .sug 的附加字段（nicokara_tags、media_path）。"""
        from strange_uta_game.backend.infrastructure.persistence.sug_io import (
            SugProjectParser,
        )

        extras = SugProjectParser.load_extras(file_path)
        if not extras:
            return

        # 应用 nicokara_tags 到 AppSettings
        nicokara_tags = extras.get("nicokara_tags")
        if nicokara_tags:
            try:
                settings = AppSettings()
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
                title="媒体文件未找到",
                content=f"上次关联的媒体文件不存在：{Path(media_path).name}",
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

    def _on_project_load_error(self, error_msg: str) -> None:
        """项目加载失败的回调"""
        if self._state_tooltip:
            self._state_tooltip.close()
            self._state_tooltip = None

        InfoBar.error(
            title="打开失败", content=error_msg,
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

    def can_load_from_clipboard(self) -> bool:
        """判断是否可以从剪贴板加载歌词。

        仅在未创建项目或项目内不存在任何歌词行时返回 True。
        """
        if not self._project:
            return True
        return len(self._project.sentences) == 0

    def load_lyrics_from_text(self, content: str):
        """从文本内容加载歌词（用于剪贴板粘贴）。

        Args:
            content: 歌词文本内容
        """
        if not content or not content.strip():
            InfoBar.warning(
                title="剪贴板为空", content="剪贴板中没有文本内容",
                orient=Qt.Orientation.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=3000,
                parent=self._editor,
            )
            return

        self._do_load_lyrics(content)

    def load_lyrics(self, path: str):
        """加载歌词文件（自动检测格式并解析）"""
        content = read_lyric_file(path)
        if content is None:
            InfoBar.error(
                title="读取失败", content="无法读取歌词文件",
                orient=Qt.Orientation.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=5000,
                parent=self._editor,
            )
            return

        self._do_load_lyrics(content)

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
                else:
                    InfoBar.warning(
                        title="无法加载", content="请先创建或打开一个项目",
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
            sentences, is_nicokara, new_singers = parse_lyric_content(
                content, default_singer.id, self._project.singers,
                software_compensation_ms=software_compensation_ms
            )

            # 添加新演唱者
            for singer in new_singers:
                self._project.add_singer(singer)
            # 通知演唱者面板刷新（即使没有新增也要刷新一次，避免遗漏复用场景）
            if new_singers and self._store:
                self._store.notify("singers")

            if not sentences:
                InfoBar.warning(
                    title="解析结果为空", content="歌词文件未解析出有效内容",
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
                title="歌词已加载", content=f"已加载 {len(sentences)} 行歌词",
                orient=Qt.Orientation.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=3000,
                parent=self._editor,
            )

            # Nicokara 格式弹窗；非 nicokara 格式自动跑一轮保持原有注音的注音分析
            if is_nicokara:
                self._prompt_nicokara_ruby_choice()
            else:
                self._editor._auto_analyze_rubies(only_noruby=True, auto_detect_chinese=True)

        except ValueError as e:
            # SUG 项目文件：直接加载为项目
            if str(e) == "__SUG_PROJECT__":
                self._load_sug_from_text(content)
            else:
                InfoBar.error(
                    title="加载失败", content=str(e),
                    orient=Qt.Orientation.Horizontal, isClosable=True,
                    position=InfoBarPosition.TOP, duration=5000,
                    parent=self._editor,
                )
        except Exception as e:
            InfoBar.error(
                title="加载失败", content=str(e),
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

            InfoBar.success(
                title="项目已加载", content="从剪贴板加载了 SUG 项目（保存时需选择路径）",
                orient=Qt.Orientation.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=3000,
                parent=self._editor,
            )
        except Exception as e:
            InfoBar.error(
                title="加载失败", content=f"解析 SUG 项目失败: {e}",
                orient=Qt.Orientation.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=5000,
                parent=self._editor,
            )

    def _prompt_nicokara_ruby_choice(self):
        """Nicokara 格式注音处理弹窗（三选一）"""
        msg = QMessageBox(self._editor)
        msg.setWindowTitle("Nicokara 格式检测")
        msg.setText("检测到 Nicokara 格式歌词（已包含注音）。")
        msg.setInformativeText(
            "「保留原有注音」使用文件中的 @Ruby 注音。\n"
            "「全部重新分析」清除原有注音，使用自动分析。\n"
            "「仅分析未注音字符」保留已有注音，补充缺失的。"
        )
        btn_keep = msg.addButton("保留原有注音", QMessageBox.ButtonRole.AcceptRole)
        btn_all = msg.addButton("全部重新分析", QMessageBox.ButtonRole.DestructiveRole)
        btn_only_noruby = msg.addButton("仅分析未注音字符", QMessageBox.ButtonRole.ActionRole)
        msg.setDefaultButton(btn_keep)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked is btn_all:
            self._editor._auto_analyze_rubies(only_noruby=False, auto_detect_chinese=True)
        elif clicked is btn_only_noruby:
            self._editor._auto_analyze_rubies(only_noruby=True, auto_detect_chinese=True)
        elif clicked is btn_keep:
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
