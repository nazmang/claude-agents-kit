"""Format an owner's answer and type it into the asking agent's tmux window.

deliver_one only does tmux work and returns a status string; the caller
(the server loop) records attempts in the Store.
"""
import os
import re
import time

from . import tmuxlib

IDLE_STATES = ("idle", "shell")
MAX_INLINE = 2000
VERIFY_DELAY = 1.0

STRINGS = {
    "ru": {"long_answer": "ответ в файле {path}"},
    "en": {"long_answer": "answer in file {path}"},
}


def pick_lang():
    forced = os.environ.get("AGENTS_LANG", "").strip().lower()
    if forced in ("ru", "en"):
        return forced
    for k in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(k, "").strip()
        if v:
            return "ru" if v.lower().startswith("ru") else "en"
    return "en"


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
        return tmuxlib.tmux("capture-pane", "-p", "-t", target)


_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_BAD_INLINE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_BAD_FILE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")   # keeps \t and \n


def format_answer(q, text, cfg) -> str:
    prefix = f"{cfg.owner_name} ({cfg.inbox_label}, {q.id}): "
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


def pick_target(name, sessions, conf_sid):
    cands = sorted((s for s in sessions if s.get("name") == name and s.get("target")),
                   key=lambda s: s["target"])
    for s in cands:
        if conf_sid and s.get("sid") == conf_sid:
            return s
    return cands[0] if cands else None


def conf_path_default() -> str:
    return os.environ.get("AGENTS_SESSIONS_CONF") or os.path.expanduser("~/.config/agents/sessions.conf")


def conf_sid(name, path=None):
    """Session id of `name` in sessions.conf (project | name | dir | model | session id ...)."""
    try:
        with open(path or conf_path_default(), encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                p = [x.strip() for x in line.split("|")]
                if len(p) >= 5 and p[1] == name:
                    return p[4] or None
    except OSError:
        pass
    return None


def deliver_one(q, cfg, tmux=None, conf_path=None, sleep=time.sleep) -> str:
    """Returns delivered | busy | no-session | unverified. Does not touch the Store."""
    tmux = tmux or TmuxOps()
    s = pick_target(q.from_, tmux.sessions(), conf_sid(q.from_, conf_path))
    if s is None:
        return "no-session"
    if s.get("status") not in IDLE_STATES or not tmux.input_empty(s["target"]):
        return "busy"
    tmux.type(s["target"], format_answer(q, q.answer, cfg))
    sleep(VERIFY_DELAY)
    return "delivered" if f", {q.id}): " in tmux.capture(s["target"]) else "unverified"
