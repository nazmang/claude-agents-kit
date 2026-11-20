"""ntfy notifications: who gets pushed, when, and how.

Push rules: only blocking questions or kind=decision. Quiet hours: blocking goes
out silent (priority 2), anything else waits. Owner away: only blocking is
pushed. The push never carries the question body or the page token.
"""
import json
import os
import sys
import urllib.request
from datetime import datetime

PRESENCE_MAX_AGE_SEC = 15 * 60


def presence(cfg, now: datetime) -> str:
    """'away' when a fresh presence file contains cfg.away_marker, else 'home'."""
    path = cfg.presence_file
    if not path:
        return "home"
    try:
        mtime = datetime.fromtimestamp(os.stat(path).st_mtime)
        if (now - mtime).total_seconds() > PRESENCE_MAX_AGE_SEC:
            return "home"
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return "home"
    return "away" if cfg.away_marker and cfg.away_marker in text else "home"


def _is_quiet(now: datetime, quiet: tuple) -> bool:
    start, end = quiet
    if start == end:
        return False
    h = now.hour
    if start < end:
        return start <= h < end
    return h >= start or h < end


def decide(q, presence: str, now: datetime, quiet: tuple):
    """None (do not push now), "push" or "silent"."""
    if q.kind != "decision" and not q.blocking:
        return None
    if _is_quiet(now, quiet):
        return "silent" if q.blocking else None
    if presence == "away":
        return "push" if q.blocking else None
    return "push"


def build_message(q, cfg, mode: str = "push") -> dict:
    msg = {
        "topic": cfg.ntfy_topic,
        "title": f"{q.from_} · {q.kind}",
        "message": q.title,
        "priority": 2 if mode == "silent" else 4,
        "click": cfg.base_url,
    }
    if q.kind == "decision" and 1 <= len(q.options) <= 3:
        msg["actions"] = [
            {"action": "http", "label": opt, "url": f"{cfg.base_url}/a/{q.otok}/{i}",
             "method": "POST", "clear": True}
            for i, opt in enumerate(q.options)
        ]
    return msg


def _read_token(path: str) -> str:
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def send(msg: dict, cfg, opener=urllib.request.urlopen) -> bool:
    """POST msg as JSON to the ntfy server root. Never raises."""
    if not cfg.ntfy_url or not cfg.ntfy_topic:
        return False
    headers = {"Content-Type": "application/json"}
    token = _read_token(cfg.ntfy_token_file)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(cfg.ntfy_url.rstrip("/"),
                                     data=json.dumps(msg).encode("utf-8"),
                                     headers=headers, method="POST")
        with opener(req, timeout=5) as r:
            status = getattr(r, "status", 200)
            return 200 <= status < 300
    except Exception as e:  # network, HTTP, anything: log one line and move on
        print(f"inbox: ntfy send failed: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def tick(store, cfg, now: datetime, send=send, presence_fn=presence) -> int:
    """One pass over unnotified questions. Returns how many were sent.
    A question is marked notified only after a successful send."""
    if not cfg.ntfy_topic:
        return 0
    pres = presence_fn(cfg, now)
    sent = 0
    for q in store.unnotified():
        mode = decide(q, pres, now, cfg.quiet_hours)
        if mode is None:
            continue
        if not send(build_message(q, cfg, mode), cfg):
            break  # server likely down: retry everything next tick
        store.mark_notified(q.id, now)
        sent += 1
    return sent
