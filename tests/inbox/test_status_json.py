"""`agents status --json` fields the panel reads: inbox and tool_hint."""
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

BIN = os.path.join(os.path.dirname(__file__), "..", "..", "bin")
sys.path.insert(0, BIN)

from agents_inbox import tmuxlib  # noqa: E402
from agents_inbox.store import Store  # noqa: E402


def load_agents():
    path = os.path.join(BIN, "agents")
    loader = importlib.machinery.SourceFileLoader("agents_cli", path)
    spec = importlib.util.spec_from_loader("agents_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


agents = load_agents()


class InboxField(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.conf = os.path.join(self.dir, "inbox.conf")
        self.env = mock.patch.dict(os.environ, {"AGENTS_INBOX_CONF": self.conf})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def write_conf(self):
        with open(self.conf, "w") as f:
            f.write(f"data_dir = {self.dir}/data\n")

    def test_no_config_means_disabled(self):
        self.assertIsNone(agents.inbox())

    def test_config_without_db_is_zero(self):
        self.write_conf()
        self.assertEqual(agents.inbox(), {"open": 0})
        self.assertFalse(os.path.exists(os.path.join(self.dir, "data", "inbox.db")))   # read-only

    def test_counts_open_questions_only(self):
        self.write_conf()
        os.makedirs(os.path.join(self.dir, "data"))
        st = Store(os.path.join(self.dir, "data", "inbox.db"))
        st.create("alpha", "decision", "one?")
        st.create("alpha", "decision", "two?")
        q = st.create("beta", "info-needed", "three?")
        st.answer(q.id, "yes")
        self.assertEqual(agents.inbox(), {"open": 2})

    def test_broken_db_is_zero(self):
        self.write_conf()
        os.makedirs(os.path.join(self.dir, "data"))
        with open(os.path.join(self.dir, "data", "inbox.db"), "w") as f:
            f.write("not a database")
        self.assertEqual(agents.inbox(), {"open": 0})


class ToolHint(unittest.TestCase):
    def test_test_commands(self):
        for cmd in ("python3 -m pytest -q", "python3 -W always -m unittest discover -s tests/inbox",
                    "claude plugin test plugins/agents-panel", "npm test", "npm run test",
                    "go test ./...", "cargo test"):
            self.assertEqual(tmuxlib.tool_hint("Bash", {"command": cmd}), "test", cmd)

    def test_other_commands_and_tools(self):
        self.assertEqual(tmuxlib.tool_hint("Bash", {"command": "git status"}), "")
        self.assertEqual(tmuxlib.tool_hint("Bash", None), "")
        self.assertEqual(tmuxlib.tool_hint("Read", {"command": "pytest"}), "")

    def test_transcript_gives_last_tool_and_hint(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "proj"))
        lines = [
            {"type": "assistant", "timestamp": "2026-11-20T10:00:00Z",
             "message": {"model": "claude-opus", "usage": {"input_tokens": 5},
                         "content": [{"type": "tool_use", "name": "Read", "input": {}},
                                     {"type": "tool_use", "name": "Bash",
                                      "input": {"command": "go test ./..."}}]}},
        ]
        with open(os.path.join(d, "proj", "sid.jsonl"), "w") as f:
            for line in lines:
                f.write(json.dumps(line, separators=(",", ":")) + "\n")
        with mock.patch.object(tmuxlib, "PROJECTS_DIR", d):
            info = tmuxlib.transcript_info("sid")
        self.assertEqual(info[4:], ("Bash", "test"))


if __name__ == "__main__":
    unittest.main()
