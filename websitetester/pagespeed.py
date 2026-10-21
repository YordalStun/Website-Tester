"""Google PageSpeed Insights: Google's own test, plus real-visitor data from Chrome users (if the site has enough)."""
import urllib.error
from urllib.parse import urlencode

from . import net
from .findings import CRITICAL, GOOD, INFO, WARNING, Finding

API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
FIELD_METRICS = [
    ("LARGEST_CONTENTFUL_PAINT_MS", "Main content appears (LCP)", "ms"),
    ("INTERACTION_TO_NEXT_PAINT", "Responds to taps/clicks (INP)", "ms"),
    ("CUMULATIVE_LAYOUT_SHIFT_SCORE", "Page jumping around (CLS)", "cls"),
    ("FIRST_CONTENTFUL_PAINT_MS", "First content appears (FCP)", "ms"),
    ("EXPERIMENTAL_TIME_TO_FIRST_BYTE", "Server response (TTFB)", "ms"),
]
CATEGORY_WORDS = {"FAST": "good", "AVERAGE": "needs improvement", "SLOW": "poor"}


def run(url, strategy="mobile", api_key="", timeout=150):
    params = [("url", url), ("strategy", strategy), ("category", "performance")]
    if api_key:
        params.append(("key", api_key))
    try:
        return net.get_json(f"{API}?{urlencode(params)}", timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return {"error": "Google's free daily limit for PageSpeed tests was reached. Add a free API key in "
                             "Settings to avoid this."}
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            detail = ""
        return {"error": f"Google PageSpeed returned an error ({e.code}). {detail}"}
    except Exception as e:
        return {"error": f"Couldn't reach Google PageSpeed ({e})"}


def _fmt(value, unit):
    if value is None:
        return "-"
    if unit == "cls":
        return f"{value / 100:.2f}"  # Google sends CLS multiplied by 100
    return f"{value / 1000:.1f} s" if value >= 1000 else f"{value:.0f} ms"


def summarise(raw):
    """Pull the useful bits out of Google's (very large) answer."""
    if not raw or "error" in raw:
        return {"error": (raw or {}).get("error", "no result")}
    out = {}
    for key, label in (("loadingExperience", "page"), ("originLoadingExperience", "site")):
        exp = raw.get(key) or {}
        metrics = exp.get("metrics") or {}
        if metrics:
            out["field"] = {
                "scope": label, "overall": exp.get("overall_category", ""),
                "metrics": [{"key": k, "label": lab, "value": _fmt(metrics[k].get("percentile"), unit),
                             "category": metrics[k].get("category", "")} for k, lab, unit in FIELD_METRICS
                            if k in metrics]}
            break
    lh = raw.get("lighthouseResult") or {}
    perf = ((lh.get("categories") or {}).get("performance") or {}).get("score")
    out["score"] = round(perf * 100) if perf is not None else None
    audits = lh.get("audits") or {}
    out["lab"] = {k: (audits.get(k) or {}).get("displayValue", "") for k in
                  ("first-contentful-paint", "largest-contentful-paint", "total-blocking-time",
                   "cumulative-layout-shift", "speed-index", "server-response-time")}
    opportunities = []
    for key, audit in audits.items():
        details = audit.get("details") or {}
        saving = details.get("overallSavingsMs") or (audit.get("metricSavings") or {}).get("LCP")
        if audit.get("score") is not None and audit["score"] < 0.9 and saving and saving > 100:
            opportunities.append({"title": audit.get("title", key), "saving_ms": saving,
                                  "display": audit.get("displayValue", "")})
    opportunities.sort(key=lambda o: -o["saving_ms"])
    out["opportunities"] = opportunities[:10]
    return out


def findings_for(summary, strategy="mobile"):
    findings = []
    if not summary or summary.get("error"):
        findings.append(Finding("google", INFO, "Google PageSpeed test didn't run",
                                (summary or {}).get("error", "")))
        return findings
    field = summary.get("field")
    if field:
        overall = field["overall"]
        where = "this page" if field["scope"] == "page" else "the site overall"
        items = [f"{m['label']}: {m['value']} ({CATEGORY_WORDS.get(m['category'], m['category'].lower())})"
                 for m in field["metrics"]]
        if overall == "SLOW":
            findings.append(Finding(
                "google", CRITICAL, "Real visitors have a slow experience (Google's data)",
                f"Google's data from real Chrome users visiting {where} over the last 28 days says the experience is "
                "poor. This also counts against you in Google search rankings.",
                "Fix the problems listed in this report, then check again in a month (the data is a 28-day average).",
                impact=800, items=items, symptom=True))
        elif overall == "AVERAGE":
            findings.append(Finding("google", WARNING, "Real visitors' experience needs improvement (Google's data)",
                                    f"Google's real-user data for {where} (last 28 days).", impact=300, items=items,
                                    symptom=True))
        else:
            findings.append(Finding("google", GOOD, "Real visitors have a fast experience (Google's data)",
                                    f"Google's real-user data for {where} (last 28 days).", items=items))
    else:
        findings.append(Finding("google", INFO, "Not enough visitors for Google's real-user data",
                                "Google only publishes real-visitor speed data for sites with enough Chrome traffic."))
    score = summary.get("score")
    if score is not None:
        sev = GOOD if score >= 90 else WARNING if score >= 50 else CRITICAL
        lab = summary.get("lab", {})
        findings.append(Finding(
            "google", sev, f"Google PageSpeed score ({strategy}): {score}/100",
            "Google's own test on a simulated mid-range phone on slow 4G. "
            + ", ".join(f"{k.replace('-', ' ')}: {v}" for k, v in lab.items() if v),
            "" if sev == GOOD else "The biggest savings Google suggests are listed below.",
            impact=(100 - score) * 5, symptom=True,
            items=[f"{o['title']} - could save about {o['saving_ms'] / 1000:.1f} s" for o in summary.get("opportunities", [])]))
    return findings
