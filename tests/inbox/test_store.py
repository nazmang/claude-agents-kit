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
