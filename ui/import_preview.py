from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QVBoxLayout,
)

from core.importer import ImportItem


class ImportPreviewDialog(QDialog):
    def __init__(self, project_name: str, prefix: str, plan: list[ImportItem], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import preview")
        self.resize(760, 520)
        self.plan = plan

        layout = QVBoxLayout(self)
        title = QLabel(f"<b>{project_name}</b>")
        layout.addWidget(title)
        prefix_text = prefix[:-1] if prefix.endswith("/") else prefix
        layout.addWidget(
            QLabel(f"Detected ZIP root: <code>{prefix_text or '(archive root)'}</code>"))

        replace = sum(x.action == "replace" for x in plan)
        new = sum(x.action == "new" for x in plan)
        layout.addWidget(QLabel(
            f"{len(plan)} files: {replace} replacements, {new} new files. Nothing will be deleted."))

        self.list = QListWidget()
        for item in plan:
            label = f"{'REPLACE' if item.action == 'replace' else 'NEW':7}  {item.target_path.relative_to(project_name) if False else item.target_path}"
            self.list.addItem(QListWidgetItem(label))
        layout.addWidget(self.list, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Apply")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
