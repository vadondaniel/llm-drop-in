from __future__ import annotations

import difflib
import zipfile
from collections import defaultdict

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.importer import ImportItem


class ImportPreviewDialog(QDialog):
    def __init__(self, project_root, project_name: str, prefix: str, plan: list[ImportItem], zip_path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Review import")
        self.resize(1180, 760)
        self.setMinimumSize(960, 640)
        self.project_root = project_root
        self.plan = plan
        self.zip_path = zip_path
        self.selected: list[ImportItem] = []
        self._zip = zipfile.ZipFile(zip_path)

        self._items_by_action = defaultdict(list)
        for item in plan:
            self._items_by_action[item.action].append(item)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)

        header = QVBoxLayout()
        header.setSpacing(3)
        title = QLabel("Review import")
        title.setObjectName("PreviewTitle")
        subtitle = QLabel(f"{project_name}  ·  {zip_path.name}")
        subtitle.setObjectName("PreviewSubtitle")
        header.addWidget(title)
        header.addWidget(subtitle)
        layout.addLayout(header)

        info = QFrame()
        info.setObjectName("InfoCard")
        info_layout = QVBoxLayout(info)
        info_layout.setContentsMargins(14, 12, 14, 12)
        info_layout.setSpacing(9)

        prefix_text = prefix[:-1] if prefix.endswith("/") else prefix
        detected = QLabel()
        detected.setText(f"<b>ZIP root:</b> {prefix_text or '(archive root)'}")
        detected.setObjectName("DetectedRoot")
        info_layout.addWidget(detected)

        stats = QGridLayout()
        stats.setHorizontalSpacing(10)
        stats.setVerticalSpacing(0)
        replace = sum(x.action == "replace" for x in plan)
        new = sum(x.action == "new" for x in plan)
        unchanged = sum(x.action == "unchanged" for x in plan)
        self._add_stat(stats, 0, "Modified", replace,
                       "Changed files that will replace existing files")
        self._add_stat(stats, 1, "New", new,
                       "Files that do not exist in the project")
        self._add_stat(stats, 2, "Unchanged", unchanged,
                       "Already identical; hidden by default")
        info_layout.addLayout(stats)
        layout.addWidget(info)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(8)

        files_header = QHBoxLayout()
        files_title = QLabel("Files")
        files_title.setObjectName("SectionTitle")
        files_header.addWidget(files_title)
        files_header.addStretch()
        self.only_changed = QCheckBox("Changed only")
        self.only_changed.setChecked(True)
        self.only_changed.toggled.connect(self._apply_filter)
        files_header.addWidget(self.only_changed)
        left_layout.addLayout(files_header)

        self.tree = QTreeWidget()
        self.tree.setObjectName("FileTree")
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Path", "Size"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(False)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.tree.itemSelectionChanged.connect(self._show_selected_diff)
        self.tree.itemChanged.connect(self._tree_item_changed)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        left_layout.addWidget(self.tree, 1)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(8)

        self.diff_title = QLabel("Select a file to inspect its changes")
        self.diff_title.setObjectName("DiffTitle")
        self.diff_title.setWordWrap(True)
        right_layout.addWidget(self.diff_title)

        self.diff = QPlainTextEdit()
        self.diff.setReadOnly(True)
        self.diff.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.diff.setPlaceholderText("A file diff will appear here.")
        self.diff.setFont(QFontDatabase.systemFont(
            QFontDatabase.SystemFont.FixedFont))
        right_layout.addWidget(self.diff, 1)
        splitter.addWidget(right)
        splitter.setSizes([500, 680])
        layout.addWidget(splitter, 1)

        footer = QHBoxLayout()
        self.select_all = QCheckBox("Select all changed files")
        self.select_all.setChecked(True)
        self.select_all.toggled.connect(self._toggle_all)
        footer.addWidget(self.select_all)
        footer.addStretch()
        self.selection_summary = QLabel()
        self.selection_summary.setObjectName("SelectionSummary")
        footer.addWidget(self.selection_summary)
        layout.addLayout(footer)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        self.apply_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.apply_button.setText("Apply selected")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setStyleSheet('''
            QDialog { background: #f3f5f7; }
            QWidget { color: #20252b; font-size: 10pt; }
            QLabel#PreviewTitle { font-size: 20pt; font-weight: 700; color: #171b20; }
            QLabel#PreviewSubtitle { color: #707780; }
            QLabel#SectionTitle { font-size: 11pt; font-weight: 700; color: #30373d; }
            QFrame#InfoCard {
                background: #ffffff;
                border: 1px solid #dfe3e8;
                border-radius: 10px;
            }
            QLabel#DetectedRoot { color: #30373d; }
            QLabel#StatValue { font-size: 13pt; font-weight: 700; color: #20252b; }
            QLabel#StatLabel { font-weight: 600; color: #59616a; }
            QLabel#StatHint { color: #808890; font-size: 9pt; }
            QTreeWidget, QPlainTextEdit {
                background: #ffffff;
                border: 1px solid #d8dde3;
                border-radius: 9px;
            }
            QTreeWidget::item {
                padding: 7px 4px;
                border-bottom: 1px solid #edf0f2;
            }
            QTreeWidget::item:selected {
                background: #e5f1ee;
                color: #17211f;
            }
            QTreeWidget::branch:has-children:closed { image: none; }
            QHeaderView::section {
                background: #f6f7f8;
                color: #606970;
                border: none;
                border-bottom: 1px solid #d8dde3;
                padding: 7px 8px;
                font-weight: 600;
            }
            QLabel#DiffTitle {
                background: #ffffff;
                border: 1px solid #dfe3e8;
                border-radius: 8px;
                padding: 9px 11px;
            }
            QLabel#SelectionSummary { color: #59616a; }
            QPushButton {
                background: #276b5f;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 14px;
                font-weight: 600;
            }
            QPushButton:hover { background: #215b51; }
            QPushButton:disabled { background: #d9dddf; color: #8a9298; }
            QCheckBox { color: #4e565d; }
        ''')

        self._populate_tree()
        self._update_selection_summary()

    @staticmethod
    def _add_stat(grid: QGridLayout, column: int, label: str, value: int, hint: str):
        block = QWidget()
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        value_label = QLabel(str(value))
        value_label.setObjectName("StatValue")
        label_widget = QLabel(label)
        label_widget.setObjectName("StatLabel")
        hint_widget = QLabel(hint)
        hint_widget.setObjectName("StatHint")
        layout.addWidget(value_label)
        layout.addWidget(label_widget)
        layout.addWidget(hint_widget)
        grid.addWidget(block, 0, column)

    def _populate_tree(self):
        self.tree.clear()
        order = [("replace", "Modified"), ("new", "New"),
                 ("unchanged", "Unchanged")]
        roots = {}
        for action, label in order:
            items = self._items_by_action.get(action, [])
            root = QTreeWidgetItem([f"{label}  ({len(items)})", ""])
            root.setExpanded(action != "unchanged")
            root.setFirstColumnSpanned(False)
            root.setFlags(Qt.ItemFlag.ItemIsEnabled |
                          Qt.ItemFlag.ItemIsSelectable)
            self.tree.addTopLevelItem(root)
            roots[action] = root
            for item in sorted(items, key=lambda x: x.target_path.as_posix().casefold()):
                rel = item.target_path.relative_to(
                    self.project_root).as_posix()
                child = QTreeWidgetItem([rel, f"{item.size:,} B"])
                child.setData(0, Qt.ItemDataRole.UserRole, item)
                child.setToolTip(0, rel)
                if item.action != "unchanged":
                    child.setFlags(
                        child.flags()
                        | Qt.ItemFlag.ItemIsUserCheckable
                        | Qt.ItemFlag.ItemIsSelectable
                    )
                    child.setCheckState(0, Qt.CheckState.Checked)
                else:
                    child.setFlags(
                        child.flags() | Qt.ItemFlag.ItemIsSelectable)
                root.addChild(child)
        if self.tree.topLevelItemCount():
            # Select the first actionable file rather than a group header.
            for i in range(self.tree.topLevelItemCount()):
                root = self.tree.topLevelItem(i)
                if root is None:
                    continue
                if root.childCount():
                    root.setExpanded(i < 2)
                    child = root.child(0)
                    if child is not None and child.data(0, Qt.ItemDataRole.UserRole) is not None:
                        self.tree.setCurrentItem(child)
                        break

    def _apply_filter(self, changed_only: bool):
        unchanged_root = None
        for i in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(i)
            if root is None:
                continue
            if root.text(0).startswith("Unchanged"):
                unchanged_root = root
                break
        if unchanged_root is not None:
            unchanged_root.setHidden(changed_only)
            if changed_only:
                for i in range(self.tree.topLevelItemCount()):
                    root = self.tree.topLevelItem(i)
                    if root is None or root.isHidden() or not root.childCount():
                        continue
                    child = root.child(0)
                    if child is not None:
                        self.tree.setCurrentItem(child)
                        break
            else:
                unchanged_root.setExpanded(True)

    def _iter_file_items(self):
        for i in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(i)
            if root is None:
                continue
            for j in range(root.childCount()):
                child = root.child(j)
                if child is not None:
                    yield child

    def _tree_item_changed(self, item, column):
        if column == 0 and item.data(0, Qt.ItemDataRole.UserRole) is not None:
            self._update_selection_summary()

    def _toggle_all(self, enabled: bool):
        for widget in self._iter_file_items():
            item = widget.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(item, ImportItem) and item.action != "unchanged":
                widget.setCheckState(
                    0, Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
        self._update_selection_summary()

    def _selected_items(self) -> list[ImportItem]:
        selected: list[ImportItem] = []
        for widget in self._iter_file_items():
            item = widget.data(0, Qt.ItemDataRole.UserRole)
            if not isinstance(item, ImportItem) or item.action == "unchanged":
                continue
            if widget.checkState(0) == Qt.CheckState.Checked:
                selected.append(item)
        return selected

    def _update_selection_summary(self):
        selected = self._selected_items()
        self.selection_summary.setText(
            f"{len(selected)} changed file{'s' if len(selected) != 1 else ''} selected")
        self.apply_button.setEnabled(bool(selected))
        self.apply_button.setText(
            f"Apply {len(selected)} file{'s' if len(selected) != 1 else ''}")

    def _show_selected_diff(self):
        current = self.tree.currentItem()
        if current is None:
            return
        item = current.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(item, ImportItem):
            return
        rel = item.target_path.relative_to(self.project_root).as_posix()
        self.diff_title.setText(
            f"<b>{item.action.upper()}</b>  ·  {rel}  ·  {item.size:,} bytes")
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

    def _accept(self):
        chosen = self._selected_items()
        if not chosen:
            return
        self.selected = chosen
        self._zip.close()
        self.accept()

    def reject(self):
        self._zip.close()
        super().reject()
