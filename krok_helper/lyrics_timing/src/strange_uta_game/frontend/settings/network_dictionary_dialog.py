"""网络读音词典管理对话框。

UI 结构
-------
* 顶部说明：缓存文件路径（让"拉取后存储位置"显式可见）
* 中部列表：源表格 [启用 | 名称 | URL | 条目数 | 上次同步]
* 操作按钮：
  - 刷新所有启用源（HTTP 拉取，遍历表格 ``enabled=True`` 行）
  - 查看/编辑条目（打开 :class:`NetworkSourceEntriesDialog`）
  - 从文件导入到所选 / 添加源 / 删除源 / 上移 / 下移
* 底部：字典源优先级列表（"本地词典" + 各网络源）
* 总开关"启用网络词典"已上移到设置卡片（外部 SwitchSettingCard），本对话框不再承载。

依赖
----
* :func:`strange_uta_game.backend.infrastructure.network_dictionary.fetch_source_entries`
* :func:`strange_uta_game.backend.infrastructure.network_dictionary.import_file_to_entries`
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from qfluentwidgets import (
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
)

from strange_uta_game.backend.infrastructure.network_dictionary import (
    fetch_source_entries,
    import_file_to_entries,
)


_LOCAL_LABEL = "📒 本地词典"
_LOCAL_ID = "local"


class _FetchWorker(QObject):
    """后台 HTTP 拉取 worker。

    在 QThread 中执行 `fetch_source_entries` 列表化拉取，避免阻塞 UI 线程。
    `finished` 信号 emit 自工作线程；用 `Qt.AutoConnection`（默认）跨线程
    自动走 queued connection 到主线程槽。
    """

    finished = pyqtSignal(list, list, list)  # results, ok_msgs, fail_msgs

    def __init__(self, targets: List[Dict[str, Any]]):
        super().__init__()
        self._targets = targets

    def run(self) -> None:
        results: List[Dict[str, Any]] = []
        ok_msgs: List[str] = []
        fail_msgs: List[str] = []
        for src in self._targets:
            sid = src.get("id")
            name = src.get("name", sid or "?")
            url = (src.get("url") or "").strip()
            try:
                entries = fetch_source_entries(url)
                results.append({"id": sid, "entries": entries, "ts": int(time.time())})
                ok_msgs.append(f"{name}: {len(entries)} 条")
            except Exception as e:
                fail_msgs.append(f"{name}: {e}")
        self.finished.emit(results, ok_msgs, fail_msgs)


class NetworkSourceEntriesDialog(QDialog):
    """查看 / 编辑单个网络源的 entries（与本地词典编辑器同型表格）。

    复用 :class:`DictionaryEditDialog` 的列结构与操作即可，但避免引入文件导入按钮，
    单纯做条目级 CRUD。
    """

    def __init__(self, source_name: str, entries: List[Dict[str, Any]], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"网络词典条目 - {source_name}")
        self.setMinimumSize(640, 480)
        self._entries: List[Dict[str, Any]] = [dict(e) for e in (entries or [])]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel(f"网络词典条目（{source_name}）")
        title.setFont(QFont("Microsoft YaHei", 14))
        layout.addWidget(title)

        desc = QLabel(
            "拉取后的 entries 在此处直接编辑。顺序自顶向下递减优先级；新增条目默认置顶。\n"
            "注音格式：{原文||段1,段2,...}（块内 | 分 mora、, 分字符；空段=无 ruby）。"
        )
        desc.setFont(QFont("Microsoft YaHei", 10))
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self._table = QTableWidget(0, 3, self)
        self._table.setHorizontalHeaderLabels(["启用", "词", "注音(annotated)"])
        header = self._table.horizontalHeader()
        if header is not None:
            header.setStretchLastSection(True)
        self._table.setColumnWidth(0, 50)
        self._table.setColumnWidth(1, 140)
        self._table.setAlternatingRowColors(True)
        layout.addWidget(self._table)

        for e in self._entries:
            self._append_row(e.get("enabled", True), e.get("word", ""), e.get("reading", ""))

        btn_row = QHBoxLayout()
        for label, slot in [
            ("添加", self._on_add),
            ("删除选中", self._on_delete),
            ("上移", lambda: self._move(-1)),
            ("下移", lambda: self._move(+1)),
        ]:
            btn = PushButton(label, self)
            btn.clicked.connect(slot)
            btn_row.addWidget(btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        ok_row = QHBoxLayout()
        btn_ok = PrimaryPushButton("确定", self)
        btn_ok.clicked.connect(self.accept)
        btn_cancel = PushButton("取消", self)
        btn_cancel.clicked.connect(self.reject)
        ok_row.addStretch()
        ok_row.addWidget(btn_ok)
        ok_row.addWidget(btn_cancel)
        layout.addLayout(ok_row)

    def _append_row(self, enabled: bool, word: str, reading: str) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        chk = QTableWidgetItem()
        chk.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
        self._table.setItem(row, 0, chk)
        self._table.setItem(row, 1, QTableWidgetItem(word))
        self._table.setItem(row, 2, QTableWidgetItem(reading))

    def _on_add(self) -> None:
        self._table.insertRow(0)
        chk = QTableWidgetItem()
        chk.setCheckState(Qt.CheckState.Checked)
        self._table.setItem(0, 0, chk)
        self._table.setItem(0, 1, QTableWidgetItem(""))
        self._table.setItem(0, 2, QTableWidgetItem(""))
        self._table.scrollToTop()
        self._table.selectRow(0)

    def _on_delete(self) -> None:
        rows = sorted(set(idx.row() for idx in self._table.selectedIndexes()), reverse=True)
        for r in rows:
            self._table.removeRow(r)

    def _move(self, delta: int) -> None:
        rows = sorted(set(idx.row() for idx in self._table.selectedIndexes()))
        if not rows:
            return
        if delta < 0 and rows[0] == 0:
            return
        if delta > 0 and rows[-1] == self._table.rowCount() - 1:
            return
        order = rows if delta < 0 else list(reversed(rows))
        for r in order:
            for col in range(self._table.columnCount()):
                a = self._table.takeItem(r, col)
                b = self._table.takeItem(r + delta, col)
                if a and b:
                    self._table.setItem(r, col, b)
                    self._table.setItem(r + delta, col, a)
        self._table.clearSelection()
        for r in rows:
            self._table.selectRow(r + delta)

    def get_entries(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for r in range(self._table.rowCount()):
            chk = self._table.item(r, 0)
            word_item = self._table.item(r, 1)
            reading_item = self._table.item(r, 2)
            word = (word_item.text() if word_item else "").strip()
            reading = (reading_item.text() if reading_item else "").strip()
            if not word:
                continue
            out.append({
                "enabled": (chk.checkState() == Qt.CheckState.Checked) if chk else True,
                "word": word,
                "reading": reading,
            })
        return out


class NetworkDictionaryDialog(QDialog):
    """编辑网络词典源的元数据 + 条目缓存。

    总开关（``enabled``）由外部 SwitchSettingCard 控制，这里只显示状态。
    """

    def __init__(self, doc: Dict[str, Any], cache_path: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("网络读音词典")
        self.setMinimumSize(760, 600)
        self._doc: Dict[str, Any] = json.loads(json.dumps(doc))
        self._cache_path = cache_path
        # 后台拉取所需句柄
        self._fetch_thread: QThread | None = None
        self._fetch_worker: _FetchWorker | None = None
        self._fetch_btn_ref = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("网络读音词典")
        title.setFont(QFont("Microsoft YaHei", 14))
        layout.addWidget(title)

        desc_lines = [
            "lookup 时按下方「字典源优先级」自顶向下遍历，每源内自顶向下首个命中即停。",
            f"条目缓存文件：{cache_path}" if cache_path else "条目缓存：network_dictionary.json（与 config.json 同目录）",
            "总开关「启用网络词典」位于设置卡片，本对话框仅编辑源列表与条目。",
        ]
        desc = QLabel("\n".join(desc_lines))
        desc.setFont(QFont("Microsoft YaHei", 10))
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # 源表格
        self._table = QTableWidget(0, 5, self)
        self._table.setHorizontalHeaderLabels(["启用", "名称", "URL", "条目数", "上次同步"])
        header = self._table.horizontalHeader()
        if header is not None:
            header.setStretchLastSection(False)
        self._table.setColumnWidth(0, 50)
        self._table.setColumnWidth(1, 160)
        self._table.setColumnWidth(2, 300)
        self._table.setColumnWidth(3, 70)
        layout.addWidget(self._table)

        # 操作按钮
        btn_row = QHBoxLayout()
        for label, slot in [
            ("刷新所有启用源", self._on_fetch_all_enabled),
            ("查看/编辑条目", self._on_edit_entries),
            ("从文件导入到所选", self._on_import_file),
            ("添加源", self._on_add_source),
            ("删除源", self._on_remove_source),
            ("源上移", lambda: self._on_move_source(-1)),
            ("源下移", lambda: self._on_move_source(+1)),
        ]:
            btn = PushButton(label, self)
            btn.clicked.connect(slot)
            btn_row.addWidget(btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # 优先级编辑已上移到设置卡片（DictionarySubInterface 的字典源优先级卡片）；
        # 本对话框只编辑源元数据与条目缓存。新增源会自动追加到外卡片的优先级末尾。

        # OK / Cancel
        ok_row = QHBoxLayout()
        btn_ok = PrimaryPushButton("确定", self)
        btn_ok.clicked.connect(self._on_accept)
        btn_cancel = PushButton("取消", self)
        btn_cancel.clicked.connect(self.reject)
        ok_row.addStretch()
        ok_row.addWidget(btn_ok)
        ok_row.addWidget(btn_cancel)
        layout.addLayout(ok_row)

        self._reload_table()

    # ──────────────────────────────────────────────
    # 数据 ↔ 控件同步
    # ──────────────────────────────────────────────

    def _reload_table(self) -> None:
        sources: List[Dict[str, Any]] = self._doc.get("sources", []) or []
        self._table.setRowCount(0)
        for src in sources:
            row = self._table.rowCount()
            self._table.insertRow(row)
            chk = QTableWidgetItem()
            chk.setCheckState(
                Qt.CheckState.Checked if src.get("enabled", True) else Qt.CheckState.Unchecked
            )
            self._table.setItem(row, 0, chk)
            self._table.setItem(row, 1, QTableWidgetItem(src.get("name", "")))
            self._table.setItem(row, 2, QTableWidgetItem(src.get("url", "")))
            count_item = QTableWidgetItem(str(len(src.get("entries", []) or [])))
            count_item.setFlags(count_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 3, count_item)
            ts = src.get("last_fetched")
            ts_str = (
                time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "—"
            )
            ts_item = QTableWidgetItem(ts_str)
            ts_item.setFlags(ts_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 4, ts_item)

    def _collect_table_into_doc(self) -> None:
        sources: List[Dict[str, Any]] = self._doc.get("sources", []) or []
        for row, src in enumerate(sources):
            chk = self._table.item(row, 0)
            if chk is not None:
                src["enabled"] = chk.checkState() == Qt.CheckState.Checked
            name_item = self._table.item(row, 1)
            if name_item and not src.get("builtin"):
                src["name"] = name_item.text().strip() or src.get("id", "")
            url_item = self._table.item(row, 2)
            if url_item:
                src["url"] = url_item.text().strip()

    # ──────────────────────────────────────────────
    # 槽
    # ──────────────────────────────────────────────

    def _selected_source_index(self) -> int:
        rows = sorted(set(idx.row() for idx in self._table.selectedIndexes()))
        return rows[0] if rows else -1

    def _on_fetch_all_enabled(self) -> None:
        """后台批量拉取所有 ``enabled=True`` 的源；不阻塞 UI 线程。

        使用 ``QThread + _FetchWorker``：worker.run 在工作线程跑 HTTP，
        ``finished`` 信号通过 queued connection 回到主线程，进而更新表格 / 提示。
        操作期间禁用刷新按钮，避免重入。
        """
        self._collect_table_into_doc()
        sources = self._doc.get("sources") or []
        targets = [s for s in sources if s.get("enabled") and (s.get("url") or "").strip()]
        if not targets:
            self._warn("没有启用且 URL 非空的源")
            return
        if getattr(self, "_fetch_thread", None) is not None and self._fetch_thread.isRunning():
            self._warn("已有拉取任务在进行中")
            return

        # 找到"刷新所有启用源"按钮以禁用（按 sender 取，不依赖具体引用）
        sender_btn = self.sender()
        if sender_btn is not None and hasattr(sender_btn, "setEnabled"):
            sender_btn.setEnabled(False)
        self._fetch_btn_ref = sender_btn

        # 深拷贝传给 worker，避免跨线程引用 _doc 内的可变 dict
        import json as _json
        worker_targets = _json.loads(_json.dumps([
            {"id": s.get("id"), "name": s.get("name"), "url": s.get("url")} for s in targets
        ]))

        self._fetch_worker = _FetchWorker(worker_targets)
        self._fetch_thread = QThread(self)
        self._fetch_worker.moveToThread(self._fetch_thread)
        self._fetch_thread.started.connect(self._fetch_worker.run)
        self._fetch_worker.finished.connect(self._on_fetch_all_done)
        # 清理：worker 完成后退出线程并 deleteLater
        self._fetch_worker.finished.connect(self._fetch_thread.quit)
        self._fetch_worker.finished.connect(self._fetch_worker.deleteLater)
        self._fetch_thread.finished.connect(self._fetch_thread.deleteLater)
        self._fetch_thread.start()

        InfoBar.success(
            title="开始拉取",
            content=f"后台同步 {len(targets)} 个源中...",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2500,
            parent=self,
        )

    def _on_fetch_all_done(
        self,
        results: List[Dict[str, Any]],
        ok_msgs: List[str],
        fail_msgs: List[str],
    ) -> None:
        """worker.finished 槽：把后台拉取结果写回 _doc 并刷新 UI。主线程。"""
        # 按 id 回写 entries / last_fetched
        by_id = {s.get("id"): s for s in (self._doc.get("sources") or []) if s.get("id")}
        for r in results:
            src = by_id.get(r["id"])
            if src is not None:
                src["entries"] = r["entries"]
                src["last_fetched"] = r["ts"]
        self._reload_table()

        # 解禁刷新按钮
        btn = getattr(self, "_fetch_btn_ref", None)
        if btn is not None and hasattr(btn, "setEnabled"):
            btn.setEnabled(True)
        self._fetch_btn_ref = None
        self._fetch_worker = None
        self._fetch_thread = None

        summary = "; ".join(ok_msgs) if ok_msgs else "(均失败)"
        if fail_msgs:
            summary += "  |  失败: " + "; ".join(fail_msgs)
        if fail_msgs and not ok_msgs:
            self._warn(summary)
        else:
            InfoBar.success(
                title="刷新完成",
                content=summary,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=4000,
                parent=self,
            )

    def _on_edit_entries(self) -> None:
        self._collect_table_into_doc()
        idx = self._selected_source_index()
        if idx < 0:
            self._warn("请先在表格中选择一个网络源")
            return
        src = self._doc["sources"][idx]
        dialog = NetworkSourceEntriesDialog(src.get("name", src["id"]), src.get("entries", []), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            src["entries"] = dialog.get_entries()
            self._reload_table()
            self._table.selectRow(idx)

    def _on_import_file(self) -> None:
        self._collect_table_into_doc()
        idx = self._selected_source_index()
        if idx < 0:
            self._warn("请先选中一个网络源")
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 RL 兼容字典文件",
            "",
            "RL 字典 (*.txt *.hsp *.ini);;所有文件 (*)",
        )
        if not path:
            return
        try:
            entries = import_file_to_entries(path)
        except Exception as e:
            self._warn(f"导入失败：{e}")
            return
        src = self._doc["sources"][idx]
        src["entries"] = entries
        src["last_fetched"] = int(time.time())
        self._reload_table()
        self._table.selectRow(idx)

    def _on_add_source(self) -> None:
        self._collect_table_into_doc()
        name, ok = QInputDialog.getText(self, "添加源", "源名称：")
        if not ok or not name.strip():
            return
        url, ok = QInputDialog.getText(self, "添加源", "源 URL（RL kakuteiyominet.php 兼容）：")
        if not ok:
            return
        base = "".join(c if c.isalnum() else "_" for c in name.strip())[:24] or "src"
        sid = f"{base}_{int(time.time())}"
        self._doc.setdefault("sources", []).append({
            "id": sid,
            "name": name.strip(),
            "url": url.strip(),
            "builtin": False,
            "enabled": True,
            "last_fetched": None,
            "entries": [],
        })
        self._doc.setdefault("source_order", []).append(sid)
        self._reload_table()

    def _on_remove_source(self) -> None:
        self._collect_table_into_doc()
        idx = self._selected_source_index()
        if idx < 0:
            return
        src = self._doc["sources"][idx]
        if src.get("builtin"):
            self._warn("内置预设不可删除（可在表格里关闭其启用开关）")
            return
        if (
            QMessageBox.question(self, "确认", f"删除源 {src.get('name', '')}？")
            != QMessageBox.StandardButton.Yes
        ):
            return
        sid = src.get("id")
        self._doc["sources"].pop(idx)
        order = self._doc.get("source_order") or []
        if sid in order:
            order.remove(sid)
        self._reload_table()

    def _on_move_source(self, delta: int) -> None:
        self._collect_table_into_doc()
        idx = self._selected_source_index()
        if idx < 0:
            return
        sources = self._doc.get("sources") or []
        new_idx = idx + delta
        if not (0 <= new_idx < len(sources)):
            return
        sources[idx], sources[new_idx] = sources[new_idx], sources[idx]
        self._reload_table()
        self._table.selectRow(new_idx)

    def _on_accept(self) -> None:
        self._collect_table_into_doc()
        self.accept()

    def _warn(self, msg: str) -> None:
        InfoBar.warning(
            title="提示",
            content=msg,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3500,
            parent=self,
        )

    def get_doc(self) -> Dict[str, Any]:
        return self._doc
