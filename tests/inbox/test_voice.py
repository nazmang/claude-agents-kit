"""Voice API: its own Bearer token, limited answers, dictated lines for an agent."""
import http.client
import json
import os
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "bin"))

from agents_inbox import deliver, server  # noqa: E402
from agents_inbox.config import Config, db_path, load  # noqa: E402
from agents_inbox.store import Say, Store  # noqa: E402


class FakeTmux:
    """One agent window; typing adds a user entry to its transcript."""

    def __init__(self):
        self.empty = True
        self.status = "idle"
        self.alive = True
        self.typed = []

    def sessions(self):
        if not self.alive:
            return []
        return [dict(name="agent", sid="s1", status=self.status, target="=w:0")]

    def input_empty(self, target):
        return self.empty

    def type(self, target, text):
        self.typed.append(text)

    def user_entry_since(self, sid, prefix, since):
        return any(t.startswith(prefix) for t in self.typed)


class VoiceBase(unittest.TestCase):
    voice = True

    def setUp(self):
        lang = mock.patch.dict(os.environ, {"AGENTS_LANG": "en"})
        lang.start()
        self.addCleanup(lang.stop)
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.cfg = Config(listen=["127.0.0.1"], port=0, data_dir=os.path.join(d, "data"),
                          token_file=os.path.join(d, "cfg", "inbox.token"),
                          voice_token_file=os.path.join(d, "cfg", "voice.token") if self.voice else "",
                          owner_name="Owner", voice_label="voice")
        os.makedirs(self.cfg.data_dir)
        self.conf = os.path.join(d, "sessions.conf")
        with open(self.conf, "w") as f:
            f.write("proj | agent | /x | | s1\nproj | sleeper | /y | | s2\n")
        self.store = Store(db_path(self.cfg))
        self.tmux = FakeTmux()
        self.now = datetime.now()
        self.logs = []
        self.app = server.App(self.cfg, self.store, tmux=self.tmux,
                              send=lambda msg, cfg: True,
                              presence_fn=lambda cfg, now: "home", conf_path=self.conf,
                              sleep=lambda s: None, log=self.logs.append,
                              clock=lambda: self.now)
        self.srv = server.serve(self.cfg, self.app)
        self.port = self.srv.ports[0]
        with open(self.cfg.token_file) as f:
            self.page_token = f.read().strip()
        self.token = ""
        if self.voice:
            with open(self.cfg.voice_token_file) as f:
                self.token = f.read().strip()

    def tearDown(self):
        self.srv.close()
        self.app.join_kicks()
        self.tmp.cleanup()

    def req(self, method, path, body=None, bearer=None, cookie=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {}
        if bearer is None:
            bearer = self.token
        if bearer:
            h["Authorization"] = f"Bearer {bearer}"
        if cookie:
            h["Cookie"] = f"inbox={cookie}"
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        try:
            c.request(method, path, body=body, headers=h)
            r = c.getresponse()
            raw = r.read()
            try:
                return r.status, json.loads(raw)
            except ValueError:
                return r.status, raw
        finally:
            c.close()

    def ask(self, kind="decision", **kw):
        return self.store.create("agent", kind, "Ship it?", **kw)


class VoiceOff(VoiceBase):
    voice = False

    def test_routes_absent_without_voice_token_file(self):
        q = self.ask()
        self.assertEqual(self.req("POST", "/api/voice/answer", {"id": q.id, "text": "yes"},
                                  bearer="anything")[0], 404)
        self.assertEqual(self.req("POST", "/api/voice/say", {"to": "agent", "text": "hi"},
                                  bearer="anything")[0], 404)
        # the page cookie does not open them either
        self.assertEqual(self.req("POST", "/api/voice/say", {"to": "agent", "text": "hi"},
                                  bearer="", cookie=self.page_token)[0], 404)
        self.assertEqual(self.store.get(q.id).status, "open")

    def test_empty_bearer_is_not_a_token(self):
        self.assertEqual(self.req("GET", "/api/questions", bearer=" ")[0], 401)


class VoiceAuth(VoiceBase):
    def test_voice_token_file_created_600_and_differs_from_page_token(self):
        mode = stat.S_IMODE(os.stat(self.cfg.voice_token_file).st_mode)
        self.assertEqual(mode, 0o600)
        self.assertGreaterEqual(len(self.token), 24)
        self.assertNotEqual(self.token, self.page_token)

    def test_wrong_or_missing_bearer_401(self):
        q = self.ask()
        for bearer in ("wrong", "", self.page_token):
            with self.subTest(bearer=bearer[:5]):
                self.assertEqual(self.req("POST", "/api/voice/answer",
                                          {"id": q.id, "text": "yes"}, bearer=bearer or " ")[0], 401)
                self.assertEqual(self.req("POST", "/api/voice/say",
                                          {"to": "agent", "text": "hi"}, bearer=bearer or " ")[0], 401)
        self.assertEqual(self.store.get(q.id).status, "open")
        self.assertEqual(self.tmux.typed, [])

    def test_page_cookie_does_not_open_voice_routes(self):
        st, _ = self.req("POST", "/api/voice/say", {"to": "agent", "text": "hi"},
                         bearer=" ", cookie=self.page_token)
        self.assertEqual(st, 401)

    def test_voice_token_cannot_use_page_routes(self):
        q = self.ask()
        self.assertEqual(self.req("POST", "/api/answer", {"id": q.id, "text": "yes"})[0], 401)
        self.assertEqual(self.req("GET", "/")[0], 401)
        self.assertEqual(self.req("GET", f"/?t={self.token}", bearer=" ")[0], 401)
        self.assertEqual(self.store.get(q.id).status, "open")

    def test_voice_token_reads_questions_without_otok(self):
        q = self.ask(options=["yes", "no"])
        st, items = self.req("GET", "/api/questions")
        self.assertEqual(st, 200)
        self.assertEqual([i["id"] for i in items], [q.id])
        self.assertNotIn("otok", items[0])

    def test_voice_token_never_logged(self):
        self.req("GET", "/api/questions")
        self.req("POST", "/api/voice/say", {"to": "agent", "text": "hi"})
        self.assertTrue(self.logs)
        self.assertFalse([x for x in self.logs if self.token in x])


class VoiceAnswer(VoiceBase):
    def test_decision_and_info_needed_are_answered(self):
        for kind in ("decision", "info-needed"):
            with self.subTest(kind=kind):
                q = self.ask(kind)
                st, body = self.req("POST", "/api/voice/answer", {"id": q.id, "text": "yes"})
                self.assertEqual(st, 200)
                self.assertEqual(body["answer"], "yes")
                self.assertIn(self.store.get(q.id).status, ("answered", "delivered"))

    def test_answer_is_typed_into_the_agent_window(self):
        q = self.ask()
        self.req("POST", "/api/voice/answer", {"id": q.id, "text": "no"})
        self.app.join_kicks()
        self.assertEqual(self.tmux.typed, [f"Owner (inbox, {q.id}): no"])
        self.assertEqual(self.store.get(q.id).status, "delivered")

    def test_command_brainstorm_and_blocking_are_refused(self):
        cases = [self.ask("command", command="make deploy"), self.ask("brainstorm"),
                 self.ask("decision", blocking=True), self.ask("info-needed", blocking=True)]
        for q in cases:
            with self.subTest(kind=q.kind, blocking=q.blocking):
                st, body = self.req("POST", "/api/voice/answer", {"id": q.id, "text": "yes"})
                self.assertEqual(st, 403)
                self.assertEqual(body["error"], "voice-defer-only")
                self.assertEqual(self.store.get(q.id).status, "open")
        self.app.join_kicks()
        self.assertEqual(self.tmux.typed, [])

    def test_unknown_closed_and_bad_input(self):
        self.assertEqual(self.req("POST", "/api/voice/answer", {"id": "q-0000-99", "text": "y"})[0], 404)
        q = self.ask()
        self.assertEqual(self.req("POST", "/api/voice/answer", {"id": q.id, "text": "  "})[0], 400)
        self.assertEqual(self.req("POST", "/api/voice/answer", {"id": q.id, "text": 1})[0], 400)
        self.assertEqual(self.req("POST", "/api/voice/answer", {"id": q.id, "text": "yes"})[0], 200)
        self.assertEqual(self.req("POST", "/api/voice/answer", {"id": q.id, "text": "no"})[0], 409)
        self.assertEqual(self.store.get(q.id).answer, "yes")


class VoiceSay(VoiceBase):
    def say(self, to="agent", text="run the tests"):
        return self.req("POST", "/api/voice/say", {"to": to, "text": text})

    def test_line_is_typed_with_the_voice_prefix(self):
        st, body = self.say()
        self.assertEqual(st, 202)
        self.assertRegex(body["id"], r"^v-\d{4}-01$")
        self.app.join_kicks()
        self.assertEqual(self.tmux.typed, [f"Owner (voice, {body['id']}): run the tests"])
        self.assertEqual(self.store.get_say(body["id"]).status, "delivered")

    def test_unknown_agent_422_and_absent_window_409(self):
        st, body = self.say(to="nobody")
        self.assertEqual((st, body["error"]), (422, "unknown-agent"))
        st, body = self.say(to="sleeper")       # in sessions.conf, no live window
        self.assertEqual((st, body["error"]), (409, "no-session"))
        self.assertEqual(self.store.pending_says(), [])

    def test_bad_input(self):
        self.assertEqual(self.say(text="   ")[0], 400)
        self.assertEqual(self.req("POST", "/api/voice/say", {"to": 5, "text": "x"})[0], 400)
        self.assertEqual(self.say(text="x" * (server.MAX_SAY + 1))[0], 413)
        self.assertEqual(self.say(text="x" * server.MAX_SAY)[0], 202)

    def test_control_characters_and_newlines_do_not_reach_the_window(self):
        st, body = self.say(text="one\ntwo\x1b[31m \x07three\r\n/clear")
        self.assertEqual(st, 202)
        self.app.join_kicks()
        line = self.tmux.typed[0]
        self.assertTrue(line.startswith(f"Owner (voice, {body['id']}): one two"))
        self.assertFalse([ch for ch in line if ord(ch) < 32 or ord(ch) == 127])
        self.assertNotIn("\x1b", line)

    def test_busy_window_keeps_the_line_queued_then_delivers(self):
        self.tmux.status = "busy"
        st, body = self.say()
        self.assertEqual(st, 202)
        self.app.join_kicks()
        self.assertEqual(self.tmux.typed, [])
        self.assertEqual(self.store.get_say(body["id"]).status, "queued")
        self.tmux.status = "idle"
        self.tmux.empty = False                 # the owner is typing there
        self.app.deliver_pending()
        self.assertEqual(self.tmux.typed, [])
        self.tmux.empty = True
        self.app.deliver_pending()
        self.assertEqual(len(self.tmux.typed), 1)
        self.assertEqual(self.store.get_say(body["id"]).status, "delivered")
        self.app.deliver_pending()              # delivered lines are not typed again
        self.assertEqual(len(self.tmux.typed), 1)

    def test_line_expires_when_nobody_takes_it(self):
        self.tmux.status = "busy"
        _, body = self.say()
        self.app.join_kicks()
        self.now += server.SAY_TTL + timedelta(minutes=1)
        self.tmux.status = "idle"
        self.app.deliver_pending()
        self.assertEqual(self.tmux.typed, [])
        self.assertEqual(self.store.get_say(body["id"]).status, "expired")

    def test_says_do_not_block_answers(self):
        self.tmux.status = "busy"
        self.say()
        q = self.ask()
        self.tmux.status = "idle"
        self.req("POST", "/api/voice/answer", {"id": q.id, "text": "yes"})
        self.app.join_kicks()
        self.assertEqual(self.store.get(q.id).status, "delivered")
        self.assertEqual(len(self.tmux.typed), 2)


class VoiceConfig(unittest.TestCase):
    def test_keys_are_read_and_expanded(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "inbox.conf")
            with open(p, "w", encoding="utf-8") as f:
                f.write("voice_token_file = ~/x/voice.token\nvoice_label = голос\n")
            cfg = load(p)
        self.assertEqual(cfg.voice_token_file, os.path.expanduser("~/x/voice.token"))
        self.assertEqual(cfg.voice_label, "голос")

    def test_voice_is_off_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(load(os.path.join(d, "missing.conf")).voice_token_file, "")

    def test_say_prefix(self):
        cfg = Config(owner_name="Аня", voice_label="голос")
        self.assertEqual(deliver.say_prefix(Say(id="v-1123-01", to="a", text="t"), cfg),
                         "Аня (голос, v-1123-01): ")


if __name__ == "__main__":
    unittest.main()
