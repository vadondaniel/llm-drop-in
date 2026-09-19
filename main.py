from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow
from ui.tooltip import StyledToolTipController


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("LLM Drop-In")

    icon_path = Path(__file__).resolve().parent / "app.ico"
    if icon_path.is_file():
        icon = QIcon(str(icon_path))
        app.setWindowIcon(icon)
    tooltip_controller = StyledToolTipController(app)
    app.setProperty("tooltipController", tooltip_controller)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
