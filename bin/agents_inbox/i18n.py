"""Language pick shared by the agents tools.

AGENTS_LANG (ru|en) wins; otherwise the first non-empty of LC_ALL, LC_MESSAGES,
LANG: "ru..." is Russian, anything else English.
"""
import os


def pick_lang():
    forced = os.environ.get("AGENTS_LANG", "").strip().lower()
    if forced in ("ru", "en"):
        return forced
    for k in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(k, "").strip()
        if v:
            return "ru" if v.lower().startswith("ru") else "en"
    return "en"
