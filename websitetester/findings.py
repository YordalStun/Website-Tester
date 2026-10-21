"""Findings (the things we tell the user) and how they turn into scores."""
from dataclasses import dataclass, field

CRITICAL = "critical"
WARNING = "warning"
INFO = "info"
GOOD = "good"

SEVERITY_ORDER = {CRITICAL: 0, WARNING: 1, INFO: 2, GOOD: 3}
SEVERITY_LABEL = {CRITICAL: "Problem", WARNING: "Needs attention", INFO: "Note", GOOD: "Good"}

# (key, title, weight in the overall score)
CATEGORIES = [
    ("server", "Server & hosting", 3.0),
    ("browser", "Real browser load (what visitors see)", 3.0),
    ("google", "Google PageSpeed & real visitors", 2.0),
    ("content", "Page content (images, CSS, JavaScript)", 2.0),
    ("caching", "Caching & CDN", 2.0),
    ("plugins", "Plugins & theme", 2.0),
    ("wordpress", "WordPress", 1.0),
    ("dns", "DNS", 1.0),
    ("ssl", "SSL / HTTPS", 1.0),
    ("security", "Security", 1.0),
    ("seo", "SEO & basics", 0.5),
    ("email", "Email DNS records", 0.5),
]
CATEGORY_TITLES = {key: title for key, title, _ in CATEGORIES}
CATEGORY_WEIGHTS = {key: weight for key, _, weight in CATEGORIES}


@dataclass
class Finding:
    category: str
    severity: str
    title: str
    detail: str = ""
    fix: str = ""
    impact: int = 0  # rough size of the problem (ms of delay where we can estimate it); used for ranking
    items: list = field(default_factory=list)  # optional list of strings (files, plugins...) to show under it
    symptom: bool = False  # describes how slow it is rather than why (the causes are listed separately)

    @property
    def sort_key(self):
        return (SEVERITY_ORDER[self.severity], -self.impact)


def category_score(findings, category):
    """0-100 for one area, or None if we found nothing to say about it."""
    mine = [f for f in findings if f.category == category]
    if not mine:
        return None
    score = 100
    for f in mine:
        if f.severity == CRITICAL:
            score -= 30
        elif f.severity == WARNING:
            score -= 10
    return max(0, score)


def overall_score(findings):
    total = weight_sum = 0.0
    for key, _, weight in CATEGORIES:
        score = category_score(findings, key)
        if score is not None:
            total += score * weight
            weight_sum += weight
    return round(total / weight_sum) if weight_sum else None


def grade(score):
    if score is None:
        return "?"
    for limit, letter in ((90, "A"), (75, "B"), (60, "C"), (45, "D"), (30, "E")):
        if score >= limit:
            return letter
    return "F"


def top_fixes(findings, limit=8):
    """The most important things to fix, worst first."""
    bad = [f for f in findings if f.severity in (CRITICAL, WARNING) and not f.symptom]
    bad.sort(key=lambda f: f.sort_key)
    return bad[:limit]
