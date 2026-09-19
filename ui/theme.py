from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QFrame, QLabel, QLineEdit, QListWidget, QPlainTextEdit, QProgressBar, QPushButton, QWidget


@dataclass(frozen=True)
class ThemeTokens:
    window: QColor
    surface: QColor
    surface_alt: QColor
    border: QColor
    border_strong: QColor
    text: QColor
    text_muted: QColor
    text_subtle: QColor
    disabled_text: QColor
    accent: QColor
    accent_text: QColor
    accent_hover: QColor
    accent_pressed: QColor
    accent_soft: QColor
    input_focus: QColor
    scrollbar_track: QColor
    scrollbar_handle: QColor
    scrollbar_hover: QColor
    success_bg: QColor
    success_fg: QColor
    danger_bg: QColor
    danger_fg: QColor
    info_bg: QColor
    info_fg: QColor
    neutral_bg: QColor
    neutral_fg: QColor
    warning_bg: QColor
    warning_fg: QColor


def _blend(first: QColor, second: QColor, amount: float) -> QColor:
    amount = max(0.0, min(1.0, amount))
    return QColor(
        round(first.red() + (second.red() - first.red()) * amount),
        round(first.green() + (second.green() - first.green()) * amount),
        round(first.blue() + (second.blue() - first.blue()) * amount),
        round(first.alpha() + (second.alpha() - first.alpha()) * amount),
    )


def _shade(color: QColor, amount: int) -> QColor:
    hsl = color.toHsl()
    lightness = max(0, min(255, hsl.lightness() + amount))
    return QColor.fromHsl(hsl.hue(), hsl.saturation(), lightness, hsl.alpha())


def _semantic_color(
    *,
    hue: int,
    dark: bool,
    surface: QColor,
) -> tuple[QColor, QColor]:
    if dark:
        fg = QColor.fromHsl(hue, 185, 190)
        bg = _blend(surface, fg, 0.16)
    else:
        fg = QColor.fromHsl(hue, 190, 92)
        bg = _blend(surface, fg, 0.10)
    return bg, fg


def tokens_from_palette(palette: QPalette | None = None) -> ThemeTokens:
    palette = palette or QApplication.palette()
    window = palette.color(QPalette.ColorRole.Window)
    surface = palette.color(QPalette.ColorRole.Base)
    surface_alt = palette.color(QPalette.ColorRole.AlternateBase)
    border = palette.color(QPalette.ColorRole.Mid)
    border_strong = palette.color(QPalette.ColorRole.Dark)
    text = palette.color(QPalette.ColorRole.WindowText)
    accent = palette.color(QPalette.ColorRole.Highlight)
    accent_text = palette.color(QPalette.ColorRole.HighlightedText)
    dark = window.lightness() < 128

    text_muted = _blend(text, window, 0.34)
    text_subtle = _blend(text, window, 0.52)
    disabled_text = palette.color(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText
    )
    accent_hover = _blend(accent, accent_text, 0.14)
    accent_pressed = _shade(accent, -22 if not dark else 22)
    accent_soft = _blend(surface, accent, 0.16)
    input_focus = accent

    scrollbar_track = _blend(window, text, 0.08)
    scrollbar_handle = _blend(border, text, 0.20)
    scrollbar_hover = _blend(scrollbar_handle, text, 0.18)

    success_bg, success_fg = _semantic_color(
        hue=145, dark=dark, surface=surface
    )
    danger_bg, danger_fg = _semantic_color(
        hue=4, dark=dark, surface=surface
    )
    info_bg, info_fg = _semantic_color(
        hue=210, dark=dark, surface=surface
    )
    warning_bg, warning_fg = _semantic_color(
        hue=42, dark=dark, surface=surface
    )
    neutral_bg = _blend(surface, text, 0.10)
    neutral_fg = _blend(text, window, 0.20)

    return ThemeTokens(
        window=window,
        surface=surface,
        surface_alt=surface_alt,
        border=border,
        border_strong=border_strong,
        text=text,
        text_muted=text_muted,
        text_subtle=text_subtle,
        disabled_text=disabled_text,
        accent=accent,
        accent_text=accent_text,
        accent_hover=accent_hover,
        accent_pressed=accent_pressed,
        accent_soft=accent_soft,
        input_focus=input_focus,
        scrollbar_track=scrollbar_track,
        scrollbar_handle=scrollbar_handle,
        scrollbar_hover=scrollbar_hover,
        success_bg=success_bg,
        success_fg=success_fg,
        danger_bg=danger_bg,
        danger_fg=danger_fg,
        info_bg=info_bg,
        info_fg=info_fg,
        neutral_bg=neutral_bg,
        neutral_fg=neutral_fg,
        warning_bg=warning_bg,
        warning_fg=warning_fg,
    )


def _css(color: QColor) -> str:
    return color.name(QColor.NameFormat.HexArgb)


def _set_palette_color(widget: QWidget, role: QPalette.ColorRole, color: QColor) -> None:
    palette = widget.palette()
    palette.setColor(role, color)
    widget.setPalette(palette)


def _set_text_color(widget: QWidget, color: QColor) -> None:
    _set_palette_color(widget, QPalette.ColorRole.WindowText, color)
    if isinstance(widget, QLineEdit | QComboBox | QListWidget | QPlainTextEdit):
        _set_palette_color(widget, QPalette.ColorRole.Text, color)


def _font(widget: QWidget) -> QFont:
    return widget.font()


def _set_font(widget: QWidget, *, point_size: float | None = None, bold: bool | None = None) -> None:
    font = _font(widget)
    if point_size is not None:
        font.setPointSizeF(point_size)
    if bold is not None:
        font.setBold(bold)
    widget.setFont(font)


def _surface_style(tokens: ThemeTokens, radius: int = 10, *, sidebar: bool = False) -> str:
    minimum = "min-width: 290px;" if sidebar else ""
    return f"""
        *[styleClass=\"surface{('-sidebar' if sidebar else '')}\"] {{
            background: {_css(tokens.surface)};
            border: 1px solid {_css(tokens.border)};
            border-radius: {radius}px;
            {minimum}
        }}
    """


def _apply_class(widget: QWidget, class_name: str, tokens: ThemeTokens) -> None:
    # Fonts and palette are applied through Qt APIs so native widgets keep their
    # platform rendering. Small local stylesheets are reserved for components
    # that genuinely need borders, fills, or state-dependent decoration.
    if class_name == "surface":
        widget.setAutoFillBackground(True)
        widget.setStyleSheet(_surface_style(tokens))
    elif class_name == "surface-sidebar":
        widget.setAutoFillBackground(True)
        widget.setStyleSheet(_surface_style(tokens, radius=12, sidebar=True))
    elif class_name == "surface-alt":
        widget.setAutoFillBackground(True)
        widget.setStyleSheet(
            f"*[styleClass=\"surface-alt\"] {{ background: {_css(tokens.surface_alt)}; border: 1px solid {_css(tokens.border)}; border-radius: 10px; }}"
        )
    elif class_name == "title":
        _set_font(widget, point_size=22, bold=True)
    elif class_name == "preview-title":
        _set_font(widget, point_size=20, bold=True)
    elif class_name == "subtitle":
        _set_text_color(widget, tokens.text_muted)
    elif class_name == "subtle":
        _set_text_color(widget, tokens.text_subtle)
    elif class_name in {"section-title", "card-title"}:
        _set_font(widget, bold=True)
    elif class_name in {"inline-status", "app-status", "selection-summary", "diff-legend"}:
        _set_text_color(widget, tokens.text_muted)
    elif class_name == "status-dot":
        _set_text_color(widget, tokens.success_fg)
        _set_font(widget, point_size=8)
    elif class_name == "status-dot-active":
        _set_text_color(widget, tokens.info_fg)
        _set_font(widget, point_size=8)
    elif class_name == "status-dot-error":
        _set_text_color(widget, tokens.danger_fg)
        _set_font(widget, point_size=8)
    elif class_name == "drop-area":
        widget.setAutoFillBackground(False)
        widget.setStyleSheet(f"""
            *[styleClass=\"drop-area\"] {{
                background: {_css(tokens.surface_alt)};
                border: 1.5px dashed {_css(tokens.accent)};
                border-radius: 12px;
            }}
            *[styleClass=\"drop-area\"]:hover {{
                background: {_css(tokens.accent_soft)};
                border-color: {_css(tokens.accent_hover)};
            }}
        """)
    elif class_name == "drop-title":
        _set_text_color(widget, tokens.accent)
        _set_font(widget, point_size=13, bold=True)
    elif class_name == "input":
        widget.setStyleSheet(f"""
            QLineEdit, QComboBox {{
                border: 1px solid {_css(tokens.border)};
                border-radius: 8px;
                padding: 7px 10px;
                min-height: 18px;
            }}
            QLineEdit:focus, QComboBox:focus {{
                border-color: {_css(tokens.input_focus)};
            }}
            QComboBox::drop-down {{ border: none; width: 28px; }}
            QComboBox QAbstractItemView {{
                background: {_css(tokens.surface)};
                border: 1px solid {_css(tokens.border)};
                selection-background-color: {_css(tokens.accent_soft)};
                selection-color: {_css(tokens.text)};
                padding: 4px;
            }}
        """)
    elif class_name == "check-option":
        _set_font(widget, bold=True)
        widget.setStyleSheet("QCheckBox { spacing: 8px; }")
    elif class_name == "primary-button":
        widget.setStyleSheet(f"""
            QPushButton, QToolButton {{
                background: {_css(tokens.accent)};
                color: {_css(tokens.accent_text)};
                border: none;
                border-radius: 8px;
                padding: 8px 14px;
                font-weight: 700;
            }}
            QPushButton:hover, QToolButton:hover {{
                background: {_css(tokens.accent_hover)};
            }}
            QWidget#PackageButton {{
                border-radius: 8px;
            }}
            QPushButton#PackageMainButton {{
                border-top-right-radius: 0px;
                border-bottom-right-radius: 0px;
                padding: 8px 12px 8px 14px;
            }}
            QPushButton#PackageMenuButton {{
                border-top-left-radius: 0px;
                border-bottom-left-radius: 0px;
                padding: 0px;
                min-width: 28px;
                max-width: 28px;
                font-size: 14px;
                font-weight: 700;
            }}
            QPushButton:pressed, QToolButton:pressed {{
                background: {_css(tokens.accent_pressed)};
            }}
            QPushButton:disabled, QToolButton:disabled {{
                background: {_css(tokens.surface_alt)};
                color: {_css(tokens.disabled_text)};
            }}
        """)
    elif class_name == "secondary-button":
        widget.setStyleSheet(f"""
            QPushButton {{
                background: {_css(tokens.surface_alt)};
                color: {_css(tokens.text)};
                border: 1px solid {_css(tokens.border)};
                border-radius: 8px;
                padding: 8px 14px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                background: {_css(tokens.accent_soft)};
                border-color: {_css(tokens.accent)};
            }}
            QPushButton:disabled {{ color: {_css(tokens.disabled_text)}; }}
        """)
    elif class_name == "list":
        widget.setStyleSheet(f"""
            QListWidget {{ border: 1px solid {_css(tokens.border)}; border-radius: 10px; }}
            QListWidget::item {{ padding: 7px 5px; }}
            QListWidget::item:selected {{
                background: {_css(tokens.accent_soft)};
                color: {_css(tokens.accent_text)};
            }}
        """)
    elif class_name == "diff-editor":
        widget.setStyleSheet(
            f"QPlainTextEdit {{ border: 1px solid {_css(tokens.border)}; border-radius: 10px; outline: none; }}")
    elif class_name == "progress":
        widget.setStyleSheet(f"""
            QProgressBar {{ background: transparent; border: none; border-radius: 3px; }}
            QProgressBar::chunk {{ background: {_css(tokens.accent)}; border-radius: 3px; }}
        """)
    elif class_name == "diff-title":
        widget.setStyleSheet(f"""
            *[styleClass=\"diff-title\"] {{
                background: {_css(tokens.surface)};
                border: 1px solid {_css(tokens.border)};
                border-radius: 9px;
                padding: 9px 11px;
            }}
        """)
    elif class_name == "stat-value":
        _set_font(widget, point_size=13, bold=True)
    elif class_name == "stat-label":
        _set_font(widget, bold=True)
        _set_text_color(widget, tokens.text_muted)
    elif class_name == "diff-swatch-added":
        widget.setStyleSheet(
            f"*[styleClass=\"diff-swatch-added\"] {{ background: {_css(tokens.success_bg)}; border: 1px solid {_css(tokens.success_fg)}; border-radius: 3px; }}")
    elif class_name == "diff-swatch-removed":
        widget.setStyleSheet(
            f"*[styleClass=\"diff-swatch-removed\"] {{ background: {_css(tokens.danger_bg)}; border: 1px solid {_css(tokens.danger_fg)}; border-radius: 3px; }}")
    elif class_name == "diff-swatch-hunk":
        widget.setStyleSheet(
            f"*[styleClass=\"diff-swatch-hunk\"] {{ background: {_css(tokens.info_bg)}; border: 1px solid {_css(tokens.info_fg)}; border-radius: 3px; }}")


def set_style_class(widget: QWidget, class_name: str) -> None:
    widget.setProperty("styleClass", class_name)


def apply_theme(widget: QWidget) -> ThemeTokens:
    tokens = tokens_from_palette()
    for target in [widget, *widget.findChildren(QWidget)]:
        class_name = target.property("styleClass")
        if isinstance(class_name, str) and class_name:
            _apply_class(target, class_name, tokens)
    return tokens
