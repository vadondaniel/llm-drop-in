from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QVBoxLayout, QWidget, QListWidget, QListWidgetItem,
    QDialog,
)

from core.importer import apply_import, history_entry, make_plan
from core.settings import add_history, load_history, load_settings, save_settings
from core.zip_analyzer import analyze
from ui.import_preview import ImportPreviewDialog


class DropArea(QFrame):
    def __init__(self, callback, parent=None):
        super().__init__(parent)
        self.callback = callback
        self.setAcceptDrops(True)
        self.setObjectName("DropArea")
        layout = QVBoxLayout(self)
        self.label = QLabel("Drop LLM ZIP here")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint = QLabel("or click to select a ZIP")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()
        layout.addWidget(self.label)
        layout.addWidget(self.hint)
        layout.addStretch()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.callback(None)
        super().mousePressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            if url.isLocalFile() and url.toLocalFile().lower().endswith(".zip"):
                self.callback(Path(url.toLocalFile()))
                event.acceptProposedAction()
                return
        event.ignore()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LLM Drop-In")
        self.resize(860, 700)
        self.settings = load_settings()
        self.project = Path(self.settings.get("project", "")
                            ) if self.settings.get("project") else None
        self._build()
        self._refresh()

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        top = QHBoxLayout()
        top.addWidget(QLabel("<h2>LLM Drop-In</h2>"))
        top.addStretch()
        change = QPushButton("Change project")
        change.clicked.connect(lambda: self.choose_project())
        top.addWidget(change)
        layout.addLayout(top)

        layout.addWidget(QLabel("CURRENT PROJECT"))
        project_row = QHBoxLayout()
        self.project_edit = QLineEdit()
        self.project_edit.setReadOnly(True)
        project_row.addWidget(self.project_edit, 1)
        choose = QPushButton("Select…")
        choose.clicked.connect(lambda: self.choose_project())
        project_row.addWidget(choose)
        layout.addLayout(project_row)

        self.drop = DropArea(self.receive_zip)
        self.drop.setMinimumHeight(210)
        layout.addWidget(self.drop)

        layout.addWidget(QLabel("RECENT IMPORTS"))
        self.history = QListWidget()
        self.history.setMinimumHeight(180)
        layout.addWidget(self.history)
        self.status = QLabel()
        layout.addWidget(self.status)

        self.setStyleSheet('''
            QMainWindow { background: #f4f5f7; }
            QLineEdit, QListWidget, QFrame#DropArea { background: white; border: 1px solid #d5d8dc; border-radius: 10px; }
            QFrame#DropArea { border: 2px dashed #aeb4bb; }
            QLabel { color: #1f2328; }
            QPushButton { padding: 8px 14px; }
        ''')

    def _refresh(self):
        if self.project and self.project.exists() and self.project.is_dir():
            self.project_edit.setText(str(self.project))
            self.status.setText("Ready")
        else:
            self.project = None
            self.project_edit.setText("No project selected")
            self.status.setText("Select a project folder to begin.")
        self.history.clear()
        for entry in load_history()[:15]:
            ts = entry.get("timestamp", "").replace("T", " ")
            name = Path(entry.get("zip", "")).name
            msg = f"✓  {name}    {ts}    {entry.get('files', 0)} files ({entry.get('replaced', 0)} replaced, {entry.get('new', 0)} new)"
            self.history.addItem(QListWidgetItem(msg))

    def choose_project(self):
        path = QFileDialog.getExistingDirectory(self, "Select project folder", str(
            self.project) if self.project else str(Path.home()))
        if not path:
            return
        self.project = Path(path)
        self.settings["project"] = str(self.project)
        save_settings(self.settings)
        self._refresh()

    def receive_zip(self, path: Path | None):
        if path is None:
            path_str, _ = QFileDialog.getOpenFileName(
                self, "Select LLM ZIP", str(Path.home()), "ZIP files (*.zip)")
            if not path_str:
                return
            path = Path(path_str)
        if not self.project or not self.project.exists():
            QMessageBox.warning(self, "No project",
                                "Select a project folder first.")
            return
        try:
            entries, candidates = analyze(path, self.project)
        except Exception as exc:
            QMessageBox.critical(self, "Could not analyze ZIP", str(exc))
            return
        if not candidates:
            QMessageBox.warning(self, "No usable structure",
                                "The ZIP does not contain any safe file paths that can be mapped into this project.")
            return

        best = candidates[0]
        # Ambiguous when the top candidates map exactly the same files but use different roots.
        ties = [c for c in candidates if (c.mapped_count, c.new_count, round(
            c.score, 6)) == (best.mapped_count, best.new_count, round(best.score, 6))]
        if len(ties) > 1 and best.prefix != ties[1].prefix:
            QMessageBox.warning(self, "Ambiguous ZIP structure",
                                "More than one ZIP root fits the project equally well. The import was not applied.")
            return
        try:
            plan = make_plan(entries, best.prefix, self.project)
        except Exception as exc:
            QMessageBox.critical(self, "Unsafe import", str(exc))
            return
        if not plan:
            QMessageBox.warning(self, "Nothing to import",
                                "No files were found after the detected ZIP root.")
            return

        dialog = ImportPreviewDialog(
            self.project.name, best.prefix, plan, self)
        # Better list rendering with paths relative to project.
        dialog.list.clear()
        for item in plan:
            rel = item.target_path.relative_to(self.project).as_posix()
            dialog.list.addItem(
                f"{'REPLACE' if item.action == 'replace' else 'NEW':7}  {rel}")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            apply_import(path, plan, best.prefix)
            add_history(history_entry(path, self.project, plan))
            self.status.setText(f"Imported {len(plan)} files from {path.name}")
            self._refresh()
            self.status.setText(f"Imported {len(plan)} files from {path.name}")
        except Exception as exc:
            QMessageBox.critical(
                self, "Import failed", f"The import stopped because of an error:\n\n{exc}")
