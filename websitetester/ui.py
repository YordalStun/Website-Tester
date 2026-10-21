"""The app window: type a website, press Test, read the answer."""
import os
import re
import sys
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPalette
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QSizePolicy, QTextBrowser, QVBoxLayout, QWidget)

from . import APP_NAME, VERSION, browser, export, report, runner

STYLE = """
QWidget { color: #1f2329; }
QMainWindow, QWidget#central { background: #f6f7f9; }
QCheckBox { spacing: 6px; }
QComboBox QAbstractItemView { background: white; color: #1f2329; selection-background-color: #e8f0fe;
                              selection-color: #1f2329; }
QLabel#title { font-size: 20px; font-weight: 600; color: #1f2329; }
QLabel#subtitle { color: #5f6368; }
QFrame#card { background: white; border: 1px solid #e3e6ea; border-radius: 10px; }
QComboBox { padding: 6px 8px; font-size: 15px; border: 1px solid #c8ccd1; border-radius: 6px; background: white; }
QPushButton { padding: 6px 14px; border-radius: 6px; border: 1px solid #c8ccd1; background: white; }
QPushButton:hover { background: #f1f3f4; }
QPushButton:disabled { color: #9aa0a6; }
QPushButton#primary { background: #1a73e8; color: white; border: 1px solid #1a73e8; font-weight: 600; font-size: 14px;
                      padding: 8px 20px; }
QPushButton#primary:hover { background: #1765cc; }
QPushButton#primary:disabled { background: #9ec2f5; border-color: #9ec2f5; }
QTextBrowser { border: none; background: white; font-size: 14px; }
QProgressBar { border: 1px solid #c8ccd1; border-radius: 6px; text-align: center; background: white; height: 18px; }
QProgressBar::chunk { background: #1a73e8; border-radius: 5px; }
QPlainTextEdit { background: white; border: 1px solid #e3e6ea; border-radius: 6px; font-family: Consolas, monospace;
                 font-size: 12px; }
"""


def apply_light_theme(app):
    """Always use the app's own light colours. Without this, Windows dark mode gives white text, which is
    invisible on the app's white boxes."""
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    except AttributeError:  # older Qt
        pass
    pal = QPalette()
    colors = {
        QPalette.Window: "#f6f7f9", QPalette.WindowText: "#1f2329", QPalette.Base: "#ffffff",
        QPalette.AlternateBase: "#f1f3f4", QPalette.Text: "#1f2329", QPalette.Button: "#ffffff",
        QPalette.ButtonText: "#1f2329", QPalette.ToolTipBase: "#ffffff", QPalette.ToolTipText: "#1f2329",
        QPalette.Highlight: "#1a73e8", QPalette.HighlightedText: "#ffffff", QPalette.Link: "#1a73e8",
        QPalette.PlaceholderText: "#80868b", QPalette.BrightText: "#ffffff",
    }
    for role, color in colors.items():
        pal.setColor(QPalette.All, role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor("#9aa0a6"))
    app.setPalette(pal)


def reports_folder():
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:  # My Documents
                base = Path(buf.value)
            else:
                base = Path.home() / "Documents"
        except Exception:
            base = Path.home() / "Documents"
    else:
        base = Path.home() / "Documents"
    folder = Path(os.environ.get("WT_REPORTS_DIR") or base / "Website Tester Reports")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def save_report(result, folder=None):
    folder = Path(folder or reports_folder())
    safe = re.sub(r"[^a-zA-Z0-9.\-]+", "_", result.domain)
    path = folder / f"{safe} {result.started:%Y-%m-%d %H%M}.html"
    path.write_text(report.html_report(result), encoding="utf-8")
    return path


class TestThread(QThread):
    progressed = Signal(str, int)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, target, options):
        super().__init__()
        self.target = target
        self.options = options
        self.cancel_event = threading.Event()

    def run(self):
        try:
            result = runner.run(self.target, self.options, progress=self.progressed.emit,
                                cancel_event=self.cancel_event)
            self.finished_ok.emit(result)
        except Exception as e:  # shown to the user rather than crashing the app
            import traceback
            self.failed.emit(f"{e}\n\n{traceback.format_exc()}")


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.settings = settings
        form = QFormLayout(self)
        self.key = QLineEdit(settings.value("pagespeed_key", ""))
        self.key.setPlaceholderText("optional")
        self.key.setMinimumWidth(360)
        form.addRow("Google PageSpeed API key:", self.key)
        help_text = QLabel("Google allows a limited number of free PageSpeed tests without a key. If you see "
                           "\"daily limit reached\", get a free key at <a href='https://developers.google.com/speed/"
                           "docs/insights/v5/get-started'>developers.google.com</a> (\"Get a Key\") and paste it here.")
        help_text.setWordWrap(True)
        help_text.setOpenExternalLinks(True)
        form.addRow(help_text)
        self.folder = QLabel(str(reports_folder()))
        self.folder.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow("Reports are saved in:", self.folder)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def save(self):
        self.settings.setValue("pagespeed_key", self.key.text().strip())
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("JJ Hosting", APP_NAME)
        self.thread = None
        self.result = None
        self.report_path = None
        self.setWindowTitle(f"{APP_NAME} {VERSION}")
        self.resize(1000, 780)
        self.setStyleSheet(STYLE)

        central = QWidget(objectName="central")
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(10)

        title = QLabel(APP_NAME, objectName="title")
        sub = QLabel("Type a website address and press Test. It finds out what's making the site slow - the server, "
                     "DNS, WordPress, plugins, images or code - and tells you how to fix it.", objectName="subtitle")
        sub.setWordWrap(True)
        outer.addWidget(title)
        outer.addWidget(sub)

        card = QFrame(objectName="card")
        card_layout = QVBoxLayout(card)
        row = QHBoxLayout()
        self.address = QComboBox()
        self.address.setEditable(True)
        self.address.setInsertPolicy(QComboBox.NoInsert)
        self.address.lineEdit().setPlaceholderText("example.com")
        self.address.addItems(self.settings.value("recent", []) or [])
        self.address.setCurrentText("")
        self.address.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.address.lineEdit().returnPressed.connect(self.start)
        self.test_button = QPushButton("Test website", objectName="primary")
        self.test_button.clicked.connect(self.start)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop)
        self.stop_button.setEnabled(False)
        row.addWidget(self.address)
        row.addWidget(self.test_button)
        row.addWidget(self.stop_button)
        card_layout.addLayout(row)

        opts = QHBoxLayout()
        path, name = browser.find_browser()
        self.opt_browser = QCheckBox(f"Real browser test ({name})" if name else "Real browser test (needs Edge or Chrome)")
        self.opt_browser.setEnabled(bool(path))
        self.opt_mobile = QCheckBox("Phone test")
        self.opt_psi = QCheckBox("Google PageSpeed")
        self.opt_updates = QCheckBox("Check plugin updates")
        self.opt_open = QCheckBox("Open full report when done")
        for box, key in ((self.opt_browser, "opt_browser"), (self.opt_mobile, "opt_mobile"), (self.opt_psi, "opt_psi"),
                         (self.opt_updates, "opt_updates"), (self.opt_open, "opt_open")):
            box.setChecked(self.settings.value(key, True, type=bool) and box.isEnabled())
            box.toggled.connect(lambda checked, k=key: self.settings.setValue(k, checked))
            opts.addWidget(box)
        self.opt_mobile.setEnabled(bool(path))
        opts.addStretch()
        settings_button = QPushButton("Settings")
        settings_button.clicked.connect(lambda: SettingsDialog(self.settings, self).exec())
        opts.addWidget(settings_button)
        card_layout.addLayout(opts)

        prog = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status = QLabel("Ready. A full test takes about 1-2 minutes.")
        self.status.setObjectName("subtitle")
        prog.addWidget(self.progress, 1)
        card_layout.addLayout(prog)
        card_layout.addWidget(self.status)
        outer.addWidget(card)

        self.summary = QTextBrowser()
        self.summary.setOpenExternalLinks(True)
        self.summary.setHtml(self._welcome())
        outer.addWidget(self.summary, 1)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)
        self.log.setVisible(False)
        outer.addWidget(self.log)

        buttons = QHBoxLayout()
        self.open_button = QPushButton("Open full report")
        self.open_button.clicked.connect(self.open_report)
        self.save_button = QPushButton("Save a copy...")
        self.save_button.clicked.connect(self.save_copy)
        self.export_button = QPushButton("Export for AI...")
        self.export_button.setToolTip("Save everything this test measured in one text file you can give to an AI "
                                      "assistant (or a developer) to analyse. Nothing is sent anywhere.")
        self.export_button.clicked.connect(self.export_for_ai)
        folder_button = QPushButton("Reports folder")
        folder_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(reports_folder()))))
        self.log_button = QPushButton("Show test log")
        self.log_button.clicked.connect(self.toggle_log)
        for b in (self.open_button, self.save_button, self.export_button):
            b.setEnabled(False)
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.export_button)
        buttons.addWidget(folder_button)
        buttons.addStretch()
        buttons.addWidget(self.log_button)
        outer.addLayout(buttons)

    def _welcome(self):
        return ("<h2>What this checks</h2><ul>"
                "<li><b>Speed in a real browser</b> - desktop and a simulated phone on 4G: when the main content "
                "appears, whether the page freezes or jumps around, and which files are slowest.</li>"
                "<li><b>The server</b> - how long it takes to answer, where it is, the hosting company, PHP version, "
                "HTTP/2, compression, and whether the slowness is the hosting or WordPress itself.</li>"
                "<li><b>DNS</b> - how quickly the domain's nameservers answer and whether they're set up properly "
                "(plus email records).</li>"
                "<li><b>WordPress</b> - version, theme, every plugin we can see (outdated, abandoned, heavy, broken), "
                "page caching, and calls back to WordPress that can't be cached.</li>"
                "<li><b>Page content</b> - images, CSS, JavaScript, fonts, third-party services, browser caching.</li>"
                "<li><b>SSL, security and SEO basics.</b></li></ul>"
                "<p style='color:#5f6368'>Everything is checked from the outside, like a visitor - no passwords or "
                "WordPress login needed. The test only makes normal page requests.</p>")

    def toggle_log(self):
        self.log.setVisible(not self.log.isVisible())
        self.log_button.setText("Hide test log" if self.log.isVisible() else "Show test log")

    def start(self):
        if self.thread is not None:
            return
        target = self.address.currentText().strip()
        try:
            url = runner.normalise(target)
        except ValueError as e:
            QMessageBox.information(self, APP_NAME, str(e))
            return
        recent = [target] + [r for r in (self.settings.value("recent", []) or []) if r != target]
        self.settings.setValue("recent", recent[:15])
        self.address.blockSignals(True)
        self.address.clear()
        self.address.addItems(recent[:15])
        self.address.setCurrentText(target)
        self.address.blockSignals(False)

        options = runner.Options(
            browser=self.opt_browser.isChecked(), mobile=self.opt_mobile.isChecked() and self.opt_browser.isChecked(),
            pagespeed=self.opt_psi.isChecked(), check_updates=self.opt_updates.isChecked(),
            pagespeed_key=self.settings.value("pagespeed_key", ""))
        self.log.clear()
        self.log.appendPlainText(f"{datetime.now():%H:%M:%S} Testing {url}")
        self.summary.setHtml(f"<h2>Testing {report.e(url)}...</h2><p>This takes about 1-2 minutes. You can keep "
                             "using your PC.</p>")
        self.progress.setValue(0)
        self.test_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.open_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.thread = TestThread(target, options)
        self.thread.progressed.connect(self.on_progress)
        self.thread.finished_ok.connect(self.on_done)
        self.thread.failed.connect(self.on_failed)
        self.thread.start()

    def stop(self):
        if self.thread:
            self.thread.cancel_event.set()
            self.status.setText("Stopping...")
            self.stop_button.setEnabled(False)

    def on_progress(self, text, pct):
        self.progress.setValue(pct)
        self.status.setText(text)
        last = self.log.toPlainText().rsplit("\n", 1)[-1]
        if not last.endswith(text):
            self.log.appendPlainText(f"{datetime.now():%H:%M:%S} {text}")

    def _finish_thread(self):
        if self.thread:
            self.thread.wait(2000)
        self.thread = None
        self.test_button.setEnabled(True)
        self.stop_button.setEnabled(False)

    def on_done(self, result):
        self._finish_thread()
        self.result = result
        try:
            self.report_path = save_report(result)
        except OSError as e:
            self.report_path = None
            QMessageBox.warning(self, APP_NAME, f"The report couldn't be saved: {e}")
        self.summary.setHtml(report.summary_html(result))
        self.progress.setValue(100)
        self.status.setText(f"Finished in {result.duration:.0f} seconds."
                            + (f" Report saved to {self.report_path}" if self.report_path else ""))
        for line in result.errors:
            self.log.appendPlainText(line)
        self.open_button.setEnabled(bool(self.report_path))
        self.save_button.setEnabled(True)
        self.export_button.setEnabled(True)
        if self.report_path and self.opt_open.isChecked():
            self.open_report()

    def on_failed(self, message):
        self._finish_thread()
        self.status.setText("The test couldn't run.")
        self.log.appendPlainText(message)
        self.summary.setHtml(f"<h2>The test couldn't run</h2><p>{report.e(message.splitlines()[0])}</p>")

    def open_report(self):
        if self.report_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.report_path)))

    def save_copy(self):
        if not self.result:
            return
        default = str(reports_folder() / (self.report_path.name if self.report_path else "report.html"))
        path, _ = QFileDialog.getSaveFileName(self, "Save report", default, "Web page (*.html)")
        if path:
            Path(path).write_text(report.html_report(self.result), encoding="utf-8")

    def export_for_ai(self):
        if not self.result:
            return
        safe = re.sub(r"[^a-zA-Z0-9.\-]+", "_", self.result.domain)
        default = str(reports_folder() / f"{safe} {self.result.started:%Y-%m-%d %H%M} for AI.md")
        path, _ = QFileDialog.getSaveFileName(self, "Export for AI", default, "Text file for AI (*.md);;Text file (*.txt)")
        if path:
            Path(path).write_text(export.ai_export(self.result), encoding="utf-8")
            self.status.setText(f"Exported everything to {path} - you can attach that file in any AI chat.")

    def closeEvent(self, event):
        if self.thread:
            self.thread.cancel_event.set()
            self.thread.wait(15000)
        super().closeEvent(event)


def app_icon():
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    path = base / "resources" / "icon.png"
    return QIcon(str(path)) if path.exists() else QIcon()
