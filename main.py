from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow
from ui.tooltip import StyledToolTipController


APP_USER_MODEL_ID = "LLM-Drop-In.Application"


def _configure_windows_app_identity() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(  # type: ignore[attr-defined]
            APP_USER_MODEL_ID
        )
    except (AttributeError, OSError):
        pass


def _app_icon_path() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        bundled = Path(frozen_root) / "app.ico"
        if bundled.is_file():
            return bundled
    return Path(__file__).resolve().parent / "app.ico"


def main() -> int:
    _configure_windows_app_identity()

    app = QApplication(sys.argv)
    app.setApplicationName("LLM Drop-In")
    app.setApplicationDisplayName("LLM Drop-In")

    icon_path = _app_icon_path()
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    tooltip_controller = StyledToolTipController(app)
    app.setProperty("tooltipController", tooltip_controller)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
