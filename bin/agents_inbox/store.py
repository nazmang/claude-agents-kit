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
    typed_at: str = ""
    typed_sid: str = ""


@dataclass
class Say:
    """A line the owner dictated for one agent's window (the voice API).
    Duck-types the Question fields deliver_one reads: from_, answer, answered_at."""
    id: str
    to: str
    text: str
    created: str = ""
    status: str = "queued"      # queued | delivered | expired
    typed_at: str = ""
    typed_sid: str = ""
    delivered_at: str = ""

    @property
    def from_(self):
        return self.to

    @property
    def answer(self):
        return self.text

    @property
    def answered_at(self):
        return self.created


_SAY_COLS = "id, to_, text, created, status, typed_at, typed_sid, delivered_at"

_SAY_SCHEMA = """
CREATE TABLE IF NOT EXISTS says (
  id TEXT PRIMARY KEY, to_ TEXT NOT NULL, text TEXT NOT NULL, created TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued', typed_at TEXT NOT NULL DEFAULT '',
  typed_sid TEXT NOT NULL DEFAULT '', delivered_at TEXT NOT NULL DEFAULT ''
)"""


def _say(r) -> Say:
    return Say(id=r[0], to=r[1], text=r[2], created=r[3], status=r[4], typed_at=r[5],
               typed_sid=r[6], delivered_at=r[7])


_COLS = ("id, from_, kind, blocking, title, body, options, command, ref, created, status, "
         "answer, answered_at, delivered_at, first_attempt, last_attempt, notified_at, otok, "
         "typed_at, typed_sid")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
  id TEXT PRIMARY KEY, from_ TEXT NOT NULL, kind TEXT NOT NULL, blocking INTEGER NOT NULL,
  title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', options TEXT NOT NULL DEFAULT '[]',
  command TEXT NOT NULL DEFAULT '', ref TEXT NOT NULL DEFAULT '', created TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open', answer TEXT NOT NULL DEFAULT '',
  answered_at TEXT NOT NULL DEFAULT '', delivered_at TEXT NOT NULL DEFAULT '',
  first_attempt TEXT NOT NULL DEFAULT '', last_attempt TEXT NOT NULL DEFAULT '',
  notified_at TEXT NOT NULL DEFAULT '', otok TEXT NOT NULL DEFAULT '',
  typed_at TEXT NOT NULL DEFAULT '', typed_sid TEXT NOT NULL DEFAULT ''
)"""

# Columns added after the first release: (name, definition) for ALTER TABLE.
_ADDED = (("typed_at", "TEXT NOT NULL DEFAULT ''"), ("typed_sid", "TEXT NOT NULL DEFAULT ''"))


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
        c = self._conn()
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute(_SCHEMA)
            c.execute(_SAY_SCHEMA)
            have = {r[1] for r in c.execute("PRAGMA table_info(questions)")}
            for name, decl in _ADDED:
                if name not in have:
                    c.execute(f"ALTER TABLE questions ADD COLUMN {name} {decl}")
            c.commit()
        finally:
            c.close()

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
                f"INSERT INTO questions ({_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,'open','','','','','','',?,'','')",
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

    def mark_typed(self, id: str, sid: str, when: datetime | None = None) -> None:
        """The answer was typed into session `sid`; delivery waits for its transcript."""
        self._set("UPDATE questions SET typed_at=?, typed_sid=? WHERE id=?",
                  (_iso(when), sid or "", id))

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

    # --- dictated lines (voice API) -----------------------------------------
    def create_say(self, to: str, text: str, now: datetime | None = None) -> Say:
        now = now or datetime.now()
        prefix = f"v-{now:%m%d}-"
        c = self._conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            n = c.execute("SELECT COUNT(*) FROM says WHERE id LIKE ?",
                          (prefix + "%",)).fetchone()[0] + 1
            sid = f"{prefix}{n:02d}"
            c.execute(f"INSERT INTO says ({_SAY_COLS}) VALUES (?,?,?,?,'queued','','','')",
                      (sid, to, text, _iso(now)))
            c.commit()
            r = c.execute(f"SELECT {_SAY_COLS} FROM says WHERE id=?", (sid,)).fetchone()
            return _say(r)
        finally:
            c.close()

    def get_say(self, id: str) -> Say | None:
        c = self._conn()
        try:
            r = c.execute(f"SELECT {_SAY_COLS} FROM says WHERE id=?", (id,)).fetchone()
            return _say(r) if r else None
        finally:
            c.close()

    def pending_says(self) -> list:
        c = self._conn()
        try:
            return [_say(r) for r in c.execute(
                f"SELECT {_SAY_COLS} FROM says WHERE status='queued' ORDER BY created, id")]
        finally:
            c.close()

    def mark_say_typed(self, id: str, sid: str, when: datetime | None = None) -> None:
        self._set("UPDATE says SET typed_at=?, typed_sid=? WHERE id=?",
                  (_iso(when), sid or "", id))

    def mark_say_delivered(self, id: str) -> None:
        self._set("UPDATE says SET status='delivered', delivered_at=? WHERE id=?", (_iso(), id))

    def mark_say_expired(self, id: str) -> None:
        self._set("UPDATE says SET status='expired' WHERE id=? AND status='queued'", (id,))
