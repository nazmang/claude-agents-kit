"""SQLite question store.

Timestamps are ISO-8601 strings (seconds precision, local time) everywhere:
in the database and in Question fields. Empty string means "not set".
Every call opens its own connection, so a Store is safe to share across threads.
"""
import json
import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

KINDS = ("decision", "command", "brainstorm", "info-needed")


class AlreadyClosed(Exception):
    """The question is no longer open (answered, delivered or withdrawn)."""


@dataclass
class Question:
    id: str
    from_: str
    kind: str
    blocking: bool
    title: str
    body: str
    options: list = field(default_factory=list)
    command: str = ""
    ref: str = ""
    created: str = ""
    status: str = "open"
    answer: str = ""
    answered_at: str = ""
    delivered_at: str = ""
    first_attempt: str = ""
    last_attempt: str = ""
    notified_at: str = ""
    otok: str = ""


_COLS = ("id, from_, kind, blocking, title, body, options, command, ref, created, status, "
         "answer, answered_at, delivered_at, first_attempt, last_attempt, notified_at, otok")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
  id TEXT PRIMARY KEY, from_ TEXT NOT NULL, kind TEXT NOT NULL, blocking INTEGER NOT NULL,
  title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', options TEXT NOT NULL DEFAULT '[]',
  command TEXT NOT NULL DEFAULT '', ref TEXT NOT NULL DEFAULT '', created TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open', answer TEXT NOT NULL DEFAULT '',
  answered_at TEXT NOT NULL DEFAULT '', delivered_at TEXT NOT NULL DEFAULT '',
  first_attempt TEXT NOT NULL DEFAULT '', last_attempt TEXT NOT NULL DEFAULT '',
  notified_at TEXT NOT NULL DEFAULT '', otok TEXT NOT NULL DEFAULT ''
)"""


def _iso(when: datetime | None = None) -> str:
    return (when or datetime.now()).isoformat(timespec="seconds")


def _row(r) -> Question:
    d = dict(zip(_COLS.replace(" ", "").split(","), r))
    d["blocking"] = bool(d["blocking"])
    d["options"] = json.loads(d["options"])
    return Question(**d)


class Store:
    def __init__(self, path: str):
        self.path = path
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=10)
        c.execute("PRAGMA busy_timeout=10000")
        return c

    def _one(self, c, id: str) -> Question | None:
        r = c.execute(f"SELECT {_COLS} FROM questions WHERE id=?", (id,)).fetchone()
        return _row(r) if r else None

    def create(self, from_: str, kind: str, title: str, *, blocking=False, body="",
               options=(), command="", ref="", now: datetime | None = None) -> Question:
        if kind not in KINDS:
            raise ValueError(f"bad kind: {kind!r}")
        now = now or datetime.now()
        prefix = f"q-{now:%m%d}-"
        c = self._conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            n = c.execute("SELECT COUNT(*) FROM questions WHERE id LIKE ?",
                          (prefix + "%",)).fetchone()[0] + 1
            qid = f"{prefix}{n:02d}"
            c.execute(
                f"INSERT INTO questions ({_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,'open','','','','','','',?)",
                (qid, from_, kind, int(bool(blocking)), title, body, json.dumps(list(options)),
                 command, ref, _iso(now), secrets.token_urlsafe(16)))
            c.commit()
            return self._one(c, qid)
        finally:
            c.close()

    def get(self, id: str) -> Question | None:
        c = self._conn()
        try:
            return self._one(c, id)
        finally:
            c.close()

    def list(self, status: str | None = None) -> list:
        sql = f"SELECT {_COLS} FROM questions"
        args = ()
        if status:
            sql += " WHERE status=?"
            args = (status,)
        c = self._conn()
        try:
            return [_row(r) for r in c.execute(sql + " ORDER BY created, id", args)]
        finally:
            c.close()

    def _close_open(self, c, id: str, new_status: str, answer: str = "") -> None:
        """Move an open question to new_status (this also invalidates its token)."""
        c.execute("BEGIN IMMEDIATE")
        q = self._one(c, id)
        if q is None:
            raise KeyError(id)
        if q.status != "open":
            raise AlreadyClosed(id)
        c.execute("UPDATE questions SET status=?, answer=?, answered_at=? WHERE id=?",
                  (new_status, answer, _iso() if new_status == "answered" else "", id))

    def answer(self, id: str, text: str) -> Question:
        c = self._conn()
        try:
            self._close_open(c, id, "answered", text)
            c.commit()
            return self._one(c, id)
        finally:
            c.close()

    def answer_by_otok(self, otok: str, option_index: int) -> Question:
        """Answer with the option at option_index. The token stays in the row but
        only works while the question is open, so it is single-use.
        KeyError: unknown token. AlreadyClosed: token used or question closed.
        IndexError: no such option."""
        if not otok:
            raise KeyError("otok")
        c = self._conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            r = c.execute("SELECT id FROM questions WHERE otok=?", (otok,)).fetchone()
            if r is None:
                raise KeyError("otok")
            q = self._one(c, r[0])
            if q.status != "open":
                raise AlreadyClosed(q.id)
            if not 0 <= option_index < len(q.options):
                raise IndexError(option_index)
            c.execute("UPDATE questions SET status='answered', answer=?, answered_at=? "
                      "WHERE id=?", (q.options[option_index], _iso(), q.id))
            c.commit()
            return self._one(c, q.id)
        finally:
            c.close()

    def withdraw(self, id: str, from_: str) -> Question:
        c = self._conn()
        try:
            q = self._one(c, id)
            if q is None:
                raise KeyError(id)
            if q.from_ != from_:
                raise PermissionError(id)
            self._close_open(c, id, "withdrawn")
            c.commit()
            return self._one(c, id)
        finally:
            c.close()

    def _set(self, sql: str, args: tuple) -> None:
        c = self._conn()
        try:
            c.execute(sql, args)
            c.commit()
        finally:
            c.close()

    def mark_delivered(self, id: str) -> None:
        self._set("UPDATE questions SET status='delivered', delivered_at=? WHERE id=?",
                  (_iso(), id))

    def mark_attempt(self, id: str) -> None:
        now = _iso()
        self._set("UPDATE questions SET last_attempt=?, "
                  "first_attempt=CASE WHEN first_attempt='' THEN ? ELSE first_attempt END "
                  "WHERE id=?", (now, now, id))

    def pending_delivery(self) -> list:
        return self.list("answered")

    def mark_notified(self, id: str, when: datetime) -> None:
        self._set("UPDATE questions SET notified_at=? WHERE id=?", (_iso(when), id))

    def unnotified(self) -> list:
        c = self._conn()
        try:
            return [_row(r) for r in c.execute(
                f"SELECT {_COLS} FROM questions WHERE status='open' AND notified_at='' "
                "ORDER BY created, id")]
        finally:
            c.close()
