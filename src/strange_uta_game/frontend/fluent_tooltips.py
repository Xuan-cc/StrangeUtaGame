"""Application-wide Fluent tooltip integration."""

from __future__ import annotations

from typing import override

from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtWidgets import QApplication, QWidget
from qfluentwidgets import ToolTipFilter, ToolTipPosition

_FILTER_ATTRIBUTE = "_strange_uta_game_fluent_tooltip_filter"
_MANAGER_ATTRIBUTE = "_strange_uta_game_fluent_tooltip_manager"


def _install_widget_tooltip(widget: QWidget) -> None:
    """Replace a widget's native tooltip rendering with Fluent Tooltip."""
    if not widget.toolTip() or getattr(widget, _FILTER_ATTRIBUTE, None) is not None:
        return

    tooltip_filter = ToolTipFilter(
        widget,
        showDelay=300,
        position=ToolTipPosition.TOP,
    )
    widget.installEventFilter(tooltip_filter)
    setattr(widget, _FILTER_ATTRIBUTE, tooltip_filter)


class _FluentToolTipManager(QObject):
    """Install ``ToolTipFilter`` when any widget receives tooltip text."""

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTipChange and isinstance(watched, QWidget):
            _install_widget_tooltip(watched)
        return False


def install_fluent_tooltips(app: QApplication | None = None) -> None:
    """Use Fluent tooltips for existing and subsequently configured widgets."""
    app = app or QApplication.instance()
    if app is None:
        return

    manager = getattr(app, _MANAGER_ATTRIBUTE, None)
    if manager is None:
        manager = _FluentToolTipManager(app)
        app.installEventFilter(manager)
        setattr(app, _MANAGER_ATTRIBUTE, manager)

    for widget in app.allWidgets():
        _install_widget_tooltip(widget)
