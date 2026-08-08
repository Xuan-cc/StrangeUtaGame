"""LocalizationManager —— app 翻译器装载与语言注册表。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from PyQt6.QtCore import QCoreApplication, QLocale, QTranslator


class _Signal:
    """轻量 signal-like。

    LocalizationManager 是**模块级单例**，import 时就构造，远早于 QApplication
    存在；如果继承 QObject + pyqtSignal，会引发"无 parent 的 QObject 被 Qt
    跨 qapp 生命周期回收，下次访问得 RuntimeError: C++ object deleted"。
    所以用纯 Python 实现 connect / disconnect / emit，规避 Qt 生命周期。
    """

    __slots__ = ("_slots",)

    def __init__(self) -> None:
        self._slots: List[Callable] = []

    def connect(self, slot: Callable) -> Callable:
        if slot not in self._slots:
            self._slots.append(slot)
        return slot  # 与 pyqtSignal.connect 返回值兼容

    def disconnect(self, slot: Callable) -> None:
        try:
            self._slots.remove(slot)
        except ValueError:
            pass

    def emit(self, *args) -> None:
        for slot in list(self._slots):
            try:
                slot(*args)
            except Exception:
                # 单个 slot 抛错不影响其他订阅者
                import logging
                logging.getLogger(__name__).warning(
                    "language_changed slot raised", exc_info=True
                )


@dataclass(frozen=True)
class Language:
    """注册一种受支持的语言。

    Attributes:
        code: 后台代号，形如 ``"zh_CN"`` / ``"en_US"`` / ``"ja_JP"``。
        native_name: 在 UI 中显示给用户的语言名称（用其本族语写）。
        qlocale_name: 用于构造 :class:`QLocale` 与 ``.qm`` 文件名的字符串。
            通常与 ``code`` 相同；保留单独字段以便未来允许同一显示名
            对应不同 locale 文件。
    """

    code: str
    native_name: str
    qlocale_name: str

    def to_qlocale(self) -> QLocale:
        return QLocale(self.qlocale_name)


#: 受支持的语言列表。EN/JA 翻译就绪后再追加。
#:
#: "auto" 是指令式选项，不是真实 .qm 语言：``apply_language("auto")`` 时
#: 用 ``QLocale.system()`` 把系统语言映射到 zh_CN / ja_JP / en_US 三选
#: 之一（简繁中文统一为 zh_CN，日文为 ja_JP，其余为 en_US）。
#:
#: "pseudo" 是开发用伪语言：所有被 ``tr()`` 包过的字符串显示成 ``⟦原文⟧``，
#: 没 wrap 的纯中文保留原样。切到 pseudo 可一眼看出哪些字符串还没纳入 i18n。
#: 它不依赖 .qm 文件——见 :class:`_PseudoTranslator`。
AVAILABLE_LANGUAGES: Tuple[Language, ...] = (
    Language(code="auto",   native_name="自动 / Auto", qlocale_name="auto"),
    Language(code="zh_CN",  native_name="简体中文", qlocale_name="zh_CN"),
    Language(code="ja_JP",  native_name="日本語",   qlocale_name="ja_JP"),
    Language(code="en_US",  native_name="English",  qlocale_name="en_US"),
    Language(code="pseudo", native_name="⟦pseudo⟧", qlocale_name="C"),
)


#: 默认语言（与 ``ui.language`` 默认值一致）。pseudo 不能作默认。
DEFAULT_LANGUAGE: Language = AVAILABLE_LANGUAGES[0]


PSEUDO_LANGUAGE_CODE: str = "pseudo"
AUTO_LANGUAGE_CODE: str = "auto"


def resolve_auto_language() -> str:
    """读 ``QLocale.system().name()`` 推断要装载的真实语言代号。

    规则（与用户约定）：
    - ``zh_*`` （含 zh_CN、zh_TW、zh_HK、zh_SG…）→ 全部走 zh_CN
    - ``ja_*`` → ja_JP
    - 其他 → en_US
    """
    from PyQt6.QtCore import QLocale
    name = ""
    try:
        name = QLocale.system().name() or ""
    except Exception:
        pass
    if name.startswith("zh"):
        return "zh_CN"
    if name.startswith("ja"):
        return "ja_JP"
    return "en_US"


def language_by_code(code: str) -> Language:
    """根据后台代号查找 Language；未知代号回退到默认。"""
    for lang in AVAILABLE_LANGUAGES:
        if lang.code == code:
            return lang
    return DEFAULT_LANGUAGE


def _translations_dir() -> Path:
    """app 翻译 ``.qm`` 所在目录。"""
    return Path(__file__).resolve().parent / "translations"


class _AppTranslator(QTranslator):
    """加载 ``localization/translations/app.<locale>.qm``。

    源字符串本身就是简体中文，因此 ``zh_CN`` 的 ``.qm`` 缺失也无妨：
    ``tr()`` 找不到翻译时直接返回源字符串。
    """

    def load_language(self, lang: Language) -> bool:
        qm = _translations_dir() / f"app.{lang.qlocale_name}.qm"
        if not qm.exists():
            return False
        return super().load(str(qm))


class _FallbackTranslator(QTranslator):
    """跨上下文回退：``self._editor.tr("X")`` 这类"借用别人 tr 入口"的
    调用，Qt 会按 ``self._editor`` 的 *class* 查上下文，而我方抽取器
    按 *enclosing class* 归类——两者不一致时主翻译器命中失败。本类无
    视 context，按 source 单键查表。

    加载方式：从同名 .ts 解析出 ``{source: translation}``。**安装顺序**：
    必须在 ``_AppTranslator`` **之后**安装，Qt 才会先查主翻译器、失败
    再回退到本类。
    """

    _source_fallback: dict[str, str] = {}

    def isEmpty(self) -> bool:  # type: ignore[override]
        return not self._source_fallback

    def load_from_ts(self, lang: Language) -> bool:
        ts_path = _translations_dir() / f"app.{lang.qlocale_name}.ts"
        self._source_fallback = self._build(ts_path)
        return bool(self._source_fallback)

    @staticmethod
    def _build(ts_path: Path) -> dict[str, str]:
        if not ts_path.exists():
            return {}
        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(ts_path)
        except Exception:
            return {}
        out: dict[str, str] = {}
        for ctx in tree.getroot().findall("context"):
            for msg in ctx.findall("message"):
                src_el = msg.find("source")
                tr_el = msg.find("translation")
                if src_el is None or src_el.text is None:
                    continue
                if tr_el is None or tr_el.get("type") == "unfinished":
                    continue
                tr = tr_el.text or ""
                if not tr:
                    continue
                out.setdefault(src_el.text, tr)
        return out

    def translate(  # type: ignore[override]
        self,
        context: str,
        sourceText: str,
        disambiguation: Optional[str] = None,
        n: int = -1,
    ) -> str:
        # 关键：未命中时返回 sourceText（**不能**返回空串）。Qt 的
        # 翻译器链一旦有任何一个 isEmpty=False 的 translator，源串
        # 回退就不再生效——返回空串会让最终 tr 结果直接是空串。
        return self._source_fallback.get(sourceText, sourceText)


class _PseudoTranslator(QTranslator):
    """开发用伪 translator——返回 ``⟦原文⟧``，不需要 .qm 文件。

    用途：切到 pseudo 语言后，所有被 ``tr()`` 包过的字符串自动套上 ``⟦⟧``
    括号；没 wrap 的硬编码中文保持原样。这样**一眼能看出哪些 UI 字符串还
    没纳入 i18n**——比对着 git diff 查 wrap 是否齐全可靠得多。

    实现要点：

    - 重写 ``translate`` 而非 ``load``——没有 .qm 可载。
    - 必须 ``isEmpty() == False``，否则 Qt 不会把这个 translator 计入
      候选；故 ``isEmpty`` 显式返回 ``False``。Qt 6 的实现里只要
      ``translate`` 返回非空就视为成功，但安全起见两个钩子都搞定。
    - n 参数（plural form）暂不处理——pseudo 仅做可视标记，不关心复数。
    """

    BRACKET_LEFT = "⟦"
    BRACKET_RIGHT = "⟧"

    def isEmpty(self) -> bool:  # type: ignore[override]
        return False

    def translate(  # type: ignore[override]
        self,
        context: str,
        sourceText: str,
        disambiguation: Optional[str] = None,
        n: int = -1,
    ) -> str:
        if not sourceText:
            return ""
        return f"{self.BRACKET_LEFT}{sourceText}{self.BRACKET_RIGHT}"


class LocalizationManager:
    """语言状态持有者 + translator 安装/卸载入口。

    生命周期：
        - app 启动时由 :func:`install_translators` 调用 :meth:`apply_language`
          完成首次安装。
        - 用户在「设置 → 关于/语言」切换语言：UI 调用 :meth:`apply_language`，
          然后弹出"重启生效"提示（Qt 的运行时翻译切换需要每个 widget 主动
          响应 ``LanguageChange`` 事件，本期不做，按"重启"对待）。

    本类**不继承 QObject**——见 :class:`_Signal` 的注释。
    """

    def __init__(self) -> None:
        self.language_changed = _Signal()
        # pseudo 语言走 _PseudoTranslator，其余走 _AppTranslator——共同基类 QTranslator
        self._app_translator: Optional[QTranslator] = None
        self._fluent_translator: Optional[QTranslator] = None
        self._fallback_translator: Optional[QTranslator] = None
        self._current: Language = DEFAULT_LANGUAGE
        self._effective_code: str = DEFAULT_LANGUAGE.code

    @property
    def current(self) -> Language:
        return self._current

    @property
    def current_code(self) -> str:
        return self._current.code

    @property
    def effective_code(self) -> str:
        """实际生效的真实语言代号（auto 解析后；其他情况等于 current_code）。"""
        return self._effective_code

    def available(self) -> List[Language]:
        return list(AVAILABLE_LANGUAGES)

    def apply_language(self, code: str) -> Language:
        """切换到指定语言，安装 app 与 fluent translator。

        Args:
            code: ``Language.code``。未知值回退到默认语言。
                ``"auto"`` 指令在此被解析成 zh_CN / ja_JP / en_US 之一，
                **但 ``self._current`` 仍记 auto** —— 这样设置面板下拉
                可以一直显示"自动"，关于刷盘/同步只用 ``"auto"`` 这个值。

        Returns:
            实际生效的 :class:`Language`。
        """
        lang = language_by_code(code)
        # auto 解析成真实语言用于安装 translator，但内部状态保留 auto
        is_auto = lang.code == AUTO_LANGUAGE_CODE
        effective = language_by_code(resolve_auto_language()) if is_auto else lang

        app = QCoreApplication.instance()
        if app is None:
            # 极少见——直接更新内部状态以便后续 install_translators 用。
            self._current = lang
            self._effective_code = effective.code
            from strange_uta_game.frontend.font_utils import set_ui_language
            set_ui_language(effective.code)
            return lang

        # ── 卸载旧的 translator ─────────────────────────────────────
        if self._app_translator is not None:
            app.removeTranslator(self._app_translator)
            self._app_translator = None
        if self._fluent_translator is not None:
            app.removeTranslator(self._fluent_translator)
            self._fluent_translator = None
        if self._fallback_translator is not None:
            app.removeTranslator(self._fallback_translator)
            self._fallback_translator = None

        # ── 安装 fallback translator（先装，Qt 按 LIFO 倒序查找，
        # fallback 作为兜底**最后**才被检查）─────────────────────────
        # pseudo 模式无需 fallback——_PseudoTranslator 永远返回 ⟦原文⟧
        if effective.code != PSEUDO_LANGUAGE_CODE:
            fb = _FallbackTranslator()
            if fb.load_from_ts(effective):
                app.installTranslator(fb)
                self._fallback_translator = fb

        # ── 安装 app translator ─────────────────────────────────────
        # pseudo 走自己的可视化分支，其余按 .qm 文件加载（zh_CN 没 .qm 时
        # 回落到源字符串——本就是简体中文，行为等价）。
        if effective.code == PSEUDO_LANGUAGE_CODE:
            app_tr: QTranslator = _PseudoTranslator()
        else:
            real = _AppTranslator()
            real.load_language(effective)
            app_tr = real
        app.installTranslator(app_tr)
        self._app_translator = app_tr

        # ── 安装 qfluentwidgets 自带 translator（OK/Cancel/Cut/Copy 等）──
        # 注意：在同时装了 PyQt-Fluent-Widgets (PyQt5) 与 PyQt6-Fluent-Widgets
        # 的混合环境里，磁盘上的 ``qfluentwidgets`` 包可能是 PyQt5 变种；其
        # ``FluentTranslator`` 继承的是 ``PyQt5.QtCore.QTranslator``，被
        # PyQt6 的 ``installTranslator`` 拒绝并抛 TypeError。我们把这视为
        # "本环境无 qfluentwidgets 翻译"，静默回退——qfluentwidgets 内部 UI
        # 仍然工作，只是右键 Cut/Copy/Paste 等会显示英文源字符串。
        fluent_tr = _build_fluent_translator(effective)
        if fluent_tr is not None:
            try:
                app.installTranslator(fluent_tr)
            except TypeError:
                fluent_tr = None
            else:
                self._fluent_translator = fluent_tr

        # 内部状态保留用户选择（含 "auto"），下拉显示一致；下游若想拿
        # 实际生效的真实语言，用 self.effective_code 读取。
        self._current = lang
        self._effective_code = effective.code
        # 翻译语言与界面字体保持一致。应用级字体负责普通/继承字体的控件，
        # ui_font() 则确保显式指定字号的新控件也使用同一套语言字体链。
        from strange_uta_game.frontend.font_utils import set_ui_language
        set_ui_language(effective.code)
        self.language_changed.emit(lang.code)
        return lang


def _build_fluent_translator(lang: Language) -> Optional[QTranslator]:
    """构造 qfluentwidgets 的 FluentTranslator。

    qfluentwidgets 现已发布到 PyQt5/6/PySide6 三套；只要包能正常 import
    就能拿到 :class:`FluentTranslator`。失败时（包结构变化或环境异常）返回
    ``None``——qfluentwidgets 内部 UI 仍可用，只是字符串回落到英文源。
    """
    try:
        from qfluentwidgets import FluentTranslator  # type: ignore
    except Exception:
        return None
    try:
        return FluentTranslator(lang.to_qlocale())
    except Exception:
        return None


#: 进程内单例。由 :func:`install_translators` 初始化，由 UI 切换语言时复用。
localization = LocalizationManager()


def install_translators(language_code: Optional[str] = None) -> Language:
    """app 启动时调用一次。在 :class:`QApplication` 创建后立即调用。

    Args:
        language_code: 来自 ``AppSettings("ui.language")``。``None`` 时用
            默认（``zh_CN``）。

    Returns:
        实际生效的 :class:`Language`。
    """
    code = language_code or DEFAULT_LANGUAGE.code
    return localization.apply_language(code)
