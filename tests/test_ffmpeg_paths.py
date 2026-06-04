import os

from krok_helper.ffmpeg import find_tool


def test_find_tool_prefers_configured_directory(monkeypatch, tmp_path):
    configured_dir = tmp_path / "configured"
    path_dir = tmp_path / "path"
    configured_dir.mkdir()
    path_dir.mkdir()

    suffix = ".exe" if os.name == "nt" else ""
    configured_tool = configured_dir / f"ffmpeg{suffix}"
    path_tool = path_dir / f"ffmpeg{suffix}"
    configured_tool.write_text("", encoding="utf-8")
    path_tool.write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", str(path_dir))

    assert find_tool("ffmpeg", configured_dir) == str(configured_tool)
