import contextlib
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "bin"))
os.environ["AGENTS_LANG"] = "en"

from agents_inbox import cli  # noqa: E402
from agents_inbox.config import Config  # noqa: E402
from agents_inbox.store import Store  # noqa: E402

SESSIONS = [{"name": "alpha", "pid": 100, "sid": "s1", "status": "idle", "target": "=a:0"},
            {"name": "ghost", "pid": 200, "sid": "s2", "status": "idle", "target": "=g:0"}]


class Env:
    def __init__(self):
        self.dir = tempfile.mkdtemp()
        self.conf = os.path.join(self.dir, "sessions.conf")
        with open(self.conf, "w") as f:
            f.write("proj | alpha | /x | | sid1\nproj | beta | /y | | sid2\n")
        self.cfg = Config(data_dir=os.path.join(self.dir, "data"), base_url="http://127.0.0.1:9")
        self.posts = []

    def run(self, *argv, pid=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv), sessions=SESSIONS, pid=pid, conf_path=self.conf,
                            cfg=self.cfg, post=self.posts.append,
                            ancestors=lambda p: [p, 150, 100, 1])
        return code, out.getvalue(), err.getvalue()

    def store(self):
        os.makedirs(self.cfg.data_dir, exist_ok=True)
        return Store(os.path.join(self.cfg.data_dir, "inbox.db"))


class DetectTest(unittest.TestCase):
    def test_detect_from_walks_ancestors(self):
        anc = lambda p: [p, 150, 100, 1]  # noqa: E731
        self.assertEqual(cli.detect_from(999, SESSIONS, {"alpha"}, ancestors=anc), "alpha")

    def test_detect_not_in_conf(self):
        anc = lambda p: [p, 200]  # noqa: E731
        self.assertIsNone(cli.detect_from(999, SESSIONS, {"alpha"}, ancestors=anc))

    def test_detect_no_session(self):
        self.assertIsNone(cli.detect_from(999, SESSIONS, {"alpha"}, ancestors=lambda p: [p, 5]))


class AskTest(unittest.TestCase):
    def setUp(self):
        self.e = Env()

    def test_no_tmux_no_from_is_error(self):
        e = Env()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["ask", "hi"], sessions=SESSIONS, pid=999, conf_path=e.conf,
                            cfg=e.cfg, post=e.posts.append, ancestors=lambda p: [p, 5])
        self.assertEqual(code, 2)
        self.assertIn("--from", err.getvalue())
        self.assertEqual(e.store().list(), [])

    def test_from_not_in_sessions_conf_rejected(self):
        code, out, err = self.e.run("ask", "--from", "stranger", "hi", pid=1)
        self.assertEqual(code, 2)
        self.assertIn("--from", err)
        self.assertEqual(self.e.store().list(), [])

    def test_detected_name_not_in_conf_rejected(self):
        e = Env()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = cli.main(["ask", "hi"], sessions=SESSIONS, pid=1, conf_path=e.conf, cfg=e.cfg,
                            post=e.posts.append, ancestors=lambda p: [p, 200])
        self.assertEqual(code, 2)
        self.assertIn("--from", err.getvalue())

    def test_options_only_for_decision(self):
        code, _, err = self.e.run("ask", "--from", "alpha", "--kind", "command",
                                  "--options", "a,b", "hi")
        self.assertEqual(code, 2)

    def test_command_only_for_command(self):
        code, _, _ = self.e.run("ask", "--from", "alpha", "--command", "ls", "hi")
        self.assertEqual(code, 2)

    def test_bad_kind(self):
        code, _, _ = self.e.run("ask", "--from", "alpha", "--kind", "nope", "hi")
        self.assertEqual(code, 2)

    def test_ask_prints_id(self):
        code, out, _ = self.e.run("ask", "--from", "alpha", "--options", "a, b,,c",
                                  "--blocking", "pick")
        self.assertEqual(code, 0)
        qs = self.e.store().list()
        self.assertEqual(out.strip(), qs[0].id)
        self.assertEqual(qs[0].options, ["a", "b", "c"])
        self.assertTrue(qs[0].blocking)
        self.assertEqual(qs[0].kind, "decision")
        self.assertEqual(len(self.e.posts), 1)

    def test_ask_autodetect(self):
        code, out, _ = self.e.run("ask", "hi", pid=999)
        self.assertEqual(code, 0)
        self.assertEqual(self.e.store().list()[0].from_, "alpha")

    def test_post_failure_swallowed(self):
        e = Env()
        def boom(url):
            raise OSError("down")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["ask", "--from", "alpha", "hi"], sessions=SESSIONS, pid=1,
                            conf_path=e.conf, cfg=e.cfg, post=boom)
        self.assertEqual(code, 0)


class WithdrawTest(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        _, out, _ = self.e.run("ask", "--from", "alpha", "hi")
        self.qid = out.strip()

    def test_withdraw_other_agents_question_fails(self):
        code, _, err = self.e.run("ask", "--from", "beta", "--withdraw", self.qid)
        self.assertEqual(code, 1)
        self.assertTrue(err)
        self.assertEqual(self.e.store().get(self.qid).status, "open")

    def test_withdraw_ok_then_closed(self):
        code, _, _ = self.e.run("ask", "--from", "alpha", "--withdraw", self.qid)
        self.assertEqual(code, 0)
        self.assertEqual(self.e.store().get(self.qid).status, "withdrawn")
        code, _, _ = self.e.run("ask", "--from", "alpha", "--withdraw", self.qid)
        self.assertEqual(code, 1)

    def test_withdraw_unknown(self):
        code, _, _ = self.e.run("ask", "--from", "alpha", "--withdraw", "q-0000-99")
        self.assertEqual(code, 1)


class InboxTest(unittest.TestCase):
    def test_empty(self):
        e = Env()
        code, out, _ = e.run("inbox")
        self.assertEqual(code, 0)
        self.assertEqual(len(out.strip().splitlines()), 1)

    def test_rows_and_all(self):
        e = Env()
        _, a, _ = e.run("ask", "--from", "alpha", "--blocking", "first")
        _, b, _ = e.run("ask", "--from", "beta", "second")
        e.run("ask", "--from", "beta", "--withdraw", b.strip())
        code, out, _ = e.run("inbox")
        self.assertIn(a.strip(), out)
        self.assertIn("first", out)
        self.assertIn("!", out)
        self.assertNotIn(b.strip(), out)
        code, out, _ = e.run("inbox", "--all")
        self.assertIn(b.strip(), out)


class AgeTest(unittest.TestCase):
    def test_age(self):
        self.assertEqual(cli.fmt_age(300), "5m")
        self.assertEqual(cli.fmt_age(3 * 3600), "3h")
        self.assertEqual(cli.fmt_age(2 * 86400), "2d")


if __name__ == "__main__":
    unittest.main()
