import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "bin"))
os.environ["AGENTS_LANG"] = "ru"

from agents_inbox import deliver  # noqa: E402
from agents_inbox.config import Config  # noqa: E402
from agents_inbox.store import Question  # noqa: E402


def q(id="q-1120-01"):
    return Question(id=id, from_="agent", kind="decision", blocking=True, title="t", body="")


def cfg(d=None):
    return Config(owner_name="owner", inbox_label="inbox", data_dir=d or tempfile.mkdtemp())


class FakeTmux:
    def __init__(self, sessions, empty=True, screen=None):
        self._s, self.empty, self.screen = sessions, empty, screen
        self.typed = []

    def sessions(self):
        return self._s

    def input_empty(self, target):
        return self.empty

    def type(self, target, text):
        self.typed.append((target, text))
        if self.screen is None:
            self.screen = text

    def capture(self, target):
        return self.screen or ""


def sess(name="agent", sid="s1", status="idle", target="=w:0"):
    return dict(name=name, sid=sid, status=status, target=target)


class FormatTests(unittest.TestCase):
    def test_prefix_and_newlines(self):
        self.assertEqual(deliver.format_answer(q(), "да\nкатай", cfg()),
                         "owner (inbox, q-1120-01): да ⏎ катай")

    def test_crlf(self):
        self.assertEqual(deliver.format_answer(q(), "a\r\nb", cfg()),
                         "owner (inbox, q-1120-01): a ⏎ b")

    def test_leading_slash_cannot_become_command(self):
        for t in ("/clear", "!ls", "#x"):
            self.assertTrue(deliver.format_answer(q(), t, cfg()).startswith("owner (inbox, q-1120-01): "))

    def test_escape_stripped(self):
        self.assertEqual(deliver.format_answer(q(), "\x1b[2Jок", cfg()),
                         "owner (inbox, q-1120-01): ок")

    def test_c0_del_c1_stripped(self):
        out = deliver.format_answer(q(), "a\x00b\x07c\x7fd\x85e\x9ff\tg", cfg())
        self.assertEqual(out, "owner (inbox, q-1120-01): abcdefg")

    def test_long_answer_goes_to_file(self):
        d = tempfile.mkdtemp()
        text = "x" * 2001
        out = deliver.format_answer(q(), text, cfg(d))
        path = os.path.join(d, "q-1120-01.md")
        self.assertTrue(os.path.exists(path))
        self.assertIn(path, out)
        self.assertEqual(out, f"owner (inbox, q-1120-01): ответ в файле {path}")
        self.assertEqual(open(path, encoding="utf-8").read(), text)

    def test_exactly_2000_stays_inline(self):
        d = tempfile.mkdtemp()
        out = deliver.format_answer(q(), "x" * 2000, cfg(d))
        self.assertFalse(os.path.exists(os.path.join(d, "q-1120-01.md")))
        self.assertTrue(out.endswith("x" * 2000))

    def test_long_file_keeps_newlines_strips_controls_creates_dir(self):
        d = os.path.join(tempfile.mkdtemp(), "sub", "inbox")
        text = "a\nb\x1b\tc" + "y" * 2100
        deliver.format_answer(q(), text, cfg(d))
        self.assertEqual(open(os.path.join(d, "q-1120-01.md"), encoding="utf-8").read(),
                         "a\nb\tc" + "y" * 2100)

    def test_threshold_on_cleaned_text(self):
        d = tempfile.mkdtemp()
        out = deliver.format_answer(q(), "x" * 2000 + "\x1b" * 50, cfg(d))
        self.assertFalse(os.path.exists(os.path.join(d, "q-1120-01.md")))
        self.assertTrue(out.endswith("x" * 2000))


class PickTests(unittest.TestCase):
    def test_duplicate_windows_pick_conf_sid(self):
        ss = [sess(sid="a", target="=w:0"), sess(sid="b", target="=w:1")]
        self.assertEqual(deliver.pick_target("agent", ss, "b")["sid"], "b")

    def test_duplicate_windows_first_by_target(self):
        ss = [sess(sid="b", target="=w:2"), sess(sid="a", target="=w:1")]
        self.assertEqual(deliver.pick_target("agent", ss, None)["target"], "=w:1")
        self.assertEqual(deliver.pick_target("agent", ss, "zzz")["target"], "=w:1")

    def test_target_order_numeric(self):
        ss = [sess(sid="a", target="=w:10"), sess(sid="b", target="=w:2")]
        self.assertEqual(deliver.pick_target("agent", ss, None)["target"], "=w:2")

    def test_no_target_ignored(self):
        self.assertIsNone(deliver.pick_target("agent", [sess(target=None)], None))
        self.assertIsNone(deliver.pick_target("other", [sess()], None))


class ConfTests(unittest.TestCase):
    def test_conf_sid(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "sessions.conf")
        open(p, "w").write("# c\nproj | agent | /tmp | opus | sid-1 | x\nproj | other | /tmp | opus | sid-2\n")
        self.assertEqual(deliver.conf_sid("agent", p), "sid-1")
        self.assertEqual(deliver.conf_sid("other", p), "sid-2")
        self.assertIsNone(deliver.conf_sid("nope", p))
        self.assertIsNone(deliver.conf_sid("agent", os.path.join(d, "missing")))


class DeliverTests(unittest.TestCase):
    def run_one(self, fake, conf=None):
        return deliver.deliver_one(q(), cfg(), fake, conf_path=conf or "/nonexistent", sleep=lambda s: None)

    def test_delivered(self):
        f = FakeTmux([sess()])
        q1 = q()
        q1.answer = "да"
        self.assertEqual(deliver.deliver_one(q1, cfg(), f, conf_path="/nonexistent",
                                             sleep=lambda s: None), "delivered")
        self.assertEqual(f.typed, [("=w:0", "owner (inbox, q-1120-01): да")])

    def test_busy_session_not_typed(self):
        f = FakeTmux([sess(status="busy")])
        self.assertEqual(self.run_one(f), "busy")
        self.assertEqual(f.typed, [])

    def test_draft_in_input_not_touched(self):
        f = FakeTmux([sess()], empty=False)
        self.assertEqual(self.run_one(f), "busy")
        self.assertEqual(f.typed, [])

    def test_no_session(self):
        self.assertEqual(self.run_one(FakeTmux([])), "no-session")
        self.assertEqual(self.run_one(FakeTmux([sess(target=None)])), "no-session")

    def test_shell_state_is_idle(self):
        self.assertEqual(self.run_one(FakeTmux([sess(status="shell")])), "delivered")

    def test_already_landed_not_retyped(self):
        f = FakeTmux([sess()], screen="owner (inbox, q-1120-01): да")
        self.assertEqual(self.run_one(f), "delivered")
        self.assertEqual(f.typed, [])

    def test_unverified_when_capture_lacks_id(self):
        f = FakeTmux([sess()], screen="nothing here")
        self.assertEqual(self.run_one(f), "unverified")
        self.assertEqual(len(f.typed), 1)

    def test_duplicate_windows_types_once_into_conf_sid(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "sessions.conf")
        open(p, "w").write("proj | agent | /tmp | opus | b\n")
        f = FakeTmux([sess(sid="a", target="=w:0"), sess(sid="b", target="=w:1")])
        self.assertEqual(self.run_one(f, p), "delivered")
        self.assertEqual([t for t, _ in f.typed], ["=w:1"])


if __name__ == "__main__":
    unittest.main()
