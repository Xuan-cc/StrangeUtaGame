"""TimingService.on_key_changed 角色化过滤 + 单次推进测试

覆盖 5 个场景:
1. 普通 cp 收到 'pressed' → 写入时间戳 + 推进
2. 普通 cp 收到 'released' → 忽略，不推进
3. 句尾末尾 cp 收到 'pressed' → 忽略，不推进
4. 句尾末尾 cp 收到 'released' → 写入 sentence_end_ts + 推进
5. 句尾字符 check_count=0 时仅有句尾 cp，'released' 写入 sentence_end_ts
"""

from typing import Callable, Optional

import pytest

from strange_uta_game.backend.application.timing_service import TimingService
from strange_uta_game.backend.domain import Project, Sentence, Character, Singer
from strange_uta_game.backend.infrastructure.audio.base import (
    AudioInfo,
    IAudioEngine,
    PlaybackState,
)


class FakeAudioEngine(IAudioEngine):
    """轻量音频引擎桩，仅满足 TimingService 接口需求"""

    def __init__(self):
        self._position_ms = 0
        self._playing = False
        self._speed = 1.0
        self._volume = 1.0

    def load(self, file_path: str) -> None:
        pass

    def play(self) -> None:
        self._playing = True

    def pause(self) -> None:
        self._playing = False

    def stop(self) -> None:
        self._playing = False
        self._position_ms = 0

    def get_position_ms(self) -> int:
        return self._position_ms

    def set_position_ms(self, position_ms: int) -> None:
        self._position_ms = position_ms

    def get_duration_ms(self) -> int:
        return 60000

    def get_playback_state(self) -> PlaybackState:
        return PlaybackState.PLAYING if self._playing else PlaybackState.STOPPED

    def is_playing(self) -> bool:
        return self._playing

    def set_speed(self, speed: float) -> None:
        self._speed = speed

    def get_speed(self) -> float:
        return self._speed

    def set_volume(self, volume: float) -> None:
        self._volume = volume

    def get_volume(self) -> float:
        return self._volume

    def set_position_callback(self, callback: Callable[[int], None]) -> None:
        pass

    def clear_position_callback(self) -> None:
        pass

    def get_audio_info(self) -> Optional[AudioInfo]:
        return None

    def release(self) -> None:
        pass


def _make_project_normal_then_sentence_end():
    """构造: '愛' (check_count=2, 非句尾) + '空' (check_count=2, is_sentence_end=True)
    全局 cp 序列: (0,0,0), (0,0,1), (0,1,0), (0,1,1), (0,1,2)←句尾末尾
    """
    project = Project()
    singer = Singer(name="default")
    project.add_singer(singer)

    sentence = Sentence(singer_id=singer.id)
    c1 = Character(char="愛", check_count=2, singer_id=singer.id)
    c2 = Character(
        char="空", check_count=2, singer_id=singer.id, is_sentence_end=True
    )
    sentence.characters.append(c1)
    sentence.characters.append(c2)
    project.add_sentence(sentence)
    return project, sentence, c1, c2


def _make_project_sentence_end_zero_check():
    """构造: '光' (check_count=2, 非句尾) + '。' (check_count=0, is_sentence_end=True)
    全局 cp 序列: (0,0,0), (0,0,1), (0,1,0)←句尾末尾 (check_count=0 时 cp_idx==0==check_count)
    """
    project = Project()
    singer = Singer(name="default")
    project.add_singer(singer)

    sentence = Sentence(singer_id=singer.id)
    c1 = Character(char="光", check_count=2, singer_id=singer.id)
    c_end = Character(
        char="。", check_count=0, singer_id=singer.id, is_sentence_end=True
    )
    sentence.characters.append(c1)
    sentence.characters.append(c_end)
    project.add_sentence(sentence)
    return project, sentence, c1, c_end


@pytest.fixture
def service():
    audio = FakeAudioEngine()
    svc = TimingService(audio_engine=audio, command_manager=None)
    return svc


def test_normal_cp_pressed_writes_and_advances(service):
    """场景1: 普通 cp pressed → 写入时间戳 + 推进到下一个 cp"""
    project, sentence, c1, c2 = _make_project_normal_then_sentence_end()
    service.set_project(project)

    # 起点应在第一个 cp (line=0, char=0, cp_idx=0)
    pos = service.get_current_position()
    assert (pos.line_idx, pos.char_idx, pos.checkpoint_idx) == (0, 0, 0)

    service.on_key_changed(1000, "pressed")

    # c1 的 cp_idx=0 应被写入
    assert c1.timestamps == [1000]
    # 应推进到 (0, 0, 1)
    pos = service.get_current_position()
    assert (pos.line_idx, pos.char_idx, pos.checkpoint_idx) == (0, 0, 1)


def test_normal_cp_released_ignored(service):
    """场景2: 普通 cp 收到 released → 忽略，不写入不推进"""
    project, sentence, c1, c2 = _make_project_normal_then_sentence_end()
    service.set_project(project)

    pos_before = service.get_current_position()
    service.on_key_changed(1500, "released")

    # 不写入，不推进
    assert c1.timestamps == []
    pos_after = service.get_current_position()
    assert (pos_after.line_idx, pos_after.char_idx, pos_after.checkpoint_idx) == (
        pos_before.line_idx,
        pos_before.char_idx,
        pos_before.checkpoint_idx,
    )


def test_sentence_end_tail_cp_pressed_ignored(service):
    """场景3: 句尾末尾 cp 收到 pressed → 忽略"""
    project, sentence, c1, c2 = _make_project_normal_then_sentence_end()
    service.set_project(project)

    # 推进到句尾末尾 cp = (0,1,2)
    assert service.move_to_checkpoint(0, 1, 2)
    pos = service.get_current_position()
    assert (pos.line_idx, pos.char_idx, pos.checkpoint_idx) == (0, 1, 2)

    service.on_key_changed(2000, "pressed")

    # 句尾 ts 不应被写入
    assert c2.sentence_end_ts is None
    # 位置不变
    pos_after = service.get_current_position()
    assert (pos_after.line_idx, pos_after.char_idx, pos_after.checkpoint_idx) == (0, 1, 2)


def test_sentence_end_tail_cp_released_writes(service):
    """场景4: 句尾末尾 cp 收到 released → 写入 sentence_end_ts + 推进"""
    project, sentence, c1, c2 = _make_project_normal_then_sentence_end()
    service.set_project(project)

    assert service.move_to_checkpoint(0, 1, 2)

    service.on_key_changed(3000, "released")

    # sentence_end_ts 写入
    assert c2.sentence_end_ts == 3000
    # 推进 (但已是末尾，停在最后)
    idx, total = service.get_progress()
    assert idx == total - 1


def test_sentence_end_zero_check_count_released_writes(service):
    """场景5: 句尾字符 check_count=0 时仅有句尾 cp（cp_idx=0=check_count），released 写入"""
    project, sentence, c1, c_end = _make_project_sentence_end_zero_check()
    service.set_project(project)

    # 推进到句尾末尾 cp = (0, 1, 0)
    assert service.move_to_checkpoint(0, 1, 0)
    pos = service.get_current_position()
    assert (pos.line_idx, pos.char_idx, pos.checkpoint_idx) == (0, 1, 0)

    # pressed 应被忽略（因为是句尾末尾 cp）
    service.on_key_changed(4000, "pressed")
    assert c_end.sentence_end_ts is None

    # released 写入
    service.on_key_changed(4500, "released")
    assert c_end.sentence_end_ts == 4500
