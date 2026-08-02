from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from strange_uta_game.backend.domain import Character, Project, Ruby, RubyPart, Sentence, Singer
from strange_uta_game.frontend.editor.timing import karaoke_preview as preview_module


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_full_width_space_is_skipped_by_wipe():
    assert preview_module._wipe_ink_bounds(None, "\u3000") == (0, 0)


def _char(text: str, ruby: str, *, linked: bool = False) -> Character:
    ch = Character(
        char=text,
        check_count=1,
        timestamps=[1000],
        linked_to_next=linked,
    )
    ch.set_ruby(Ruby(parts=[RubyPart(text=ruby)]))
    ch.push_to_ruby()
    return ch


def _project_with_linked_word() -> Project:
    singer = Singer(name="default", is_default=True)
    return Project(
        singers=[singer],
        sentences=[
            Sentence(
                singer_id=singer.id,
                characters=[
                    _char("長", "なが", linked=True),
                    _char("連", "れん", linked=True),
                    _char("詞", "し"),
                ],
            )
        ],
    )


class _DummySignal:
    def connect(self, callback):
        pass


class _DummyTheme:
    changed = _DummySignal()


def test_position_and_focus_changes_do_not_invalidate_render_cache(qapp, monkeypatch):
    monkeypatch.setattr(preview_module, "theme", _DummyTheme())

    preview = preview_module.KaraokePreview()
    preview.set_project(_project_with_linked_word())

    assert preview._sentence_cache
    cached_entry = preview._sentence_cache[0]
    global_version = preview._global_version

    preview.set_current_position(0, 1)
    preview.set_focus_position(0, 2)
    preview.scroll_current_line_to_center()
    preview.request_repaint()

    assert preview._global_version == global_version
    assert preview._sentence_cache[0] is cached_entry

    preview._update_display()

    assert preview._global_version == global_version + 1


def test_line_invalidation_advances_uncached_line_version(qapp, monkeypatch):
    monkeypatch.setattr(preview_module, "theme", _DummyTheme())

    preview = preview_module.KaraokePreview()
    preview.set_project(_project_with_linked_word())

    assert preview._sentence_cache
    assert preview._line_versions.get(0, 0) == 0

    preview._invalidate_line(0)

    assert preview._line_versions[0] == 1


# ---------- _invalidate_line_and_dependents 闭包语义 ----------

def _skip_line(singer_id: str) -> Sentence:
    """全 cc=0 且无时间戳——next/prev 扫描都会跳过它。"""
    return Sentence(
        singer_id=singer_id,
        characters=[Character(char="·", check_count=0, singer_id=singer_id)],
    )


def _stamped_line(singer_id: str, ts: int) -> Sentence:
    """有时间戳——两种扫描都会停在此行并产出 ts。"""
    return Sentence(
        singer_id=singer_id,
        characters=[
            Character(char="あ", check_count=1, timestamps=[ts], singer_id=singer_id)
        ],
    )


def _barrier_line(singer_id: str) -> Sentence:
    """cc>0 但 timestamps 为空——两种扫描的「未完整打轴」屏障。"""
    return Sentence(
        singer_id=singer_id,
        characters=[Character(char="あ", check_count=1, singer_id=singer_id)],
    )


def _project_from(sentences: list[Sentence]) -> Project:
    singer = Singer(name="default", is_default=True)
    for s in sentences:
        s.singer_id = singer.id
        for ch in s.characters:
            ch.singer_id = singer.id
    return Project(singers=[singer], sentences=sentences)


def _versions_after_invalidate(preview, changed_idx: int) -> dict[int, int]:
    """快照 invalidate 前后的 line_versions 增量。"""
    before = {i: preview._line_versions.get(i, 0) for i in range(len(preview._project.sentences))}
    preview._invalidate_line_and_dependents(changed_idx)
    return {
        i: preview._line_versions.get(i, 0) - before[i]
        for i in range(len(preview._project.sentences))
    }


def test_invalidate_dependents_spans_skipped_lines_both_sides(qapp, monkeypatch):
    """A · B(skip) · C · D(skip) · E：改 C 时 A、E 都应被失效。"""
    monkeypatch.setattr(preview_module, "theme", _DummyTheme())
    singer_id = "s"
    sentences = [
        _stamped_line(singer_id, 1000),  # 0 = A
        _skip_line(singer_id),            # 1 = B (skip)
        _stamped_line(singer_id, 3000),  # 2 = C (changed)
        _skip_line(singer_id),            # 3 = D (skip)
        _stamped_line(singer_id, 5000),  # 4 = E
    ]
    preview = preview_module.KaraokePreview()
    preview.set_project(_project_from(sentences))

    delta = _versions_after_invalidate(preview, 2)

    # C 自身 + 跨 B 到 A、跨 D 到 E 全部应失效一次
    assert delta == {0: 1, 1: 1, 2: 1, 3: 1, 4: 1}


def test_invalidate_dependents_stops_at_yielding_neighbor(qapp, monkeypatch):
    """A · B(stamped) · C · D(stamped) · E：改 C 时 A、E 不应被失效。"""
    monkeypatch.setattr(preview_module, "theme", _DummyTheme())
    singer_id = "s"
    sentences = [
        _stamped_line(singer_id, 1000),  # 0 = A (远端，不应受影响)
        _stamped_line(singer_id, 2000),  # 1 = B (yields ts → next-scan 屏障)
        _stamped_line(singer_id, 3000),  # 2 = C
        _stamped_line(singer_id, 4000),  # 3 = D (yields ts → prev-scan 屏障)
        _stamped_line(singer_id, 5000),  # 4 = E (远端，不应受影响)
    ]
    preview = preview_module.KaraokePreview()
    preview.set_project(_project_from(sentences))

    delta = _versions_after_invalidate(preview, 2)

    # C 及 B、D 失效；A、E 不应被波及
    assert delta == {0: 0, 1: 1, 2: 1, 3: 1, 4: 0}


def test_invalidate_dependents_stops_at_barrier(qapp, monkeypatch):
    """A · B(barrier) · C · D(barrier) · E：屏障行本身被失效，再向外不扩散。"""
    monkeypatch.setattr(preview_module, "theme", _DummyTheme())
    singer_id = "s"
    sentences = [
        _stamped_line(singer_id, 1000),  # 0 = A (远端)
        _barrier_line(singer_id),         # 1 = B (barrier)
        _stamped_line(singer_id, 3000),  # 2 = C
        _barrier_line(singer_id),         # 3 = D (barrier)
        _stamped_line(singer_id, 5000),  # 4 = E (远端)
    ]
    preview = preview_module.KaraokePreview()
    preview.set_project(_project_from(sentences))

    delta = _versions_after_invalidate(preview, 2)

    assert delta == {0: 0, 1: 1, 2: 1, 3: 1, 4: 0}


def test_invalidate_dependents_extends_to_list_boundary(qapp, monkeypatch):
    """C · D(skip) · E(skip)：改 C 时 D、E 直到列表末尾都应被失效。"""
    monkeypatch.setattr(preview_module, "theme", _DummyTheme())
    singer_id = "s"
    sentences = [
        _stamped_line(singer_id, 1000),  # 0 = C (changed)
        _skip_line(singer_id),            # 1 = D
        _skip_line(singer_id),            # 2 = E
    ]
    preview = preview_module.KaraokePreview()
    preview.set_project(_project_from(sentences))

    delta = _versions_after_invalidate(preview, 0)

    assert delta == {0: 1, 1: 1, 2: 1}
