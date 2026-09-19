from __future__ import annotations

from html import escape
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal, QMimeData, QUrl, QPoint
from PySide6.QtGui import QAction, QDrag, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QVBoxLayout, QWidget, QListWidget, QListWidgetItem,
    QDialog, QProgressBar, QAbstractItemView, QComboBox, QMenu,
)

from core.importer import apply_import, cleanup_orphaned_transactions, history_entry, make_plan, undo_import
from core.settings import add_history, load_history, load_settings, save_settings, update_history_entry, remember_project
from core.zip_analyzer import ImportAnalysis, analyze_for_import
from core.packer import (
    PACKAGE_HEAD,
    PACKAGE_STAGED,
    PACKAGE_WORKTREE,
    PackageResult,
    package_project,
)
from ui.import_preview import ImportPreviewDialog
from ui.theme import apply_theme, set_style_class


class ZipWorker(QObject):
    analyzed = Signal(object, object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, path: Path, project: Path):
        super().__init__()
        self.path = path
        self.project = project

    def run(self):
        try:
            analysis = analyze_for_import(self.path, self.project)
            self.analyzed.emit(self.path, analysis)
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


class PackageWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, project: Path, scope: str):
        super().__init__()
        self.project = project
        self.scope = scope

    def run(self):
        try:
            result = package_project(self.project, scope=self.scope)
            self.succeeded.emit(result)
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
        self.title.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setToolTip("Drop a ZIP here, or click to choose one.")

        layout.addStretch()
        layout.addWidget(self.title)
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


class DraggableArchive(QFrame):
    """A small file surface that can be dragged into another application."""

    def __init__(self, result: PackageResult, parent=None):
        super().__init__(parent)
        self.result = result
        self._press_pos = None
        self.setObjectName("DraggableArchive")
        set_style_class(self, "surface-alt")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip(
            "Drag this ZIP into the browser chat to upload it. "
            "The file is stored next to the project."
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(2)

        self.name_label = QLabel(result.path.name)
        self.name_label.setObjectName("ArchiveName")
        self.name_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.name_label)

        scope_names = {
            PACKAGE_WORKTREE: "Working tree · tracked + non-ignored untracked files",
            PACKAGE_STAGED: "Staged state · current Git index",
            PACKAGE_HEAD: "HEAD · last committed state",
        }
        metadata = (
            f"{scope_names.get(result.scope, result.scope)} · "
            f"{result.files:,} files · {result.size_bytes / (1024 * 1024):.2f} MB"
        )
        if result.missing:
            metadata += f" · {len(result.missing):,} missing"
        self.metadata_label = QLabel(metadata)
        self.metadata_label.setObjectName("ArchiveMetadata")
        self.metadata_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        set_style_class(self.metadata_label, "subtle")
        layout.addWidget(self.metadata_label)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_pos is None or not (
            event.buttons() & Qt.MouseButton.LeftButton
        ):
            return
        distance = (event.position().toPoint() -
                    self._press_pos).manhattanLength()
        if distance < QApplication.startDragDistance():
            return

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(self.result.path))])
        drag = QDrag(self)
        drag.setMimeData(mime)
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        try:
            drag.exec(Qt.DropAction.CopyAction)
        finally:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self._press_pos = None


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
        self._worker_kind: str | None = None
        self._package_artifact: DraggableArchive | None = None
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
        self._clear_package_artifact()
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
        title_block.addWidget(title)
        header.addLayout(title_block, 1)

        # The built-in QToolButton menu subcontrol gets awkward quickly: its
        # arrow sizing is style-dependent, while the clickable menu-button
        # region is separate from the main text layout. Use a real split
        # button instead so the hit target, padding, and arrow glyph are all
        # explicit and stable.
        self.package_button = QWidget()
        self.package_button.setObjectName("PackageButton")
        set_style_class(self.package_button, "primary-button")
        package_button_layout = QHBoxLayout(self.package_button)
        package_button_layout.setContentsMargins(0, 0, 0, 0)
        package_button_layout.setSpacing(0)

        self.package_main_button = QPushButton("Create ZIP")
        self.package_main_button.setObjectName("PackageMainButton")
        set_style_class(self.package_main_button, "primary-button")
        self.package_main_button.setMinimumHeight(40)
        self.package_main_button.setToolTip(
            "Create a ZIP from the current working tree."
        )
        self.package_main_button.clicked.connect(
            lambda _checked=False: self._create_package(PACKAGE_WORKTREE)
        )
        package_button_layout.addWidget(self.package_main_button)

        self.package_menu_button = QPushButton("▼")
        self.package_menu_button.setObjectName("PackageMenuButton")
        set_style_class(self.package_menu_button, "primary-button")
        self.package_menu_button.setFixedSize(28, 40)
        self.package_menu_button.setToolTip(
            "Choose whether to package the working tree, staged state, or HEAD."
        )
        package_menu = QMenu(self.package_menu_button)
        for scope, text in (
            (PACKAGE_WORKTREE, "Create from working tree"),
            (PACKAGE_STAGED, "Create from staged state"),
            (PACKAGE_HEAD, "Create from HEAD"),
        ):
            action = QAction(text, self)
            action.setData(scope)
            action.triggered.connect(
                lambda _checked=False, scope=scope: self._create_package(scope)
            )
            package_menu.addAction(action)
        self.package_menu_button.clicked.connect(
            lambda _checked=False: package_menu.popup(
                self.package_menu_button.mapToGlobal(
                    QPoint(0, self.package_menu_button.height())
                )
            )
        )
        package_button_layout.addWidget(self.package_menu_button)
        header.addWidget(self.package_button)
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
        import_layout.addLayout(import_header)
        self.drop = DropArea(self.receive_zip)
        self.drop.setMinimumHeight(220)
        import_layout.addWidget(self.drop)
        main_column.addWidget(import_card, 1)

        self.package_artifact_host = QWidget()
        self.package_artifact_layout: QVBoxLayout = QVBoxLayout(
            self.package_artifact_host
        )
        self.package_artifact_layout.setContentsMargins(0, 0, 0, 0)
        self.package_artifact_layout.setSpacing(0)
        self.package_artifact_host.hide()
        main_column.addWidget(self.package_artifact_host)

        status_bar = QFrame()
        status_bar.setObjectName("AppStatusBar")
        status_layout = QHBoxLayout(status_bar)
        status_layout.setContentsMargins(2, 2, 2, 0)
        status_layout.setSpacing(9)
        self.status_dot: QLabel = QLabel("●")
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
        history_card.setMinimumWidth(320)
        history_layout = QVBoxLayout(history_card)
        history_layout.setContentsMargins(16, 16, 16, 14)
        history_layout.setSpacing(10)

        history_header = QVBoxLayout()
        history_header.setSpacing(2)
        history_title = QLabel("Recent imports")
        history_title.setObjectName("CardTitle")
        set_style_class(history_title, "card-title")
        history_header.addWidget(history_title)
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
        self.history.setMouseTracking(True)
        history_layout.addWidget(self.history, 1)

        self.undo = QPushButton("Undo selected import")
        self.undo.setObjectName("SecondaryButton")
        set_style_class(self.undo, "secondary-button")
        self.undo.setMinimumHeight(38)
        self.undo.setEnabled(False)
        self.undo.setToolTip(
            "Revert the selected import while the imported files are still unchanged."
        )
        self.undo.clicked.connect(self._undo_history_item)
        history_layout.addWidget(self.undo)

        body_layout.addLayout(main_column, 3)
        body_layout.addWidget(history_card, 2)
        layout.addWidget(body, 1)

    def _theme_changed(self, _palette=None):
        self._theme_tokens = apply_theme(self)
        app = QApplication.instance()
        tooltip_controller = app.property(
            "tooltipController") if app is not None else None
        if tooltip_controller is not None and hasattr(tooltip_controller, "refresh_theme"):
            tooltip_controller.refresh_theme()
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
            item.setToolTip(
                f"<b>{escape(name)}</b><br>"
                f"{escape(counts)}<br>"
                f"{escape(ts)} · {escape(state)}"
            )
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.history.addItem(item)
        self._history_selection_changed()

    def _history_selection_changed(self):
        row = self.history.currentItem()
        entry = row.data(Qt.ItemDataRole.UserRole) if row else None
        can_undo = isinstance(entry, dict) and not entry.get(
            "undone") and bool(entry.get("transaction_id"))
        self.undo.setEnabled(can_undo and self.worker_thread is None)

    def _clear_package_artifact(self):
        if self._package_artifact is not None:
            layout = self.package_artifact_host.layout()
            if layout is not None:
                layout.removeWidget(self._package_artifact)
            self._package_artifact.setParent(None)
            self._package_artifact.deleteLater()
            self._package_artifact = None
        if hasattr(self, "package_artifact_host"):
            self.package_artifact_host.hide()

    @staticmethod
    def _format_package_status(result: PackageResult) -> str:
        size = result.size_bytes
        if size >= 1024 * 1024:
            size_text = f"{size / (1024 * 1024):.2f} MB"
        elif size >= 1024:
            size_text = f"{size / 1024:.1f} KB"
        else:
            size_text = f"{size} B"
        return f"Created {result.path.name} · {result.files:,} files · {size_text}"

    def _create_package(self, scope: str):
        if self.worker_thread is not None:
            return
        project = self.project
        if project is None or not project.exists() or not project.is_dir():
            QMessageBox.warning(
                self,
                "No project",
                "Select a project folder first.",
            )
            return

        self._clear_package_artifact()
        self._worker_kind = "package"
        self.package_button.setEnabled(False)
        self.drop.setEnabled(False)
        self.progress.show()

        scope_text = {
            PACKAGE_WORKTREE: "working tree",
            PACKAGE_STAGED: "staged state",
            PACKAGE_HEAD: "HEAD",
        }.get(scope, scope)
        self._set_status(f"Creating ZIP from {scope_text}…", active=True)

        self.worker_thread = QThread(self)
        self.worker = PackageWorker(project, scope)
        self.worker.moveToThread(self.worker_thread)
        self.worker.succeeded.connect(self._package_succeeded)
        self.worker.failed.connect(self._package_failed)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self._worker_finished)
        self.worker_thread.start()

    def _package_succeeded(self, result: PackageResult):
        self._clear_package_artifact()
        self._package_artifact = DraggableArchive(
            result, self.package_artifact_host)
        self.package_artifact_layout.addWidget(self._package_artifact)
        self.package_artifact_host.show()

        if result.missing:
            self._set_status(
                self._format_package_status(result)
                + f" · {len(result.missing):,} missing on disk",
                error=True,
            )
        else:
            self._set_status(self._format_package_status(result))

    def _package_failed(self, message: str):
        QMessageBox.critical(self, "Could not create ZIP", message)
        self._set_status("ZIP creation failed", error=True)

    def choose_project(self):
        path = QFileDialog.getExistingDirectory(
            self,
            "Select project folder",
            str(self.project) if self.project else str(Path.home()),
        )
        if not path:
            return
        self.project = Path(path).resolve()
        self._clear_package_artifact()
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
        self._worker_kind = "analyze"
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
        self.package_button.setEnabled(True)
        self._worker_kind = None
        self._history_selection_changed()

    def _analysis_failed(self, message: str):
        QMessageBox.critical(self, "Could not analyze ZIP", message)
        self._set_status("Analysis failed", error=True)

    def _analysis_ready(self, path, analysis: ImportAnalysis):
        project = self.project
        if project is None or not project.exists() or not project.is_dir():
            QMessageBox.warning(self, "No project",
                                "Select a project folder first.")
            return
        entries = analysis.filtered_entries
        candidates = analysis.filtered_candidates
        included_entries = analysis.included_entries
        included_candidates = analysis.included_candidates
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
            plan = make_plan(
                entries,
                best.prefix,
                project,
                existing_files=analysis.existing_files,
                archive_hashes=analysis.archive_hashes,
            )
            included_plan = make_plan(
                included_entries,
                best.prefix,
                project,
                existing_files=analysis.existing_files,
                archive_hashes=analysis.archive_hashes,
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
        self._worker_kind = "import"
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
