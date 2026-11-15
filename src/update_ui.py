"""The update window and the quiet check when the app starts (see updater.py)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton,
    QTextBrowser, QVBoxLayout,
)

import paths
import updater as U
from version import APP_NAME, __version__

CHECK_EVERY_S = 20 * 3600
_alive: list = []


class _Signals(QObject):
    checked = Signal(object, str)          # Release or None, error text
    progress = Signal(int, int)
    downloaded = Signal(object, str)       # Path or None, error text


def check_async(on_done) -> None:
    """Looks up the newest release in the background; on_done(release | None, error) runs
    on the GUI thread."""
    sig = _Signals()
    sig.checked.connect(on_done)

    def work():
        try:
            rel, err = U.latest(), ""
        except U.UpdateError as e:
            rel, err = None, str(e)
        sig.checked.emit(rel, err)
    sig._thread = threading.Thread(target=work, daemon=True, name="update-check")
    sig._thread.start()
    _alive.append(sig)                     # keep the signal object alive until it fires


class UpdateDialog(QDialog):
    """Shows what's new and installs it."""

    def __init__(self, win, rel: U.Release):
        super().__init__(win)
        self.win, self.rel = win, rel
        self.kind = U.install_kind()
        self.asset = U.asset_for(self.kind, rel)
        self.file: Path | None = None
        self.proc: QProcess | None = None
        self._cancel = False
        self._sig = _Signals()
        self._sig.progress.connect(self._on_progress)
        self._sig.downloaded.connect(self._on_downloaded)
        self.setWindowTitle(f"Update {APP_NAME}")
        self.resize(560, 520)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(QLabel(f"<b>{APP_NAME} {rel.version}</b> is available "
                             f"(you have {__version__}).", objectName="title"))
        self.notes = QTextBrowser(openExternalLinks=True)
        self.notes.setMarkdown(rel.notes or "No release notes.")
        lay.addWidget(self.notes, 1)

        blocker = U.why_not(self.kind)
        if not blocker and not self.asset:
            blocker = (f"This release has no file for {U.KIND_NAMES[self.kind]} yet - "
                       "try again in a few minutes, or use the download page.")
        self.info = QLabel(objectName="hint", wordWrap=True)
        self.info.setText(blocker or self._plan_text())
        lay.addWidget(self.info)
        self.bar = QProgressBar(visible=False)
        lay.addWidget(self.bar)
        self.out = QPlainTextEdit(readOnly=True, visible=False)
        self.out.setMaximumBlockCount(400)
        self.out.setFixedHeight(120)
        lay.addWidget(self.out)

        row = QHBoxLayout()
        self.page_btn = QPushButton("Download page")
        self.page_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(rel.url)))
        row.addWidget(self.page_btn)
        self.skip_btn = QPushButton("Skip this version")
        self.skip_btn.setToolTip("Don't tell me about this version again")
        self.skip_btn.clicked.connect(self._skip)
        row.addWidget(self.skip_btn)
        row.addStretch()
        self.later_btn = QPushButton("Later")
        self.later_btn.clicked.connect(self._later)
        row.addWidget(self.later_btn)
        self.go_btn = QPushButton("Update now", objectName="primary")
        self.go_btn.clicked.connect(self._start)
        self.go_btn.setEnabled(not blocker)
        row.addWidget(self.go_btn)
        lay.addLayout(row)
        if blocker:
            self.page_btn.setObjectName("primary")
            self.page_btn.setDefault(True)
        else:
            self.go_btn.setDefault(True)

    def _plan_text(self) -> str:
        mb = int(self.asset.get("size") or 0) / 1e6
        size = f" ({mb:.0f} MB)" if mb else ""
        if self.kind == "windows":
            how = ("Downloads the setup" + size + ", closes Chatbox Studio and installs the "
                   "update. The setup window then brings your parts up to date - click Install.")
        elif self.kind in ("deb", "rpm", "arch"):
            how = (f"Downloads {U.KIND_NAMES[self.kind]}{size} and installs it with your "
                   "package manager - your system asks for your password. Then Chatbox Studio "
                   "restarts.")
        else:
            how = ("Downloads the Linux ZIP" + size + ", closes Chatbox Studio and runs its "
                   "installer. The setup window then brings your parts up to date.")
        return how + " Settings and models are kept."

    # ---- buttons
    def _skip(self):
        self.win.s.extra["update_skip"] = self.rel.version
        self.win.save()
        self.win.hide_update_button()
        self.reject()

    def _later(self):
        if self.proc and self.proc.state() != QProcess.NotRunning:
            return                          # don't leave the package manager half-way
        self._cancel = True
        self.reject()

    def closeEvent(self, e):
        if self.proc and self.proc.state() != QProcess.NotRunning:
            e.ignore()
            return
        self._cancel = True
        super().closeEvent(e)

    def _busy(self, text: str):
        self.go_btn.setEnabled(False)
        self.skip_btn.setEnabled(False)
        self.info.setText(text)

    def _failed(self, text: str):
        self.info.setText(text)
        self.bar.setVisible(False)
        self.go_btn.setEnabled(True)
        self.go_btn.setText("Try again")
        self.skip_btn.setEnabled(True)
        self.later_btn.setEnabled(True)
        self.win.add_log(f"Update failed: {text}")

    def _start(self):
        self._cancel = False
        self._busy("Downloading…")
        self.bar.setVisible(True)
        self.bar.setRange(0, 0)
        asset, sig = self.asset, self._sig

        def work():
            try:
                f, err = U.download(asset, lambda d, t: sig.progress.emit(d, t),
                                    lambda: self._cancel), ""
            except U.UpdateError as e:
                f, err = None, str(e)
            sig.downloaded.emit(f, err)
        threading.Thread(target=work, daemon=True, name="update-download").start()

    def _on_progress(self, done: int, total: int):
        if total:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(done * 1000 / total))
            self.bar.setFormat(f"{done / 1e6:.1f} / {total / 1e6:.1f} MB")

    def _on_downloaded(self, file, err: str):
        if self._cancel:
            return
        if not file:
            self._failed(err)
            return
        self.file = file
        self.win.add_log(f"Update {self.rel.version} downloaded and checked.")
        if self.kind in ("deb", "rpm", "arch"):
            self._install_package()
        else:
            self._install_after_exit()

    # ---- Linux packages: install now (the password prompt), then restart
    def _install_package(self):
        try:
            cmd = U.package_command(self.kind, self.file)
        except U.UpdateError as e:
            self._failed(str(e))
            return
        self._before = U.parts_fingerprint(U.OPT / "app")
        self._busy("Installing - enter your password in the window your system shows…")
        self.later_btn.setEnabled(False)
        self.bar.setRange(0, 0)
        self.out.setVisible(True)
        self.out.appendPlainText("$ " + U.describe(cmd))
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._on_output)
        self.proc.finished.connect(self._on_package_done)
        self.proc.errorOccurred.connect(
            lambda e: e == QProcess.FailedToStart and self._failed("The installer couldn't start."))
        self.proc.start(cmd[0], cmd[1:])

    def _on_output(self):
        text = bytes(self.proc.readAllStandardOutput()).decode(errors="replace")
        for line in text.replace("\r", "\n").splitlines():
            if line.strip():
                self.out.appendPlainText(line.rstrip())

    def _on_package_done(self, code: int, _status):
        self.later_btn.setEnabled(True)
        if code in (126, 127):              # pkexec: password prompt cancelled / not allowed
            self._failed("The update wasn't installed: the password prompt was cancelled "
                         "or your user isn't allowed to install packages.")
            return
        if code != 0:
            self._failed(f"The package manager stopped with an error (code {code}) - its "
                         "messages are above. The downloaded file is kept in "
                         f"{self.file.parent}.")
            return
        self.win.add_log(f"Updated to {self.rel.version}. Restarting…")
        U.run_after_exit(U.after_package_command(self._before))
        self._quit_app()

    # ---- Windows setup / Linux ZIP: they replace the app's files, so the app closes first
    def _install_after_exit(self):
        try:
            cmd = U.prepare_after_exit(self.kind, self.file)
            log = paths.data_dir() / "update.log" if self.kind == "linux-zip" else None
            U.run_after_exit(cmd, log)
        except (U.UpdateError, OSError) as e:
            self._failed(str(e))
            return
        self.win.add_log(f"Installing {self.rel.version} - Chatbox Studio closes now.")
        self._quit_app()

    def _quit_app(self):
        self.info.setText("Closing Chatbox Studio…")
        QApplication.processEvents()
        self.accept()
        self.win.close()
        QApplication.quit()


class UpdateChecker:
    """The quiet check on start: at most about once a day, and only a button in the
    window header (never a pop-up while you play)."""

    def __init__(self, win):
        self.win = win
        self.release: U.Release | None = None

    def maybe_check(self):
        s = self.win.s
        if not s.extra.get("update_check", True):
            return
        if time.time() - float(s.extra.get("update_checked_at") or 0) < CHECK_EVERY_S:
            seen = str(s.extra.get("update_seen") or "")
            if seen and U.is_newer(seen) and seen != s.extra.get("update_skip"):
                self.win.show_update_button(seen)      # known from the last check
            return
        check_async(self._on_checked)

    def _on_checked(self, rel, err):
        s = self.win.s
        s.extra["update_checked_at"] = int(time.time())
        if rel is None:
            self.win.save()
            return                          # silent: no internet is fine
        self.release = rel
        s.extra["update_seen"] = rel.version
        self.win.save()
        if U.is_newer(rel.version) and rel.version != s.extra.get("update_skip"):
            self.win.show_update_button(rel.version)
            self.win.add_log(f"{APP_NAME} {rel.version} is available - click "
                             f"“Update” at the top to install it.")

    def check_now(self):
        """Help → Check for updates: always asks GitHub and always answers."""
        self.win.add_log("Checking for updates…")

        def done(rel, err):
            if rel is None:
                self.win.add_log(f"Couldn't check for updates: {err}.")
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self.win, APP_NAME, f"Couldn't check for updates:\n{err}.")
                return
            self.release = rel
            self.win.s.extra["update_checked_at"] = int(time.time())
            self.win.s.extra["update_seen"] = rel.version
            self.win.save()
            if U.is_newer(rel.version):
                self.win.show_update_button(rel.version)
                self.open(rel)
            else:
                self.win.add_log(f"{APP_NAME} {__version__} is the newest version.")
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.information(self.win, APP_NAME,
                                        f"You have the newest version ({__version__}).")
        check_async(done)

    def open(self, rel: U.Release | None = None):
        rel = rel or self.release
        if rel is None:
            self.check_now()
            return
        UpdateDialog(self.win, rel).exec()
