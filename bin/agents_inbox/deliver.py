"""Format an owner's answer and type it into the asking agent's tmux window.

deliver_one only does tmux work and returns a status string; the caller
(the server loop) records attempts in the Store.
"""
import glob
from datetime import datetime, timedelta, timezone
import json
import os
import re
import time

from . import tmuxlib
from .i18n import tr

IDLE_STATES = ("idle", "shell")
MAX_INLINE = 2000
VERIFY_DELAY = 1.0
SCROLLBACK_LINES = 3000
TRANSCRIPT_TAIL = 1024 * 1024

STRINGS = {
    "ru": {"long_answer": "ответ в файле {path}"},
    "en": {"long_answer": "answer in file {path}"},
    "uk": {"long_answer": "відповідь у файлі {path}"},
}


def t(key, **vars):
    return tr(STRINGS, key, **vars)


class TmuxOps:
    """Thin wrapper over tmuxlib; tests substitute a fake."""

    def sessions(self):
        return tmuxlib.sessions()

    def input_empty(self, target):
        return tmuxlib.input_empty(target)

    def type(self, target, text):
        tmuxlib.tmux("send-keys", "-t", target, "-l", text)
        time.sleep(0.5)
        tmuxlib.tmux("send-keys", "-t", target, "Enter")

    def user_entry_since(self, sid, prefix, since, projects_dir=None):
        """True if the session's transcript has a user entry newer than `since`
        (aware datetime) whose text starts with `prefix`. Assistant text that
        merely quotes the line does not count."""
        if not sid:
            return False
        root = projects_dir or tmuxlib.PROJECTS_DIR
        for p in glob.glob(os.path.join(root, "*", f"{sid}.jsonl")):
            try:
                size = os.path.getsize(p)
                with open(p, "rb") as f:
                    f.seek(max(0, size - TRANSCRIPT_TAIL))
                    lines = f.read().decode("utf-8", "replace").splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                if '"user"' not in line:
                    continue
                if _user_entry_matches(line, prefix, since):
                    return True
        return False


def _entry_text(msg):
    content = (msg or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                return c.get("text") or ""
    return ""


def _user_entry_matches(line, prefix, since):
    try:
        d = json.loads(line)
        if d.get("type") != "user":
            return False
        ts = datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00"))
    except (ValueError, KeyError, TypeError, AttributeError):
        return False
    if ts.tzinfo is None:
        ts = ts.astimezone()
    return ts >= since and _entry_text(d.get("message")).lstrip().startswith(prefix)


_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_BAD_INLINE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_BAD_FILE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")   # keeps \t and \n


def answer_prefix(q, cfg) -> str:
    return f"{cfg.owner_name} ({cfg.inbox_label}, {q.id}): "


def say_prefix(say, cfg) -> str:
    """Prefix of a dictated line: `<owner_name> (<voice_label>, <id>): `."""
    return f"{cfg.owner_name} ({cfg.voice_label}, {say.id}): "


def format_answer(q, text, cfg, prefix=None) -> str:
    prefix = prefix or answer_prefix(q, cfg)
    text = _CSI.sub("", text)
    flat = _BAD_INLINE.sub("", re.sub(r"\r\n|\r|\n", " ⏎ ", text))
    if len(flat) <= MAX_INLINE:
        return prefix + flat
    data_dir = os.path.expanduser(cfg.data_dir)
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, f"{q.id}.md")
    original = _BAD_FILE.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(original)
    return prefix + t("long_answer", path=path)


def _target_key(target):
    """"=session:index" -> (session, int index); unparsable -> (target, -1)."""
    try:
        sess, idx = target.rsplit(":", 1)
        return (sess, int(idx))
    except ValueError:
        return (target, -1)


def pick_target(name, sessions, conf_sid):
    cands = sorted((s for s in sessions if s.get("name") == name and s.get("target")),
                   key=lambda s: _target_key(s["target"]))
    for s in cands:
        if conf_sid and s.get("sid") == conf_sid:
            return s
    return cands[0] if cands else None


def conf_path_default() -> str:
    return os.environ.get("AGENTS_SESSIONS_CONF") or os.path.expanduser("~/.config/agents/sessions.conf")


def conf_rows(path=None):
    """Rows of sessions.conf (project | name | dir | model | session id ...) as lists."""
    rows = []
    try:
        with open(path or conf_path_default(), encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if line:
                    rows.append([x.strip() for x in line.split("|")])
    except OSError:
        pass
    return rows


def conf_names(path=None) -> set:
    return {p[1] for p in conf_rows(path) if len(p) >= 2 and p[1]}


def conf_sid(name, path=None):
    """Session id of `name` in sessions.conf."""
    for p in conf_rows(path):
        if len(p) >= 5 and p[1] == name:
            return p[4] or None
    return None


VERIFY_TIMEOUT = 5.0                  # wait this long for the typed line in the transcript
RETYPE_AFTER = timedelta(minutes=2)   # typed but never seen: type again after this
TS_SLACK = timedelta(seconds=2)       # typed_at has seconds precision


def _aware(iso):
    try:
        return datetime.fromisoformat(iso).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def deliver_one(q, cfg, tmux=None, conf_path=None, sleep=time.sleep, clock=datetime.now,
                on_typed=None, prefix=None) -> str:
    """Returns delivered | busy | no-session | unverified. Does not touch the Store;
    on_typed(sid, when) is called right after typing so the caller can record it.

    Delivered means the session's transcript has a user entry starting with the
    answer prefix, newer than the typing moment. Screen text is never proof: the
    agent's own reply may quote the expected line.

    `prefix` replaces the answer prefix; a dictated line passes say_prefix()."""
    tmux = tmux or TmuxOps()
    s = pick_target(q.from_, tmux.sessions(), conf_sid(q.from_, conf_path))
    if s is None:
        return "no-session"
    prefix = prefix or answer_prefix(q, cfg)
    typed = _aware(getattr(q, "typed_at", ""))
    since = typed or _aware(q.answered_at)
    # /clear gives the window a new sid: look in the one typed into and the current one
    sids = [x for x in dict.fromkeys((getattr(q, "typed_sid", ""), s.get("sid"))) if x]

    def landed(after):
        return after is not None and any(tmux.user_entry_since(x, prefix, after - TS_SLACK)
                                         for x in sids)

    if landed(since):
        return "delivered"
    now = clock()
    if typed and now.astimezone(timezone.utc) - typed < RETYPE_AFTER:
        return "unverified"          # typed recently: wait for the transcript, do not retype
    if s.get("status") not in IDLE_STATES or not tmux.input_empty(s["target"]):
        return "busy"
    typed = now.astimezone(timezone.utc)
    tmux.type(s["target"], format_answer(q, q.answer, cfg, prefix))
    if on_typed:
        on_typed(s.get("sid") or "", now)
    sids = [s.get("sid")] if s.get("sid") else []
    waited = 0.0
    while True:
        if landed(typed):
            return "delivered"
        if waited >= VERIFY_TIMEOUT:
            return "unverified"
        sleep(VERIFY_DELAY)
        waited += VERIFY_DELAY
