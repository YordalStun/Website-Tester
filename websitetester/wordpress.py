"""WordPress: version, theme, plugins (with update checks against WordPress.org) and common WordPress problems."""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote, urljoin, urlsplit

from . import net
from .findings import CRITICAL, GOOD, INFO, WARNING, Finding
from .pagecheck import kb, short_url

PLUGIN_PATH = re.compile(r"/wp-content/plugins/([a-zA-Z0-9_.\-]+)/", re.I)
THEME_PATH = re.compile(r"/wp-content/themes/([a-zA-Z0-9_.\-]+)/", re.I)
VER_PARAM = re.compile(r"[?&]ver=([0-9][0-9a-zA-Z.\-]*)")

CACHE_PLUGINS = {
    "wp-rocket": "WP Rocket", "litespeed-cache": "LiteSpeed Cache", "w3-total-cache": "W3 Total Cache",
    "wp-super-cache": "WP Super Cache", "wp-fastest-cache": "WP Fastest Cache", "sg-cachepress": "SiteGround Speed Optimizer",
    "breeze": "Breeze", "hummingbird-performance": "Hummingbird", "cache-enabler": "Cache Enabler",
    "swift-performance-lite": "Swift Performance", "swift-performance": "Swift Performance", "comet-cache": "Comet Cache",
    "wp-optimize": "WP-Optimize", "flying-press": "FlyingPress", "nitropack": "NitroPack",
    "powered-cache": "Powered Cache", "cloudflare": "Cloudflare (plugin)", "jetpack-boost": "Jetpack Boost",
}
OPTIMISE_PLUGINS = {"autoptimize": "Autoptimize", "perfmatters": "Perfmatters", "asset-cleanup": "Asset CleanUp",
                    "wp-asset-clean-up": "Asset CleanUp", "flying-scripts": "Flying Scripts"}

# slug -> (severity if found, advice). Only plugins with a well known speed/server cost.
KNOWN_PLUGINS = {
    "elementor": (INFO, "Elementor is a heavy page builder. Turn on its speed options in Elementor > Settings > "
                        "Features/Performance (Improved CSS loading, Optimized DOM output, Inline font icons, "
                        "Lazy load background images) and stop it loading Google Fonts / Font Awesome if unused."),
    "elementor-pro": (INFO, "Adds more Elementor scripts - only use the widgets you need."),
    "js_composer": (WARNING, "WPBakery Page Builder adds a lot of CSS/JS to every page. If possible rebuild key pages "
                             "with the block editor or a lighter builder."),
    "revslider": (WARNING, "Slider Revolution is one of the heaviest plugins (often 500 KB+ of scripts). Only load it "
                           "on pages that use a slider (its Global Settings > \"Include libraries globally\" = Off), or "
                           "replace the slider with a single image."),
    "LayerSlider": (WARNING, "LayerSlider is heavy. Only load it on pages that have a slider, or replace the slider "
                             "with a single image."),
    "smart-slider-3": (INFO, "Sliders slow the top of the page down - consider a single static image."),
    "master-slider": (WARNING, "Master Slider is heavy and no longer maintained - replace it."),
    "contact-form-7": (INFO, "Contact Form 7 loads its CSS/JS on every page, not just the contact page. Use Perfmatters/"
                             "Asset CleanUp to load it only where the form is."),
    "woocommerce": (INFO, "WooCommerce adds scripts to every page. Disable its scripts and \"cart fragments\" on "
                          "non-shop pages (Perfmatters, or the \"Disable Cart Fragments\" plugin)."),
    "jetpack": (INFO, "Jetpack is many plugins in one. Turn off every module you don't use (Jetpack > Settings)."),
    "wordfence": (INFO, "Wordfence's firewall runs on every page load and its scans use a lot of server power on "
                        "small hosting plans. If the server is slow, set scans to \"low resource\" mode."),
    "query-monitor": (WARNING, "Query Monitor is a developer debugging tool - it slows the site and should not be left "
                               "active on a live site."),
    "broken-link-checker": (WARNING, "Broken Link Checker is well known for using lots of server power and database "
                                     "space. Deactivate it and run link checks occasionally with an online tool."),
    "wp-statistics": (WARNING, "WP Statistics writes to the database on every visit, which slows uncached pages and "
                               "bloats the database. Google Analytics or a lightweight tool is lighter."),
    "wp-slimstat": (WARNING, "Slimstat writes to the database on every visit - heavy on a busy site."),
    "yet-another-related-posts-plugin": (WARNING, "YARPP runs heavy database queries. Use a lighter related-posts "
                                                  "option."),
    "wordpress-popular-posts": (INFO, "Popular Posts tracks views in the database - enable its caching options."),
    "nextgen-gallery": (WARNING, "NextGEN Gallery is heavy. Consider the built-in WordPress gallery block."),
    "google-analytics-for-wordpress": (INFO, "MonsterInsights adds extra scripts; a plain Google tag (or Site Kit) is "
                                             "lighter."),
    "instagram-feed": (INFO, "Social feeds pull data and images from other sites - load them lower down / lazily."),
    "custom-facebook-feed": (INFO, "Social feeds pull data and images from other sites - load them lower down / lazily."),
    "essential-addons-for-elementor-lite": (INFO, "Turn off every Essential Addons widget you don't use "
                                                  "(Essential Addons > Elements)."),
    "the-plus-addons-for-elementor-page-builder": (INFO, "Turn off unused widgets in The Plus Addons."),
    "premium-addons-for-elementor": (INFO, "Turn off unused widgets in Premium Addons."),
    "ultimate-elementor": (INFO, "Turn off unused widgets in Ultimate Addons."),
    "divi-builder": (INFO, "Turn on Divi's performance options (Divi > Theme Options > Performance)."),
    "fusion-builder": (INFO, "Avada's builder: use Avada > Performance wizard to turn off unused features."),
    "bb-plugin": (INFO, "Beaver Builder - keep its modules to what you use."),
    "wpforms-lite": (INFO, "WPForms is fine, but check it only loads on pages with a form."),
    "tawkto-live-chat": (INFO, "Live chat widgets load a lot of JavaScript - delay them until the visitor interacts."),
    "wp-google-maps": (INFO, "Google Maps embeds are heavy - use a static map image linking to Google Maps."),
    "all-in-one-wp-migration": (INFO, "Migration plugins aren't needed day to day - deactivate when not in use."),
    "duplicator": (INFO, "Migration plugins aren't needed day to day - deactivate when not in use (old backup files "
                         "left on the server can also be a security risk)."),
    "updraftplus": (INFO, "Make sure backups run at night - backups are hard work for the server."),
    "really-simple-ssl": (INFO, "Once the site is fully on HTTPS, Really Simple SSL's redirects can usually be done in "
                                ".htaccess instead."),
    "wp-smushit": (INFO, "Image optimiser - good. Make sure WebP and lazy loading are switched on."),
}

# Friendly names for common plugins (used when WordPress.org can't be asked)
PLUGIN_NAMES = {
    "elementor": "Elementor", "elementor-pro": "Elementor Pro", "js_composer": "WPBakery Page Builder",
    "revslider": "Slider Revolution", "LayerSlider": "LayerSlider", "smart-slider-3": "Smart Slider 3",
    "contact-form-7": "Contact Form 7", "woocommerce": "WooCommerce", "jetpack": "Jetpack", "wordfence": "Wordfence",
    "wordpress-seo": "Yoast SEO", "seo-by-rank-math": "Rank Math SEO", "all-in-one-seo-pack": "All in One SEO",
    "query-monitor": "Query Monitor", "broken-link-checker": "Broken Link Checker", "wp-statistics": "WP Statistics",
    "google-analytics-for-wordpress": "MonsterInsights", "wpforms-lite": "WPForms", "divi-builder": "Divi Builder",
    "fusion-builder": "Avada Builder", "bb-plugin": "Beaver Builder", "nextgen-gallery": "NextGEN Gallery",
    "instagram-feed": "Smash Balloon Instagram Feed", "custom-facebook-feed": "Smash Balloon Facebook Feed",
    "essential-addons-for-elementor-lite": "Essential Addons for Elementor", "updraftplus": "UpdraftPlus",
    "all-in-one-wp-migration": "All-in-One WP Migration", "duplicator": "Duplicator", "wp-smushit": "Smush",
    "really-simple-ssl": "Really Simple SSL", "google-site-kit": "Site Kit by Google", "akismet": "Akismet",
    "mailchimp-for-wp": "MC4WP: Mailchimp for WordPress", "the-events-calendar": "The Events Calendar",
    "gravityforms": "Gravity Forms", "complianz-gdpr": "Complianz", "cookie-law-info": "CookieYes",
    **CACHE_PLUGINS, **OPTIMISE_PLUGINS,
}

# WordPress REST API "namespace" -> plugin slug. Lets us find plugins that don't load anything visible.
REST_NAMESPACES = {
    "yoast/v1": "wordpress-seo", "rankmath/v1": "seo-by-rank-math", "aioseo/v1": "all-in-one-seo-pack",
    "elementor/v1": "elementor", "elementor-pro/v1": "elementor-pro", "wc/v3": "woocommerce", "wc/store": "woocommerce",
    "contact-form-7/v1": "contact-form-7", "jetpack/v4": "jetpack", "litespeed/v1": "litespeed-cache",
    "litespeed/v3": "litespeed-cache", "wp-rocket/v1": "wp-rocket", "redirection/v1": "redirection",
    "wordfence/v1": "wordfence", "akismet/v1": "akismet", "mc4wp/v1": "mailchimp-for-wp",
    "tribe/events/v1": "the-events-calendar", "siteground-optimizer/v1": "sg-cachepress",
    "ithemes-security/v1": "better-wp-security", "complianz/v1": "complianz-gdpr", "wp-statistics/v2": "wp-statistics",
    "generateblocks/v1": "generateblocks", "kadence-blocks/v1": "kadence-blocks", "fluentform/v1": "fluentform",
    "wpforms/v1": "wpforms-lite", "gf/v2": "gravityforms", "updraftplus/v1": "updraftplus", "wp-smush/v1": "wp-smushit",
    "monsterinsights/v1": "google-analytics-for-wordpress", "google-site-kit/v1": "google-site-kit",
    "regenerate-thumbnails/v1": "regenerate-thumbnails", "wpml/v1": "sitepress-multilingual-cms",
    "w3tc/v1": "w3-total-cache", "wp-super-cache/v1": "wp-super-cache", "ninja-forms-submissions": "ninja-forms",
    "nf-submissions/v1": "ninja-forms", "wp-optimize/v1": "wp-optimize", "wp-mail-smtp/v1": "wp-mail-smtp",
    "spectra/v1": "ultimate-addons-for-gutenberg", "uagb/v1": "ultimate-addons-for-gutenberg",
    "metform/v1": "metform", "elementskit/v1": "elementskit-lite", "forminator/v1": "forminator",
    "real-cookie-banner/v1": "real-cookie-banner", "cookieyes/v1": "cookie-law-info", "cky/v1": "cookie-law-info",
    "duplicator/v1": "duplicator", "ai1wm/v1": "all-in-one-wp-migration", "sucuri/v1": "sucuri-scanner",
    "query-monitor/v1": "query-monitor", "otter/v1": "otter-blocks", "themeisle-gutenberg-blocks/v1": "otter-blocks",
    "astra-sites/v1": "astra-sites", "wpcode/v1": "insert-headers-and-footers", "simple-history/v1": "simple-history",
}
CORE_NAMESPACES = ("oembed/1.0", "wp/v2", "wp-site-health/v1", "wp-block-editor/v1", "wp-abilities/v1")

# Comments plugins leave in the HTML
HTML_SIGNATURES = [
    ("This site is optimized with the Yoast SEO", "wordpress-seo"),
    ("Search Engine Optimization by Rank Math", "seo-by-rank-math"),
    ("All in One SEO", "all-in-one-seo-pack"),
    ("This website is like a Rocket", "wp-rocket"),
    ("Performance optimized by W3 Total Cache", "w3-total-cache"),
    ("Cached page generated by WP-Super-Cache", "wp-super-cache"),
    ("WP Fastest Cache", "wp-fastest-cache"),
    ("Page optimized by LiteSpeed Cache", "litespeed-cache"),
    ("Page generated by LiteSpeed Cache", "litespeed-cache"),
    ("Cache Enabler by KeyCDN", "cache-enabler"),
    ("Hummingbird", "hummingbird-performance"),
    ("Swift Performance", "swift-performance-lite"),
    ("SiteGround Optimizer", "sg-cachepress"),
    ("Optimized with NitroPack", "nitropack"),
    ("Site Kit by Google", "google-site-kit"),
    ("MonsterInsights", "google-analytics-for-wordpress"),
    ("Autoptimize", "autoptimize"),
]

HEAVY_THEMES = {"avada": "Avada", "divi": "Divi", "enfold": "Enfold", "betheme": "BeTheme", "dt-the7": "The7",
                "x": "X Theme", "pro": "Pro (Themeco)", "bridge": "Bridge", "salient": "Salient", "jupiter": "Jupiter",
                "porto": "Porto", "flatsome": "Flatsome", "newspaper": "Newspaper", "jnews": "JNews"}
LIGHT_THEMES = {"astra", "generatepress", "kadence", "blocksy", "neve", "hello-elementor", "twentytwentyfive",
                "twentytwentyfour", "twentytwentythree", "oceanwp", "storefront"}


def detect(html, headers):
    evidence = []
    if "/wp-content/" in html:
        evidence.append("files under /wp-content/")
    if "/wp-includes/" in html:
        evidence.append("files under /wp-includes/")
    m = re.search(r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']WordPress\s*([0-9.]*)', html, re.I)
    if m:
        evidence.append("generator tag")
    if "api.w.org" in html or "api.w.org" in headers.get("link", ""):
        evidence.append("WordPress REST API link")
    if "x-pingback" in headers and "xmlrpc.php" in headers.get("x-pingback", ""):
        evidence.append("X-Pingback header")
    return len(evidence) >= 1 and ("files under /wp-content/" in evidence or len(evidence) >= 2), evidence


def _version_tuple(v):
    parts = re.findall(r"\d+", v or "")
    return tuple(int(p) for p in parts[:4]) if parts else ()


def is_older(installed, latest):
    a, b = _version_tuple(installed), _version_tuple(latest)
    if not a or not b:
        return False
    length = max(len(a), len(b))
    return a + (0,) * (length - len(a)) < b + (0,) * (length - len(b))


def parse_style_header(css):
    """The comment block at the top of a theme's style.css."""
    out = {}
    head = css[:8000]
    for key in ("Theme Name", "Version", "Template", "Author", "Theme URI"):
        m = re.search(rf"^[\s*#@]*{key}\s*:\s*(.+)$", head, re.I | re.M)
        if m:
            out[key.lower().replace(" ", "_")] = m.group(1).strip().rstrip("*/").strip()
    return out


def _readme_version(text):
    m = re.search(r"^\s*Stable tag:\s*([0-9][^\s]*)", text, re.I | re.M)
    return m.group(1) if m else ""


def _wporg_plugin(slug):
    url = ("https://api.wordpress.org/plugins/info/1.2/?action=plugin_information"
           f"&request[slug]={quote(slug)}&request[fields][sections]=0&request[fields][short_description]=0"
           "&request[fields][reviews]=0&request[fields][banners]=0&request[fields][icons]=0")
    try:
        return net.get_json(url, timeout=15)
    except Exception as e:  # network trouble, or 404 json - both mean "can't tell"
        msg = str(e)
        if "404" in msg:
            return {"error": "not found"}
        return {"error": f"couldn't check ({msg[:80]})"}


def _wporg_theme(slug):
    url = f"https://api.wordpress.org/themes/info/1.2/?action=theme_information&request[slug]={quote(slug)}"
    try:
        return net.get_json(url, timeout=15)
    except Exception as e:
        return {"error": str(e)[:80]}


def latest_wordpress():
    try:
        offers = net.get_json("https://api.wordpress.org/core/version-check/1.7/", timeout=15).get("offers", [])
        return offers[0].get("current", "") if offers else ""
    except Exception:
        return ""


def _years_since(date_text):
    try:
        when = datetime.strptime(date_text[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return (datetime.now(timezone.utc) - when).days / 365.25


def owner_of(url, site_host=""):
    """Who a file belongs to: ('plugin', slug) / ('theme', slug) / ('core', '') / ('uploads', '') / ('other site', host)."""
    path = urlsplit(url).path
    m = PLUGIN_PATH.search(path)
    if m:
        return "plugin", m.group(1)
    m = THEME_PATH.search(path)
    if m:
        return "theme", m.group(1)
    if "/wp-includes/" in path or "/wp-admin/" in path:
        return "core", ""
    if "/wp-content/uploads/" in path:
        return "uploads", ""
    host = net.host_of(url)
    if site_host and host and not net.same_site(host, site_host):
        return "other site", host
    return "site", ""


def check(base_url, page, parser, assets, progress=None, check_updates=True):
    """Returns (data, findings). `assets` are the downloaded page files (pagecheck.Asset)."""
    html = page.text
    findings = []
    data = {"plugins": {}, "theme": {}}
    site_host = net.host_of(page.url)
    all_urls = [page.url] + [a.url for a in assets] + [s["url"] for s in parser.scripts] + \
               [s["url"] for s in parser.stylesheets]
    text_blob = html + "\n" + "\n".join(all_urls)

    # --- Version
    version = ""
    m = re.search(r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']WordPress\s*([0-9.]+)', html, re.I)
    if m:
        version = m.group(1)
        data["version_source"] = "generator tag"
    if not version:
        feed = net.fetch(urljoin(base_url, "/feed/"), timeout=15)
        m = re.search(r"wordpress\.org/\?v=([0-9.]+)", feed.text if feed.ok else "")
        if m:
            version = m.group(1)
            data["version_source"] = "RSS feed"
    if not version:
        core_vers = [v for u in all_urls if "/wp-includes/" in u for v in VER_PARAM.findall(u)]
        if core_vers:
            version = max(set(core_vers), key=core_vers.count)
            data["version_source"] = "version numbers on core files (best guess)"
    data["version"] = version
    latest = latest_wordpress() if check_updates else ""
    data["latest_version"] = latest
    if version and latest:
        if is_older(version, latest):
            v, l = _version_tuple(version) + (0, 0), _version_tuple(latest) + (0, 0)
            releases_behind = (l[0] * 10 + l[1]) - (v[0] * 10 + v[1])  # 6.9 is followed by 7.0
            findings.append(Finding(
                "wordpress", CRITICAL if releases_behind >= 2 else WARNING,
                f"WordPress is out of date ({version}, latest is {latest})",
                "Old versions miss security fixes and speed improvements.",
                "Take a backup, then update in Dashboard > Updates.", impact=50))
        else:
            findings.append(Finding("wordpress", GOOD, f"WordPress is up to date ({version})"))
    elif version:
        findings.append(Finding("wordpress", INFO, f"WordPress version {version}",
                                "Couldn't check the latest version (WordPress.org didn't answer)."))
    if data.get("version_source") == "generator tag":
        findings.append(Finding("security", INFO, "The page shows the WordPress version number",
                                "Hiding it makes it slightly harder for automated attacks to target known bugs.",
                                "Most security and SEO plugins can remove the generator tag."))

    # --- Theme
    theme_slugs = []
    for u in all_urls + re.findall(r"[\"'(]([^\"'()]*/wp-content/themes/[^\"'()]+)", html):
        tm = THEME_PATH.search(u)
        if tm and tm.group(1) not in theme_slugs:
            theme_slugs.append(tm.group(1))
    data["theme_slugs"] = theme_slugs
    if theme_slugs:
        themes = {}
        for slug in theme_slugs[:3]:
            style = net.fetch(urljoin(page.url, f"/wp-content/themes/{slug}/style.css"), timeout=15)
            info = parse_style_header(style.text) if style.ok else {}
            info["slug"] = slug
            themes[slug] = info
        child = next((t for t in themes.values() if t.get("template")), None)
        active = child or themes[theme_slugs[0]]
        parent_slug = (child or {}).get("template") or active["slug"]
        parent = themes.get(parent_slug) or {"slug": parent_slug}
        data["theme"] = {"name": active.get("theme_name") or active["slug"], "slug": active["slug"],
                         "version": active.get("version", ""), "child_of": child and parent_slug,
                         "parent_version": parent.get("version", "")}
        if check_updates:
            info = _wporg_theme(parent_slug)
            if "version" in info and parent.get("version"):
                data["theme"]["latest"] = info["version"]
                if is_older(parent["version"], info["version"]):
                    findings.append(Finding("plugins", WARNING,
                                            f"Theme \"{info.get('name', parent_slug)}\" is out of date "
                                            f"({parent['version']}, latest {info['version']})",
                                            "Theme updates often include speed and security fixes.",
                                            "Update it in Appearance > Themes (back up first)."))
        key = parent_slug.lower()
        if key in HEAVY_THEMES:
            findings.append(Finding(
                "plugins", INFO, f"Theme: {HEAVY_THEMES[key]} (a heavy, feature-packed theme)",
                "Multipurpose themes load a lot of code. They can be fast, but only with their performance settings "
                "switched on.", f"Look for {HEAVY_THEMES[key]}'s performance / optimisation options and turn off "
                "features you don't use. A lightweight theme (GeneratePress, Astra, Kadence, Blocksy) is the "
                "long-term fix for a slow site."))
        elif key in LIGHT_THEMES:
            findings.append(Finding("plugins", GOOD, f"Theme: {data['theme']['name']} (a lightweight theme)"))

    # --- Plugins
    found = {}
    def note(slug, how, ver=""):
        slug = slug.strip()
        if not slug or slug.lower() in ("index.php",):
            return
        entry = found.setdefault(slug, {"slug": slug, "seen_by": set(), "ver_params": set()})
        entry["seen_by"].add(how)
        if ver:
            entry["ver_params"].add(ver)
    for u in all_urls:
        pm = PLUGIN_PATH.search(u)
        if pm:
            vm = VER_PARAM.search(u)
            note(pm.group(1), "files on the page", vm.group(1) if vm else "")
    for pm in PLUGIN_PATH.finditer(html):
        note(pm.group(1), "mentioned in the page")
    comment_blob = "\n".join(parser.comments) + html[-4000:]
    for text, slug in HTML_SIGNATURES:
        if text in comment_blob:
            note(slug, "comment in the page")
    rest = net.fetch(urljoin(base_url, "/wp-json/"), timeout=20, headers={"Accept": "application/json"})
    data["rest_api"] = {"status": rest.status, "ms": rest.timing.total, "error": rest.error}
    namespaces = []
    if rest.ok and rest.content_type == "application/json":
        try:
            namespaces = json.loads(rest.text).get("namespaces", [])
        except (ValueError, AttributeError):
            namespaces = []
    data["rest_namespaces"] = namespaces
    unknown_ns = []
    for ns in namespaces:
        if ns in CORE_NAMESPACES:
            continue
        slug = REST_NAMESPACES.get(ns)
        if slug:
            note(slug, "REST API")
        else:
            unknown_ns.append(ns)
    data["unknown_namespaces"] = unknown_ns

    # Installed version from each plugin's readme.txt (usually public)
    def look_up(entry):
        slug = entry["slug"]
        readme = net.fetch(urljoin(page.url, f"/wp-content/plugins/{slug}/readme.txt"), timeout=15)
        entry["version"] = _readme_version(readme.text) if readme.ok and "text" in readme.content_type else ""
        if not entry["version"] and len(entry["ver_params"]) == 1:
            entry["version"] = next(iter(entry["ver_params"]))
            entry["version_guess"] = True
        if check_updates:
            entry["wporg"] = _wporg_plugin(slug)
        return entry
    entries = list(found.values())
    with ThreadPoolExecutor(max_workers=6) as pool:
        for i, entry in enumerate(pool.map(look_up, entries)):
            if progress:
                progress(i + 1, len(entries))

    # Weight per plugin from the files we downloaded
    weights = {}
    for a in assets:
        kind, slug = owner_of(a.url, site_host)
        if kind in ("plugin", "theme"):
            w = weights.setdefault((kind, slug), {"files": 0, "bytes": 0, "js": 0, "css": 0, "failed": 0})
            w["files"] += 1
            w["bytes"] += a.transfer
            if a.kind in ("js", "css"):
                w[a.kind] += 1
            if a.failed:
                w["failed"] += 1
    data["weights"] = {f"{k[0]}:{k[1]}": v for k, v in weights.items()}

    outdated, abandoned, closed, premium = [], [], [], []
    for entry in entries:
        slug = entry["slug"]
        info = entry.get("wporg") or {}
        w = weights.get(("plugin", slug), {"files": 0, "bytes": 0})
        p = {"slug": slug, "name": info.get("name") or PLUGIN_NAMES.get(slug, slug), "version": entry.get("version", ""),
             "version_guess": entry.get("version_guess", False), "latest": info.get("version", ""),
             "last_updated": info.get("last_updated", ""), "installs": info.get("active_installs"),
             "files": w["files"], "bytes": w["bytes"], "seen_by": sorted(entry["seen_by"]),
             "on_wporg": "error" not in info and bool(info), "closed": info.get("error") == "closed" or info.get("closed")}
        p["name"] = re.sub(r"<[^>]+>", "", p["name"]).replace("&#8211;", "-").replace("&amp;", "&")
        data["plugins"][slug] = p
        if p["closed"]:
            closed.append(p)
        elif p["on_wporg"]:
            if p["version"] and p["latest"] and is_older(p["version"], p["latest"]):
                outdated.append(p)
            years = _years_since(p["last_updated"])
            if years is not None and years > 2:
                p["years_since_update"] = years
                abandoned.append(p)
        elif check_updates and info.get("error") == "not found":
            premium.append(p)

    plugins = list(data["plugins"].values())
    if plugins:
        findings.append(Finding(
            "plugins", INFO, f"{len(plugins)} plugins detected from the outside",
            "These are the plugins that leave a trace on the public site. Plugins that only work in the admin area "
            "can't be seen from outside, so the real total may be higher.",
            items=[f"{p['name']} {p['version'] or ''}".strip() + (f" - adds {p['files']} files ({kb(p['bytes'])})"
                   if p["files"] else "") for p in sorted(plugins, key=lambda p: -p["bytes"])]))
    if closed:
        findings.append(Finding(
            "plugins", CRITICAL, f"{len(closed)} plugin{'s have' if len(closed) != 1 else ' has'} been removed from WordPress.org",
            "WordPress.org closes plugins that have unfixed security problems or have been abandoned.",
            "Replace or remove these plugins as soon as possible.", impact=100,
            items=[p["name"] for p in closed]))
    if outdated:
        findings.append(Finding(
            "plugins", WARNING if len(outdated) < 4 else CRITICAL,
            f"{len(outdated)} plugin{'s are' if len(outdated) != 1 else ' is'} out of date",
            "Out-of-date plugins are the number one way WordPress sites get hacked, and updates often include speed "
            "fixes.", "Back up, then update them in Dashboard > Updates. Consider turning on auto-updates for "
                      "trusted plugins.", impact=60,
            items=[f"{p['name']}: {p['version']} -> {p['latest']}" + (" (version is a best guess)" if p["version_guess"] else "")
                   for p in outdated]))
    if abandoned:
        findings.append(Finding(
            "plugins", WARNING, f"{len(abandoned)} plugin{'s haven' if len(abandoned) != 1 else ' hasn'}'t been updated by the author in over 2 years",
            "Abandoned plugins don't get security or compatibility fixes, and often aren't written for current PHP "
            "versions.", "Look for an actively maintained alternative.",
            items=[f"{p['name']}: last updated {p['last_updated'][:10]}" for p in abandoned]))
    if premium:
        findings.append(Finding(
            "plugins", INFO, f"{len(premium)} plugins aren't on WordPress.org (premium or custom)",
            "We can't check these for updates - make sure their licences are active so they keep updating.",
            items=[f"{p['name']} {p['version']}".strip() for p in premium]))

    heavy = sorted([(k, v) for k, v in weights.items() if v["bytes"] > 150 * 1024 or v["files"] >= 6],
                   key=lambda kv: -kv[1]["bytes"])
    if heavy:
        findings.append(Finding(
            "plugins", WARNING, f"{len(heavy)} plugin{'s/themes add' if len(heavy) != 1 else '/theme adds'} a lot to every page load",
            "These plugins (or the theme) add many files or a lot of data to this page.",
            "Check each one is really needed on this page. Perfmatters or Asset CleanUp let you switch a plugin's "
            "files off on pages that don't use it.", impact=int(sum(v["bytes"] for _, v in heavy) / 1024 / 4),
            items=[f"{(data['plugins'].get(slug, {}).get('name') or slug) if kind == 'plugin' else 'Theme ' + slug}: "
                   f"{v['files']} files, {kb(v['bytes'])}" for (kind, slug), v in heavy[:12]]))

    for p in plugins:
        known = KNOWN_PLUGINS.get(p["slug"])
        if known:
            title = f"{p['name']} slows the site down" if known[0] == WARNING else f"About {p['name']}"
            findings.append(Finding("plugins", known[0], title, known[1]))

    caching = [CACHE_PLUGINS[p["slug"]] for p in plugins if p["slug"] in CACHE_PLUGINS]
    data["cache_plugins"] = caching
    if len(caching) > 1:
        findings.append(Finding(
            "caching", WARNING, f"{len(caching)} caching plugins at once",
            "Caching plugins fight each other: double minification, broken pages and caches that never clear.",
            "Keep one caching plugin (the one your host recommends - e.g. LiteSpeed Cache on LiteSpeed servers) and "
            "remove the others.", items=caching))
    if len(plugins) > 30:
        findings.append(Finding("plugins", WARNING, f"Lots of plugins ({len(plugins)}+ visible)",
                                "Every active plugin's code runs on every uncached page load.",
                                "Deactivate and delete plugins you don't really need."))

    # Front-end bits of WordPress itself that most sites don't need
    small = []
    if "wp-emoji-release" in text_blob or "wp-emoji" in text_blob:
        small.append("Emoji script (wp-emoji) - only needed for very old browsers")
    if "dashicons.min.css" in text_blob:
        small.append("Dashicons CSS loaded for visitors (normally only needed when logged in)")
    if "jquery-migrate" in text_blob:
        small.append("jQuery Migrate - only needed by very old plugins/themes")
    if "wp-embed.min.js" in text_blob:
        small.append("wp-embed script")
    if small:
        findings.append(Finding("wordpress", INFO, "WordPress loads a few extras most sites don't need",
                                "Each is small, but they add up.",
                                "Perfmatters, WP Rocket, LiteSpeed Cache or Asset CleanUp can switch these off.",
                                items=small))
    if "wc-cart-fragments" in text_blob or "cart-fragments" in text_blob:
        findings.append(Finding(
            "plugins", WARNING, "WooCommerce \"cart fragments\" runs on this page",
            "On every page view this makes an extra request to the server that can't be cached, just to update the "
            "cart icon. On a busy site this is a major cause of slowness.",
            "Disable cart fragments on pages that aren't the shop/cart (Perfmatters, or the \"Disable Cart "
            "Fragments\" plugin).", impact=300))

    # --- Checks on well-known WordPress addresses (all simple GET requests)
    def get(path, **kw):
        return net.fetch(urljoin(page.url, path), timeout=15, **kw)
    xmlrpc = get("/xmlrpc.php")
    if xmlrpc.status == 405 or b"XML-RPC server accepts POST requests only" in xmlrpc.body:
        findings.append(Finding(
            "security", WARNING, "XML-RPC is switched on (xmlrpc.php)",
            "Bots hammer xmlrpc.php with password guesses, which uses server power and can slow the site for real "
            "visitors. Very few sites need it (only the old WordPress app and some Jetpack features).",
            "Disable XML-RPC with your security plugin, or block xmlrpc.php in .htaccess / Cloudflare."))
    users = get("/wp-json/wp/v2/users", headers={"Accept": "application/json"})
    if users.ok and users.content_type == "application/json" and b'"slug"' in users.body:
        names = re.findall(r'"slug"\s*:\s*"([^"]+)"', users.text)[:10]
        findings.append(Finding(
            "security", WARNING, "Usernames are publicly listed",
            "Anyone can see the site's login usernames at /wp-json/wp/v2/users, which helps password-guessing "
            "attacks.", "Block the users endpoint for logged-out visitors (most security plugins have an option).",
            items=names))
    debug = get("/wp-content/debug.log", read_body=True, max_body=20000)
    if debug.status == 200 and ("PHP " in debug.text[:20000] or "[" in debug.text[:5]) and "html" not in debug.content_type:
        findings.append(Finding(
            "security", CRITICAL, "The WordPress error log is public (/wp-content/debug.log)",
            "Anyone can read it. It can reveal file paths, plugin errors and sometimes private data. It also means "
            "debug logging is on, which slows the site down when errors happen.",
            "Turn off WP_DEBUG_LOG in wp-config.php and delete wp-content/debug.log.",
            items=[line[:160] for line in debug.text.splitlines()[-5:] if line.strip()]))
    uploads = get("/wp-content/uploads/")
    if uploads.ok and b"Index of" in uploads.body[:3000]:
        findings.append(Finding("security", WARNING, "Folder listing is turned on",
                                "Anyone can browse every file in /wp-content/uploads/.",
                                "Add \"Options -Indexes\" to .htaccess or ask your host to turn off directory "
                                "listing."))
    readme = get("/readme.html", max_body=4000)
    if readme.ok and b"WordPress" in readme.body[:4000]:
        findings.append(Finding("security", INFO, "WordPress readme.html is public",
                                "It's harmless but reveals the site is WordPress (and sometimes the version).",
                                "Delete readme.html from the site's main folder."))
    login = get("/wp-login.php", read_body=False)
    data["login_status"] = login.status
    return data, findings


def plugin_findings_from_browser(browser_data, wp_data, site_host):
    """Per-plugin JavaScript CPU time and unused code from the real browser run."""
    findings = []
    if not browser_data:
        return findings
    cpu = {}
    for url, ms in (browser_data.get("js_cpu_by_url") or {}).items():
        kind, slug = owner_of(url, site_host)
        key = (kind, slug or net.host_of(url))
        cpu[key] = cpu.get(key, 0) + ms
    unused = {}
    for item in browser_data.get("coverage") or []:
        kind, slug = owner_of(item["url"], site_host)
        key = (kind, slug or net.host_of(item["url"]))
        u = unused.setdefault(key, [0, 0])
        u[0] += item["unused"]
        u[1] += item["total"]

    def label(key):
        kind, slug = key
        if kind == "plugin":
            return "Plugin " + ((wp_data or {}).get("plugins", {}).get(slug, {}).get("name") or PLUGIN_NAMES.get(slug, slug))
        if kind == "theme":
            return "Theme " + slug
        if kind == "core":
            return "WordPress core"
        if kind == "other site":
            return slug
        return "Your site's own files"

    busy = sorted([(k, v) for k, v in cpu.items() if v >= 50], key=lambda kv: -kv[1])
    if busy:
        total = sum(v for _, v in busy)
        findings.append(Finding(
            "plugins" if any(k[0] in ("plugin", "theme") for k, _ in busy) else "browser",
            WARNING if total > 500 else INFO,
            "Who uses the processor while the page loads (JavaScript time)",
            "Time the visitor's browser spent running each group's JavaScript (desktop PC; phones are 3-5 times "
            "slower). Long JavaScript makes the page freeze and respond late to taps and clicks.",
            "Remove or delay the worst offenders - optimisation plugins can delay non-essential JavaScript until "
            "the visitor interacts.", impact=int(total / 2),
            items=[f"{label(k)}: {v:.0f} ms" for k, v in busy[:12]]))
    wasteful = sorted([(k, v) for k, v in unused.items() if v[0] > 50 * 1024], key=lambda kv: -kv[1][0])
    if wasteful:
        findings.append(Finding(
            "plugins", INFO, "Code that's downloaded but not used on this page",
            "These CSS/JavaScript files are mostly not needed for this page - usually plugins loading their code on "
            "every page \"just in case\".",
            "Use \"remove unused CSS\" in WP Rocket/LiteSpeed/Perfmatters, and stop plugins loading on pages that "
            "don't use them.",
            items=[f"{label(k)}: {kb(v[0])} unused of {kb(v[1])} ({v[0] * 100 // max(1, v[1])}%)"
                   for k, v in wasteful[:12]]))
    ajax = browser_data.get("backend_calls") or []
    if ajax:
        worst = max(c["ms"] for c in ajax)
        findings.append(Finding(
            "plugins", WARNING if worst > 500 or len(ajax) > 2 else INFO,
            f"{len(ajax)} call{'s' if len(ajax) != 1 else ''} back to WordPress while the page loads",
            "After the page arrives, something on it asks the server for more (admin-ajax.php / REST API). These "
            "requests can't be cached, so each one makes the server run all of WordPress again.",
            "Find which plugin makes them (the address usually says - e.g. action=... in admin-ajax) and turn that "
            "feature off or make it load later.", impact=int(worst),
            items=[f"{c['ms']:.0f} ms - {short_url(c['url'], 110)}" for c in ajax[:10]]))
    return findings
