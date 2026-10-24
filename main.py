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
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from porter import (
    CLEAN_ITEMS,
    REQUIRED_CATEGORIES,
    SETUPTER_VERSION,
    SKILL_CATEGORIES,
    SetupReport,
    _locate_moai,
    _version_tuple,
    claude_running,
    claude_suspended,
    clean_project,
    find_moai,
    get_latest_moai_version,
    install_or_update_moai,
    resume_moai_claude,
    setup_project,
    suspend_moai_claude,
)


class InstallWorker(QThread):
    finished_report = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        report = SetupReport(target="(moai-adk install)")
        try:
            ok = install_or_update_moai(report)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        if not ok:
            self.failed.emit("installer did not produce a v3 moai binary")
            return
        self.finished_report.emit(report)


class LatestVersionWorker(QThread):
    """Fetch the latest moai-adk release tag in the background."""

    got_latest = Signal(str)

    def run(self) -> None:
        self.got_latest.emit(get_latest_moai_version() or "unknown")


class SetupWorker(QThread):
    finished_report = Signal(object)
    failed = Signal(str)

    def __init__(self, target: str, categories: set[str], skill_targets: set[str]):
        super().__init__()
        self.target = target
        self.categories = categories
        self.skill_targets = skill_targets

    def run(self) -> None:
        try:
            report = setup_project(self.target, self.categories, self.skill_targets)
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
        kind, moai_version = find_moai()
        moai_label = f" | moai-adk: {moai_version}" if moai_version else " | moai-adk: not found"
        self.setWindowTitle(f"MoAI-ADK → ZCode Setupter v{SETUPTER_VERSION}{moai_label}")

        self.status_label = QLabel(f"moai-adk installed: {moai_version or 'not found'} | latest: checking ...")
        self.latest_version: str | None = None
        self.latest_worker = LatestVersionWorker()
        self.latest_worker.got_latest.connect(self.on_latest)
        self.latest_worker.start()

        self.install_worker: InstallWorker | None = None
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
        layout.addWidget(self.status_label)

        self.antigravity_cb = QCheckBox(
            "Also port skills for Antigravity (.agents/skills)")
        layout.addWidget(self.antigravity_cb)
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
        self.suspend_btn = QPushButton("Suspend moai in Claude Code")
        self.suspend_btn.clicked.connect(self.toggle_suspend)
        buttons.addWidget(self.run_btn, stretch=1)
        buttons.addWidget(self.clean_btn)
        buttons.addWidget(self.suspend_btn)
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
            self._refresh_suspend_label()

    def on_latest(self, latest: str) -> None:
        self.latest_version = None if latest == "unknown" else latest
        kind, installed = find_moai()
        self.status_label.setText(
            f"moai-adk installed: {installed or 'not found'} | latest: {latest}"
        )

    def _moai_needs_install(self) -> tuple[bool, str]:
        """(needs_install, reason) — v3 binary missing or older than latest."""
        kind, installed, _path = _locate_moai()
        if kind != "v3":
            return True, "moai-adk is not installed (or only a legacy version was found)"
        if self.latest_version and _version_tuple(installed) < _version_tuple(self.latest_version):
            return True, f"installed {installed} is older than latest {self.latest_version}"
        return False, ""

    def run_setup(self) -> None:
        target = self.folder_edit.text().strip()
        if not target:
            self.log.appendPlainText("Please choose a project folder first.")
            return
        needs, reason = self._moai_needs_install()
        if needs:
            answer = QMessageBox.question(
                self,
                "Install moai-adk?",
                f"{reason}.\n\nInstall/update moai-adk now using the official "
                "installer (requires internet)?\n\nChoose 'Ignore' to continue anyway.",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.Ignore
                | QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return
            if answer == QMessageBox.StandardButton.Yes:
                self.log.appendPlainText(f"=== Installing/updating moai-adk ({reason}) ===")
                self.run_btn.setEnabled(False)
                self.clean_btn.setEnabled(False)
                self.pending_setup_target = target
                self.install_worker = InstallWorker()
                self.install_worker.finished_report.connect(self.on_installed)
                self.install_worker.failed.connect(self.on_install_fail)
                self.install_worker.start()
                return
        self._start_setup(target)

    def on_installed(self, report) -> None:
        for line in report.steps:
            self.log.appendPlainText(line)
        for w in report.warnings:
            self.log.appendPlainText(f"WARNING: {w}")
        kind, installed = find_moai()
        self.status_label.setText(
            f"moai-adk installed: {installed or 'not found'} | latest: {self.latest_version or 'unknown'}"
        )
        self.log.appendPlainText("=== moai-adk install/update finished. ===")
        self.install_worker = None
        target = getattr(self, "pending_setup_target", None)
        if target:
            self.pending_setup_target = None
            self._start_setup(target)

    def on_install_fail(self, msg: str) -> None:
        self.log.appendPlainText(f"moai-adk INSTALL FAILED: {msg}\n")
        self.run_btn.setEnabled(True)
        self.clean_btn.setEnabled(True)
        self.install_worker = None

    def _start_setup(self, target: str) -> None:
        categories = {cat for cat, cb in self.boxes.items() if cb.isChecked()}
        categories |= REQUIRED_CATEGORIES
        skill_targets = {"zcode"}
        if self.antigravity_cb.isChecked():
            skill_targets.add("antigravity")
        self.run_btn.setEnabled(False)
        self.clean_btn.setEnabled(False)
        self.log.appendPlainText(
            f"=== Setting up {target} (categories: {sorted(categories)}; "
            f"targets: {sorted(skill_targets)}) ===")
        self.worker = SetupWorker(target, categories, skill_targets)
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

    def toggle_suspend(self) -> None:
        target = self.folder_edit.text().strip()
        if not target:
            self.log.appendPlainText("Please choose a project folder first.")
            return
        suspended = claude_suspended(target)
        action = "Resume" if suspended else "Suspend"
        if claude_running():
            self.log.appendPlainText(
                f"NOTE: a 'claude' process appears to be running. {action} only "
                "affects sessions started AFTER this change.")
        answer = QMessageBox.question(
            self,
            f"{action} moai in Claude Code?",
            (
                "Rename CLAUDE.md and .claude/rules to *.off so new "
                "Claude Code sessions start with a light context (for "
                "context-demanding tasks)? Hooks, commands and ZCode's "
                "AGENTS.md stay active. Reversible with this same button."
                if not suspended
                else "Rename the *.off files back so Claude Code loads the full "
                "moai instructions again?"
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        report = SetupReport(target=target)
        (resume_moai_claude if suspended else suspend_moai_claude)(target, report)
        for line in report.steps:
            self.log.appendPlainText(line)
        for w in report.warnings:
            self.log.appendPlainText(f"WARNING: {w}")
        self._refresh_suspend_label()
        self.log.appendPlainText("")

    def _refresh_suspend_label(self) -> None:
        target = self.folder_edit.text().strip()
        if not target:
            return
        self.suspend_btn.setText(
            "Resume moai in Claude Code"
            if claude_suspended(target)
            else "Suspend moai in Claude Code"
        )

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
