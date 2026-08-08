"""LocalizationManager + pseudo translator 单测。

主要守护：
- 语言注册表至少包含 zh_CN 默认 + pseudo 可视化校验语言
- pseudo translator 把 tr 调用映射为 ⟦原文⟧
- 切换语言时正确卸装旧 translator
- embedded 模式不需要专门守护（由 test_embedded_contract.py 覆盖）
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QWidget

from strange_uta_game.frontend.localization import (
    AVAILABLE_LANGUAGES,
    DEFAULT_LANGUAGE,
    PSEUDO_LANGUAGE_CODE,
    install_translators,
    language_by_code,
    localization,
)


class TestLanguageRegistry:
    def test_default_is_auto(self):
        # 默认从 zh_CN 改成 auto（按系统 locale 解析到 zh_CN/ja_JP/en_US）
        assert DEFAULT_LANGUAGE.code == "auto"
        assert DEFAULT_LANGUAGE.native_name == "自动 / Auto"

    def test_pseudo_registered(self):
        codes = {l.code for l in AVAILABLE_LANGUAGES}
        assert PSEUDO_LANGUAGE_CODE in codes

    def test_auto_resolves_to_real_language(self, qapp):
        from strange_uta_game.frontend.localization import resolve_auto_language
        assert resolve_auto_language() in ("zh_CN", "ja_JP", "en_US")

    def test_native_names_unique(self):
        names = [l.native_name for l in AVAILABLE_LANGUAGES]
        assert len(names) == len(set(names))

    def test_language_lookup_is_public(self):
        assert language_by_code("ja_JP").code == "ja_JP"


class TestPseudoTranslator:
    """⟦pseudo⟧ 模式：tr() 必须返回 ⟦原文⟧。"""

    @pytest.fixture(autouse=True)
    def _restore(self, qapp):
        before = localization.current_code
        yield
        # 测试结束恢复默认 zh_CN，免得污染后续测试
        install_translators(before)

    def test_pseudo_wraps_tr_calls(self, qapp):
        install_translators("pseudo")
        w = QWidget()
        assert w.tr("打轴设定") == "⟦打轴设定⟧"
        assert w.tr("Cancel") == "⟦Cancel⟧"

    def test_pseudo_empty_returns_empty(self, qapp):
        install_translators("pseudo")
        w = QWidget()
        assert w.tr("") == ""  # 空源串不加括号，避免 ⟦⟧ 噪点

    def test_zh_cn_passthrough(self, qapp):
        install_translators("zh_CN")
        w = QWidget()
        # zh_CN 无 .qm 时回落到源串
        assert w.tr("打轴设定") == "打轴设定"

    def test_switch_between_languages(self, qapp):
        install_translators("pseudo")
        assert QWidget().tr("X") == "⟦X⟧"
        install_translators("zh_CN")
        assert QWidget().tr("X") == "X"
        install_translators("pseudo")
        assert QWidget().tr("X") == "⟦X⟧"


class TestApplyLanguageState:
    @pytest.fixture(autouse=True)
    def _restore(self, qapp):
        before = localization.current_code
        yield
        install_translators(before)

    def test_unknown_code_falls_back_to_default(self, qapp):
        result = localization.apply_language("xx_YY")
        assert result.code == DEFAULT_LANGUAGE.code

    def test_current_code_reflects_applied(self, qapp):
        localization.apply_language("pseudo")
        assert localization.current_code == "pseudo"
        localization.apply_language("zh_CN")
        assert localization.current_code == "zh_CN"

    def test_language_changed_signal(self, qapp):
        received: list[str] = []
        conn = localization.language_changed.connect(received.append)
        try:
            localization.apply_language("pseudo")
            assert received[-1] == "pseudo"
            localization.apply_language("zh_CN")
            assert received[-1] == "zh_CN"
        finally:
            localization.language_changed.disconnect(conn)
