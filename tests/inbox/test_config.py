import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "bin"))

from agents_inbox import config  # noqa: E402


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "inbox.conf")

    def test_config_defaults_and_parse(self):
        c = config.load(os.path.join(self.tmp.name, "missing.conf"))
        self.assertEqual(c.port, 8787)
        self.assertEqual(c.listen, ["127.0.0.1"])
        self.assertEqual(c.quiet_hours, (23, 8))
        self.assertEqual(c.owner_name, "owner")
        self.assertTrue(c.data_dir.endswith(".local/share/agents/inbox"))
        self.assertTrue(c.token_file.endswith(".config/agents/inbox.token"))
        self.assertNotIn("~", c.data_dir)
        with open(self.path, "w") as f:
            f.write("# comment\nquiet_hours = 22-07\nlisten = a, b\nport = 9000\n"
                    "owner_name = Boss  # x\nbogus = 1\ndata_dir = ~/d\n")
        c = config.load(self.path)
        self.assertEqual(c.quiet_hours, (22, 7))
        self.assertEqual(c.listen, ["a", "b"])
        self.assertEqual(c.port, 9000)
        self.assertEqual(c.data_dir, os.path.expanduser("~/d"))

    def test_env_path(self):
        with open(self.path, "w") as f:
            f.write("port = 1234\n")
        os.environ["AGENTS_INBOX_CONF"] = self.path
        self.addCleanup(os.environ.pop, "AGENTS_INBOX_CONF", None)
        self.assertEqual(config.load().port, 1234)


if __name__ == "__main__":
    unittest.main()
