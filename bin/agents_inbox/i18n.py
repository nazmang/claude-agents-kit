"""Language pick shared by the agents tools.

A strings table is a dict {language code: {key: text}}; the languages it
supports are its keys, so adding a language means adding one dict, nothing
else. "en" is the fallback and must hold every key.

AGENTS_LANG wins if the table has it; otherwise the first non-empty of LC_ALL,
LC_MESSAGES, LANG decides by its language part (uk_UA.UTF-8 -> uk,
ru_RU -> ru); anything the table lacks (C, POSIX, de_DE...) is English.
"""
import os
import re

FALLBACK = "en"
LOCALE_VARS = ("LC_ALL", "LC_MESSAGES", "LANG")


def pick_lang(supported):
    """Language code for `supported` (a strings table or any iterable of codes)."""
    supported = set(supported)
    forced = os.environ.get("AGENTS_LANG", "").strip().lower()
    if forced in supported:
        return forced
    for k in LOCALE_VARS:
        v = os.environ.get(k, "").strip()
        if v:
            code = re.split(r"[_.@-]", v.lower(), maxsplit=1)[0]
            return code if code in supported else FALLBACK
    return FALLBACK


def strings_for(table, lang=None):
    """The table's strings in `lang` (picked if None), English for missing keys."""
    lang = pick_lang(table) if lang is None else lang
    return {**table[FALLBACK], **table.get(lang, {})}


def tr(table, key, **vars):
    """One string by key, formatted; a non-string value (a tuple of headers) as is."""
    v = strings_for(table).get(key, key)
    return v.format(**vars) if isinstance(v, str) else v
