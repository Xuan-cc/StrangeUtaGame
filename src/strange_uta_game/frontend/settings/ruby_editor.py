"""注音编辑界面。

全文本视图编辑歌词注音（ルビ），支持批量操作。
格式: 漢字{かんじ} — 花括号内为注音。
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QDialog,
    QCheckBox,
    QMessageBox,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from qfluentwidgets import (
    PushButton,
    PrimaryPushButton,
    InfoBar,
    InfoBarPosition,
    FluentIcon as FIF,
)

from typing import Optional, List, Tuple
from difflib import SequenceMatcher

from strange_uta_game.backend.domain import (
    Project,
    LyricLine,
    Ruby,
    TimeTag,
    CheckpointConfig,
)
from strange_uta_game.backend.application import AutoCheckService
from strange_uta_game.backend.infrastructure.parsers.text_splitter import (
    CharType,
    get_char_type,
)


def _rebuild_timetags_and_checkpoints(
    line: LyricLine, old_chars: List[str], new_chars: List[str]
) -> None:
    """文本变更后重建 timetag 和 checkpoint 索引。

    使用 SequenceMatcher 计算旧字符列表到新字符列表的映射，
    将原有 timetag 的 char_idx 平移到新位置。新插入的字符无 timetag，
    删除的字符的 timetag 被丢弃。Checkpoint 也做相应重建。
    """
    if old_chars == new_chars:
        return

    # 构建 old_idx → new_idx 映射
    sm = SequenceMatcher(None, old_chars, new_chars)
    old_to_new: dict[int, int] = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                old_to_new[i1 + k] = j1 + k
        # 'replace' / 'delete' / 'insert' 不映射（被删除/替换的字符无法保留 timetag）

    # 重建 timetags：仅保留有映射的，更新 char_idx
    new_timetags: List[TimeTag] = []
    for tag in line.timetags:
        new_idx = old_to_new.get(tag.char_idx)
        if new_idx is not None:
            new_timetags.append(
                TimeTag(
                    timestamp_ms=tag.timestamp_ms,
                    singer_id=tag.singer_id,
                    char_idx=new_idx,
                    checkpoint_idx=tag.checkpoint_idx,
                    tag_type=tag.tag_type,
                )
            )
    line.timetags = sorted(new_timetags, key=lambda t: t.timestamp_ms)

    # 重建 checkpoints：保留映射到的旧配置，新字符用默认值
    old_cp_map: dict[int, CheckpointConfig] = {
        cp.char_idx: cp for cp in line.checkpoints
    }
    new_checkpoints: List[CheckpointConfig] = []
    for j in range(len(new_chars)):
        # 查找是否有旧字符映射到这个新位置
        mapped_old_idx = None
        for old_idx, new_mapped in old_to_new.items():
            if new_mapped == j:
                mapped_old_idx = old_idx
                break

        if mapped_old_idx is not None and mapped_old_idx in old_cp_map:
            old_cp = old_cp_map[mapped_old_idx]
            new_checkpoints.append(
                CheckpointConfig(
                    char_idx=j,
                    check_count=old_cp.check_count,
                    is_line_end=(j == len(new_chars) - 1),
                    is_rest=old_cp.is_rest,
                    linked_to_next=old_cp.linked_to_next,
                    singer_id=old_cp.singer_id,
                )
            )
        else:
            # 新字符：默认 check_count=1，末尾字符 check_count=2
            is_last = j == len(new_chars) - 1
            new_checkpoints.append(
                CheckpointConfig(
                    char_idx=j,
                    check_count=2 if is_last else 1,
                    is_line_end=is_last,
                )
            )
    line.checkpoints = new_checkpoints


def _apply_parsed_metadata(
    line: LyricLine,
    linked_flags: List[bool],
    cp_counts: List[Optional[int]],
    s_names: List[str],
    name_to_id: dict,
) -> None:
    """将解析出的连词/节奏点/演唱者元数据应用到行的 checkpoints。"""
    for ci in range(min(len(line.checkpoints), len(line.chars))):
        old = line.checkpoints[ci]
        new_linked = linked_flags[ci] if ci < len(linked_flags) else False
        _raw = cp_counts[ci] if ci < len(cp_counts) else None
        new_count: int = _raw if _raw is not None else old.check_count
        new_singer = old.singer_id
        if ci < len(s_names) and s_names[ci]:
            mapped = name_to_id.get(s_names[ci])
            if mapped:
                new_singer = mapped
        if (
            new_linked != old.linked_to_next
            or new_count != old.check_count
            or new_singer != old.singer_id
        ):
            line.checkpoints[ci] = CheckpointConfig(
                char_idx=ci,
                check_count=new_count,
                is_line_end=old.is_line_end,
                is_rest=old.is_rest,
                linked_to_next=new_linked,
                singer_id=new_singer,
            )


def _is_kanji_char(char: str) -> bool:
    """判断是否为汉字"""
    if len(char) != 1:
        return False
    code = ord(char)
    return (
        (0x4E00 <= code <= 0x9FFF)
        or (0x3400 <= code <= 0x4DBF)
        or (0xF900 <= code <= 0xFAFF)
    )


def _parse_annotated_line(
    line_text: str,
) -> Tuple[
    str,
    List[str],
    List[Ruby],
    List[bool],
    List[Optional[int]],
    List[str],
]:
    """解析带注音标注的扩展文本行。

    格式:
      漢字{かんじ}          — 普通注音
      [连词]{注音1,注音2}    — 连词 + 逗号分隔注音
      字<count>             — 节奏点数量（非默认时显示）
      字@(演唱者名)          — 演唱者（非空时显示）

    Returns:
        (原文, 字符列表, 注音列表, linked_to_next标记, 节奏点数量, 演唱者名称)
    """
    raw_chars: List[str] = []
    rubies: List[Ruby] = []
    linked_flags: List[bool] = []
    check_counts: List[Optional[int]] = []
    singer_names: List[str] = []
    i = 0
    n = len(line_text)

    def _parse_ruby_after(pos: int, g_start: int, g_end: int) -> int:
        """解析 {ruby} 注解，返回新位置。"""
        if pos >= n or line_text[pos] != "{":
            return pos
        cb = line_text.find("}", pos + 1)
        if cb == -1:
            return pos
        ruby_text = line_text[pos + 1 : cb]
        g_len = g_end - g_start
        if not ruby_text:
            return cb + 1
        if g_len > 1 and "," in ruby_text:
            # 连词逐字注音
            parts = ruby_text.split(",")
            for k, rt in enumerate(parts):
                rt = rt.strip()
                if rt and g_start + k < g_end:
                    rubies.append(
                        Ruby(text=rt, start_idx=g_start + k, end_idx=g_start + k + 1)
                    )
        else:
            rubies.append(Ruby(text=ruby_text, start_idx=g_start, end_idx=g_end))
        return cb + 1

    def _parse_checkpoint_after(pos: int, g_start: int, g_end: int) -> int:
        """解析 <checkpoint> 注解，返回新位置。"""
        if pos >= n or line_text[pos] != "<":
            return pos
        ca = line_text.find(">", pos + 1)
        if ca == -1:
            return pos
        cp_text = line_text[pos + 1 : ca]
        # 验证内容仅为数字和逗号
        stripped = cp_text.replace(",", "").replace(" ", "")
        if not stripped.lstrip("-").isdigit():
            return pos  # 不是合法节奏点，原样保留
        parts = cp_text.split(",")
        for k, ct in enumerate(parts):
            idx = g_start + k
            if idx < len(check_counts):
                try:
                    check_counts[idx] = int(ct.strip())
                except ValueError:
                    pass
        return ca + 1

    def _parse_singer_after(pos: int, g_start: int, g_end: int) -> int:
        """解析 @(singer) 注解，返回新位置。"""
        if pos >= n or line_text[pos] != "@":
            return pos
        if pos + 1 >= n or line_text[pos + 1] != "(":
            return pos
        cp = line_text.find(")", pos + 2)
        if cp == -1:
            return pos
        s_text = line_text[pos + 2 : cp]
        g_len = g_end - g_start
        if "," in s_text and g_len > 1:
            parts = s_text.split(",")
            for k, sn in enumerate(parts):
                idx = g_start + k
                if idx < len(singer_names):
                    singer_names[idx] = sn.strip()
        else:
            sn = s_text.strip()
            for k in range(g_len):
                idx = g_start + k
                if idx < len(singer_names):
                    singer_names[idx] = sn
        return cp + 1

    while i < n:
        ch = line_text[i]

        if ch == "[":
            # ===== 连词组 =====
            close = line_text.find("]", i + 1)
            if close == -1:
                raw_chars.append(ch)
                linked_flags.append(False)
                check_counts.append(None)
                singer_names.append("")
                i += 1
                continue
            group_text = line_text[i + 1 : close]
            group_chars = list(group_text)
            g_start = len(raw_chars)
            for k, gc in enumerate(group_chars):
                raw_chars.append(gc)
                linked_flags.append(k < len(group_chars) - 1)
                check_counts.append(None)
                singer_names.append("")
            g_end = len(raw_chars)
            i = close + 1
            i = _parse_ruby_after(i, g_start, g_end)
            i = _parse_checkpoint_after(i, g_start, g_end)
            i = _parse_singer_after(i, g_start, g_end)

        elif ch == "{":
            # ===== 普通注音（原始格式） =====
            close = line_text.find("}", i + 1)
            if close == -1:
                raw_chars.append(ch)
                linked_flags.append(False)
                check_counts.append(None)
                singer_names.append("")
                i += 1
                continue
            ruby_text = line_text[i + 1 : close]
            if ruby_text and raw_chars:
                end_idx = len(raw_chars)
                start_idx = end_idx
                while start_idx > 0 and _is_kanji_char(raw_chars[start_idx - 1]):
                    start_idx -= 1
                if start_idx == end_idx and end_idx > 0:
                    start_idx = end_idx - 1
                if start_idx < end_idx:
                    overlap = False
                    for existing in rubies:
                        if (
                            start_idx < existing.end_idx
                            and end_idx > existing.start_idx
                        ):
                            overlap = True
                            break
                    if not overlap:
                        rubies.append(
                            Ruby(
                                text=ruby_text,
                                start_idx=start_idx,
                                end_idx=end_idx,
                            )
                        )
            i = close + 1
            # 后续注解（单字节奏点/演唱者）
            last_idx = len(raw_chars) - 1 if raw_chars else 0
            i = _parse_checkpoint_after(i, last_idx, last_idx + 1)
            i = _parse_singer_after(i, last_idx, last_idx + 1)

        elif ch == "<":
            # ===== 单字节奏点（无注音前缀） =====
            ca = line_text.find(">", i + 1)
            if ca != -1:
                cp_text = line_text[i + 1 : ca]
                stripped = cp_text.replace(",", "").replace(" ", "")
                if stripped.lstrip("-").isdigit() and raw_chars:
                    try:
                        check_counts[-1] = int(cp_text.strip())
                    except ValueError:
                        pass
                    i = ca + 1
                    last_idx = len(raw_chars) - 1
                    i = _parse_singer_after(i, last_idx, last_idx + 1)
                    continue
            # 不合法，当普通字符
            raw_chars.append(ch)
            linked_flags.append(False)
            check_counts.append(None)
            singer_names.append("")
            i += 1

        elif ch == "@":
            # ===== 单字演唱者（无注音/节奏点前缀） =====
            if i + 1 < n and line_text[i + 1] == "(":
                cp = line_text.find(")", i + 2)
                if cp != -1 and raw_chars:
                    singer_names[-1] = line_text[i + 2 : cp].strip()
                    i = cp + 1
                    continue
            raw_chars.append(ch)
            linked_flags.append(False)
            check_counts.append(None)
            singer_names.append("")
            i += 1

        else:
            # ===== 普通字符 =====
            raw_chars.append(ch)
            linked_flags.append(False)
            check_counts.append(None)
            singer_names.append("")
            i += 1

    raw_text = "".join(raw_chars)
    return raw_text, raw_chars, rubies, linked_flags, check_counts, singer_names


class DeleteRubyByTypeDialog(QDialog):
    """按字符类型选择要删除注音的对话框。"""

    _TYPE_LABELS = [
        (CharType.HIRAGANA, "ひらがな（平假名）"),
        (CharType.KATAKANA, "カタカナ（片假名）"),
        (CharType.KANJI, "漢字（汉字）"),
        (CharType.ALPHABET, "アルファベット（英文字母）"),
        (CharType.NUMBER, "数字"),
        (CharType.SYMBOL, "記号（符号）"),
        (CharType.SPACE, "空格"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("按类型删除注音")
        self.resize(320, 300)
        self.setFont(QFont("Microsoft YaHei", 10))

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        lbl = QLabel("选择要删除注音的字符类型：")
        lbl.setStyleSheet("font-weight: bold;")
        layout.addWidget(lbl)

        self._checkboxes: list[tuple[CharType, QCheckBox]] = []
        for char_type, label in self._TYPE_LABELS:
            cb = QCheckBox(label, self)
            if char_type in (CharType.HIRAGANA, CharType.KATAKANA):
                cb.setChecked(True)
            layout.addWidget(cb)
            self._checkboxes.append((char_type, cb))

        layout.addStretch()

        btn_layout = QHBoxLayout()
        btn_ok = PrimaryPushButton("删除选中类型", self)
        btn_ok.clicked.connect(self.accept)
        btn_cancel = PushButton("取消", self)
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_ok)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)

    def selected_types(self) -> list[CharType]:
        """返回用户选中的字符类型列表。"""
        return [ct for ct, cb in self._checkboxes if cb.isChecked()]


class RubyInterface(QWidget):
    """注音编辑界面

    全文本视图 + 批量操作。
    """

    rubies_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self._project: Optional[Project] = None

        self._init_ui()

    def _init_ui(self):
        """初始化界面"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # 标题
        title = QLabel("注音编辑")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)

        # 说明
        desc = QLabel(
            "全文本编辑：汉字注音用 {假名} 标注，如 赤{あか}い花{はな}\n"
            "连词用 [字字字]{注音1,注音2} 表示，节奏点 <数量>，演唱者 @(名称)\n"
            "支持增删行（换行/排版），编辑后点击「应用修改」保存"
        )
        desc.setStyleSheet("color: gray;")
        layout.addWidget(desc)

        layout.addSpacing(5)

        # 批量操作按钮
        batch_layout = QHBoxLayout()

        self.btn_auto_all = PushButton("自动分析全部注音", self)
        self.btn_auto_all.setIcon(FIF.SYNC)
        self.btn_auto_all.clicked.connect(self._on_auto_analyze_all)
        self.btn_auto_all.setEnabled(False)
        batch_layout.addWidget(self.btn_auto_all)

        self.btn_delete_by_type = PushButton("按类型删除注音", self)
        self.btn_delete_by_type.setIcon(FIF.DELETE)
        self.btn_delete_by_type.clicked.connect(self._on_delete_rubies_by_type)
        self.btn_delete_by_type.setEnabled(False)
        batch_layout.addWidget(self.btn_delete_by_type)

        self.btn_update_cp = PushButton("更新节奏点", self)
        self.btn_update_cp.setIcon(FIF.UPDATE)
        self.btn_update_cp.clicked.connect(self._on_update_checkpoints)
        self.btn_update_cp.setEnabled(False)
        batch_layout.addWidget(self.btn_update_cp)

        batch_layout.addStretch()

        layout.addLayout(batch_layout)

        # 全文本编辑器
        self.text_edit = QPlainTextEdit()
        self.text_edit.setFont(QFont("Microsoft YaHei", 12))
        self.text_edit.setPlaceholderText(
            "加载项目后，歌词将以注音标注格式显示在此处...\n"
            "示例: 赤{あか}い花{はな}が咲{さ}いた"
        )
        self.text_edit.setMinimumHeight(300)
        layout.addWidget(self.text_edit, stretch=1)

        # 应用/还原
        action_layout = QHBoxLayout()

        self.btn_apply = PrimaryPushButton("应用修改", self)
        self.btn_apply.setIcon(FIF.ACCEPT)
        self.btn_apply.clicked.connect(self._on_apply_changes)
        self.btn_apply.setEnabled(False)
        action_layout.addWidget(self.btn_apply)

        self.btn_revert = PushButton("还原", self)
        self.btn_revert.setIcon(FIF.CANCEL)
        self.btn_revert.clicked.connect(self._on_revert)
        self.btn_revert.setEnabled(False)
        action_layout.addWidget(self.btn_revert)

        action_layout.addStretch()

        self.lbl_stats = QLabel("共 0 行，0 个注音")
        self.lbl_stats.setStyleSheet("color: gray;")
        action_layout.addWidget(self.lbl_stats)

        layout.addLayout(action_layout)

    # ==================== 公共接口 ====================

    def set_project(self, project: Project, line_idx: int = 0):
        """设置项目"""
        self._project = project
        self._refresh_display()

    def set_store(self, store):
        """接入 ProjectStore 统一数据中心。"""
        self._store = store
        store.data_changed.connect(self._on_data_changed)

    def _on_data_changed(self, change_type: str):
        """响应 ProjectStore 的数据变更。"""
        if change_type == "project":
            self._project = self._store.project
            self._refresh_display()
        elif change_type in ("rubies", "lyrics"):
            self._refresh_display()

    # ==================== 内部方法 ====================

    def _refresh_display(self):
        """刷新全部显示"""
        has_project = self._project is not None and len(self._project.lines) > 0

        for btn in (
            self.btn_auto_all,
            self.btn_delete_by_type,
            self.btn_update_cp,
            self.btn_apply,
            self.btn_revert,
        ):
            btn.setEnabled(has_project)

        if has_project:
            self.text_edit.setPlainText(self._lines_to_text())
            self._update_stats()
        else:
            self.text_edit.setPlainText("")
            self.lbl_stats.setText("共 0 行，0 个注音")

    def _lines_to_text(self) -> str:
        """将项目歌词转为带注音标注的扩展文本。

        格式:
          漢字{かんじ}          — 普通注音
          [连词]{注音1,注音2}    — 连词 + 逗号分隔注音
          字<2>                — 节奏点数量（非默认时显示）
          字@(演唱者名)          — 演唱者（非空时显示）
        """
        if not self._project:
            return ""

        singer_map = {s.id: s.name for s in self._project.singers}
        result = []
        for line in self._project.lines:
            parts: list[str] = []
            i = 0
            num = len(line.chars)
            while i < num:
                cp_i = line.checkpoints[i] if i < len(line.checkpoints) else None
                # ===== 连词组 =====
                if cp_i and cp_i.linked_to_next:
                    gs = i
                    while (
                        i < num - 1
                        and i < len(line.checkpoints)
                        and line.checkpoints[i].linked_to_next
                    ):
                        i += 1
                    ge = i + 1
                    i = ge
                    gl = ge - gs
                    g_text = "".join(line.chars[gs:ge])
                    part = f"[{g_text}]"
                    # 注音
                    g_rubies = sorted(
                        [
                            r
                            for r in line.rubies
                            if r.start_idx >= gs and r.end_idx <= ge
                        ],
                        key=lambda r: r.start_idx,
                    )
                    if g_rubies:
                        if (
                            len(g_rubies) == 1
                            and g_rubies[0].start_idx == gs
                            and g_rubies[0].end_idx == ge
                        ):
                            part += f"{{{g_rubies[0].text}}}"
                        else:
                            pc = [""] * gl
                            for r in g_rubies:
                                pc[r.start_idx - gs] = r.text
                            part += "{" + ",".join(pc) + "}"
                    # 节奏点
                    counts = [
                        (
                            line.checkpoints[j].check_count
                            if j < len(line.checkpoints)
                            else 1
                        )
                        for j in range(gs, ge)
                    ]
                    defaults = [2 if j == num - 1 else 1 for j in range(gs, ge)]
                    if counts != defaults:
                        part += "<" + ",".join(str(c) for c in counts) + ">"
                    # 演唱者
                    sids = [
                        (
                            line.checkpoints[j].singer_id
                            if j < len(line.checkpoints)
                            else ""
                        )
                        for j in range(gs, ge)
                    ]
                    if any(sids):
                        names = [singer_map.get(s, s) if s else "" for s in sids]
                        unique = set(nm for nm in names if nm)
                        if len(unique) == 1 and all(names):
                            part += f"@({names[0]})"
                        else:
                            part += "@(" + ",".join(names) + ")"
                    parts.append(part)
                    continue

                # ===== 多字符注音（非连词） =====
                ruby = line.get_ruby_for_char(i)
                if ruby and ruby.start_idx == i and ruby.end_idx > i + 1:
                    span = "".join(line.chars[ruby.start_idx : ruby.end_idx])
                    parts.append(f"{span}{{{ruby.text}}}")
                    i = ruby.end_idx
                    continue

                # ===== 单字符 =====
                part = line.chars[i]
                if ruby and ruby.start_idx == i:
                    part += f"{{{ruby.text}}}"
                # 节奏点
                if cp_i:
                    default_cc = 2 if i == num - 1 else 1
                    if cp_i.check_count != default_cc:
                        part += f"<{cp_i.check_count}>"
                # 演唱者
                if cp_i and cp_i.singer_id:
                    name = singer_map.get(cp_i.singer_id, cp_i.singer_id)
                    part += f"@({name})"
                parts.append(part)
                i += 1
            result.append("".join(parts))
        return "\n".join(result)

    def _update_stats(self):
        """更新统计标签"""
        if not self._project:
            self.lbl_stats.setText("共 0 行，0 个注音")
            return

        total = sum(len(line.rubies) for line in self._project.lines)
        self.lbl_stats.setText(f"共 {len(self._project.lines)} 行，{total} 个注音")

    def _create_auto_check_service(self):
        """创建带设置的自动检查服务"""
        from strange_uta_game.frontend.settings.settings_interface import AppSettings

        app_settings = AppSettings()
        all_settings = app_settings.get_all()
        auto_check_flags = all_settings.get("auto_check", {})
        user_dict = all_settings.get("ruby_dictionary", {}).get("entries", [])
        return AutoCheckService(
            auto_check_flags=auto_check_flags, user_dictionary=user_dict
        )

    # ==================== 批量操作 ====================

    def _on_auto_analyze_all(self):
        """自动分析全部注音（同时更新节奏点）"""
        if not self._project:
            return

        try:
            auto_check = self._create_auto_check_service()
            auto_check.apply_to_project(self._project)
            self._refresh_display()
            if hasattr(self, "_store"):
                self._store.notify("rubies")

            InfoBar.success(
                title="分析完成",
                content=f"已为 {len(self._project.lines)} 行自动分析注音并更新节奏点",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2000,
                parent=self,
            )
        except Exception as e:
            InfoBar.warning(
                title="分析失败",
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )

    def _on_delete_rubies_by_type(self):
        """打开对话框，按字符类型删除注音。"""
        if not self._project:
            return

        dlg = DeleteRubyByTypeDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        selected = dlg.selected_types()
        if not selected:
            return

        removed = 0
        for line in self._project.lines:
            to_remove = []
            for ruby in line.rubies:
                chars_in_range = line.chars[
                    ruby.start_idx : min(ruby.end_idx, len(line.chars))
                ]
                if chars_in_range and all(
                    get_char_type(c) in selected for c in chars_in_range
                ):
                    to_remove.append(ruby)

            for ruby in to_remove:
                line.rubies.remove(ruby)
                removed += 1

        self._refresh_display()
        if hasattr(self, "_store"):
            self._store.notify("rubies")

        InfoBar.success(
            title="删除完成",
            content=f"已删除 {removed} 个注音（类型: {', '.join(label for ct, label in DeleteRubyByTypeDialog._TYPE_LABELS if ct in selected)}）",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _on_update_checkpoints(self):
        """根据当前注音更新节奏点（不重新分析注音）"""
        if not self._project:
            return

        try:
            auto_check = self._create_auto_check_service()
            auto_check.update_checkpoints_for_project(self._project)
            if hasattr(self, "_store"):
                self._store.notify("checkpoints")

            InfoBar.success(
                title="更新完成",
                content=f"已根据注音更新 {len(self._project.lines)} 行的节奏点",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2000,
                parent=self,
            )
        except Exception as e:
            InfoBar.warning(
                title="更新失败",
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )

    # ==================== 应用/还原 ====================

    def _on_apply_changes(self):
        """将文本编辑器内容应用回项目（支持增删行，保留打轴数据）。

        使用行级 SequenceMatcher 将旧行映射到新行，
        匹配到的旧行保留 timetag/checkpoint 并做字符级 diff，
        新插入行使用默认设置，删除行被丢弃。
        扩展：应用连词标记(linked_to_next)、节奏点数量、per-char 演唱者。
        """
        if not self._project:
            return

        text = self.text_edit.toPlainText()
        new_line_strs = text.split("\n")

        # singer name → id 映射
        name_to_id: dict[str, str] = {s.name: s.id for s in self._project.singers}

        # 解析每行的带注音文本
        parsed_new: List[
            Tuple[
                str,
                List[str],
                List[Ruby],
                List[bool],
                List[Optional[int]],
                List[str],
            ]
        ] = []
        parse_errors = []
        for i, ls in enumerate(new_line_strs):
            try:
                result = _parse_annotated_line(ls)
                raw_text = result[0]
                raw_chars = result[1]
                if not raw_text:
                    parsed_new.append((" ", [" "], [], [False], [None], [""]))
                else:
                    parsed_new.append(result)
            except Exception as e:
                parse_errors.append(f"第 {i + 1} 行: {e}")
                parsed_new.append((" ", [" "], [], [False], [None], [""]))

        if parse_errors:
            InfoBar.warning(
                title="部分行解析失败",
                content="\n".join(parse_errors[:3]),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )

        old_lines = list(self._project.lines)
        old_texts = [line.text for line in old_lines]
        new_texts = [p[0] for p in parsed_new]

        # 行级 diff：将旧行映射到新行
        line_sm = SequenceMatcher(None, old_texts, new_texts)

        default_singer = old_lines[0].singer_id if old_lines else "default"
        result_lines: List[LyricLine] = [None] * len(parsed_new)  # type: ignore

        for tag, i1, i2, j1, j2 in line_sm.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    old_line = old_lines[i1 + k]
                    p = parsed_new[j1 + k]
                    raw_text, raw_chars, rubies = p[0], p[1], p[2]
                    linked_f, cp_c, s_n = p[3], p[4], p[5]
                    old_chars = list(old_line.chars)
                    old_line.text = raw_text
                    old_line.chars = raw_chars
                    old_line.rubies.clear()
                    for r in rubies:
                        old_line.add_ruby(r)
                    _rebuild_timetags_and_checkpoints(old_line, old_chars, raw_chars)
                    _apply_parsed_metadata(old_line, linked_f, cp_c, s_n, name_to_id)
                    result_lines[j1 + k] = old_line

            elif tag == "replace":
                old_count = i2 - i1
                new_count = j2 - j1
                matched = min(old_count, new_count)
                for k in range(matched):
                    old_line = old_lines[i1 + k]
                    p = parsed_new[j1 + k]
                    raw_text, raw_chars, rubies = p[0], p[1], p[2]
                    linked_f, cp_c, s_n = p[3], p[4], p[5]
                    old_chars = list(old_line.chars)
                    old_line.text = raw_text
                    old_line.chars = raw_chars
                    old_line.rubies.clear()
                    for r in rubies:
                        old_line.add_ruby(r)
                    _rebuild_timetags_and_checkpoints(old_line, old_chars, raw_chars)
                    _apply_parsed_metadata(old_line, linked_f, cp_c, s_n, name_to_id)
                    result_lines[j1 + k] = old_line
                # 多出的新行 → 创建
                for k in range(matched, new_count):
                    p = parsed_new[j1 + k]
                    raw_text, raw_chars, rubies = p[0], p[1], p[2]
                    linked_f, cp_c, s_n = p[3], p[4], p[5]
                    nl = LyricLine(
                        singer_id=default_singer,
                        text=raw_text,
                        chars=raw_chars,
                    )
                    for r in rubies:
                        nl.add_ruby(r)
                    _apply_parsed_metadata(nl, linked_f, cp_c, s_n, name_to_id)
                    result_lines[j1 + k] = nl

            elif tag == "insert":
                for k in range(j2 - j1):
                    p = parsed_new[j1 + k]
                    raw_text, raw_chars, rubies = p[0], p[1], p[2]
                    linked_f, cp_c, s_n = p[3], p[4], p[5]
                    nl = LyricLine(
                        singer_id=default_singer,
                        text=raw_text,
                        chars=raw_chars,
                    )
                    for r in rubies:
                        nl.add_ruby(r)
                    _apply_parsed_metadata(nl, linked_f, cp_c, s_n, name_to_id)
                    result_lines[j1 + k] = nl

            # tag == "delete": 旧行被删除，不出现在 result_lines 中

        self._project.lines = result_lines

        if hasattr(self, "_store"):
            self._store.notify("lyrics")
            self._store.notify("rubies")
        self._update_stats()

        InfoBar.success(
            title="应用成功",
            content=f"已更新 {len(result_lines)} 行",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self,
        )

    def _on_revert(self):
        """还原编辑器内容为项目当前状态"""
        self._refresh_display()

        InfoBar.info(
            title="已还原",
            content="编辑器内容已还原为项目当前状态",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self,
        )
