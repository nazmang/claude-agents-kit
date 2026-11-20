import contextlib
import http.client
import json
import os
import sqlite3
import stat
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "bin"))

from agents_inbox import server  # noqa: E402
from agents_inbox.config import Config, db_path  # noqa: E402
from agents_inbox.store import Store  # noqa: E402


class FakeTmux:
    """One idle agent window; typing adds a user entry to its transcript."""

    def __init__(self, empty=True):
        self.empty = empty
        self.typed = []

    def sessions(self):
        return [dict(name="agent", sid="s1", status="idle", target="=w:0")]

    def input_empty(self, target):
        return self.empty

    def type(self, target, text):
        self.typed.append(text)

    def user_entry_since(self, sid, prefix, since):
        return any(t.startswith(prefix) for t in self.typed)


class ServerTest(unittest.TestCase):
    def setUp(self):
        lang = mock.patch.dict(os.environ, {"AGENTS_LANG": "en"})
        lang.start()
        self.addCleanup(lang.stop)
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.cfg = Config(listen=["127.0.0.1"], port=0, data_dir=os.path.join(d, "data"),
                          token_file=os.path.join(d, "cfg", "inbox.token"),
                          ntfy_url="http://127.0.0.1:9", ntfy_topic="topic",
                          base_url="http://127.0.0.1:8787")
        os.makedirs(self.cfg.data_dir)
        self.conf = os.path.join(d, "sessions.conf")
        with open(self.conf, "w") as f:
            f.write("proj | agent | /x | | s1\n")
        self.store = Store(db_path(self.cfg))
        self.tmux = FakeTmux()
        self.sent = []
        self.logs = []
        self.app = server.App(self.cfg, self.store, tmux=self.tmux,
                              send=lambda msg, cfg: self.sent.append(msg) or True,
                              presence_fn=lambda cfg, now: "home", conf_path=self.conf,
                              sleep=lambda s: None, log=self.logs.append)
        self.srv = server.serve(self.cfg, self.app)
        self.port = self.srv.ports[0]
        with open(self.cfg.token_file) as f:
            self.token = f.read().strip()

    def tearDown(self):
        self.srv.close()
        self.app.join_kicks()
        self.tmp.cleanup()

    def req(self, method, path, body=None, headers=None, cookie=True, source=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5,
                                       source_address=source)
        h = dict(headers or {})
        if cookie:
            h["Cookie"] = f"inbox={self.token}"
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
        try:
            c.request(method, path, body=body, headers=h)
            r = c.getresponse()
            return r.status, dict(r.getheaders()), r.read()
        finally:
            c.close()

    def ask(self, **kw):
        kw.setdefault("options", ["yes", "no"])
        return self.store.create("agent", kw.pop("kind", "decision"), "Ship it?", **kw)

    # --- auth -------------------------------------------------------------
    def test_no_cookie_401(self):
        st, _, _ = self.req("GET", "/", cookie=False)
        self.assertEqual(st, 401)
        st, _, _ = self.req("GET", "/api/questions", cookie=False)
        self.assertEqual(st, 401)
        st, _, _ = self.req("GET", "/", headers={"Cookie": "inbox=wrong"}, cookie=False)
        self.assertEqual(st, 401)

    def test_token_sets_cookie(self):
        st, h, _ = self.req("GET", f"/?t={self.token}", cookie=False)
        self.assertEqual(st, 302)
        self.assertEqual(h["Location"], "/")
        ck = h["Set-Cookie"]
        self.assertIn(f"inbox={self.token}", ck)
        self.assertIn("HttpOnly", ck)
        self.assertIn("SameSite=Lax", ck)
        self.assertNotIn("Strict", ck)
        st, _, _ = self.req("GET", "/?t=bad", cookie=False)
        self.assertEqual(st, 401)
        st, h, body = self.req("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn(b"<html", body)
        self.assertEqual(h["X-Frame-Options"], "DENY")
        self.assertEqual(h["Cache-Control"], "no-store")
        self.assertNotIn(b"/*STRINGS*/", body)

    def test_token_file_created_600(self):
        mode = stat.S_IMODE(os.stat(self.cfg.token_file).st_mode)
        self.assertEqual(mode, 0o600)
        self.assertGreaterEqual(len(self.token), 24)

    def test_page_token_never_logged(self):
        self.req("GET", f"/?t={self.token}", cookie=False)
        self.req("GET", "/?t=bad", cookie=False)
        self.assertTrue(self.logs)
        self.assertFalse([x for x in self.logs if self.token in x or "bad" in x])

    def test_unknown_path_404(self):
        self.assertEqual(self.req("GET", "/nope")[0], 404)

    # --- listing ----------------------------------------------------------
    def test_questions_never_expose_otok(self):
        q = self.ask()
        st, _, body = self.req("GET", "/api/questions")
        self.assertEqual(st, 200)
        items = json.loads(body)
        self.assertEqual([i["id"] for i in items], [q.id])
        self.assertNotIn("otok", items[0])
        self.assertNotIn(q.otok.encode(), body)
        self.assertFalse(items[0]["stuck"])

    def test_old_closed_hidden(self):
        old = self.ask()
        self.store.withdraw(old.id, "agent")
        with contextlib.closing(sqlite3.connect(db_path(self.cfg))) as c, c:
            c.execute("UPDATE questions SET created=? WHERE id=?",
                      ((datetime.now() - timedelta(days=8)).isoformat(timespec="seconds"), old.id))
        new = self.ask()
        items = json.loads(self.req("GET", "/api/questions")[2])
        self.assertEqual([i["id"] for i in items], [new.id])

    def test_stuck_flag_after_15_min(self):
        q = self.ask()
        self.store.answer(q.id, "yes")
        past = (datetime.now() - timedelta(minutes=16)).isoformat(timespec="seconds")
        with contextlib.closing(sqlite3.connect(db_path(self.cfg))) as c, c:
            c.execute("UPDATE questions SET first_attempt=? WHERE id=?", (past, q.id))
        items = json.loads(self.req("GET", "/api/questions")[2])
        self.assertTrue(items[0]["stuck"])
        with contextlib.closing(sqlite3.connect(db_path(self.cfg))) as c, c:
            c.execute("UPDATE questions SET first_attempt=? WHERE id=?",
                      (datetime.now().isoformat(timespec="seconds"), q.id))
        items = json.loads(self.req("GET", "/api/questions")[2])
        self.assertFalse(items[0]["stuck"])

    # --- answering --------------------------------------------------------
    def test_answer_then_delivered_by_loop(self):
        self.tmux.empty = False            # owner is typing in the agent window
        q = self.ask()
        st, _, body = self.req("POST", "/api/answer", {"id": q.id, "text": "go"})
        self.assertEqual(st, 200)
        self.assertNotIn("otok", json.loads(body))
        self.app.join_kicks()
        self.assertEqual(self.tmux.typed, [])
        self.assertEqual(self.store.get(q.id).status, "answered")
        self.assertTrue(self.store.get(q.id).first_attempt)
        self.tmux.empty = True
        self.app.deliver_pending()
        self.assertEqual(self.store.get(q.id).status, "delivered")
        self.assertEqual(self.tmux.typed, [f"owner (inbox, {q.id}): go"])
        self.app.deliver_pending()
        self.assertEqual(len(self.tmux.typed), 1)

    def test_typed_but_not_in_transcript_stays_answered_no_retype(self):
        self.tmux.user_entry_since = lambda sid, prefix, since: False
        q = self.ask()
        self.req("POST", "/api/answer", {"id": q.id, "text": "go"})
        self.app.join_kicks()
        got = self.store.get(q.id)
        self.assertEqual(got.status, "answered")
        self.assertTrue(got.typed_at)
        self.assertEqual(got.typed_sid, "s1")
        self.app.deliver_pending()
        self.assertEqual(len(self.tmux.typed), 1)
        self.tmux.user_entry_since = lambda sid, prefix, since: True
        self.app.deliver_pending()
        self.assertEqual(self.store.get(q.id).status, "delivered")
        self.assertEqual(len(self.tmux.typed), 1)

    def test_answer_kick_delivers(self):
        q = self.ask()
        self.req("POST", "/api/answer", {"id": q.id, "text": "go"})
        self.app.join_kicks()
        self.assertEqual(self.store.get(q.id).status, "delivered")
        self.assertEqual(len(self.tmux.typed), 1)

    def test_answer_errors(self):
        q = self.ask()
        self.assertEqual(self.req("POST", "/api/answer", {"id": "q-0000-99", "text": "x"})[0], 404)
        self.assertEqual(self.req("POST", "/api/answer", {"id": q.id, "text": "  "})[0], 400)
        self.assertEqual(self.req("POST", "/api/answer", b"{}", cookie=True,
                                  headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.req("POST", "/api/answer", {"id": q.id, "text": "x" * 70000})[0], 413)
        self.assertEqual(self.req("POST", "/api/answer", {"id": q.id, "text": "ok"},
                                  cookie=False)[0], 401)
        self.assertEqual(self.req("POST", "/api/answer", {"id": q.id, "text": "ok"})[0], 200)
        self.assertEqual(self.req("POST", "/api/answer", {"id": q.id, "text": "again"})[0], 409)

    def test_otok_replay_409(self):
        q = self.ask()
        st, _, body = self.req("POST", f"/a/{q.otok}/1", cookie=False)
        self.assertEqual(st, 200)
        self.assertIn(b"accepted", body.lower())
        st, _, _ = self.req("POST", f"/a/{q.otok}/1", cookie=False)
        self.assertEqual(st, 409)
        self.app.join_kicks()
        self.assertEqual(self.tmux.typed, [f"owner (inbox, {q.id}): no"])
        self.assertFalse([x for x in self.logs if q.otok in x])

    def test_otok_errors(self):
        q = self.ask()
        self.assertEqual(self.req("POST", "/a/nosuchtoken/0", cookie=False)[0], 404)
        self.assertEqual(self.req("POST", f"/a/{q.otok}/5", cookie=False)[0], 400)
        self.assertEqual(self.req("POST", f"/a/{q.otok}/x", cookie=False)[0], 400)
        self.assertEqual(self.store.get(q.id).status, "open")

    # --- created hook -----------------------------------------------------
    def test_created_endpoint_runs_tick(self):
        self.ask(blocking=True)
        st, _, _ = self.req("POST", "/api/created", b"{}", cookie=False)
        self.assertEqual(st, 204)
        self.app.join_kicks()
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn("body", self.sent[0]["message"])

    def test_created_endpoint_rejects_non_local(self):
        self.ask(blocking=True)
        try:
            st, _, _ = self.req("POST", "/api/created", b"{}", cookie=False,
                                headers={"X-Forwarded-For": "127.0.0.1", "Host": "127.0.0.1"},
                                source=("127.0.0.2", 0))
        except OSError:
            self.skipTest("cannot bind 127.0.0.2")
        self.assertEqual(st, 403)
        self.app.join_kicks()
        self.assertEqual(self.sent, [])

    def test_created_accepts_listen_address_source(self):
        cfg = Config(listen=["127.0.0.2"], port=0, data_dir=self.cfg.data_dir,
                     token_file=self.cfg.token_file, ntfy_url="http://127.0.0.1:9",
                     ntfy_topic="topic", base_url="http://127.0.0.2:8787")
        try:
            srv = server.serve(cfg, self.app)
        except OSError:
            self.skipTest("cannot bind 127.0.0.2")
        self.addCleanup(srv.close)
        def post(source):
            c = http.client.HTTPConnection("127.0.0.2", srv.ports[0], timeout=5, source_address=source)
            try:
                c.request("POST", "/api/created", body=b"{}")
                return c.getresponse().status
            finally:
                c.close()
        self.assertEqual(post(("127.0.0.2", 0)), 204)
        self.app.join_kicks()
        self.assertEqual(post(("127.0.0.1", 0)), 204)   # loopback stays local

    # --- loop -------------------------------------------------------------
    def test_loop_survives_errors(self):
        def boom():
            raise RuntimeError("tmux gone")
        self.tmux.sessions = boom
        q = self.ask()
        self.store.answer(q.id, "x")
        self.app.deliver_pending()          # must not raise
        self.assertTrue(any("tmux gone" in x for x in self.logs))
        self.assertEqual(self.store.get(q.id).status, "answered")

    def test_notify_tick(self):
        self.ask(blocking=True)
        self.app.notify_tick()
        self.app.notify_tick()
        self.assertEqual(len(self.sent), 1)


class ListenTest(unittest.TestCase):
    def test_refuses_wildcard_listen(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for bad in (["0.0.0.0"], ["::"], ["[::]"], ["*"], [""], ["127.0.0.1", "0.0.0.0"], [],
                    ["0"], ["::0"], ["0:0:0:0:0:0:0:0"], ["[::0]"], [" 0.0.0.0 "]):
            cfg = Config(listen=bad, port=0, data_dir=tmp.name,
                         token_file=os.path.join(tmp.name, "tok"))
            app = server.App(cfg, Store(db_path(cfg)), log=lambda s: None)
            with self.assertRaises(SystemExit, msg=repr(bad)):
                server.serve(cfg, app)


if __name__ == "__main__":
    unittest.main()
