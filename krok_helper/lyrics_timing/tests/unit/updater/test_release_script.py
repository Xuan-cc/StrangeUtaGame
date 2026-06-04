"""``scripts/release.py`` 单元测试。

通过 monkeypatch 重定向 ROOT / VERSION_FILE / CHANGELOG，避免影响真实仓库。
"""

import importlib
import sys
import textwrap
from pathlib import Path

import pytest


@pytest.fixture
def release_mod(tmp_path, monkeypatch):
    """加载 ``scripts.release`` 并把所有路径常量重定向到 tmp_path。"""
    scripts_dir = Path(__file__).resolve().parents[3] / "scripts"
    monkeypatch.syspath_prepend(str(scripts_dir))
    mod = importlib.import_module("release")
    # 重定向所有路径
    monkeypatch.setattr(mod, "ROOT", tmp_path, raising=True)
    monkeypatch.setattr(
        mod,
        "VERSION_FILE",
        tmp_path / "src" / "strange_uta_game" / "__version__.py",
        raising=True,
    )
    monkeypatch.setattr(mod, "CHANGELOG", tmp_path / "CHANGELOG.md", raising=True)

    # 准备最小化的 __version__.py 与 CHANGELOG.md
    (tmp_path / "src" / "strange_uta_game").mkdir(parents=True)
    (tmp_path / "src" / "strange_uta_game" / "__version__.py").write_text(
        textwrap.dedent("""
            __version__ = "0.3.2"
            TAG_PREFIX = "SUGv"
            ASSET_NAME_TEMPLATE = "StrangeUtaGame-v{version}.zip"
        """).strip(),
        encoding="utf-8",
    )
    (tmp_path / "CHANGELOG.md").write_text(
        textwrap.dedent("""
            # Changelog

            ## [Unreleased]

            ### 新增功能
            - placeholder

            ## [0.3.2] - 2026-05-01

            ### 新增功能
            - 旧版本说明
        """).lstrip(),
        encoding="utf-8",
    )
    yield mod
    # 清理 import 缓存避免影响其它测试
    sys.modules.pop("release", None)


class TestPrepare:
    def test_updates_version_file(self, release_mod):
        rc = release_mod.cmd_prepare("0.3.3")
        assert rc == 0
        assert release_mod._read_version() == "0.3.3"

    def test_inserts_changelog_section(self, release_mod):
        release_mod.cmd_prepare("0.3.3")
        content = release_mod.CHANGELOG.read_text(encoding="utf-8")
        assert "## [0.3.3]" in content
        # 注入位置应当在 Unreleased 段之后、0.3.2 段之前
        idx_unrel = content.index("[Unreleased]")
        idx_new = content.index("[0.3.3]")
        idx_old = content.index("[0.3.2]")
        assert idx_unrel < idx_new < idx_old

    def test_idempotent_on_existing_section(self, release_mod):
        release_mod.cmd_prepare("0.3.3")
        # 再跑一次不应该重复插入
        release_mod.cmd_prepare("0.3.3")
        content = release_mod.CHANGELOG.read_text(encoding="utf-8")
        # 只能找到一次 [0.3.3] 段落标题
        assert content.count("## [0.3.3]") == 1

    def test_invalid_version_format(self, release_mod):
        with pytest.raises(SystemExit):
            release_mod._check_version_format("v0.3.3")
        with pytest.raises(SystemExit):
            release_mod._check_version_format("0.3")


class TestExtractNotes:
    def test_extract_existing(self, release_mod, tmp_path):
        release_mod.cmd_prepare("0.3.3")
        out = tmp_path / "notes.md"
        rc = release_mod.cmd_extract_notes("0.3.3", out)
        assert rc == 0
        body = out.read_text(encoding="utf-8")
        assert "### 新增功能" in body
        # 不应该把下一段也卷进来
        assert "[0.3.2]" not in body

    def test_extract_missing_raises(self, release_mod, tmp_path):
        with pytest.raises(SystemExit):
            release_mod.cmd_extract_notes("9.9.9", tmp_path / "x.md")
