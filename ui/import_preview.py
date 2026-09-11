from __future__ import annotations

import difflib
import zipfile

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QVBoxLayout, QCheckBox, QPlainTextEdit, QSplitter,
    QWidget,
)

from core.importer import ImportItem


class ImportPreviewDialog(QDialog):
    def __init__(self, project_root, project_name: str, prefix: str, plan: list[ImportItem], zip_path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import preview")
        self.resize(1100, 700)
        self.project_root = project_root
        self.plan = plan
        self.zip_path = zip_path
        self.selected: list[ImportItem] = []
        self._zip = zipfile.ZipFile(zip_path)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<h3>{project_name}</h3>"))
        prefix_text = prefix[:-1] if prefix.endswith("/") else prefix
        layout.addWidget(
            QLabel(f"Detected ZIP root: <code>{prefix_text or '(archive root)'}</code>"))

        replace = sum(x.action == "replace" for x in plan)
        new = sum(x.action == "new" for x in plan)
        unchanged = sum(x.action == "unchanged" for x in plan)
        layout.addWidget(QLabel(
            f"{len(plan)} files: {replace} modified, {new} new, {unchanged} unchanged. Nothing will be deleted."
        ))

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._show_diff)
        for item in plan:
            checkbox = QCheckBox()
            checkbox.setChecked(item.action != "unchanged")
            rel = item.target_path.relative_to(project_root).as_posix()
            text = f"{item.action.upper():9}  {rel}"
            row = QListWidgetItem()
            self.list.addItem(row)
            row.setData(Qt.ItemDataRole.UserRole, item)
            widget = self._row_widget(checkbox, text)
            self.list.setItemWidget(row, widget)
            row.setSizeHint(widget.sizeHint())
            checkbox.item = item  # type: ignore[attr-defined]
        splitter.addWidget(self.list)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.diff_title = QLabel("Select a file to inspect its changes")
        right_layout.addWidget(self.diff_title)
        self.diff = QPlainTextEdit()
        self.diff.setReadOnly(True)
        self.diff.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        right_layout.addWidget(self.diff, 1)
        splitter.addWidget(right)
        splitter.setSizes([420, 680])
        layout.addWidget(splitter, 1)

        controls = QHBoxLayout()
        self.select_all = QCheckBox("Select all changed files")
        self.select_all.setChecked(True)
        self.select_all.toggled.connect(self._toggle_all)
        controls.addWidget(self.select_all)
        controls.addStretch()
        layout.addLayout(controls)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Apply selected")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if self.list.count():
            self.list.setCurrentRow(0)

    @staticmethod
    def _row_widget(checkbox, text):
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(4, 2, 4, 2)
        row.addWidget(checkbox)
        label = QLabel(text)
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(label, 1)
        return widget

    @staticmethod
    def _decode(data: bytes):
        if b"\x00" in data[:8192]:
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                return data.decode()
            except UnicodeDecodeError:
                return None

    def _show_diff(self, current, previous):
        if current is None:
            return
        item = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(item, ImportItem):
            return
        rel = item.target_path.relative_to(self.project_root).as_posix()
        self.diff_title.setText(
            f"<b>{item.action.upper()}</b> · {rel} · {item.size:,} bytes")
        try:
            new_data = self._zip.read(item.archive_path)
            old_data = item.target_path.read_bytes() if item.target_path.exists(
            ) and item.target_path.is_file() else b""
        except OSError as exc:
            self.diff.setPlainText(f"Unable to read file for preview:\n{exc}")
            return
        old_text = self._decode(old_data)
        new_text = self._decode(new_data)
        if old_text is None or new_text is None:
            same = old_data == new_data
            self.diff.setPlainText(
                "Binary file. Content is " + ("unchanged." if same else "different."))
            return
        diff = difflib.unified_diff(
            old_text.splitlines(),
            new_text.splitlines(),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
            lineterm="",
        )
        text = "\n".join(diff)
        self.diff.setPlainText(text or "No textual differences.")

    def _toggle_all(self, enabled: bool):
        for i in range(self.list.count()):
            widget = self.list.itemWidget(self.list.item(i))
            checkbox = widget.findChild(QCheckBox)
            if checkbox:
                item = self.list.item(i).data(Qt.ItemDataRole.UserRole)
                checkbox.setChecked(
                    enabled and item is not None and item.action != "unchanged")

    def _accept(self):
        chosen: list[ImportItem] = []
        for i in range(self.list.count()):
            row = self.list.item(i)
            widget = self.list.itemWidget(row)
            checkbox = widget.findChild(QCheckBox)
            item = row.data(Qt.ItemDataRole.UserRole)
            if checkbox and checkbox.isChecked() and isinstance(item, ImportItem):
                chosen.append(item)
        if not chosen:
            return
        self.selected = chosen
        self._zip.close()
        self.accept()

    def reject(self):
        self._zip.close()
        super().reject()
