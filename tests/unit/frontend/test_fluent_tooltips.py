"""Tests for application-wide Fluent tooltip replacement."""

from PyQt6.QtCore import QEvent, QPoint
from PyQt6.QtGui import QHelpEvent
from PyQt6.QtWidgets import QApplication, QWidget
from qfluentwidgets import ToolTipFilter

from strange_uta_game.frontend.fluent_tooltips import install_fluent_tooltips


def test_tooltip_text_installs_fluent_filter_for_new_widgets(qapp):
    install_fluent_tooltips(qapp)
    widget = QWidget()

    widget.setToolTip("Fluent tip")

    filters = widget.findChildren(ToolTipFilter)
    assert len(filters) == 1
    event = QHelpEvent(QEvent.Type.ToolTip, QPoint(0, 0), QPoint(0, 0))
    assert QApplication.sendEvent(widget, event)


def test_tooltip_install_is_idempotent(qapp):
    widget = QWidget()
    widget.setToolTip("Existing tip")

    install_fluent_tooltips(qapp)
    install_fluent_tooltips(qapp)

    assert len(widget.findChildren(ToolTipFilter)) == 1
