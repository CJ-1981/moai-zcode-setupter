"""MoAI-ADK -> ZCode project setupter.

Pick a git repo folder, run `moai-adk init`, and port AGENTS.md plus the
Claude Code skills/commands into ZCode's `.zcode/` layout.
"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from porter import CLEAN_ITEMS, REQUIRED_CATEGORIES, SKILL_CATEGORIES, clean_project, setup_project


class SetupWorker(QThread):
    finished_report = Signal(object)
    failed = Signal(str)

    def __init__(self, target: str, categories: set[str]):
        super().__init__()
        self.target = target
        self.categories = categories

    def run(self) -> None:
        try:
            report = setup_project(self.target, self.categories)
        except Exception as exc:  # surfaced in the log pane
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.finished_report.emit(report)


class CleanWorker(QThread):
    finished_report = Signal(object)
    failed = Signal(str)

    def __init__(self, target: str, items: set[str]):
        super().__init__()
        self.target = target
        self.items = items

    def run(self) -> None:
        try:
            report = clean_project(self.target, self.items)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.finished_report.emit(report)


class CleanDialog(QDialog):
    def __init__(self, target: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Clean moai-adk files in {os.path.basename(target)}")
        self.resize(520, 300)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Select what to remove. ZCode-side items (created by this setupter) are "
            "pre-checked. Claude Code / .moai items are optional — removing .moai/ "
            "deletes your SPEC documents and is not reversible."))
        self.boxes: dict[str, QCheckBox] = {}
        for key, (label, risk) in CLEAN_ITEMS.items():
            cb = QCheckBox(label)
            cb.setChecked(risk == "safe")
            self.boxes[key] = cb
            layout.addWidget(cb)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Remove selected")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected(self) -> set[str]:
        return {key for key, cb in self.boxes.items() if cb.isChecked()}


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MoAI-ADK → ZCode Project Setupter")
        self.resize(760, 540)
        self.worker: SetupWorker | None = None
        self.clean_worker: CleanWorker | None = None

        layout = QVBoxLayout()

        row = QHBoxLayout()
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("Path to target git repo ...")
        browse = QPushButton("Choose ...")
        browse.clicked.connect(self.choose_folder)
        row.addWidget(QLabel("Project:"))
        row.addWidget(self.folder_edit, stretch=1)
        row.addWidget(browse)
        layout.addLayout(row)

        layout.addWidget(QLabel(
            "Skill categories to copy (foundation & workflow are always included):"))
        self.boxes: dict[str, QCheckBox] = {}
        grid = QHBoxLayout()
        col = QVBoxLayout()
        for i, (cat, label) in enumerate(SKILL_CATEGORIES.items()):
            cb = QCheckBox(label.split(" (")[0])
            cb.setChecked(cat in REQUIRED_CATEGORIES)
            cb.setEnabled(cat not in REQUIRED_CATEGORIES)
            self.boxes[cat] = cb
            col.addWidget(cb)
            if (i + 1) % 4 == 0:
                grid.addLayout(col)
                col = QVBoxLayout()
        if col.count():
            grid.addLayout(col)
        layout.addLayout(grid)

        buttons = QHBoxLayout()
        self.run_btn = QPushButton("Run setup (moai-adk init + port skills)")
        self.run_btn.clicked.connect(self.run_setup)
        self.clean_btn = QPushButton("Clean / remove moai files ...")
        self.clean_btn.clicked.connect(self.run_clean)
        buttons.addWidget(self.run_btn, stretch=1)
        buttons.addWidget(self.clean_btn)
        layout.addLayout(buttons)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, stretch=1)

        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose target project folder")
        if folder:
            self.folder_edit.setText(folder)

    def run_setup(self) -> None:
        target = self.folder_edit.text().strip()
        if not target:
            self.log.appendPlainText("Please choose a project folder first.")
            return
        categories = {cat for cat, cb in self.boxes.items() if cb.isChecked()}
        categories |= REQUIRED_CATEGORIES
        self.run_btn.setEnabled(False)
        self.clean_btn.setEnabled(False)
        self.log.appendPlainText(
            f"=== Setting up {target} (categories: {sorted(categories)}) ===")
        self.worker = SetupWorker(target, categories)
        self.worker.finished_report.connect(self.on_done)
        self.worker.failed.connect(self.on_fail)
        self.worker.start()

    def on_done(self, report) -> None:
        for line in report.steps:
            self.log.appendPlainText(line)
        for w in report.warnings:
            self.log.appendPlainText(f"WARNING: {w}")
        self.log.appendPlainText(
            "=== Done. Open the project in ZCode and use the moai-cmd-* skills. ===\n")
        self.run_btn.setEnabled(True)
        self.clean_btn.setEnabled(True)
        self.worker = None

    def run_clean(self) -> None:
        target = self.folder_edit.text().strip()
        if not target:
            self.log.appendPlainText("Please choose a project folder first.")
            return
        dlg = CleanDialog(target, self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.selected():
            return
        self.run_btn.setEnabled(False)
        self.clean_btn.setEnabled(False)
        self.log.appendPlainText(f"=== Cleaning moai files in {target} ===")
        self.clean_worker = CleanWorker(target, dlg.selected())
        self.clean_worker.finished_report.connect(self.on_clean_done)
        self.clean_worker.failed.connect(self.on_clean_fail)
        self.clean_worker.start()

    def on_clean_done(self, report) -> None:
        for line in report.steps:
            self.log.appendPlainText(line)
        for w in report.warnings:
            self.log.appendPlainText(f"WARNING: {w}")
        self.log.appendPlainText("=== Clean finished. ===\n")
        self.run_btn.setEnabled(True)
        self.clean_btn.setEnabled(True)
        self.clean_worker = None

    def on_clean_fail(self, msg: str) -> None:
        self.log.appendPlainText(f"FAILED: {msg}\n")
        self.run_btn.setEnabled(True)
        self.clean_btn.setEnabled(True)
        self.clean_worker = None

    def on_fail(self, msg: str) -> None:
        self.log.appendPlainText(f"FAILED: {msg}\n")
        self.run_btn.setEnabled(True)
        self.clean_btn.setEnabled(True)
        self.worker = None


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
