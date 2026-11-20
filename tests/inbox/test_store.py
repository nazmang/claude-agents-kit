import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "bin"))

from agents_inbox.store import Store, AlreadyClosed  # noqa: E402


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(os.path.join(self.tmp.name, "inbox.db"))
        self.t = datetime(2026, 11, 20, 12, 0)

    def test_create_assigns_daily_id(self):
        a = self.store.create("alpha", "decision", "A", now=self.t)
        b = self.store.create("alpha", "decision", "B", now=self.t)
        c = self.store.create("alpha", "decision", "C", now=datetime(2026, 11, 21, 9, 0))
        self.assertEqual(a.id, "q-1120-01")
        self.assertEqual(b.id, "q-1120-02")
        self.assertEqual(c.id, "q-1121-01")
        self.assertEqual(a.status, "open")

    def test_mark_typed(self):
        q = self.store.create("alpha", "decision", "A", now=self.t)
        self.assertEqual((q.typed_at, q.typed_sid), ("", ""))
        self.store.mark_typed(q.id, "sid-1", self.t)
        got = self.store.get(q.id)
        self.assertEqual((got.typed_at, got.typed_sid), ("2026-11-20T12:00:00", "sid-1"))

    def test_old_database_gains_typed_columns(self):
        import sqlite3
        path = os.path.join(self.tmp.name, "old.db")
        c = sqlite3.connect(path)
        c.execute("""CREATE TABLE questions (
  id TEXT PRIMARY KEY, from_ TEXT NOT NULL, kind TEXT NOT NULL, blocking INTEGER NOT NULL,
  title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', options TEXT NOT NULL DEFAULT '[]',
  command TEXT NOT NULL DEFAULT '', ref TEXT NOT NULL DEFAULT '', created TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open', answer TEXT NOT NULL DEFAULT '',
  answered_at TEXT NOT NULL DEFAULT '', delivered_at TEXT NOT NULL DEFAULT '',
  first_attempt TEXT NOT NULL DEFAULT '', last_attempt TEXT NOT NULL DEFAULT '',
  notified_at TEXT NOT NULL DEFAULT '', otok TEXT NOT NULL DEFAULT '')""")
        c.execute("INSERT INTO questions (id, from_, kind, blocking, title, created) "
                  "VALUES ('q-1120-01', 'a', 'decision', 0, 'T', '2026-11-20T12:00:00')")
        c.commit()
        c.close()
        st = Store(path)
        self.assertEqual(st.get("q-1120-01").typed_at, "")
        st.mark_typed("q-1120-01", "s", self.t)
        self.assertEqual(Store(path).get("q-1120-01").typed_sid, "s")

    def test_create_roundtrip(self):
        q = self.store.create("alpha", "command", "T", blocking=True, body="b",
                              options=["yes", "no"], command="ls", ref="r", now=self.t)
        g = self.store.get(q.id)
        self.assertEqual(g.options, ["yes", "no"])
        self.assertTrue(g.blocking)
        self.assertEqual(g.command, "ls")
        self.assertIsNone(self.store.get("nope"))
        self.assertEqual([x.id for x in self.store.list("open")], [q.id])

    def test_answer_twice_raises_already_closed(self):
        q = self.store.create("alpha", "decision", "A", now=self.t)
        r = self.store.answer(q.id, "ok")
        self.assertEqual((r.status, r.answer), ("answered", "ok"))
        self.assertTrue(r.answered_at)
        with self.assertRaises(AlreadyClosed):
            self.store.answer(q.id, "again")

    def test_otok_single_use(self):
        q = self.store.create("alpha", "decision", "A", options=["x", "y"], now=self.t)
        r = self.store.answer_by_otok(q.otok, 1)
        self.assertEqual(r.answer, "y")
        with self.assertRaises(AlreadyClosed):
            self.store.answer_by_otok(q.otok, 0)

    def test_otok_errors(self):
        q = self.store.create("alpha", "decision", "A", options=["x"], now=self.t)
        with self.assertRaises(KeyError):
            self.store.answer_by_otok("bogus", 0)
        with self.assertRaises(IndexError):
            self.store.answer_by_otok(q.otok, 5)

    def test_otok_dead_after_plain_answer(self):
        q = self.store.create("alpha", "decision", "A", options=["x"], now=self.t)
        self.store.answer(q.id, "z")
        with self.assertRaises(AlreadyClosed):
            self.store.answer_by_otok(q.otok, 0)

    def test_withdraw_foreign_question_denied(self):
        q = self.store.create("alpha", "decision", "A", now=self.t)
        with self.assertRaises(PermissionError):
            self.store.withdraw(q.id, "beta")
        self.assertEqual(self.store.withdraw(q.id, "alpha").status, "withdrawn")
        with self.assertRaises(AlreadyClosed):
            self.store.withdraw(q.id, "alpha")

    def test_pending_delivery_lists_answered_not_delivered(self):
        a = self.store.create("alpha", "decision", "A", now=self.t)
        b = self.store.create("alpha", "decision", "B", now=self.t)
        self.store.create("alpha", "decision", "C", now=self.t)
        self.store.answer(a.id, "1")
        self.store.answer(b.id, "2")
        self.store.mark_attempt(a.id)
        got = self.store.get(a.id)
        self.assertTrue(got.first_attempt and got.last_attempt)
        self.store.mark_delivered(b.id)
        self.assertEqual([q.id for q in self.store.pending_delivery()], [a.id])
        self.assertEqual(self.store.get(b.id).status, "delivered")
        self.assertTrue(self.store.get(b.id).delivered_at)

    def test_unnotified(self):
        a = self.store.create("alpha", "decision", "A", now=self.t)
        b = self.store.create("alpha", "decision", "B", now=self.t)
        self.store.mark_notified(a.id, self.t)
        self.assertEqual([q.id for q in self.store.unnotified()], [b.id])

    def test_kind_validation(self):
        with self.assertRaises(ValueError):
            self.store.create("alpha", "foo", "A", now=self.t)


if __name__ == "__main__":
    unittest.main()
