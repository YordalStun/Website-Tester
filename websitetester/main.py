"""Start the app, or run from the command line:

    WebsiteTester.exe                                   the normal window
    WebsiteTester.exe --selftest                        prove the build works (tests a fake site on this PC)
    WebsiteTester.exe example.com --report out.html [--text out.txt] [--export out.md] [--no-browser] [--no-pagespeed]
"""
import os
import sys
import tempfile
import traceback
from pathlib import Path

from . import APP_NAME


def selftest():
    out = os.environ.get("WT_SELFTEST_OUT") or os.path.join(tempfile.gettempdir(), "websitetester-selftest.txt")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    steps = []
    notes = []
    try:
        from . import browser, report, runner
        from .fakesite import FakeSite

        with FakeSite() as url:
            options = runner.Options(pagespeed=False, check_updates=False, location=False, samples=3)
            r = runner.run(url, options)
        steps.append("test-run")
        assert r.is_wp, "WordPress not detected"
        titles = " | ".join(f.title for f in r.findings)
        for expected in ("No page caching found", "Error messages are shown on the page", "PHP version",
                         "debug.log", "Slider Revolution"):
            assert expected in titles, f"missing finding: {expected}"
        assert len(r.wp["plugins"]) >= 4, "plugins not found"
        steps.append("findings")
        path, name = browser.find_browser()
        if path:
            d = r.browser.get("desktop") or {}
            assert not d.get("error"), f"browser test failed: {d.get('error')}"
            assert d.get("lcp"), "no LCP measured"
            assert d.get("coverage"), "no code coverage measured"
            steps.append(f"browser ({name})")
            notes.append(f"desktop LCP {d['lcp']:.0f} ms, mobile LCP {(r.browser.get('mobile') or {}).get('lcp') or 0:.0f} ms")
        else:
            notes.append("no browser found - browser test not checked")
        html = report.html_report(r)
        assert "Fix these first" in html and r.domain in html
        from .export import ai_export
        text = ai_export(r)
        assert "## Findings" in text and "## Every file the page uses" in text
        assert "/9j/" not in text, "screenshots must not be in the export"  # base64 JPEG data
        steps.append("report")

        import dns.rdata
        for kind, text in (("A", "1.2.3.4"), ("AAAA", "::1"), ("NS", "ns1.example.com."), ("MX", "10 mail.example.com."),
                           ("TXT", '"v=spf1 -all"'), ("CNAME", "example.com."), ("PTR", "example.com.")):
            dns.rdata.from_text("IN", kind, text)  # proves dnspython's record types were packaged
        steps.append("dns")

        from PySide6.QtWidgets import QApplication
        from . import ui
        app = QApplication([sys.argv[0]])
        app.setStyle("Fusion")
        ui.apply_light_theme(app)
        win = ui.MainWindow()
        win.summary.setHtml(report.summary_html(r))
        win.show()
        app.processEvents()
        win.close()
        steps.append("ui")
        result, code = "OK " + ", ".join(steps) + "\n" + "\n".join(notes) + "\n\n" + runner.plain_text_summary(r), 0
    except Exception:
        result, code = "FAILED after " + ", ".join(steps) + "\n" + traceback.format_exc(), 1
    with open(out, "w", encoding="utf-8") as f:
        f.write(result)
    print(result)
    return code


def command_line(args):
    from . import report, runner

    def take(flag):
        if flag in args:
            i = args.index(flag)
            value = args[i + 1]
            del args[i:i + 2]
            return value
        return None
    html_out, text_out, export_out = take("--report"), take("--text"), take("--export")
    options = runner.Options()
    for flag, attr in (("--no-browser", "browser"), ("--no-mobile", "mobile"), ("--no-pagespeed", "pagespeed"),
                       ("--no-updates", "check_updates")):
        if flag in args:
            args.remove(flag)
            setattr(options, attr, False)
    if not args:
        print(__doc__)
        return 2
    r = runner.run(args[0], options, progress=lambda text, pct: print(f"{pct:3d}% {text}", flush=True))
    text = runner.plain_text_summary(r)
    if html_out:
        Path(html_out).write_text(report.html_report(r), encoding="utf-8")
    if text_out:
        Path(text_out).write_text(text, encoding="utf-8")
    if export_out:
        from .export import ai_export
        Path(export_out).write_text(ai_export(r), encoding="utf-8")
    print(text)
    return 0


def main():
    args = sys.argv[1:]
    if "--selftest" in args:
        return selftest()
    if args:
        return command_line(list(args))
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("JJHosting.WebsiteTester")
        except Exception:
            pass
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication
    from . import ui

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("JJ Hosting")
    app.setStyle("Fusion")
    ui.apply_light_theme(app)
    app.setWindowIcon(ui.app_icon())
    app.setFont(QFont("Segoe UI" if sys.platform == "win32" else app.font().family(), 10))
    win = ui.MainWindow()
    win.show()
    return app.exec()
