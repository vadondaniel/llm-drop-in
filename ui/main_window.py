from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QVBoxLayout, QWidget, QListWidget, QListWidgetItem,
    QDialog, QProgressBar, QAbstractItemView, QComboBox,
)

from core.importer import apply_import, cleanup_orphaned_transactions, enrich_plan_hashes, history_entry, make_plan, undo_import
from core.settings import add_history, load_history, load_settings, save_settings, update_history_entry, remember_project
from core.zip_analyzer import analyze_variants
from ui.import_preview import ImportPreviewDialog
from ui.theme import apply_theme, set_style_class


class ZipWorker(QObject):
    analyzed = Signal(object, object, object, object, object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, path: Path, project: Path):
        super().__init__()
        self.path = path
        self.project = project

    def run(self):
        try:
            filtered, included = analyze_variants(self.path, self.project)
            filtered_entries, filtered_candidates = filtered
            included_entries, included_candidates = included
            self.analyzed.emit(
                self.path,
                filtered_entries,
                filtered_candidates,
                included_entries,
                included_candidates,
            )
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
        set_style_class(self, "drop-area")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 28, 24, 28)
        layout.setSpacing(5)

        self.title = QLabel("Drop an LLM ZIP here")
        self.title.setObjectName("DropTitle")
        set_style_class(self.title, "drop-title")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint = QLabel("or click to browse for a .zip archive")
        self.hint.setObjectName("DropHint")
        set_style_class(self.hint, "hint")
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
        if not self._valid_project(self.project):
            self.project = next((Path(value) for value in self.settings.get(
                "recent_projects", []) if self._valid_project(Path(value))), None)
            if self.project:
                remember_project(self.settings, self.project)
                save_settings(self.settings)
        self.worker_thread: QThread | None = None
        self.worker: QObject | None = None
        self._pending_zip: Path | None = None
        self._pending_plan = None
        self._cleanup_stale_transactions()
        self._build()
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.paletteChanged.connect(self._theme_changed)
        self._theme_changed()
        self._refresh()

    @staticmethod
    def _valid_project(path: Path | None) -> bool:
        return path is not None and path.exists() and path.is_dir()

    def _refresh_recent_projects(self) -> None:
        if not hasattr(self, "project_picker"):
            return
        project = self.project
        current = ""
        if project is not None and self._valid_project(project):
            current = str(project.resolve())
        values = []
        seen = set()
        for value in self.settings.get("recent_projects", []):
            if not isinstance(value, str):
                continue
            path = Path(value)
            if not self._valid_project(path):
                continue
            resolved = str(path.resolve())
            key = resolved.casefold()
            if key in seen:
                continue
            seen.add(key)
            values.append(resolved)
        if current and current.casefold() not in seen:
            values.insert(0, current)
        self.project_picker.blockSignals(True)
        self.project_picker.clear()
        self.project_picker.addItems(values)
        if current:
            index = self.project_picker.findText(current)
            if index >= 0:
                self.project_picker.setCurrentIndex(index)
        self.project_picker.blockSignals(False)

    def _select_recent_project(self, index: int) -> None:
        if index < 0:
            return
        value = self.project_picker.itemText(index)
        if not value:
            return
        path = Path(value)
        if not self._valid_project(path):
            self._refresh_recent_projects()
            return
        self.project = path.resolve()
        remember_project(self.settings, self.project)
        save_settings(self.settings)
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
        set_style_class(title, "title")
        subtitle = QLabel(
            "Review generated changes before they touch your project.")
        subtitle.setObjectName("WindowSubtitle")
        set_style_class(subtitle, "subtitle")
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
        set_style_class(project_card, "surface")
        project_layout = QVBoxLayout(project_card)
        project_layout.setContentsMargins(18, 16, 18, 16)
        project_layout.setSpacing(10)
        project_header = QHBoxLayout()
        project_title = QLabel("Project")
        project_title.setObjectName("CardTitle")
        set_style_class(project_title, "card-title")
        project_header.addWidget(project_title)
        project_header.addStretch()
        project_hint = QLabel("The ZIP is matched against this folder.")
        project_hint.setObjectName("CardHint")
        set_style_class(project_hint, "hint")
        project_header.addWidget(project_hint)
        project_layout.addLayout(project_header)
        project_row = QHBoxLayout()
        project_row.setSpacing(10)
        self.project_picker = QComboBox()
        set_style_class(self.project_picker, "input")
        self.project_picker.setMinimumHeight(40)
        self.project_picker.setPlaceholderText("No recent project")
        self.project_picker.currentIndexChanged.connect(
            self._select_recent_project)
        project_row.addWidget(self.project_picker, 1)
        choose = QPushButton("Choose folder")
        choose.setObjectName("SecondaryButton")
        set_style_class(choose, "secondary-button")
        choose.setMinimumHeight(40)
        choose.clicked.connect(self.choose_project)
        project_row.addWidget(choose)
        project_layout.addLayout(project_row)
        main_column.addWidget(project_card)
        self._refresh_recent_projects()

        import_card = QFrame()
        import_card.setObjectName("SectionCard")
        set_style_class(import_card, "surface")
        import_layout = QVBoxLayout(import_card)
        import_layout.setContentsMargins(18, 16, 18, 16)
        import_layout.setSpacing(10)
        import_header = QHBoxLayout()
        import_title = QLabel("Import")
        import_title.setObjectName("CardTitle")
        set_style_class(import_title, "card-title")
        import_header.addWidget(import_title)
        import_header.addStretch()
        import_hint = QLabel(
            "Drop a ZIP to inspect it before applying anything.")
        import_hint.setObjectName("CardHint")
        set_style_class(import_hint, "hint")
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
        set_style_class(self.status_dot, "status-dot")
        status_layout.addWidget(self.status_dot)
        self.status = QLabel("Select a project folder to get started.")
        self.status.setObjectName("AppStatus")
        set_style_class(self.status, "app-status")
        self.status.setWordWrap(True)
        status_layout.addWidget(self.status, 1)
        self.progress = QProgressBar()
        set_style_class(self.progress, "progress")
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(4)
        self.progress.setTextVisible(False)
        self.progress.setMaximumWidth(170)
        self.progress.hide()
        status_layout.addWidget(self.progress)
        main_column.addWidget(status_bar)

        history_card = QFrame()
        history_card.setObjectName("HistorySidebar")
        set_style_class(history_card, "surface-sidebar")
        history_card.setMinimumWidth(290)
        history_card.setMaximumWidth(360)
        history_layout = QVBoxLayout(history_card)
        history_layout.setContentsMargins(16, 16, 16, 14)
        history_layout.setSpacing(10)

        history_header = QVBoxLayout()
        history_header.setSpacing(2)
        history_title = QLabel("Recent imports")
        history_title.setObjectName("CardTitle")
        set_style_class(history_title, "card-title")
        history_header.addWidget(history_title)
        history_hint = QLabel(
            "Imports for the current project.")
        history_hint.setObjectName("CardHint")
        set_style_class(history_hint, "hint")
        history_hint.setWordWrap(True)
        history_header.addWidget(history_hint)
        history_layout.addLayout(history_header)

        self.history = QListWidget()
        set_style_class(self.history, "list")
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
        set_style_class(self.history_summary, "subtle")
        history_layout.addWidget(self.history_summary)

        self.undo = QPushButton("Undo selected import")
        self.undo.setObjectName("SecondaryButton")
        set_style_class(self.undo, "secondary-button")
        self.undo.setMinimumHeight(38)
        self.undo.setEnabled(False)
        self.undo.clicked.connect(self._undo_history_item)
        history_layout.addWidget(self.undo)

        body_layout.addLayout(main_column, 1)
        body_layout.addWidget(history_card, 0)
        layout.addWidget(body, 1)

    def _theme_changed(self, _palette=None):
        self._theme_tokens = apply_theme(self)
        self._apply_status_dot_style(self._theme_tokens)

    def _apply_status_dot_style(self, tokens):
        style_class = self.status_dot.property("styleClass")
        if style_class == "status-dot-error":
            color = tokens.danger_fg
        elif style_class == "status-dot-active":
            color = tokens.info_fg
        else:
            color = tokens.success_fg
        palette = self.status_dot.palette()
        palette.setColor(palette.ColorRole.WindowText, color)
        self.status_dot.setPalette(palette)

    def _cleanup_stale_transactions(self):
        history = load_history()
        active_ids = {
            entry.get("transaction_id")
            for entry in history
            if isinstance(entry, dict) and entry.get("transaction_id") and not entry.get("undone")
        }
        cleanup_orphaned_transactions({str(value)
                                      for value in active_ids if value})

    def _current_project_key(self) -> str | None:
        if self.project is None or not self._valid_project(self.project):
            return None
        return str(self.project.resolve()).casefold()

    def _history_for_current_project(self) -> list[dict]:
        project_key = self._current_project_key()
        if project_key is None:
            return []
        result = []
        for entry in load_history():
            if not isinstance(entry, dict):
                continue
            raw_project = entry.get("project")
            if not isinstance(raw_project, str) or not raw_project:
                continue
            try:
                entry_key = str(Path(raw_project).resolve()).casefold()
            except OSError:
                continue
            if entry_key == project_key:
                result.append(entry)
        return result

    def _set_status(self, text: str, *, active: bool = False, error: bool = False):
        self.status.setText(text)
        if error:
            set_style_class(self.status_dot, "status-dot-error")
        elif active:
            set_style_class(self.status_dot, "status-dot-active")
        else:
            set_style_class(self.status_dot, "status-dot")
        tokens = getattr(self, "_theme_tokens", None)
        if tokens is not None:
            self._apply_status_dot_style(tokens)

    def _refresh(self):
        if self.project and self.project.exists() and self.project.is_dir():
            self._refresh_recent_projects()
            if not self.progress.isVisible():
                self._set_status("Ready")
        else:
            self.project = None
            self._refresh_recent_projects()
            if not self.progress.isVisible():
                self._set_status("Select a project folder to get started.")
        self.history.clear()
        entries = self._history_for_current_project()[:15]
        self.history_summary.setText(
            f"{len(entries)} recent import{'s' if len(entries) != 1 else ''}"
            if entries
            else ("No imports for this project" if self.project else "No project selected")
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
        self.project = Path(path).resolve()
        remember_project(self.settings, self.project)
        save_settings(self.settings)
        self._refresh_recent_projects()
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

    def _analysis_ready(self, path, entries, candidates, included_entries, included_candidates):
        project = self.project
        if project is None or not project.exists() or not project.is_dir():
            QMessageBox.warning(self, "No project",
                                "Select a project folder first.")
            return
        detection_candidates = candidates or included_candidates
        if not detection_candidates:
            QMessageBox.warning(self, "No usable structure",
                                "The ZIP does not contain any safe file paths that can be mapped into this project.")
            return
        best = detection_candidates[0]
        ties = [c for c in detection_candidates if (
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
            plan = enrich_plan_hashes(
                path, make_plan(entries, best.prefix, project)
            )
            included_plan = enrich_plan_hashes(
                path, make_plan(included_entries, best.prefix, project)
            )
            filtered_archive_paths = {item.archive_path for item in plan}
            gitignored_plan = [
                item for item in included_plan
                if item.archive_path not in filtered_archive_paths
            ]
        except Exception as exc:
            QMessageBox.critical(self, "Unsafe import", str(exc))
            self._set_status("Import could not be prepared", error=True)
            return
        if not included_plan:
            QMessageBox.warning(self, "Nothing to import",
                                "No files were found after the detected ZIP root.")
            self._set_status("Nothing to import")
            return
        dialog = ImportPreviewDialog(
            project, project.name, best.prefix, plan, included_plan,
            gitignored_plan, path, self)
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
            self._cleanup_stale_transactions()
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
        self._cleanup_stale_transactions()
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
