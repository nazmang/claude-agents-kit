import importlib.machinery
import importlib.util
import os
import string
import sys
import unittest
from unittest import mock

BIN = os.path.join(os.path.dirname(__file__), "..", "..", "bin")
sys.path.insert(0, BIN)

from agents_inbox import cli, deliver, server  # noqa: E402
from agents_inbox.i18n import pick_lang, strings_for, tr  # noqa: E402


def load_agents():
    path = os.path.join(BIN, "agents")
    loader = importlib.machinery.SourceFileLoader("agents_cli", path)
    spec = importlib.util.spec_from_loader("agents_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


AGENTS = load_agents()
TABLES = {"bin/agents": AGENTS.STRINGS, "cli": cli.STRINGS,
          "deliver": deliver.STRINGS, "server": server.STRINGS}
TABLE = {"en": {"a": "A {x}", "b": "B"}, "ru": {"a": "А {x}", "b": "Б"},
         "uk": {"a": "А {x}"}}
CLEAN = {"AGENTS_LANG": "", "LC_ALL": "", "LC_MESSAGES": "", "LANG": ""}


def env(**kv):
    return mock.patch.dict(os.environ, {**CLEAN, **kv})


def fields(v):
    if not isinstance(v, str):
        return len(v)
    return sorted(f for _, f, _, _ in string.Formatter().parse(v) if f)


class PickLang(unittest.TestCase):
    def test_locale_language_part(self):
        for loc, want in (("uk_UA.UTF-8", "uk"), ("ru_RU", "ru"), ("ru_RU.UTF-8", "ru"),
                          ("en_GB.UTF-8", "en"), ("de_DE.UTF-8", "en"), ("C", "en"),
                          ("POSIX", "en"), ("C.UTF-8", "en"), ("uk", "uk")):
            with self.subTest(loc=loc), env(LANG=loc):
                self.assertEqual(pick_lang(TABLE), want)

    def test_agents_lang_wins(self):
        with env(AGENTS_LANG="uk", LANG="ru_RU.UTF-8"):
            self.assertEqual(pick_lang(TABLE), "uk")
        with env(AGENTS_LANG=" UK ", LANG="ru_RU.UTF-8"):
            self.assertEqual(pick_lang(TABLE), "uk")

    def test_unsupported_agents_lang_falls_to_locale_then_en(self):
        with env(AGENTS_LANG="de", LANG="uk_UA.UTF-8"):
            self.assertEqual(pick_lang(TABLE), "uk")
        with env(AGENTS_LANG="de"):
            self.assertEqual(pick_lang(TABLE), "en")

    def test_first_non_empty_locale_var(self):
        with env(LC_ALL="C", LANG="uk_UA.UTF-8"):
            self.assertEqual(pick_lang(TABLE), "en")
        with env(LC_MESSAGES="uk_UA.UTF-8", LANG="ru_RU.UTF-8"):
            self.assertEqual(pick_lang(TABLE), "uk")

    def test_supported_is_the_table(self):
        with env(LANG="uk_UA.UTF-8"):
            self.assertEqual(pick_lang({"en": {}, "ru": {}}), "en")
            self.assertEqual(pick_lang({"en": {}, "uk": {}}), "uk")

    def test_missing_key_falls_back_to_en(self):
        with env(AGENTS_LANG="uk"):
            self.assertEqual(tr(TABLE, "a", x=1), "А 1")
            self.assertEqual(tr(TABLE, "b"), "B")
            self.assertEqual(strings_for(TABLE)["b"], "B")
            self.assertEqual(tr(TABLE, "nope"), "nope")

    def test_real_tables_speak_ukrainian(self):
        with env(LANG="uk_UA.UTF-8"):
            self.assertEqual(cli.t("empty"), "відкритих питань немає")
            self.assertEqual(deliver.t("long_answer", path="/p"), "відповідь у файлі /p")
            self.assertEqual(server.strings()["title"], "Вхідні агентів")


class Parity(unittest.TestCase):
    def test_every_language_has_exactly_the_english_keys(self):
        for name, table in TABLES.items():
            self.assertIn("en", table)
            for lang in ("ru", "uk"):
                with self.subTest(table=name, lang=lang):
                    self.assertEqual(set(table[lang]), set(table["en"]))

    def test_placeholders_match_english(self):
        for name, table in TABLES.items():
            for lang, strings in table.items():
                for key, v in strings.items():
                    with self.subTest(table=name, lang=lang, key=key):
                        self.assertEqual(fields(v), fields(table["en"][key]))

    def test_help_text_in_every_language(self):
        self.assertEqual(set(AGENTS.DOCS), set(AGENTS.STRINGS))
        for lang, doc in AGENTS.DOCS.items():
            with self.subTest(lang=lang):
                for cmd in ("agents status", "agents messages", "agents compact", "agents ask",
                            "agents inbox", "agents up", "agents down", "AGENTS_LANG"):
                    self.assertIn(cmd, doc)


if __name__ == "__main__":
    unittest.main()
