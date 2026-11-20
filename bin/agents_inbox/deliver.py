"""Format an owner's answer and type it into the asking agent's tmux window.

deliver_one only does tmux work and returns a status string; the caller
(the server loop) records attempts in the Store.
"""
import glob
import json
import os
import re
import time

from . import tmuxlib
from .i18n import pick_lang

IDLE_STATES = ("idle", "shell")
MAX_INLINE = 2000
VERIFY_DELAY = 1.0
SCROLLBACK_LINES = 3000
TRANSCRIPT_TAIL = 1024 * 1024

STRINGS = {
    "ru": {"long_answer": "ответ в файле {path}"},
    "en": {"long_answer": "answer in file {path}"},
}


def t(key, **vars):
    return STRINGS[pick_lang()][key].format(**vars)


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

    def capture(self, target):
        return tmuxlib.tmux("capture-pane", "-p", "-J", "-S", f"-{SCROLLBACK_LINES}", "-t", target)

    def transcript_has(self, sid, marker, projects_dir=None):
        """True if `marker` is in the last TAIL_BYTES of the session's transcript."""
        if not sid:
            return False
        root = projects_dir or tmuxlib.PROJECTS_DIR
        for p in glob.glob(os.path.join(root, "*", f"{sid}.jsonl")):
            try:
                size = os.path.getsize(p)
                with open(p, "rb") as f:
                    f.seek(max(0, size - TRANSCRIPT_TAIL))
                    if marker in f.read().decode("utf-8", "replace"):
                        return True
            except OSError:
                continue
        return False


_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_BAD_INLINE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_BAD_FILE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")   # keeps \t and \n


def answer_prefix(q, cfg) -> str:
    return f"{cfg.owner_name} ({cfg.inbox_label}, {q.id}): "


def format_answer(q, text, cfg) -> str:
    prefix = answer_prefix(q, cfg)
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


def deliver_one(q, cfg, tmux=None, conf_path=None, sleep=time.sleep) -> str:
    """Returns delivered | busy | no-session | unverified. Does not touch the Store."""
    tmux = tmux or TmuxOps()
    s = pick_target(q.from_, tmux.sessions(), conf_sid(q.from_, conf_path))
    if s is None:
        return "no-session"
    if s.get("status") not in IDLE_STATES or not tmux.input_empty(s["target"]):
        return "busy"
    marker = answer_prefix(q, cfg)
    escaped = json.dumps(marker, ensure_ascii=False)[1:-1]   # as stored in a jsonl transcript
    escaped_ascii = json.dumps(marker)[1:-1]

    def landed():
        if marker in tmux.capture(s["target"]):
            return True
        return any(tmux.transcript_has(s.get("sid"), m) for m in dict.fromkeys((marker, escaped, escaped_ascii)))

    if landed():   # already landed earlier (maybe scrolled away): never type twice
        return "delivered"
    tmux.type(s["target"], format_answer(q, q.answer, cfg))
    sleep(VERIFY_DELAY)
    return "delivered" if landed() else "unverified"
