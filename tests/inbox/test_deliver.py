import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "bin"))
os.environ["AGENTS_LANG"] = "ru"

from agents_inbox import deliver  # noqa: E402
from agents_inbox.config import Config  # noqa: E402
from agents_inbox.store import Question  # noqa: E402


def q(id="q-1120-01"):
    return Question(id=id, from_="agent", kind="decision", blocking=True, title="t", body="")


def cfg(d=None):
    return Config(owner_name="owner", inbox_label="inbox", data_dir=d or tempfile.mkdtemp())


T0 = datetime(2026, 11, 20, 16, 45, 0)


class FakeTmux:
    """Typing adds a user entry to the transcript of the session typed into
    (unless echo=False); entries are (sid, aware ts, text)."""

    def __init__(self, sessions, empty=True, echo=True, entries=None, clock=None):
        self._s, self.empty, self.echo = sessions, empty, echo
        self.entries = list(entries or [])
        self.clock = clock or (lambda: T0)
        self.typed = []

    def sessions(self):
        return self._s

    def input_empty(self, target):
        return self.empty

    def type(self, target, text):
        self.typed.append((target, text))
        if self.echo:
            sid = next(s["sid"] for s in self._s if s["target"] == target)
            self.entries.append((sid, self.clock().astimezone(timezone.utc), text))

    def user_entry_since(self, sid, prefix, since):
        return any(e[0] == sid and e[1] >= since and e[2].startswith(prefix) for e in self.entries)


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
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), text)

    def test_exactly_2000_stays_inline(self):
        d = tempfile.mkdtemp()
        out = deliver.format_answer(q(), "x" * 2000, cfg(d))
        self.assertFalse(os.path.exists(os.path.join(d, "q-1120-01.md")))
        self.assertTrue(out.endswith("x" * 2000))

    def test_long_file_keeps_newlines_strips_controls_creates_dir(self):
        d = os.path.join(tempfile.mkdtemp(), "sub", "inbox")
        text = "a\nb\x1b\tc" + "y" * 2100
        deliver.format_answer(q(), text, cfg(d))
        with open(os.path.join(d, "q-1120-01.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "a\nb\tc" + "y" * 2100)

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
        with open(p, "w") as f:
            f.write("# c\nproj | agent | /tmp | opus | sid-1 | x\nproj | other | /tmp | opus | sid-2\n")
        self.assertEqual(deliver.conf_sid("agent", p), "sid-1")
        self.assertEqual(deliver.conf_sid("other", p), "sid-2")
        self.assertIsNone(deliver.conf_sid("nope", p))
        self.assertIsNone(deliver.conf_sid("agent", os.path.join(d, "missing")))


def entry(type_, text, ts, sid="s1"):
    return json.dumps({"type": type_, "sessionId": sid, "timestamp": ts,
                       "message": {"role": type_, "content": text}}, ensure_ascii=False)


class DeliverTests(unittest.TestCase):
    def run_one(self, fake, conf=None, question=None, now=T0, typed=None):
        q1 = question or q()
        q1.answer = q1.answer or "да"
        calls = typed if typed is not None else []
        return deliver.deliver_one(q1, cfg(), fake, conf_path=conf or "/nonexistent",
                                   sleep=lambda s: None, clock=lambda: now,
                                   on_typed=lambda sid, when: calls.append((sid, when)))

    def test_delivered(self):
        f = FakeTmux([sess()])
        calls = []
        self.assertEqual(self.run_one(f, typed=calls), "delivered")
        self.assertEqual(f.typed, [("=w:0", "owner (inbox, q-1120-01): да")])
        self.assertEqual(calls, [("s1", T0)])

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

    def test_quoted_marker_on_screen_is_not_delivery(self):
        """Regression 20.11: the agent's reply quoted the expected line; the screen
        had the marker, the transcript had no user entry. Must type, must not
        report delivered until the transcript shows the typed line."""
        class Screen(FakeTmux):
            def capture(self, target):
                return "● Жду: owner (inbox, q-1120-01): да\n❯ "
        f = Screen([sess()], echo=False)   # assistant text is not a user entry
        self.assertEqual(self.run_one(f), "unverified")
        self.assertEqual(len(f.typed), 1)
        f.entries.append(("s1", (T0 + timedelta(seconds=3)).astimezone(timezone.utc),
                          "owner (inbox, q-1120-01): да"))
        q1 = q()
        q1.answer, q1.typed_at, q1.typed_sid = "да", T0.isoformat(), "s1"
        self.assertEqual(self.run_one(f, question=q1, now=T0 + timedelta(seconds=30)), "delivered")
        self.assertEqual(len(f.typed), 1)

    def test_transcript_entry_present_is_delivered_without_typing(self):
        q1 = q()
        q1.answer, q1.typed_at, q1.typed_sid = "да", T0.isoformat(), "s1"
        f = FakeTmux([sess()], entries=[("s1", (T0 + timedelta(seconds=1)).astimezone(timezone.utc),
                                         "owner (inbox, q-1120-01): да")])
        self.assertEqual(self.run_one(f, question=q1, now=T0 + timedelta(seconds=40)), "delivered")
        self.assertEqual(f.typed, [])

    def test_entry_before_typing_does_not_count(self):
        q1 = q()
        q1.answer, q1.typed_at, q1.typed_sid = "да", T0.isoformat(), "s1"
        f = FakeTmux([sess()], echo=False, entries=[
            ("s1", (T0 - timedelta(minutes=5)).astimezone(timezone.utc), "owner (inbox, q-1120-01): да")])
        self.assertEqual(self.run_one(f, question=q1, now=T0 + timedelta(minutes=3)), "unverified")
        self.assertEqual(len(f.typed), 1)

    def test_no_retype_within_two_minutes(self):
        q1 = q()
        q1.answer, q1.typed_at, q1.typed_sid = "да", T0.isoformat(), "s1"
        f = FakeTmux([sess()], echo=False)
        self.assertEqual(self.run_one(f, question=q1, now=T0 + timedelta(seconds=119)), "unverified")
        self.assertEqual(f.typed, [])

    def test_retype_after_two_minutes_if_input_empty(self):
        q1 = q()
        q1.answer, q1.typed_at, q1.typed_sid = "да", T0.isoformat(), "s1"
        later = T0 + timedelta(minutes=2, seconds=1)
        f = FakeTmux([sess()], clock=lambda: later)
        calls = []
        self.assertEqual(self.run_one(f, question=q1, now=later, typed=calls), "delivered")
        self.assertEqual(len(f.typed), 1)
        self.assertEqual(calls, [("s1", later)])

    def test_no_retype_after_two_minutes_if_input_not_empty(self):
        q1 = q()
        q1.answer, q1.typed_at, q1.typed_sid = "да", T0.isoformat(), "s1"
        f = FakeTmux([sess()], empty=False)
        self.assertEqual(self.run_one(f, question=q1, now=T0 + timedelta(minutes=5)), "busy")
        self.assertEqual(f.typed, [])

    def test_sid_changed_after_clear_finds_entry_in_typed_sid(self):
        q1 = q()
        q1.answer, q1.typed_at, q1.typed_sid = "да", T0.isoformat(), "old"
        f = FakeTmux([sess(sid="new")], entries=[
            ("old", (T0 + timedelta(seconds=1)).astimezone(timezone.utc), "owner (inbox, q-1120-01): да")])
        self.assertEqual(self.run_one(f, question=q1, now=T0 + timedelta(minutes=5)), "delivered")
        self.assertEqual(f.typed, [])

    def test_untyped_answer_already_in_transcript_since_answered(self):
        q1 = q()
        q1.answer, q1.answered_at = "да", T0.isoformat()
        f = FakeTmux([sess()], entries=[
            ("s1", (T0 + timedelta(seconds=5)).astimezone(timezone.utc), "owner (inbox, q-1120-01): да")])
        self.assertEqual(self.run_one(f, question=q1), "delivered")
        self.assertEqual(f.typed, [])

    def test_duplicate_windows_types_once_into_conf_sid(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "sessions.conf")
        with open(p, "w") as f:
            f.write("proj | agent | /tmp | opus | b\n")
        f = FakeTmux([sess(sid="a", target="=w:0"), sess(sid="b", target="=w:1")])
        self.assertEqual(self.run_one(f, p), "delivered")
        self.assertEqual([t for t, _ in f.typed], ["=w:1"])

    def test_other_owner_prefix_does_not_count(self):
        q1 = q()
        q1.answer, q1.answered_at = "да", T0.isoformat()
        f = FakeTmux([sess()], echo=False, entries=[
            ("s1", (T0 + timedelta(seconds=5)).astimezone(timezone.utc), "someone (inbox, q-1120-01): old")])
        self.assertEqual(self.run_one(f, question=q1), "unverified")
        self.assertEqual(len(f.typed), 1)

    def test_verify_waits_for_late_transcript(self):
        class Late(FakeTmux):
            polls = 0

            def user_entry_since(self, sid, prefix, since):
                self.polls += 1
                return self.polls > 3 and super().user_entry_since(sid, prefix, since)
        f = Late([sess()])
        self.assertEqual(self.run_one(f), "delivered")
        self.assertEqual(len(f.typed), 1)


class TranscriptTests(unittest.TestCase):
    def write(self, *lines):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "proj-x"))
        with open(os.path.join(d, "proj-x", "sid-9.jsonl"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return d

    SINCE = datetime(2026, 11, 20, 14, 45, 0, tzinfo=timezone.utc)

    def has(self, d, prefix="owner (inbox, q-1120-01): ", sid="sid-9", since=None):
        return deliver.TmuxOps().user_entry_since(sid, prefix, since or self.SINCE, projects_dir=d)

    def test_user_entry_after_since(self):
        d = self.write(entry("user", "owner (inbox, q-1120-01): да", "2026-11-20T14:45:03.120Z"))
        self.assertTrue(self.has(d))
        self.assertFalse(self.has(d, prefix="owner (inbox, q-1120-02): "))
        self.assertFalse(self.has(d, sid="nope"))
        self.assertFalse(self.has(d, sid=""))

    def test_list_content(self):
        line = json.dumps({"type": "user", "timestamp": "2026-11-20T14:45:03Z", "message": {
            "role": "user", "content": [{"type": "text", "text": "owner (inbox, q-1120-01): да"}]}})
        self.assertTrue(self.has(self.write(line)))

    def test_assistant_quote_does_not_count(self):
        d = self.write(entry("assistant", "Жду: owner (inbox, q-1120-01): да", "2026-11-20T14:45:31Z"),
                       entry("user", "ок, а что с q-1120-01? owner (inbox, q-1120-01): да",
                             "2026-11-20T14:45:40Z"))
        self.assertFalse(self.has(d))

    def test_entry_older_than_since_does_not_count(self):
        d = self.write(entry("user", "owner (inbox, q-1120-01): да", "2026-11-20T14:44:00Z"))
        self.assertFalse(self.has(d))

    def test_garbage_lines_ignored(self):
        d = self.write('{"type":"user"', "not json",
                       entry("user", "owner (inbox, q-1120-01): да", "2026-11-20T14:46:00Z"))
        self.assertTrue(self.has(d))

    def test_only_tail_is_read(self):
        d = self.write(entry("user", "owner (inbox, q-1120-01): да", "2026-11-20T14:46:00Z"),
                       "x" * (2 * 1024 * 1024))
        self.assertFalse(self.has(d))


if __name__ == "__main__":
    unittest.main()
