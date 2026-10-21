"""Drive the Speed Inspector admin page in a real browser.

    python admin_ui_check.py http://127.0.0.1:8899 admin admin out_dir

Logs in, opens Tools > Speed Inspector, runs a test, waits for the report, saves a screenshot and downloads the
"Export for AI" file. Exits non-zero if anything is missing.
"""
import os
import sys

from playwright.sync_api import sync_playwright


def main():
    site, user, password, out = sys.argv[1:5]
    os.makedirs(out, exist_ok=True)
    chromium = os.environ.get("CHROMIUM")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chromium) if chromium else p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900}, accept_downloads=True)
        try:
            check(page, site, user, password, out)
        except BaseException:
            # Keep evidence of what the page looked like when it went wrong
            try:
                print("FAILED on:", page.url, "-", page.title())
                print((page.inner_text("body") or "")[:1500])
            except Exception:
                pass
            try:
                page.screenshot(path=os.path.join(out, "failure.png"), full_page=True)
                with open(os.path.join(out, "failure.html"), "w", encoding="utf-8") as f:
                    f.write(page.content())
            except Exception:
                pass
            raise
        browser.close()
    print("OK")


def check(page, site, user, password, out):
    page.goto(f"{site}/wp-login.php", wait_until="domcontentloaded")
    page.fill("#user_login", user)
    page.fill("#user_pass", password)
    page.click("#wp-submit")
    # Don't wait for the whole dashboard to load (its widgets can be slow) - just for the login to go through,
    # or for WordPress to show a login error
    page.wait_for_function(
        "() => location.pathname.indexOf('/wp-admin') !== -1 || !!document.getElementById('login_error')",
        timeout=120000)
    if "/wp-admin" not in page.url:
        raise SystemExit("login failed: " + (page.text_content("#login_error") or "").strip())
    page.goto(f"{site}/wp-admin/tools.php?page=jj-speed-inspector", wait_until="domcontentloaded", timeout=120000)
    before = page.get_attribute(".jjsi-hero", "data-created") if page.query_selector(".jjsi-hero") else ""
    page.click("#jjsi-run")
    # The page reloads with the new report when the test is done; if the test stops, it says so instead
    page.wait_for_function(
        """(before) => {
            const status = document.getElementById('jjsi-status');
            if (status && status.textContent.indexOf('stopped') !== -1) return true;
            const hero = document.querySelector('.jjsi-hero');
            return !!hero && hero.getAttribute('data-created') !== before;
        }""",
        arg=before or "", timeout=300000)
    status = page.text_content("#jjsi-status") or ""
    if "stopped" in status:
        raise SystemExit("test stopped: " + status)
    headline = page.text_content(".jjsi-hero h2")
    print("Headline:", headline)
    page.screenshot(path=os.path.join(out, "admin-report.png"), full_page=True)
    with page.expect_download() as info:
        page.click("text=Export for AI")
    download = info.value
    path = os.path.join(out, download.suggested_filename)
    download.save_as(path)
    text = open(path, encoding="utf-8").read()
    print("Export:", download.suggested_filename, len(text), "characters")
    for needed in ("# Speed Inspector log export", "## Findings", "## Time per plugin", "## Raw measurements"):
        if needed not in text:
            raise SystemExit(f"export is missing: {needed}")
    if "Slowpoke" not in (headline or ""):
        raise SystemExit("Slowpoke wasn't named as the main problem")


if __name__ == "__main__":
    main()
