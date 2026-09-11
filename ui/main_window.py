from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QVBoxLayout, QWidget, QListWidget, QListWidgetItem,
    QDialog, QProgressBar, QAbstractItemView, QScrollBar,
)

from core.importer import apply_import, enrich_plan_hashes, history_entry, make_plan, undo_import
from core.settings import add_history, load_history, load_settings, save_settings, update_history_entry
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
    succeeded = Signal(int, object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, path: Path, project: Path, plan):
        super().__init__()
        self.path = path
        self.project = project
        self.plan = plan

    def run(self):
        try:
            applied, transaction_id = apply_import(
                self.path, self.plan, project_root=self.project)
            self.succeeded.emit(applied, transaction_id)
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
        layout.setContentsMargins(24, 28, 24, 28)
        layout.setSpacing(5)

        self.title = QLabel("Drop an LLM ZIP here")
        self.title.setObjectName("DropTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint = QLabel("or click to browse for a .zip archive")
        self.hint.setObjectName("DropHint")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()
        layout.addWidget(self.title)
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
        self.resize(940, 760)
        self.setMinimumSize(760, 640)
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
        layout.setContentsMargins(30, 26, 30, 26)
        layout.setSpacing(18)

        header = QHBoxLayout()
        header.setSpacing(16)
        title_block = QVBoxLayout()
        title_block.setSpacing(3)
        title = QLabel("LLM Drop-In")
        title.setObjectName("WindowTitle")
        subtitle = QLabel(
            "Review generated changes before they touch your project.")
        subtitle.setObjectName("WindowSubtitle")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        header.addLayout(title_block, 1)
        layout.addLayout(header)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(16)

        main_column = QVBoxLayout()
        main_column.setContentsMargins(0, 0, 0, 0)
        main_column.setSpacing(14)

        project_card = QFrame()
        project_card.setObjectName("SectionCard")
        project_layout = QVBoxLayout(project_card)
        project_layout.setContentsMargins(18, 16, 18, 16)
        project_layout.setSpacing(10)
        project_header = QHBoxLayout()
        project_title = QLabel("Project")
        project_title.setObjectName("CardTitle")
        project_header.addWidget(project_title)
        project_header.addStretch()
        project_hint = QLabel("The ZIP is matched against this folder.")
        project_hint.setObjectName("CardHint")
        project_header.addWidget(project_hint)
        project_layout.addLayout(project_header)
        project_row = QHBoxLayout()
        project_row.setSpacing(10)
        self.project_edit = QLineEdit()
        self.project_edit.setReadOnly(True)
        self.project_edit.setPlaceholderText("No project selected")
        self.project_edit.setMinimumHeight(40)
        project_row.addWidget(self.project_edit, 1)
        choose = QPushButton("Choose folder")
        choose.setObjectName("SecondaryButton")
        choose.setMinimumHeight(40)
        choose.clicked.connect(self.choose_project)
        project_row.addWidget(choose)
        project_layout.addLayout(project_row)
        main_column.addWidget(project_card)

        import_card = QFrame()
        import_card.setObjectName("SectionCard")
        import_layout = QVBoxLayout(import_card)
        import_layout.setContentsMargins(18, 16, 18, 16)
        import_layout.setSpacing(10)
        import_header = QHBoxLayout()
        import_title = QLabel("Import")
        import_title.setObjectName("CardTitle")
        import_header.addWidget(import_title)
        import_header.addStretch()
        import_hint = QLabel(
            "Drop a ZIP to inspect it before applying anything.")
        import_hint.setObjectName("CardHint")
        import_header.addWidget(import_hint)
        import_layout.addLayout(import_header)
        self.drop = DropArea(self.receive_zip)
        self.drop.setMinimumHeight(220)
        import_layout.addWidget(self.drop)
        main_column.addWidget(import_card, 1)

        status_bar = QFrame()
        status_bar.setObjectName("AppStatusBar")
        status_layout = QHBoxLayout(status_bar)
        status_layout.setContentsMargins(2, 2, 2, 0)
        status_layout.setSpacing(9)
        self.status_dot = QLabel("●")
        self.status_dot.setObjectName("StatusDot")
        status_layout.addWidget(self.status_dot)
        self.status = QLabel("Select a project folder to get started.")
        self.status.setObjectName("AppStatus")
        self.status.setWordWrap(True)
        status_layout.addWidget(self.status, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(4)
        self.progress.setTextVisible(False)
        self.progress.setMaximumWidth(170)
        self.progress.hide()
        status_layout.addWidget(self.progress)
        main_column.addWidget(status_bar)

        history_card = QFrame()
        history_card.setObjectName("HistorySidebar")
        history_card.setMinimumWidth(290)
        history_card.setMaximumWidth(360)
        history_layout = QVBoxLayout(history_card)
        history_layout.setContentsMargins(16, 16, 16, 14)
        history_layout.setSpacing(10)

        history_header = QVBoxLayout()
        history_header.setSpacing(2)
        history_title = QLabel("Recent imports")
        history_title.setObjectName("CardTitle")
        history_header.addWidget(history_title)
        history_hint = QLabel(
            "Select an import to inspect its status.")
        history_hint.setObjectName("CardHint")
        history_hint.setWordWrap(True)
        history_header.addWidget(history_hint)
        history_layout.addLayout(history_header)

        self.history = QListWidget()
        self.history.itemSelectionChanged.connect(
            self._history_selection_changed)
        # Double-click intentionally has no destructive action.
        self.history.setFrameShape(QFrame.Shape.NoFrame)
        self.history.setAlternatingRowColors(False)
        self.history.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.history.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.history.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.history.setUniformItemSizes(False)
        history_layout.addWidget(self.history, 1)

        self.history_summary = QLabel("No imports yet")
        self.history_summary.setObjectName("HistorySummary")
        history_layout.addWidget(self.history_summary)

        self.undo = QPushButton("Undo selected import")
        self.undo.setObjectName("SecondaryButton")
        self.undo.setMinimumHeight(38)
        self.undo.setEnabled(False)
        self.undo.clicked.connect(self._undo_history_item)
        history_layout.addWidget(self.undo)

        body_layout.addLayout(main_column, 1)
        body_layout.addWidget(history_card, 0)
        layout.addWidget(body, 1)

        self.setStyleSheet("""
            QMainWindow { background: #f4f6f7; }
            QWidget { color: #1f272d; font-size: 10pt; }
            QFrame#SectionCard, QFrame#HistorySidebar {
                background: #ffffff;
                border: 1px solid #dce2e5;
                border-radius: 13px;
            }
            QFrame#HistorySidebar {
                min-width: 290px;
            }
            QLabel#WindowTitle { font-size: 22pt; font-weight: 700; color: #172027; }
            QLabel#WindowSubtitle { color: #707a82; font-size: 10pt; }
            QLabel#CardTitle { font-size: 11pt; font-weight: 700; color: #273238; }
            QLabel#CardHint, QLabel#DropHint { color: #778188; }
            QLabel#InlineStatus { color: #4f5b61; }
            QLabel#HistorySummary { color: #7a848b; }
            QLabel#StatusDot { color: #4b967f; font-size: 8pt; }
            QLabel#AppStatus { color: #5d676e; }
            QFrame#AppStatusBar { background: transparent; }
            QLineEdit {
                background: #fbfcfc;
                border: 1px solid #d5dde0;
                border-radius: 8px;
                padding: 7px 10px;
                selection-background-color: #dcefe9;
                selection-color: #17211f;
            }
            QLineEdit:focus { border: 1px solid #6d9f93; }
            QPushButton {
                background: #276b5f;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 14px;
                font-weight: 700;
            }
            QPushButton:hover { background: #215b51; }
            QPushButton:pressed { background: #1b4e46; }
            QPushButton:disabled { background: #d9dddf; color: #8a9298; }
            QPushButton#SecondaryButton {
                background: #f0f3f4;
                color: #30393f;
                border: 1px solid #d6dde0;
            }
            QPushButton#SecondaryButton:hover { background: #e7ebed; }
            QFrame#DropArea {
                background: #fbfdfc;
                border: 1.5px dashed #8eb8ad;
                border-radius: 12px;
            }
            QFrame#DropArea:hover { background: #f3faf8; border-color: #5d9588; }
            QLabel#DropTitle { font-size: 13pt; font-weight: 700; color: #2d6256; }
            QListWidget {
                background: #fbfcfc;
                border: 1px solid #e1e6e8;
                border-radius: 9px;
                padding: 2px;
                outline: none;
            }
            QListWidget::item {
                padding: 10px 9px;
                border-bottom: 1px solid #edf0f1;
            }
            QListWidget::item:selected {
                background: #e8f3f0;
                color: #17211f;
                border-radius: 6px;
            }
            QScrollBar:vertical {
                background: #f3f5f6;
                width: 12px;
                margin: 3px 2px 3px 2px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical {
                background: #b4c2c7;
                min-height: 24px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical:hover { background: #95a6ad; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
                border: none;
                height: 0px;
            }
            QProgressBar { background: transparent; border: none; border-radius: 3px; }
            QProgressBar::chunk { background: #5b9a8b; border-radius: 3px; }
        """)

    def _set_status(self, text: str, *, active: bool = False, error: bool = False):
        self.status.setText(text)
        if error:
            self.status_dot.setStyleSheet("color:#bd5a5a;font-size:8pt;")
        elif active:
            self.status_dot.setStyleSheet("color:#6b8fb2;font-size:8pt;")
        else:
            self.status_dot.setStyleSheet("color:#4b967f;font-size:8pt;")

    def _refresh(self):
        if self.project and self.project.exists() and self.project.is_dir():
            self.project_edit.setText(str(self.project))
            if not self.progress.isVisible():
                self._set_status("Ready")
        else:
            self.project = None
            self.project_edit.setText("")
            if not self.progress.isVisible():
                self._set_status("Select a project folder to get started.")
        self.history.clear()
        entries = load_history()[:15]
        self.history_summary.setText(
            f"{len(entries)} recent import{'s' if len(entries) != 1 else ''}"
            if entries else "No imports yet"
        )
        for entry in entries:
            ts = entry.get("timestamp", "").replace("T", " ")
            name = Path(entry.get("zip", "")).name
            counts = (
                f"{entry.get('files', 0)} files · "
                f"{entry.get('replaced', 0)} modified · "
                f"{entry.get('new', 0)} new · "
                f"{entry.get('unchanged', 0)} unchanged"
            )
            state = "UNDONE" if entry.get("undone") else "COMPLETED"
            msg = f"{name}\n{counts}  ·  {ts}  ·  {state}"
            item = QListWidgetItem(msg)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.history.addItem(item)
        self._history_selection_changed()

    def _history_selection_changed(self):
        row = self.history.currentItem()
        entry = row.data(Qt.ItemDataRole.UserRole) if row else None
        can_undo = isinstance(entry, dict) and not entry.get(
            "undone") and bool(entry.get("transaction_id"))
        self.undo.setEnabled(can_undo and self.worker_thread is None)

    def choose_project(self):
        path = QFileDialog.getExistingDirectory(
            self,
            "Select project folder",
            str(self.project) if self.project else str(Path.home()),
        )
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
        project = self.project
        if project is None or not project.exists() or not project.is_dir():
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
        self._set_status(f"Analyzing {path.name}…", active=True)
        self.worker_thread = QThread(self)
        self.worker = ZipWorker(path, project)
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
        self._history_selection_changed()

    def _analysis_failed(self, message: str):
        QMessageBox.critical(self, "Could not analyze ZIP", message)
        self._set_status("Analysis failed", error=True)

    def _analysis_ready(self, path, entries, candidates):
        project = self.project
        if project is None or not project.exists() or not project.is_dir():
            QMessageBox.warning(self, "No project",
                                "Select a project folder first.")
            return
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
            self._set_status("Waiting for another archive")
            return
        try:
            plan = make_plan(entries, best.prefix, project)
            plan = enrich_plan_hashes(path, plan)
        except Exception as exc:
            QMessageBox.critical(self, "Unsafe import", str(exc))
            self._set_status("Import could not be prepared", error=True)
            return
        if not plan:
            QMessageBox.warning(self, "Nothing to import",
                                "No files were found after the detected ZIP root.")
            self._set_status("Nothing to import")
            return
        dialog = ImportPreviewDialog(
            project, project.name, best.prefix, plan, path, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._set_status("Import cancelled")
            self._pending_zip = None
            return
        selected = dialog.selected
        self._pending_zip = path
        self._pending_plan = selected
        self.drop.setEnabled(False)
        self.progress.show()
        self._set_status(f"Applying {len(selected)} files…", active=True)
        self.worker_thread = QThread(self)
        self.worker = ImportWorker(path, project, selected)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.succeeded.connect(self._import_succeeded)
        self.worker.failed.connect(self._import_failed)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self._worker_finished)
        self.worker_thread.start()

    def _import_succeeded(self, applied: int, transaction_id):
        path = self._pending_zip
        plan = self._pending_plan or []
        if path and self.project:
            add_history(history_entry(path, self.project,
                        plan, transaction_id=transaction_id))
            self._refresh()
            self._set_status(f"Imported {applied} files from {path.name}")
        self._pending_zip = None
        self._pending_plan = None

    def _undo_history_item(self):
        if self.worker_thread is not None:
            return
        row = self.history.currentItem()
        if row is None:
            return
        entry = row.data(Qt.ItemDataRole.UserRole)
        if not isinstance(entry, dict) or entry.get("undone"):
            return
        transaction_id = entry.get("transaction_id")
        if not isinstance(transaction_id, str) or not transaction_id:
            return
        if QMessageBox.question(
            self,
            "Undo import",
            "Undo this import? The current files must still match the imported versions.",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            restored = undo_import(transaction_id)
        except Exception as exc:
            QMessageBox.critical(self, "Cannot undo import", str(exc))
            self._set_status("Undo failed", error=True)
            return
        update_history_entry(entry.get("id", ""), undone=True)
        self._refresh()
        self._set_status(f"Undid import: restored {restored} files")

    def _import_failed(self, message: str):
        QMessageBox.critical(
            self,
            "Import failed",
            f"The import was rolled back because of an error:\n\n{message}",
        )
        self._set_status("Import rolled back", error=True)
        self._pending_zip = None
        self._pending_plan = None
