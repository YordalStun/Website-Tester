=== Speed Inspector ===
Contributors: jjhosting
Tags: performance, speed, profiler, slow, diagnostics
Requires at least: 5.8
Tested up to: 7.2
Requires PHP: 7.4
Stable tag: 1.1.0
License: GPLv2 or later
License URI: https://www.gnu.org/licenses/gpl-2.0.html

Finds out exactly what is slowing your WordPress site down and tells you how to fix it.

== Description ==

Go to **Tools > Speed Inspector** and press **Run speed test**. In about a minute you get:

* A plain-English diagnosis: "Main problem: <plugin> adds 850 ms to every page because it calls api.example.com".
* How long each plugin and the theme adds to every page, including their database queries and calls to other websites.
* Slow database queries, and which plugin ran them.
* Calls to other websites made while building pages (a classic cause of slow sites).
* PHP warnings and errors, and which plugin caused them.
* Server checks: PHP version, OPcache, a processor speed test, database speed.
* Caching: whether page caching actually works, object cache (Redis), conflicting cache plugins.
* Database: autoloaded options (and which plugin they belong to), huge tables, MyISAM tables, clean-up candidates.
* Scheduled tasks: overdue WP-Cron jobs, stuck Action Scheduler queues, tasks left behind by deleted plugins.
* Settings: debug mode left on, outdated plugins/theme/WordPress.
* **Export for AI**: download everything as one text file to hand to an AI assistant or developer for analysis.

How it measures: the site's own pages (home, newest post, a page, the shop) are visited several times from the server
itself as a logged-out visitor, with page caching skipped, using a one-time secret key. Only those test visits are
measured - normal visitors are never affected. Nothing is sent outside your site.

If your host blocks the server from visiting its own pages, the test visits are made by your browser instead.

A tiny "early loader" is added to wp-content/mu-plugins so the time each plugin takes to load can be measured. It does
nothing unless a test is running, and is removed when Speed Inspector is deactivated.

WP-CLI: `wp speed-inspector run [--page=<url>] [--export=<file>] [--format=json]`

== Changelog ==

= 1.1.0 =
* Measures how long the server takes to reach its own website, and corrects "normal visit" times for it.
* WP-Cron calls to the site itself are reported as scheduled tasks making visitors wait, not as "other websites".
* Page caching is only counted as on when it really runs (a leftover advanced-cache.php with WP_CACHE off doesn't count).
* Detects permalinks being rebuilt on every page load, and finds the code that does it.
* Shows what fills a huge database (post types and metadata), and tables/settings left by removed plugins.
* WordPress's own scheduled tasks are no longer listed as orphans.
* PHP warnings inside WordPress's files are blamed on the plugin or theme that caused them.
* The one-time test key is removed from the export file.

= 1.0.0 =
* First version.
