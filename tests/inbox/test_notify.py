import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "bin"))

from agents_inbox import notify  # noqa: E402
from agents_inbox.config import Config  # noqa: E402
from agents_inbox.store import Store, Question  # noqa: E402

NOON = datetime(2026, 11, 20, 12, 0)
QUIET = (23, 8)


def mkq(**kw):
    base = dict(id="q-1120-01", from_="alpha", kind="decision", blocking=False,
                title="Pick one", body="", options=["a", "b"], otok="tok123")
    base.update(kw)
    return Question(**base)


def mkcfg(**kw):
    base = dict(base_url="http://127.0.0.1:8787", ntfy_url="https://ntfy.example.org",
                ntfy_topic="inbox", token_file="/nonexistent/page.token")
    base.update(kw)
    return Config(**base)


class DecideTest(unittest.TestCase):
    def test_brainstorm_never_pushed(self):
        q = mkq(kind="brainstorm")
        self.assertIsNone(notify.decide(q, "home", NOON, QUIET))
        self.assertIsNone(notify.decide(q, "away", NOON, QUIET))

    def test_quiet_hours_blocking_silent(self):
        t = datetime(2026, 11, 20, 2, 0)
        self.assertEqual(notify.decide(mkq(kind="command", blocking=True), "home", t, QUIET), "silent")

    def test_quiet_hours_decision_held(self):
        t = datetime(2026, 11, 20, 2, 0)
        self.assertIsNone(notify.decide(mkq(), "home", t, QUIET))

    def test_quiet_hours_wrap_midnight(self):
        q = mkq(blocking=True)
        d = lambda h, m: notify.decide(q, "home", datetime(2026, 11, 20, h, m), QUIET)
        self.assertEqual(d(23, 30), "silent")
        self.assertEqual(d(7, 59), "silent")
        self.assertEqual(d(8, 0), "push")

    def test_equal_hours_never_quiet(self):
        self.assertEqual(notify.decide(mkq(), "home", datetime(2026, 11, 20, 3, 0), (5, 5)), "push")

    def test_away_decision_held(self):
        self.assertIsNone(notify.decide(mkq(), "away", NOON, QUIET))

    def test_away_blocking_pushed(self):
        self.assertEqual(notify.decide(mkq(kind="command", blocking=True), "away", NOON, QUIET), "push")

    def test_home_decision_pushed(self):
        self.assertEqual(notify.decide(mkq(), "home", NOON, QUIET), "push")


class BuildMessageTest(unittest.TestCase):
    def test_message_has_no_body_and_no_page_token(self):
        with tempfile.TemporaryDirectory() as d:
            tf = os.path.join(d, "page.token")
            with open(tf, "w") as f:
                f.write("PAGE-SECRET-TOKEN")
            msg = notify.build_message(mkq(body="секрет"), mkcfg(token_file=tf))
        s = json.dumps(msg, ensure_ascii=False)
        self.assertNotIn("секрет", s)
        self.assertNotIn("PAGE-SECRET-TOKEN", s)
        self.assertEqual(msg["topic"], "inbox")
        self.assertEqual(msg["message"], "Pick one")
        self.assertEqual(msg["title"], "alpha · decision")
        self.assertEqual(msg["priority"], 4)
        self.assertEqual(msg["click"], "http://127.0.0.1:8787")

    def test_silent_priority(self):
        self.assertEqual(notify.build_message(mkq(), mkcfg(), mode="silent")["priority"], 2)

    def test_buttons(self):
        msg = notify.build_message(mkq(), mkcfg())
        self.assertEqual(msg["actions"][1], {
            "action": "http", "label": "b", "url": "http://127.0.0.1:8787/a/tok123/1",
            "method": "POST", "clear": True})
        self.assertEqual(len(msg["actions"]), 2)

    def test_buttons_only_up_to_three_options(self):
        msg = notify.build_message(mkq(options=["1", "2", "3", "4"]), mkcfg())
        self.assertNotIn("actions", msg)
        self.assertNotIn("actions", notify.build_message(mkq(options=[]), mkcfg()))
        self.assertNotIn("actions", notify.build_message(mkq(kind="command", blocking=True), mkcfg()))


class PresenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "presence")

    def write(self, text, age_min=0):
        with open(self.path, "w") as f:
            f.write(text)
        ts = (NOON - timedelta(minutes=age_min)).timestamp()
        os.utime(self.path, (ts, ts))

    def test_empty_path_and_missing_file_home(self):
        self.assertEqual(notify.presence(mkcfg(presence_file=""), NOON), "home")
        self.assertEqual(notify.presence(mkcfg(presence_file=self.path, away_marker="away"), NOON), "home")

    def test_marker_means_away(self):
        self.write("owner away (since 10:00)", 1)
        self.assertEqual(notify.presence(mkcfg(presence_file=self.path, away_marker="away"), NOON), "away")

    def test_no_marker_home(self):
        self.write("owner home (since 10:00)", 1)
        self.assertEqual(notify.presence(mkcfg(presence_file=self.path, away_marker="away"), NOON), "home")

    def test_presence_stale_file_means_home(self):
        self.write("owner away", 16)
        self.assertEqual(notify.presence(mkcfg(presence_file=self.path, away_marker="away"), NOON), "home")


class SendTest(unittest.TestCase):
    def test_no_network_when_unconfigured(self):
        def boom(req, timeout=None):
            raise AssertionError("network used")
        self.assertFalse(notify.send({"topic": "x"}, mkcfg(ntfy_url=""), opener=boom))
        self.assertFalse(notify.send({"topic": "x"}, mkcfg(ntfy_topic=""), opener=boom))

    def test_posts_json_with_bearer(self):
        seen = {}

        class R:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            status = 200

        def opener(req, timeout=None):
            seen["url"] = req.full_url
            seen["auth"] = req.get_header("Authorization")
            seen["body"] = json.loads(req.data.decode())
            seen["timeout"] = timeout
            return R()

        with tempfile.TemporaryDirectory() as d:
            tf = os.path.join(d, "ntfy.token")
            with open(tf, "w") as f:
                f.write("  tk_abc\n")
            cfg = mkcfg(ntfy_url="https://ntfy.example.org/", ntfy_token_file=tf)
            self.assertTrue(notify.send({"topic": "inbox"}, cfg, opener=opener))
        self.assertEqual(seen["url"], "https://ntfy.example.org")
        self.assertEqual(seen["auth"], "Bearer tk_abc")
        self.assertEqual(seen["body"], {"topic": "inbox"})
        self.assertEqual(seen["timeout"], 5)

    def test_no_auth_header_without_token_file(self):
        seen = {}

        class R:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            status = 200

        def opener(req, timeout=None):
            seen["auth"] = req.get_header("Authorization")
            return R()

        self.assertTrue(notify.send({"topic": "i"}, mkcfg(), opener=opener))
        self.assertIsNone(seen["auth"])

    def test_error_returns_false(self):
        def opener(req, timeout=None):
            raise OSError("down")
        self.assertFalse(notify.send({"topic": "i"}, mkcfg(), opener=opener))


class TickTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(os.path.join(self.tmp.name, "inbox.db"))
        self.sent = []

    def ok(self, msg, cfg):
        self.sent.append(msg)
        return True

    def home(self, cfg, now):
        return "home"

    def test_tick_sends_and_marks(self):
        q = self.store.create("alpha", "decision", "T", options=["a"], now=NOON)
        n = notify.tick(self.store, mkcfg(), NOON, send=self.ok, presence_fn=self.home)
        self.assertEqual(n, 1)
        self.assertTrue(self.store.get(q.id).notified_at)
        self.assertEqual(notify.tick(self.store, mkcfg(), NOON, send=self.ok, presence_fn=self.home), 0)

    def test_tick_does_not_mark_when_send_fails(self):
        q = self.store.create("alpha", "decision", "T", now=NOON)
        n = notify.tick(self.store, mkcfg(), NOON, send=lambda m, c: False, presence_fn=self.home)
        self.assertEqual(n, 0)
        self.assertEqual(self.store.get(q.id).notified_at, "")
        self.assertEqual(len(self.store.unnotified()), 1)

    def test_tick_stops_after_first_failure(self):
        self.store.create("alpha", "decision", "A", now=NOON)
        self.store.create("alpha", "decision", "B", now=NOON)
        calls = []

        def fail(m, c):
            calls.append(m)
            return False

        n = notify.tick(self.store, mkcfg(), NOON, send=fail, presence_fn=self.home)
        self.assertEqual((n, len(calls)), (0, 1))
        self.assertEqual(len(self.store.unnotified()), 2)

    def test_tick_does_not_mark_none(self):
        q = self.store.create("alpha", "brainstorm", "T", now=NOON)
        notify.tick(self.store, mkcfg(), NOON, send=self.ok, presence_fn=self.home)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.store.get(q.id).notified_at, "")

    def test_decision_held_in_quiet_hours_pushed_at_0800(self):
        q = self.store.create("alpha", "decision", "T", now=datetime(2026, 11, 20, 2, 0))
        cfg = mkcfg()
        night = datetime(2026, 11, 20, 2, 5)
        self.assertEqual(notify.tick(self.store, cfg, night, send=self.ok, presence_fn=self.home), 0)
        self.assertEqual(self.store.get(q.id).notified_at, "")
        morning = datetime(2026, 11, 20, 8, 0)
        self.assertEqual(notify.tick(self.store, cfg, morning, send=self.ok, presence_fn=self.home), 1)
        self.assertEqual(self.sent[0]["priority"], 4)
        self.assertTrue(self.store.get(q.id).notified_at)

    def test_blocking_in_quiet_hours_silent(self):
        self.store.create("alpha", "command", "T", blocking=True, now=NOON)
        notify.tick(self.store, mkcfg(), datetime(2026, 11, 20, 2, 0), send=self.ok, presence_fn=self.home)
        self.assertEqual(self.sent[0]["priority"], 2)

    def test_feature_off_without_topic(self):
        q = self.store.create("alpha", "decision", "T", now=NOON)
        n = notify.tick(self.store, mkcfg(ntfy_topic=""), NOON, send=self.ok, presence_fn=self.home)
        self.assertEqual((n, self.sent), (0, []))
        self.assertEqual(self.store.get(q.id).notified_at, "")


if __name__ == "__main__":
    unittest.main()
