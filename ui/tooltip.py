from __future__ import annotations

from PySide6.QtCore import QPoint, QEvent, QObject, QTimer, Qt
from PySide6.QtGui import QColor, QCursor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QFrame,
    QLabel,
    QListWidget,
    QSizePolicy,
    QToolTip,
    QVBoxLayout,
    QWidget,
)


class _TooltipPopup(QFrame):
    """Small, self-painted tooltip card with no native window shadow/artifacts."""

    HORIZONTAL_PADDING = 12
    VERTICAL_PADDING = 9
    BORDER_WIDTH = 1
    CORNER_RADIUS = 8
    CONTENT_MAX_WIDTH = 440

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setObjectName("tooltipPopup")

        self.label = QLabel(self)
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(
            Qt.TextInteractionFlag.NoTextInteraction)
        self.label.setStyleSheet("background: transparent; border: none;")
        self.label.setMaximumWidth(self.CONTENT_MAX_WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            self.HORIZONTAL_PADDING + self.BORDER_WIDTH,
            self.VERTICAL_PADDING + self.BORDER_WIDTH,
            self.HORIZONTAL_PADDING + self.BORDER_WIDTH,
            self.VERTICAL_PADDING + self.BORDER_WIDTH,
        )
        layout.setSpacing(0)
        layout.addWidget(self.label)

        self._background = QColor("#ffffff")
        self._border = QColor("#9aa0a6")

    def set_colors(self, background: QColor, border: QColor) -> None:
        self._background = background
        self._border = border
        self.update()

    def set_text(self, text: str, font) -> None:
        self.label.setFont(font)
        self.label.setText(text)

        # QLabel keeps the minimum/maximum width imposed by setFixedWidth().
        # Clear those constraints before measuring each new tooltip; otherwise
        # a previously large tooltip can force every subsequent short tooltip
        # to retain that width.
        self.label.setMinimumWidth(0)
        self.label.setMaximumWidth(16777215)
        self.label.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        self.label.setWordWrap(False)

        # Measure the unwrapped content first. Only genuinely long content is
        # constrained and wrapped.
        natural_width = self.label.sizeHint().width()

        if natural_width > self.CONTENT_MAX_WIDTH:
            self.label.setFixedWidth(self.CONTENT_MAX_WIDTH)
            self.label.setWordWrap(True)
        else:
            self.label.setFixedWidth(natural_width)

        self.label.adjustSize()
        self.adjustSize()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(
            self.BORDER_WIDTH // 2,
            self.BORDER_WIDTH // 2,
            -self.BORDER_WIDTH // 2,
            -self.BORDER_WIDTH // 2,
        )
        painter.setBrush(self._background)
        painter.setPen(QPen(self._border, self.BORDER_WIDTH))
        painter.drawRoundedRect(rect, self.CORNER_RADIUS, self.CORNER_RADIUS)


class StyledToolTipController(QObject):
    """App-wide tooltips driven by the actual widget beneath the cursor."""

    SHOW_DELAY_MS = 70
    POLL_INTERVAL_MS = 35
    CURSOR_OFFSET = 14
    SCREEN_MARGIN = 8

    def __init__(self, app: QApplication):
        super().__init__(app)
        self.app = app
        self._pending_target: QWidget | None = None
        self._pending_text = ""
        self._visible_target: QWidget | None = None
        self._visible_text = ""

        self._show_timer = QTimer(self)
        self._show_timer.setSingleShot(True)
        self._show_timer.timeout.connect(self._show_pending)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self.POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_hover)
        self._poll_timer.start()

        self._tooltip = _TooltipPopup()
        self._tooltip.hide()

        app.installEventFilter(self)
        self.refresh_theme()

    def refresh_theme(self) -> None:
        palette = self.app.palette()
        self._tooltip.set_colors(
            palette.color(palette.ColorRole.ToolTipBase),
            palette.color(palette.ColorRole.Mid),
        )
        self._tooltip.label.setStyleSheet(
            f"color: {palette.color(palette.ColorRole.ToolTipText).name()};"
            "background: transparent; border: none;"
        )
        self._tooltip.label.setFont(self.app.font())

    @staticmethod
    def _list_ancestor(widget: QWidget | None) -> QListWidget | None:
        current = widget
        while current is not None:
            if isinstance(current, QListWidget):
                return current
            current = current.parentWidget()
        return None

    @staticmethod
    def _item_tooltip(view: QAbstractItemView, global_pos) -> str:
        viewport = view.viewport()
        if viewport is None:
            return ""
        item = view.indexAt(viewport.mapFromGlobal(global_pos))
        if not item.isValid():
            return ""
        model = view.model()
        if model is None:
            return ""
        value = model.data(item, Qt.ItemDataRole.ToolTipRole)
        return str(value).strip() if value is not None else ""

    def _resolve_target(self, global_pos) -> tuple[QWidget | None, str]:
        widget = QApplication.widgetAt(global_pos)
        if widget is None or widget is self._tooltip:
            return None, ""

        current: QWidget | None = widget
        while current is not None:
            if current is self._tooltip:
                return None, ""

            if isinstance(current, QAbstractItemView):
                item_text = self._item_tooltip(current, global_pos)
                if item_text:
                    return current, item_text

            text = current.toolTip().strip()
            if text:
                return current, text
            current = current.parentWidget()

        # QListWidget viewport children are not guaranteed to report the view
        # itself as the widget under the cursor, so resolve the item separately.
        list_widget = self._list_ancestor(widget)
        if list_widget is not None:
            item_text = self._item_tooltip(list_widget, global_pos)
            if item_text:
                return list_widget, item_text

        return None, ""

    def _hide(self) -> None:
        self._show_timer.stop()
        self._pending_target = None
        self._pending_text = ""
        self._visible_target = None
        self._visible_text = ""
        self._tooltip.hide()
        QToolTip.hideText()

    def _schedule(self, target: QWidget, text: str) -> None:
        self._pending_target = target
        self._pending_text = text
        self._show_timer.start(self.SHOW_DELAY_MS)

    def _poll_hover(self) -> None:
        if self.app.applicationState() != Qt.ApplicationState.ApplicationActive:
            self._hide()
            return

        # Native popup widgets such as QMenu own the interaction while they
        # are open. Do not leave a tooltip from the widget that opened the
        # popup hanging underneath the popup, and do not try to show new
        # tooltips for popup contents unless they explicitly participate in
        # the app tooltip system later.
        popup = self.app.activePopupWidget()
        if popup is not None and popup is not self._tooltip:
            self._hide()
            return

        target, text = self._resolve_target(QCursor.pos())
        if target is None or not text:
            if self._visible_target is not None or self._pending_target is not None:
                self._hide()
            return

        if (
            target is self._visible_target
            and text == self._visible_text
            and self._tooltip.isVisible()
        ):
            # Keep the tooltip anchored to the cursor while it remains over
            # the same target. The target/text do not change as the cursor
            # moves within a widget, so without this update the popup would
            # stay at the position where it was originally shown.
            self._place_tooltip()
            return

        if target is self._pending_target and text == self._pending_text:
            return

        self._tooltip.hide()
        self._visible_target = None
        self._visible_text = ""
        self._schedule(target, text)

    def _place_tooltip(self) -> None:
        cursor = QCursor.pos()
        size = self._tooltip.size()
        screen = QApplication.screenAt(cursor) or QApplication.primaryScreen()
        if screen is None:
            self._tooltip.move(
                cursor + QPoint(self.CURSOR_OFFSET, self.CURSOR_OFFSET))
            return

        available = screen.availableGeometry().adjusted(
            self.SCREEN_MARGIN,
            self.SCREEN_MARGIN,
            -self.SCREEN_MARGIN,
            -self.SCREEN_MARGIN,
        )

        pos = QPoint(
            cursor.x() + self.CURSOR_OFFSET,
            cursor.y() + self.CURSOR_OFFSET,
        )
        if pos.x() + size.width() > available.right():
            pos.setX(cursor.x() - size.width() - self.CURSOR_OFFSET)
        if pos.y() + size.height() > available.bottom():
            pos.setY(cursor.y() - size.height() - self.CURSOR_OFFSET)

        pos.setX(min(max(pos.x(), available.left()),
                 available.right() - size.width()))
        pos.setY(min(max(pos.y(), available.top()),
                 available.bottom() - size.height()))
        self._tooltip.move(pos)

    def _show_pending(self) -> None:
        target = self._pending_target
        text = self._pending_text
        if target is None or not text or not target.isVisible():
            self._hide()
            return
        if self.app.applicationState() != Qt.ApplicationState.ApplicationActive:
            self._hide()
            return

        current_target, current_text = self._resolve_target(QCursor.pos())
        if current_target is not target or current_text != text:
            self._hide()
            return

        QToolTip.hideText()
        self._tooltip.set_text(text, self.app.font())
        self._place_tooltip()
        self._tooltip.show()
        self._tooltip.raise_()
        self._visible_target = target
        self._visible_text = text
        self._pending_target = None
        self._pending_text = ""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            # Never let Qt's native tooltip compete with the styled popup.
            QToolTip.hideText()
            return True

        if event.type() in {
            QEvent.Type.WindowDeactivate,
            QEvent.Type.Hide,
            QEvent.Type.ApplicationDeactivate,
        }:
            self._hide()
        return super().eventFilter(watched, event)
