from __future__ import annotations

import difflib
import zipfile
from collections import defaultdict

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QBrush, QFontDatabase, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
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
from ui.theme import ThemeTokens, apply_theme, set_style_class, tokens_from_palette


GROUP_ROLE = Qt.ItemDataRole.UserRole + 1


class DiffHighlighter(QSyntaxHighlighter):
    def __init__(self, document, tokens: ThemeTokens | None = None):
        super().__init__(document)
        self.formats = {}
        self.apply_theme(tokens or tokens_from_palette())

    def apply_theme(self, tokens: ThemeTokens):
        semantic = {
            "add": (tokens.success_bg, tokens.success_fg),
            "remove": (tokens.danger_bg, tokens.danger_fg),
            "meta": (tokens.info_bg, tokens.info_fg),
            "header": (tokens.neutral_bg, tokens.neutral_fg),
        }
        self.formats = {}
        for kind, (background, foreground) in semantic.items():
            fmt = QTextCharFormat()
            fmt.setBackground(QBrush(background))
            fmt.setForeground(QBrush(foreground))
            self.formats[kind] = fmt
        self.rehighlight()

    def highlightBlock(self, text: str):
        if text.startswith("+++") or text.startswith("---"):
            self.setFormat(0, len(text), self.formats["header"])
        elif text.startswith("@@"):
            self.setFormat(0, len(text), self.formats["meta"])
        elif text.startswith("+"):
            self.setFormat(0, len(text), self.formats["add"])
        elif text.startswith("-"):
            self.setFormat(0, len(text), self.formats["remove"])


class DiffView(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.highlighter = DiffHighlighter(self.document())

    def show_diff(self, text: str):
        self.setPlainText(text)
        self.highlighter.rehighlight()


class ImportPreviewDialog(QDialog):
    def __init__(
        self,
        project_root,
        project_name: str,
        prefix: str,
        plan: list[ImportItem],
        include_gitignored_plan: list[ImportItem],
        gitignored_plan: list[ImportItem],
        zip_path,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Review import")
        self.resize(1180, 760)
        self.setMinimumSize(960, 640)
        self.project_root = project_root
        self.plan = plan
        self._filtered_plan = list(plan)
        self.include_gitignored_plan = include_gitignored_plan
        self.gitignored_plan = gitignored_plan
        self._ignored_archive_paths = {
            item.archive_path for item in gitignored_plan}
        self._show_gitignored = False
        self.zip_path = zip_path
        self.selected: list[ImportItem] = []
        self._zip = zipfile.ZipFile(zip_path)

        self._items_by_action = defaultdict(list)
        for item in plan:
            self._items_by_action[item.action].append(item)
        self._stat_values: dict[str, QLabel] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)

        header = QVBoxLayout()
        header.setSpacing(3)
        title = QLabel("Review import")
        title.setObjectName("PreviewTitle")
        set_style_class(title, "preview-title")
        subtitle = QLabel(f"{project_name}  ·  {zip_path.name}")
        subtitle.setObjectName("PreviewSubtitle")
        set_style_class(subtitle, "subtitle")
        header.addWidget(title)
        header.addWidget(subtitle)
        layout.addLayout(header)

        info = QFrame()
        info.setObjectName("InfoCard")
        set_style_class(info, "surface")
        info_layout = QVBoxLayout(info)
        info_layout.setContentsMargins(14, 12, 14, 12)
        info_layout.setSpacing(9)

        prefix_text = prefix[:-1] if prefix.endswith("/") else prefix
        detected = QLabel()
        detected.setText(f"<b>ZIP root:</b> {prefix_text or '(archive root)'}")
        detected.setObjectName("DetectedRoot")
        set_style_class(detected, "inline-status")
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
        self._add_stat(
            stats, 3, "Git-ignored", len(self.gitignored_plan),
            "Matched by Git ignore rules; excluded by default",
        )
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
        set_style_class(files_title, "section-title")
        files_header.addWidget(files_title)
        files_header.addStretch()
        self.include_gitignored_toggle = QCheckBox("Include Git-ignored files")
        set_style_class(self.include_gitignored_toggle, "check-option")
        ignored_count = len(self.gitignored_plan)
        self.include_gitignored_toggle.setEnabled(bool(ignored_count))
        self.include_gitignored_toggle.setToolTip(
            f"{ignored_count} file(s) in this archive are matched by Git's ignore rules. "
            "They are excluded by default for this import only."
            if ignored_count
            else "No additional Git-ignored files are present in this import."
        )
        self.include_gitignored_toggle.toggled.connect(
            self._include_gitignored_changed)
        files_header.addWidget(self.include_gitignored_toggle)
        self.only_changed = QCheckBox("Changed only")
        set_style_class(self.only_changed, "check-option")
        self.only_changed.setChecked(True)
        self.only_changed.toggled.connect(self._apply_filter)
        files_header.addWidget(self.only_changed)
        left_layout.addLayout(files_header)

        self.tree = QTreeWidget()
        self.tree.setObjectName("FileTree")
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Path", "Change", "Size"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setFrameShape(QFrame.Shape.NoFrame)
        self.tree.setAlternatingRowColors(False)
        # Keep row selection fully native so Qt controls the background, hover,
        # branch/chevron, checkbox, and focus rendering consistently.
        self.tree.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.tree.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.tree.setAllColumnsShowFocus(True)
        self.tree.currentItemChanged.connect(self._show_selected_diff)
        self.tree.itemChanged.connect(self._tree_item_changed)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents)
        tree_frame = QFrame()
        set_style_class(tree_frame, "surface-alt")
        tree_frame_layout = QVBoxLayout(tree_frame)
        tree_frame_layout.setContentsMargins(0, 0, 0, 0)
        tree_frame_layout.setSpacing(0)
        tree_frame_layout.addWidget(self.tree)
        left_layout.addWidget(tree_frame, 1)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(8)

        self.diff_title = QLabel("Select a file to inspect its changes")
        self.diff_title.setObjectName("DiffTitle")
        set_style_class(self.diff_title, "diff-title")
        self.diff_title.setWordWrap(True)
        right_layout.addWidget(self.diff_title)

        legend = QHBoxLayout()
        for label in ("Added", "Removed", "Hunk"):
            swatch = QLabel()
            swatch.setFixedSize(12, 12)
            set_style_class(swatch, f"diff-swatch-{label.lower()}")
            text = QLabel(label)
            text.setObjectName("DiffLegend")
            set_style_class(text, "diff-legend")
            legend.addWidget(swatch)
            legend.addWidget(text)
            legend.addSpacing(8)
        legend.addStretch()
        right_layout.addLayout(legend)

        self.diff = DiffView()
        set_style_class(self.diff, "diff-editor")
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
        set_style_class(self.select_all, "check-option")
        self.select_all.setChecked(True)
        self.select_all.toggled.connect(self._toggle_all)
        footer.addWidget(self.select_all)
        footer.addStretch()
        self.selection_summary = QLabel()
        self.selection_summary.setObjectName("SelectionSummary")
        set_style_class(self.selection_summary, "selection-summary")
        footer.addWidget(self.selection_summary)
        layout.addLayout(footer)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        self.apply_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        set_style_class(self.apply_button, "primary-button")
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_button is not None:
            set_style_class(cancel_button, "secondary-button")
        self.apply_button.setText("Apply selected")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.paletteChanged.connect(self._theme_changed)
        self._theme_changed()
        self._populate_tree()
        self._update_selection_summary()

    def _theme_changed(self, _palette=None):
        self._tokens = apply_theme(self)
        self.diff.highlighter.apply_theme(self._tokens)
        self._apply_tree_semantic_colors()

    def _add_stat(self, grid: QGridLayout, column: int, label: str, value: int, hint: str):
        block = QWidget()
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        value_label = QLabel(str(value))
        value_label.setObjectName("StatValue")
        set_style_class(value_label, "stat-value")
        self._stat_values[label] = value_label
        label_widget = QLabel(label)
        label_widget.setObjectName("StatLabel")
        set_style_class(label_widget, "stat-label")
        hint_widget = QLabel(hint)
        hint_widget.setObjectName("StatHint")
        set_style_class(hint_widget, "stat-hint")
        layout.addWidget(value_label)
        layout.addWidget(label_widget)
        layout.addWidget(hint_widget)
        grid.addWidget(block, 0, column)

    def _include_gitignored_changed(self, enabled: bool):
        self._show_gitignored = enabled
        self.plan = (
            self.include_gitignored_plan
            if enabled
            else self._filtered_plan
        )
        self._rebuild_plan_view()

    def _rebuild_plan_view(self):
        self._items_by_action = defaultdict(list)
        for item in self.plan:
            self._items_by_action[item.action].append(item)
        replace = sum(x.action == "replace" for x in self.plan)
        new = sum(x.action == "new" for x in self.plan)
        unchanged = sum(x.action == "unchanged" for x in self.plan)
        for label, value in (("Modified", replace), ("New", new), ("Unchanged", unchanged)):
            widget = self._stat_values.get(label)
            if widget is not None:
                widget.setText(str(value))
        self._populate_tree()
        self._update_selection_summary()

    @staticmethod
    def _set_tree_row_height(item: QTreeWidgetItem, height: int) -> None:
        item.setSizeHint(0, QSize(0, height))

    def _populate_tree(self):
        self.tree.clear()
        order = [("replace", "Modified"), ("new", "New"),
                 ("unchanged", "Unchanged")]
        group_role = GROUP_ROLE

        regular_items = [
            item for item in self.plan
            if item.archive_path not in self._ignored_archive_paths
        ]
        items_by_action = defaultdict(list)
        for item in regular_items:
            items_by_action[item.action].append(item)

        for action, label in order:
            items = items_by_action.get(action, [])
            if not items:
                continue
            action_label = {"replace": "Replace",
                            "new": "Add", "unchanged": "Same"}[action]
            root = QTreeWidgetItem([f"{label}  ·  {len(items)}", "", ""])
            root.setExpanded(action != "unchanged")
            root.setData(0, group_role, action)
            root.setFlags(Qt.ItemFlag.ItemIsEnabled |
                          Qt.ItemFlag.ItemIsSelectable)
            self._set_tree_row_height(root, 32)
            self.tree.addTopLevelItem(root)
            self._add_tree_files(root, items, action_label, selectable=True)

        ignored = sorted(
            self.gitignored_plan,
            key=lambda x: x.target_path.as_posix().casefold(),
        )
        if ignored:
            action_label = "Ignored"
            root = QTreeWidgetItem([f"Git-ignored  ·  {len(ignored)}", "", ""])
            root.setExpanded(True)
            root.setData(0, group_role, "ignored")
            root.setFlags(Qt.ItemFlag.ItemIsEnabled |
                          Qt.ItemFlag.ItemIsSelectable)
            self._set_tree_row_height(root, 32)
            self.tree.addTopLevelItem(root)
            self._add_tree_files(
                root, ignored, action_label,
                selectable=self._show_gitignored,
            )

        self._apply_tree_semantic_colors()
        # Apply the current Changed-only setting to the freshly rebuilt tree.
        # The checkbox is connected before population, but its initial checked
        # state does not emit a signal, so without this the Unchanged group
        # incorrectly appears on first load.
        self._apply_filter(self.only_changed.isChecked())

        if self.tree.topLevelItemCount():
            # Prefer a regular actionable file; fall back to a visible ignored file.
            for i in range(self.tree.topLevelItemCount()):
                root = self.tree.topLevelItem(i)
                if root is None or not root.childCount():
                    continue
                root.setExpanded(i < 2 or root.text(
                    0).startswith("Git-ignored"))
                for j in range(root.childCount()):
                    child = root.child(j)
                    if child is not None and child.data(0, Qt.ItemDataRole.UserRole) is not None:
                        self.tree.setCurrentItem(child)
                        return

    def _apply_tree_semantic_colors(self):
        tokens = getattr(self, "_tokens", None)
        if tokens is None or not hasattr(self, "tree"):
            return

        foregrounds = {
            "replace": tokens.danger_fg,
            "new": tokens.success_fg,
            "unchanged": tokens.neutral_fg,
            "ignored": tokens.warning_fg,
        }
        size_brush = QBrush(tokens.text_muted)

        for index in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(index)
            if root is None:
                continue
            action = root.data(0, GROUP_ROLE)
            semantic_brush = QBrush(foregrounds.get(action, tokens.text))
            root.setForeground(0, semantic_brush)

            for child_index in range(root.childCount()):
                child = root.child(child_index)
                if child is None:
                    continue
                child.setForeground(0, semantic_brush)
                child.setForeground(1, semantic_brush)
                child.setForeground(2, size_brush)

    def _add_tree_files(
        self,
        root: QTreeWidgetItem,
        items: list[ImportItem],
        action_label: str,
        *,
        selectable: bool,
    ):
        for item in items:
            rel = item.target_path.relative_to(self.project_root).as_posix()
            child = QTreeWidgetItem(
                [rel, action_label, f"{item.size:,} B"]
            )
            child.setData(0, Qt.ItemDataRole.UserRole, item)
            child.setToolTip(
                0,
                f"{rel}\nExcluded by Git ignore rules by default."
                if item.archive_path in self._ignored_archive_paths
                else rel,
            )
            if item.action != "unchanged" and selectable:
                child.setFlags(
                    child.flags()
                    | Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsSelectable
                )
                child.setCheckState(0, Qt.CheckState.Checked)
            else:
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsSelectable)
            self._set_tree_row_height(child, 34)
            root.addChild(child)

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
        if item.archive_path in self._ignored_archive_paths:
            label = "GIT-IGNORED"
        else:
            label = "MODIFIED" if item.action == "replace" else "NEW" if item.action == "new" else "UNCHANGED"
        self.diff_title.setText(
            f"<b>{label}</b>  ·  {rel}  ·  {item.size:,} bytes")
        try:
            new_data = self._zip.read(item.archive_path)
            old_data = item.target_path.read_bytes() if item.target_path.exists(
            ) and item.target_path.is_file() else b""
        except OSError as exc:
            self.diff.show_diff(f"Unable to read file for preview:\n{exc}")
            return
        old_text = self._decode(old_data)
        new_text = self._decode(new_data)
        if old_text is None or new_text is None:
            same = old_data == new_data
            self.diff.show_diff("Binary file. Content is " +
                                ("unchanged." if same else "different."))
            return
        diff = difflib.unified_diff(
            old_text.splitlines(),
            new_text.splitlines(),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
            lineterm="",
        )
        text = "\n".join(diff)
        self.diff.show_diff(text or "No textual differences.")

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
