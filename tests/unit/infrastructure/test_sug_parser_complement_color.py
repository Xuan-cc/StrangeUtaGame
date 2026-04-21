"""sug_parser 关于 complement_color 的序列化 / 向后兼容测试（Issue #9 第十六批）。"""

import json
import tempfile
from pathlib import Path

from strange_uta_game.backend.domain.entities import Singer
from strange_uta_game.backend.domain.project import Project
from strange_uta_game.backend.infrastructure.persistence.sug_parser import (
    SugProjectParser,
)


def _save_and_reload(project: Project) -> Project:
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "test.sug"
        SugProjectParser.save(project, str(path))
        return SugProjectParser.load(str(path))


class TestSugParserComplementColor:
    def test_complement_color_roundtrip(self):
        s = Singer(
            name="测试",
            color="#FF0000",
            is_default=True,
            backend_number=1,
        )
        original_comp = s.complement_color
        project = Project(singers=[s])

        loaded = _save_and_reload(project)
        assert loaded.singers[0].color == "#FF0000"
        assert loaded.singers[0].complement_color == original_comp
        assert loaded.singers[0].complement_color == "#00FFFF"

    def test_backward_compat_old_sug_without_complement(self):
        """旧 .sug 文件（无 complement_color 字段）加载时自动补算。"""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "legacy.sug"
            # 手写一份没有 complement_color 字段的旧格式数据
            legacy_data = {
                "version": "1.0.0",
                "id": "legacy-project-id",
                "metadata": {
                    "title": "",
                    "artist": "",
                    "album": "",
                    "language": "ja",
                    "created_at": "2024-01-01T00:00:00",
                    "updated_at": "2024-01-01T00:00:00",
                },
                "audio_duration_ms": 0,
                "singers": [
                    {
                        "id": "singer-1",
                        "name": "旧演唱者",
                        "color": "#00FF00",
                        # 注意：没有 complement_color
                        "is_default": True,
                        "display_priority": 0,
                        "enabled": True,
                        "backend_number": 1,
                    }
                ],
                "sentences": [],
            }
            path.write_text(json.dumps(legacy_data), encoding="utf-8")

            loaded = SugProjectParser.load(str(path))
            assert loaded.singers[0].color == "#00FF00"
            # 补色应自动补算为 #FF00FF
            assert loaded.singers[0].complement_color == "#FF00FF"
