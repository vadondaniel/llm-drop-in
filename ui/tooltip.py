from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QTimer, Qt
from PySide6.QtGui import QColor, QCursor
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QLabel,
    QListWidget,
    QWidget,
)


class StyledToolTipController(QObject):
    """Small, app-wide hover cards for the handful of controls that need help."""

    DELAY_MS = 180
    MAX_WIDTH = 460

    def __init__(self, app: QApplication):
        super().__init__(app)
        self.app = app
        self._pending_widget: QWidget | None = None
        self._pending_text = ""

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._show_pending)

        self._tooltip = QLabel(None, Qt.WindowType.ToolTip |
                               Qt.WindowType.FramelessWindowHint)
        self._tooltip.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._tooltip.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._tooltip.setTextFormat(Qt.TextFormat.RichText)
        self._tooltip.setWordWrap(True)
        self._tooltip.setMaximumWidth(self.MAX_WIDTH)
        self._tooltip.hide()

        shadow = QGraphicsDropShadowEffect(self._tooltip)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 5)
        shadow.setColor(QColor(0, 0, 0, 72))
        self._tooltip.setGraphicsEffect(shadow)

        app.installEventFilter(self)
        self.refresh_theme()

    def refresh_theme(self) -> None:
        palette = self.app.palette()
        base = palette.color(palette.ColorRole.ToolTipBase)
        text = palette.color(palette.ColorRole.ToolTipText)
        border = palette.color(palette.ColorRole.Mid)
        accent = palette.color(palette.ColorRole.Highlight)
        self._tooltip.setStyleSheet(
            "QLabel {"
            f"background: {base.name(QColor.NameFormat.HexArgb)};"
            f"color: {text.name(QColor.NameFormat.HexArgb)};"
            f"border: 1px solid {border.name(QColor.NameFormat.HexArgb)};"
            f"border-left: 3px solid {accent.name(QColor.NameFormat.HexArgb)};"
            "border-radius: 8px;"
            "padding: 8px 11px;"
            "}"
        )

    @staticmethod
    def _list_ancestor(widget: QWidget | None) -> QListWidget | None:
        current = widget
        while current is not None:
            if isinstance(current, QListWidget):
                return current
            current = current.parentWidget()
        return None

    def _tooltip_for_event(self, watched: QObject, event: QEvent) -> tuple[QWidget | None, str]:
        if not isinstance(watched, QWidget):
            return None, ""

        list_widget = self._list_ancestor(watched)
        if list_widget is not None and event.type() in {
            QEvent.Type.MouseMove,
            QEvent.Type.Enter,
        }:
            item = list_widget.itemAt(
                list_widget.viewport().mapFromGlobal(QCursor.pos())
            )
            if item is not None:
                text = item.toolTip().strip()
                if text:
                    return list_widget, text

        # Do not inherit tooltips from parents. A tooltip is an explicit affordance
        # of the widget being hovered, not generic explanatory text attached above it.
        text = watched.toolTip().strip()
        return (watched, text) if text else (None, "")

    def _hide(self) -> None:
        self._timer.stop()
        self._pending_widget = None
        self._pending_text = ""
        self._tooltip.hide()

    def _schedule(self, widget: QWidget, text: str) -> None:
        self._pending_widget = widget
        self._pending_text = text
        self._timer.start(self.DELAY_MS)

    def _show_pending(self) -> None:
        widget = self._pending_widget
        text = self._pending_text
        if widget is None or not text or not widget.isVisible():
            return

        self._tooltip.setText(text)
        self._tooltip.adjustSize()
        cursor = QCursor.pos()
        screen = QApplication.screenAt(cursor) or QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            pos = QPoint(cursor.x() + 16, cursor.y() + 20)
            if pos.x() + self._tooltip.width() > available.right():
                pos.setX(cursor.x() - self._tooltip.width() - 16)
            if pos.y() + self._tooltip.height() > available.bottom():
                pos.setY(cursor.y() - self._tooltip.height() - 12)
            pos.setX(max(available.left(), pos.x()))
            pos.setY(max(available.top(), pos.y()))
        else:
            pos = QPoint(cursor.x() + 16, cursor.y() + 20)
        self._tooltip.move(pos)
        self._tooltip.show()
        self._tooltip.raise_()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        event_type = event.type()
        if event_type in {QEvent.Type.Hide, QEvent.Type.WindowDeactivate}:
            self._hide()
        elif event_type in {QEvent.Type.Enter, QEvent.Type.MouseMove}:
            widget, text = self._tooltip_for_event(watched, event)
            if widget is not None and text:
                self._tooltip.hide()
                self._schedule(widget, text)
            else:
                self._hide()
        elif event_type in {
            QEvent.Type.Leave,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.FocusOut,
        }:
            self._hide()
        return super().eventFilter(watched, event)
