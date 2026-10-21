"""'Export for AI': everything a test measured, in one text file to hand to an AI assistant (or a developer).

Nothing is sent anywhere - this only builds the file.
"""
import json

from . import APP_NAME, VERSION
from .findings import CATEGORY_TITLES


def _json(data):
    return json.dumps(data, indent=1, default=str, ensure_ascii=False)


def _browser_run(data):
    if not data or data.get("error"):
        return data
    out = {k: v for k, v in data.items() if k not in ("screenshot", "requests", "imgs")}
    out["requests"] = [{k: r.get(k) for k in ("url", "type", "status", "size", "start_ms", "ms", "wait", "protocol",
                                              "failed", "initiator")} for r in data.get("requests") or []]
    out["images"] = data.get("imgs") or []
    return out


def ai_export(r):
    lines = [
        f"# {APP_NAME} log export",
        "",
        f"Site tested: {r.final_url or r.start_url} (typed: {r.target})",
        f"Tested: {r.started:%Y-%m-%d %H:%M} (local time of the PC that ran the test), took {r.duration:.0f} s",
        f"{APP_NAME} {VERSION}",
        "",
        "## About this file (for whoever analyses it)",
        "",
        "This is a performance log exported by a Windows website-testing app. It tested the site from the OUTSIDE, like a",
        "visitor, from the PC it ran on (location below). Nothing was measured from inside the server.",
        "- All times are milliseconds unless marked.",
        "- 'think' = time the server spent before answering (wait minus one network round trip); 'rtt' = round trip.",
        "- Server timing compares: home page (normal), home page with a cache-busting query string, a plain static file,",
        "  and WordPress's admin-ajax.php (WordPress + plugins with no page content). Static slow = hosting problem;",
        "  only WordPress slow = plugins/theme/database.",
        "- Browser runs: a real Edge/Chrome loaded the page with an empty cache, on desktop and as a phone on simulated",
        "  slow 4G with 4x CPU slowdown (like Google Lighthouse). LCP/FCP/TBT/CLS are the usual Core Web Vitals.",
        "- Plugins can only be seen if they leave a trace on the public site.",
        "Task: find the root causes of slowness and give specific, prioritised fixes.",
        "",
        "## Summary",
        "",
        f"Grade: {r.grade} ({r.score}/100)",
        r.diagnosis.get("headline", ""),
    ]
    lines += [f"- {p}" for p in r.diagnosis.get("paragraphs", [])]
    lines += ["", "## Where the time goes (first visit, desktop)", ""]
    lines += [f"- {label}: {ms:.0f} ms" for label, ms, _ in r.breakdown]
    lines += ["", f"## Findings ({len(r.findings)})"]
    for f in sorted(r.findings, key=lambda f: f.sort_key):
        lines += ["", f"### [{f.severity.upper()}] ({CATEGORY_TITLES.get(f.category, f.category)}) {f.title}"]
        if f.detail:
            lines.append(f.detail)
        if f.fix:
            lines.append(f"Suggested fix: {f.fix}")
        lines += [f"- {item}" for item in f.items]

    lines += ["", "## Redirect chain", ""]
    for resp in r.chain:
        lines.append(f"- {resp.status} {resp.url} -> {resp.header('location') or '(final)'} ({resp.timing.total:.0f} ms)"
                     + (f" error: {resp.error}" if resp.error else ""))
    if r.page is not None:
        lines += ["", "## Home page response headers", ""]
        lines += [f"- {k}: {v}" for k, v in r.page.raw_headers]

    sections = [
        ("DNS", r.dns),
        ("Server, hosting and timing samples", r.server),
        ("SSL", r.ssl),
        ("Page content statistics", r.content),
        ("SEO data", r.seo),
        ("WordPress, theme and plugins", r.wp),
        ("Google PageSpeed", r.psi),
    ]
    for title, data in sections:
        if data:
            lines += ["", f"## {title} (JSON)", "", "```json", _json(data), "```"]

    if r.assets:
        lines += ["", f"## Every file the page uses ({len(r.assets)}), downloaded directly", "",
                  "| URL | Kind | Status | Transfer bytes | Size bytes | Total ms | Wait ms | Encoding | Cache-Control |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for a in r.assets:
            resp = a.resp
            if resp is None:
                lines.append(f"| {a.url} | {a.kind} | not fetched | | | | | | |")
                continue
            lines.append(f"| {a.url} | {a.kind} | {resp.status or resp.error} | {resp.transfer_size} | {len(resp.body)} | "
                         f"{resp.timing.total:.0f} | {resp.timing.wait:.0f} | {resp.header('content-encoding')} | "
                         f"{resp.header('cache-control')} |")

    for name in ("desktop", "mobile"):
        data = (r.browser or {}).get(name)
        if data:
            lines += ["", f"## Real browser run: {name} (JSON)", "", "```json", _json(_browser_run(data)), "```"]

    if r.errors:
        lines += ["", "## Parts of the test that failed", ""] + [f"- {e}" for e in r.errors]
    lines += ["", "(No passwords or personal data are included. Screenshots are left out.)", ""]
    return "\n".join(lines)
