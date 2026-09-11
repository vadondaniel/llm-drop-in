from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QVBoxLayout, QWidget, QListWidget, QListWidgetItem,
    QDialog,
    QProgressBar,
)

from core.importer import apply_import, enrich_plan_hashes, history_entry, make_plan
from core.settings import add_history, load_history, load_settings, save_settings
from core.zip_analyzer import analyze
from ui.import_preview import ImportPreviewDialog


class ZipWorker(QObject):
    analyzed = Signal(object, object, object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, path: Path, project: Path):
        super().__init__()
        self.path = path
        self.project = project

    def run(self):
        try:
            entries, candidates = analyze(self.path, self.project)
            self.analyzed.emit(self.path, entries, candidates)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class ImportWorker(QObject):
    succeeded = Signal(int)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, path: Path, project: Path, plan):
        super().__init__()
        self.path = path
        self.project = project
        self.plan = plan

    def run(self):
        try:
            applied = apply_import(self.path, self.plan)
            self.succeeded.emit(applied)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


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
        if any(url.isLocalFile() and url.toLocalFile().lower().endswith(".zip") for url in event.mimeData().urls()):
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
        self.resize(900, 720)
        self.settings = load_settings()
        self.project = Path(self.settings.get("project", "")
                            ) if self.settings.get("project") else None
        self.worker_thread: QThread | None = None
        self.worker: QObject | None = None
        self._pending_zip: Path | None = None
        self._pending_plan = None
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
        change.clicked.connect(self.choose_project)
        top.addWidget(change)
        layout.addLayout(top)

        layout.addWidget(QLabel("CURRENT PROJECT"))
        project_row = QHBoxLayout()
        self.project_edit = QLineEdit()
        self.project_edit.setReadOnly(True)
        project_row.addWidget(self.project_edit, 1)
        choose = QPushButton("Select…")
        choose.clicked.connect(self.choose_project)
        project_row.addWidget(choose)
        layout.addLayout(project_row)

        self.drop = DropArea(self.receive_zip)
        self.drop.setMinimumHeight(230)
        layout.addWidget(self.drop)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)

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
            if not self.progress.isVisible():
                self.status.setText("Ready")
        else:
            self.project = None
            self.project_edit.setText("No project selected")
            self.status.setText("Select a project folder to begin.")
        self.history.clear()
        for entry in load_history()[:15]:
            ts = entry.get("timestamp", "").replace("T", " ")
            name = Path(entry.get("zip", "")).name
            msg = (
                f"✓  {name}    {ts}    {entry.get('files', 0)} files "
                f"({entry.get('replaced', 0)} modified, {entry.get('new', 0)} new, "
                f"{entry.get('unchanged', 0)} unchanged)"
            )
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
        if self.worker_thread is not None:
            return
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
        if path.suffix.lower() != ".zip":
            QMessageBox.warning(self, "Invalid file",
                                "Please select a ZIP archive.")
            return
        self._pending_zip = path
        self.drop.setEnabled(False)
        self.progress.show()
        self.status.setText(f"Analyzing {path.name}…")
        self.worker_thread = QThread(self)
        self.worker = ZipWorker(path, self.project)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.analyzed.connect(self._analysis_ready)
        self.worker.failed.connect(self._analysis_failed)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self._worker_finished)
        self.worker_thread.start()

    def _worker_finished(self):
        if self.worker:
            self.worker.deleteLater()
        if self.worker_thread:
            self.worker_thread.deleteLater()
        self.worker = None
        self.worker_thread = None
        self.progress.hide()
        self.drop.setEnabled(True)

    def _analysis_failed(self, message: str):
        QMessageBox.critical(self, "Could not analyze ZIP", message)
        self.status.setText("Ready")

    def _analysis_ready(self, path, entries, candidates):
        if not candidates:
            QMessageBox.warning(self, "No usable structure",
                                "The ZIP does not contain any safe file paths that can be mapped into this project.")
            return
        best = candidates[0]
        ties = [c for c in candidates if (
            c.considered_count, c.unchanged_count, c.mapped_count, round(
                c.score, 6)
        ) == (
            best.considered_count, best.unchanged_count, best.mapped_count, round(
                best.score, 6)
        )]
        if len(ties) > 1 and best.prefix != ties[1].prefix:
            QMessageBox.warning(self, "Ambiguous ZIP structure",
                                "More than one ZIP root fits the project equally well. The import was not applied.")
            return
        try:
            project = self.project
            if project is None:
                QMessageBox.warning(self, "No project",
                                    "Select a project folder first.")
                return
            plan = make_plan(entries, best.prefix, project)
            plan = enrich_plan_hashes(path, plan)
        except Exception as exc:
            QMessageBox.critical(self, "Unsafe import", str(exc))
            return
        if not plan:
            QMessageBox.warning(self, "Nothing to import",
                                "No files were found after the detected ZIP root.")
            return
        dialog = ImportPreviewDialog(
            project, project.name, best.prefix, plan, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.status.setText("Ready")
            return
        selected = dialog.selected
        self._pending_zip = path
        self._pending_plan = selected
        self.drop.setEnabled(False)
        self.progress.show()
        self.status.setText(f"Applying {len(selected)} files transactionally…")
        self.worker_thread = QThread(self)
        self.worker = ImportWorker(path, project, selected)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.succeeded.connect(self._import_succeeded)
        self.worker.failed.connect(self._import_failed)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self._worker_finished)
        self.worker_thread.start()

    def _import_succeeded(self, applied: int):
        path = self._pending_zip
        plan = self._pending_plan or []
        if path and self.project:
            add_history(history_entry(path, self.project, plan))
            self._refresh()
            self.status.setText(f"Imported {applied} files from {path.name}")
        self._pending_zip = None
        self._pending_plan = None

    def _import_failed(self, message: str):
        QMessageBox.critical(
            self, "Import failed", f"The import was rolled back because of an error:\n\n{message}")
        self.status.setText("Import rolled back")
        self._pending_zip = None
        self._pending_plan = None
