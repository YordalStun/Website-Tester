# Website Tester

Two tools from JJ Hosting for finding out why a website is slow:

- **Website Tester for Windows** tests any website from the outside, the way a visitor sees it.
- **Speed Inspector**, a WordPress plugin, tests a WordPress site from the inside and measures exactly how long each plugin adds to every page (see [Speed Inspector](#speed-inspector-wordpress-plugin) below).

Both have an **Export for AI** button. It saves everything the test measured in one text file, which you can attach in any AI chat (or send to a developer) for a second opinion. Neither tool connects to an AI itself, and nothing is sent anywhere.

## Website Tester for Windows

A Windows app from JJ Hosting. Type in a website address and press **Test website**. It works out why the site is slow and tells you how to fix each problem. It checks whether the slowness comes from the server, DNS, WordPress, a plugin, the theme, images or code.

A full test takes about 1-2 minutes. The app then shows a summary and opens a full report in your web browser. Every report is also saved in `Documents\Website Tester Reports`.

## Getting the app

GitHub builds, tests and publishes the Windows app automatically every time the code changes.

1. On the GitHub page for this repository, click **Releases** (on the right-hand side).
2. Open **Latest build** and download one of these:
   - `WebsiteTester.exe`: a single file. Put it anywhere and double-click it.
   - `WebsiteTester-folder.zip`: the same app as a folder. It starts a bit faster. Unzip it and run `WebsiteTester.exe` inside.
   - `jj-speed-inspector.zip`: the WordPress plugin (see below).

> **"Windows protected your PC"?** This message appears because the app isn't code-signed. Click **More info**, then **Run anyway**. You only need to do this once.

The app doesn't need installing. It uses the Microsoft Edge that is already on every Windows 10/11 PC for the real-browser test, or Google Chrome if Edge is missing.

## What it tests

Everything is tested from the outside, the same way a visitor sees the site. You don't need a WordPress login or passwords. The test only makes normal page requests.

**The answer first.** The top of the report gives a plain-English **diagnosis**, for example *"Main problem: WordPress (no page cache)"*, and explains why. Below that come:
- a traffic-light list showing **where the problem is**: server, DNS, SSL, caching, WordPress, plugins, page content, security, SEO and email
- **where the time goes**, broken down into DNS, connecting, SSL, server, download and drawing the page
- a **Fix these first** list, with step-by-step fixes for each item

**Real browser test.** The page is loaded in Edge like a first-time visitor. This runs twice: once as a desktop PC, and once as a mid-range phone on 4G (the same conditions Google uses). It measures:
- when the main content appears (LCP)
- whether the page freezes (Total Blocking Time)
- whether the page jumps around (CLS)
- every file downloaded, with JavaScript errors, missing files and images far bigger than they're shown
- **how much processor time each plugin's JavaScript uses**, and how much of each plugin's code goes unused
- a screenshot of the page

**Server and hosting.** The home page is visited several times and timed. The test separates network time from the time the server spends working. It then compares:
- a normal visit
- a visit that skips the cache
- a plain file that doesn't involve WordPress at all
- WordPress starting up on its own (`admin-ajax.php`)

That comparison shows **whether the slowness is the hosting or WordPress itself**. It also checks:
- the server's location and distance from you
- the hosting company
- the PHP version (and whether it's out of date)
- HTTP/2 and HTTP/3
- compression
- whether page caching is working
- whether a CDN is in front of the site

**DNS.** Each nameserver is asked directly and timed. The test also checks:
- that every nameserver gives the same answer
- that none of them are dead
- that DNS cache times aren't too short
- how fast big public DNS services resolve the domain
- email records: MX, SPF and DMARC

**WordPress, theme and plugins.** The test finds:
- the WordPress version and whether it's out of date
- the theme, including child and parent themes
- every plugin that is visible from outside, found through page files, the REST API and HTML signatures

For each plugin it then checks:
- the installed version against the latest on WordPress.org
- whether the plugin has been abandoned or removed from WordPress.org
- how many files and how much data it adds to the page

It also gives specific advice for plugins known to be heavy, such as Slider Revolution, WPBakery, Elementor, WooCommerce cart fragments, Query Monitor left switched on, Broken Link Checker and WP Statistics. Other things it spots:
- PHP errors printed on the page
- more than one caching plugin
- calls back to WordPress after the page loads (which can't be cached)
- a public `debug.log`
- XML-RPC switched on
- usernames listed publicly

**Page content.** The test checks the size and number of files, large images, old image formats and missing lazy-loading. It also looks for:
- scripts and CSS that block the page from showing
- uncompressed or unminified files
- missing browser caching
- slow files and files from too many other websites
- fonts, embedded videos and maps
- duplicate copies of the same library (jQuery, for example)
- huge HTML
- insecure (mixed) content

**SSL, security and SEO basics.** The test checks:
- certificate expiry and the TLS version
- that http:// redirects to https://
- security headers
- the page title and description
- the mobile viewport tag
- "noindex" settings and robots.txt blocking Google
- the sitemap

**Google PageSpeed.** Google's own score, plus **real-visitor speed data** from Chrome users when the site gets enough traffic.

### Export for AI

After a test, click **Export for AI...** to save a `.md` text file containing:
- the diagnosis and every finding
- the redirect chain and response headers
- the DNS and server timing samples
- every file the page uses
- the WordPress plugins found
- both browser runs (every request, the Core Web Vitals and JavaScript errors)

Attach the file in an AI chat and ask it what to fix first. Screenshots, passwords and personal data are not included.

### Options

- **Real browser test** and **Phone test**: untick these for a quicker test.
- **Google PageSpeed**: Google allows a limited number of free tests a day without a key. If you see "daily limit reached", get a free API key (see **Settings** for the link) and paste it in.
- **Check plugin updates**: asks WordPress.org for the latest version of each plugin.

### What it can't see

Plugins that only work inside the WordPress admin area leave no trace on the public site, so the real number of plugins may be higher than the report shows. Server internals such as database size and CPU load can't be measured from outside. The report says when it's estimating these from timings.

Results depend a little on where you run the test from and on how busy the server is at that moment. Run the test again after each change you make.

## Command line

```
WebsiteTester.exe example.com --report report.html --text report.txt --export for-ai.md [--no-browser] [--no-mobile] [--no-pagespeed] [--no-updates]
WebsiteTester.exe --selftest
```

## Speed Inspector (WordPress plugin)

Speed Inspector works from inside WordPress, so it can measure things no outside test can see. Supports PHP 7.4.33 and newer, and WordPress 5.8 and newer.

**Installing:** download `jj-speed-inspector.zip` from **Releases > Latest build**. In WordPress, go to **Plugins > Add New > Upload Plugin**, choose the zip, then click **Install Now** and **Activate**. Then open **Tools > Speed Inspector** and press **Run speed test**.

**What it does:** it visits the site's own pages as a logged-out visitor, several times each, with the page cache skipped. It tests the home page, the newest post, a page, the shop (if WooCommerce is installed) and any page you add. During those visits it records:
- **how long each plugin and the theme add to every page**, including the time spent loading their files and running their actions/filters. For example: "Slider Revolution adds 420 ms to every page (38%)".
- **every database query**, and which plugin ran it, so slow queries can be traced to their plugin
- **every call to another website** while building a page, and which plugin made it (licence checks, feeds and APIs are classic hidden causes of slow sites)
- **PHP warnings and errors**, and which plugin caused them
- **whether page caching actually works**, by comparing a normal visit with a page built from scratch

It also checks:
- **the server:** PHP version, OPcache, memory limit, a processor speed test compared with a typical good host, database speed and version, and disk space
- **the database:** autoloaded options (with the plugins they probably belong to), huge tables, old MyISAM tables, expired transients, revisions, spam and orphaned data
- **caching:** object cache (and whether Redis is available but unused), and more than one caching plugin active at once
- **scheduled tasks:** overdue WP-Cron jobs, WP-Cron switched off with no real cron, very frequent tasks, tasks left behind by deleted plugins, and stuck Action Scheduler queues
- **settings and updates:** debug mode left on, huge error logs, unlimited revisions, and outdated plugins, theme or WordPress

The result is a grade, a "Main problem: ..." diagnosis, a breakdown of where the time goes, a time-per-plugin table and a "Fix these first" list. **Export for AI (.md file)** downloads all of it, including the raw measurements from every test visit.

**Safety:**
- Profiling only switches on for test visits that carry a one-time secret key, so normal visitors are never affected.
- A tiny early loader in `wp-content/mu-plugins` lets it time how long plugins take to load. That loader does nothing unless a test is running.
- Deactivating the plugin removes the loader, and deleting the plugin removes everything it stored.
- If the host blocks the site from calling itself, your browser makes the test visits instead.

**WP-CLI:** `wp speed-inspector run [--page=<url>] [--export=<file>] [--format=json]`

## For developers

- Python 3.12, PySide6 for the window. dnspython handles DNS, and websocket-client talks to Edge/Chrome over the DevTools protocol (the same way Google Lighthouse does).
- `python run.py` starts the app. `python -m pytest` runs the tests: they use a deliberately badly set-up fake WordPress site (`websitetester/fakesite.py`), plus the real-browser test when Edge, Chrome or Chromium is installed.
- `build.bat` builds `dist\WebsiteTester.exe` on a Windows PC.
- The WordPress plugin is in `wordpress-plugin/jj-speed-inspector`. The **WordPress plugin** workflow tests it on PHP 7.4 + WordPress 5.8, PHP 7.4 + the latest WordPress, and PHP 8.3 + the latest WordPress. Each test runs against a deliberately slow test plugin (`wordpress-plugin/tests/slowpoke`), drives the real admin page in a browser, checks the browser fallback for when the site can't call itself, and checks that uninstalling cleans up.
- On every push, GitHub Actions:
  1. runs the tests
  2. builds the `.exe`
  3. self-tests the built `.exe` with Edge on Windows
  4. tests a few real websites (the reports are attached to the run as `live-test-reports`)
  5. publishes the **Latest build** release
