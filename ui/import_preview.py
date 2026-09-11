from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QVBoxLayout, QCheckBox,
)

from core.importer import ImportItem


class ImportPreviewDialog(QDialog):
    def __init__(self, project_root, project_name: str, prefix: str, plan: list[ImportItem], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import preview")
        self.resize(820, 580)
        self.project_root = project_root
        self.plan = plan
        self.selected: list[ImportItem] = []

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

        self.list = QListWidget()
        for item in plan:
            checkbox = QCheckBox()
            checkbox.setChecked(item.action != "unchanged")
            rel = item.target_path.relative_to(project_root).as_posix()
            text = f"{item.action.upper():9}  {rel}"
            row = QListWidgetItem()
            self.list.addItem(row)
            self.list.setItemWidget(row, self._row_widget(checkbox, text))
            row.setSizeHint(self.list.itemWidget(row).sizeHint())
            checkbox.item = item  # type: ignore[attr-defined]
        layout.addWidget(self.list, 1)

        self.select_all = QCheckBox("Select all changed files")
        self.select_all.setChecked(True)
        self.select_all.toggled.connect(self._toggle_all)
        layout.addWidget(self.select_all)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Apply selected")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _row_widget(checkbox, text):
        from PySide6.QtWidgets import QWidget
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(4, 2, 4, 2)
        row.addWidget(checkbox)
        label = QLabel(text)
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(label, 1)
        return widget

    def _toggle_all(self, enabled: bool):
        for i in range(self.list.count()):
            widget = self.list.itemWidget(self.list.item(i))
            checkbox = widget.findChild(QCheckBox)
            if checkbox:
                item = getattr(checkbox, "item", None)
                checkbox.setChecked(
                    enabled and item is not None and item.action != "unchanged")

    def _accept(self):
        chosen: list[ImportItem] = []
        for i in range(self.list.count()):
            widget = self.list.itemWidget(self.list.item(i))
            checkbox = widget.findChild(QCheckBox)
            item = getattr(checkbox, "item", None) if checkbox else None
            if checkbox and checkbox.isChecked() and item is not None:
                chosen.append(item)
        if not chosen:
            return
        self.selected = chosen
        self.accept()
