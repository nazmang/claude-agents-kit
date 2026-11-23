"""prompt_empty: is the input box of a captured Claude screen free of a draft?"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "bin"))

from agents_inbox.tmuxlib import prompt_empty  # noqa: E402


class PromptEmpty(unittest.TestCase):
    def test_bare_prompt(self):
        self.assertTrue(prompt_empty("text\n\x1b[39m❯\xa0\x1b[7m \x1b[0m\n"))
        self.assertTrue(prompt_empty("❯ "))

    def test_dim_placeholder(self):
        self.assertTrue(prompt_empty('❯\xa0\x1b[2mTry "fix lint errors"\x1b[0m'))

    def test_placeholder_with_cursor_on_first_letter(self):
        line = '\x1b[39m❯\xa0\x1b[7mT\x1b[0;2mry "how does <filepath> work?"\x1b[0m'
        self.assertTrue(prompt_empty(line))

    def test_draft_is_not_empty(self):
        self.assertFalse(prompt_empty("❯\xa0half a thought\x1b[7m \x1b[0m"))
        self.assertFalse(prompt_empty("\x1b[39m❯\xa0\x1b[7mh\x1b[0malf"))

    def test_colour_codes_with_a_2_inside_are_not_dim(self):
        # 38;5;246 and 32 contain the digit 2 but are not the dim attribute
        self.assertFalse(prompt_empty("❯\xa0\x1b[38;5;246mdraft\x1b[0m"))
        self.assertFalse(prompt_empty("❯\xa0\x1b[32mdraft\x1b[0m"))
        self.assertFalse(prompt_empty("❯\xa0\x1b[38;2;10;20;30mdraft\x1b[0m"))
        self.assertFalse(prompt_empty("❯\xa0\x1b[48;5;2mdraft\x1b[0m"))
        self.assertTrue(prompt_empty("❯\xa0\x1b[38;2;10;20;30;2mhint\x1b[0m"))

    def test_last_prompt_line_wins(self):
        self.assertTrue(prompt_empty("❯ old command\noutput\n❯\xa0"))

    def test_no_prompt_means_busy(self):
        self.assertFalse(prompt_empty("just output\nmore"))


if __name__ == "__main__":
    unittest.main()
