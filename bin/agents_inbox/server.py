"""Inbox HTTP server: the owner's page, the answer API and the background loop.

Listens only on the addresses from `listen` in the config, never on a wildcard.
The page is protected by a token cookie; one-shot ntfy buttons use the
per-question `otok` instead; /api/created is accepted only from loopback.
"""
import argparse
import hmac
import ipaddress
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
import traceback
from dataclasses import asdict
from datetime import datetime, timedelta
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import config as config_mod
from . import deliver, notify
from .i18n import pick_lang, strings_for
from .store import AlreadyClosed, Store

DELIVER_EVERY = 30
NOTIFY_EVERY = 60
STUCK_AFTER = timedelta(minutes=15)
CLOSED_DAYS = 7
MAX_BODY = 64 * 1024
LOCAL = ("127.0.0.1", "::1")
COOKIE_MAX_AGE = 365 * 24 * 3600
_QUERY = re.compile(r"\?[^\s\"']*")
_OTOK = re.compile(r"/a/[^/\s\"']+")
PAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html")

STRINGS = {
    "ru": {
        "title": "Входящие агентов", "urgent": "Срочно", "decisions": "Решения",
        "commands": "Команды", "talk": "Обсудить", "closed": "Закрыто за 7 дней",
        "empty": "Вопросов нет", "own": "Свой ответ", "send": "Отправить",
        "copy": "Скопировать", "copied": "Скопировано", "details": "Подробнее",
        "waiting": "ждёт доставки", "stuck": "застряло > 15 мин", "delivered": "доставлено",
        "withdrawn": "отозван", "answered": "ответ", "error": "Ошибка", "closed_now": "Уже закрыт",
        "offline": "Нет связи с сервером", "ago_m": "{n} мин", "ago_h": "{n} ч", "ago_d": "{n} д",
        "accepted": "Принято", "accepted_text": "Ответ записан. Окно можно закрыть.",
        "replay": "Уже отвечено", "unauth": "Нужна ссылка с токеном.",
    },
    "en": {
        "title": "Agents inbox", "urgent": "Urgent", "decisions": "Decisions",
        "commands": "Commands", "talk": "Discuss", "closed": "Closed in the last 7 days",
        "empty": "No questions", "own": "Own answer", "send": "Send",
        "copy": "Copy", "copied": "Copied", "details": "Details",
        "waiting": "waiting for delivery", "stuck": "stuck > 15 min", "delivered": "delivered",
        "withdrawn": "withdrawn", "answered": "answer", "error": "Error", "closed_now": "Already closed",
        "offline": "Server unreachable", "ago_m": "{n} min", "ago_h": "{n} h", "ago_d": "{n} d",
        "accepted": "Accepted", "accepted_text": "The answer is recorded. You can close this page.",
        "replay": "Already answered", "unauth": "Open the link with the token.",
    },
    "uk": {
        "title": "Вхідні агентів", "urgent": "Терміново", "decisions": "Рішення",
        "commands": "Команди", "talk": "Обговорити", "closed": "Закрито за 7 днів",
        "empty": "Питань немає", "own": "Своя відповідь", "send": "Надіслати",
        "copy": "Скопіювати", "copied": "Скопійовано", "details": "Докладніше",
        "waiting": "чекає на доставку", "stuck": "застрягло > 15 хв", "delivered": "доставлено",
        "withdrawn": "відкликано", "answered": "відповідь", "error": "Помилка", "closed_now": "Уже закрито",
        "offline": "Немає зв'язку з сервером", "ago_m": "{n} хв", "ago_h": "{n} год", "ago_d": "{n} дн",
        "accepted": "Прийнято", "accepted_text": "Відповідь записано. Вікно можна закрити.",
        "replay": "Уже відповіли", "unauth": "Потрібне посилання з токеном.",
    },
}


def strings():
    return strings_for(STRINGS)


def _log_stderr(line):
    print(line, file=sys.stderr, flush=True)


def page_token(path):
    """Read the page token; create it (dir 0700, file 0600) when missing or empty."""
    try:
        with open(path, encoding="utf-8") as f:
            tok = f.read().strip()
        if tok:
            return tok
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(path) or ".", mode=0o700, exist_ok=True)
    tok = secrets.token_urlsafe(24)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(tok + "\n")
    os.chmod(path, 0o600)
    return tok


class App:
    """Shared state: store, delivery and notify passes, background kicks."""

    def __init__(self, cfg, store, *, tmux=None, send=notify.send, presence_fn=notify.presence,
                 clock=datetime.now, conf_path=None, sleep=time.sleep, log=_log_stderr):
        self.cfg, self.store, self.tmux = cfg, store, tmux
        self.send, self.presence_fn, self.clock = send, presence_fn, clock
        self.conf_path, self.sleep, self.log = conf_path, sleep, log
        self._deliver_lock = threading.Lock()
        self._notify_lock = threading.Lock()
        self._kicks = []
        self._kicks_lock = threading.Lock()

    def _guard(self, what, fn, *args):
        try:
            return fn(*args)
        except Exception as e:
            self.log(f"inbox: {what} failed: {type(e).__name__}: {e}")
            self.log(traceback.format_exc().rstrip())
            return None

    def deliver_pending(self):
        with self._deliver_lock:
            for q in self._guard("pending", self.store.pending_delivery) or []:
                self._guard(f"deliver {q.id}", self._deliver, q)

    def _deliver(self, q):
        self.store.mark_attempt(q.id)
        r = deliver.deliver_one(q, self.cfg, tmux=self.tmux, conf_path=self.conf_path,
                                sleep=self.sleep, clock=self.clock,
                                on_typed=lambda sid, when: self.store.mark_typed(q.id, sid, when))
        if r == "delivered":
            self.store.mark_delivered(q.id)
        return r

    def notify_tick(self):
        with self._notify_lock:
            self._guard("notify", notify.tick, self.store, self.cfg, self.clock(),
                        self.send, self.presence_fn)

    def kick(self, fn):
        """Run fn in a background thread so the HTTP response is not delayed."""
        th = threading.Thread(target=fn, daemon=True)
        with self._kicks_lock:
            self._kicks = [k for k in self._kicks if k.is_alive()] + [th]
        th.start()

    def join_kicks(self, timeout=10):
        with self._kicks_lock:
            ks = list(self._kicks)
        for k in ks:
            k.join(timeout)

    def run_loop(self, stop):
        n = 0
        while not stop.is_set():
            self.deliver_pending()
            if n % (NOTIFY_EVERY // DELIVER_EVERY) == 0:
                self.notify_tick()
            n += 1
            stop.wait(DELIVER_EVERY)

    def visible(self):
        """Open and answered questions, plus closed ones from the last 7 days."""
        now = self.clock()
        cutoff = now - timedelta(days=CLOSED_DAYS)
        out = []
        for q in self.store.list():
            if q.status not in ("open", "answered"):
                last = max(q.created, q.answered_at, q.delivered_at)
                if datetime.fromisoformat(last) < cutoff:
                    continue
            out.append(public(q, now))
        return out


def public(q, now):
    d = asdict(q)
    d.pop("otok", None)
    d["stuck"] = bool(q.status == "answered" and q.first_attempt
                      and now - datetime.fromisoformat(q.first_attempt) > STUCK_AFTER)
    d["age_sec"] = max(0, int((now - datetime.fromisoformat(q.created)).total_seconds()))
    return d


class Handler(BaseHTTPRequestHandler):
    server_version = "inbox"
    sys_version = ""
    app = None      # set per server
    token = ""

    # --- plumbing ---------------------------------------------------------
    def log_message(self, fmt, *args):
        # never log a query string (page token) or a one-shot answer token
        line = _OTOK.sub("/a/…", _QUERY.sub("?…", fmt % args))
        self.app.log(f"inbox: {self.client_address[0]} {line}")

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", headers=()):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in headers:
            self.send_header(k, v)
        if code != 204:
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if code != 204 and self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def _authed(self):
        c = cookies.SimpleCookie()
        try:
            c.load(self.headers.get("Cookie", ""))
        except cookies.CookieError:
            return False
        m = c.get("inbox")
        return bool(m) and hmac.compare_digest(m.value.encode(), self.token.encode())

    def _body(self):
        """Parsed JSON body, or None after an error response has been sent."""
        if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
            self._send(415, "json required")
            return None
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            self.close_connection = True
            self._send(413, "too large")
            return None
        try:
            data = json.loads(self.rfile.read(n) or b"null")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            self._send(400, "bad json")
            return None
        return data

    # --- routes -----------------------------------------------------------
    def do_GET(self):
        u = urlsplit(self.path)
        if u.path == "/":
            t = parse_qs(u.query).get("t")
            if t:
                if hmac.compare_digest(t[0].encode(), self.token.encode()):
                    ck = (f"inbox={self.token}; HttpOnly; SameSite=Lax; Path=/; "
                          f"Max-Age={COOKIE_MAX_AGE}")
                    return self._send(302, "", headers=(("Location", "/"), ("Set-Cookie", ck)))
                return self._send(401, strings()["unauth"])
            if not self._authed():
                return self._send(401, strings()["unauth"])
            return self._page()
        if u.path == "/api/questions":
            if not self._authed():
                return self._send(401, "unauthorized")
            return self._json(200, self.app.visible())
        self._send(404, "not found")

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == "/api/answer":
            return self._answer()
        if path == "/api/created":
            if self.client_address[0] not in LOCAL and self.client_address[0] not in self.local_addrs:
                return self._send(403, "forbidden")
            self.app.kick(self.app.notify_tick)
            return self._send(204)
        parts = path.split("/")
        if len(parts) == 4 and parts[0] == "" and parts[1] == "a":
            return self._otok(parts[2], parts[3])
        self._send(404, "not found")

    def _answer(self):
        if not self._authed():
            return self._send(401, "unauthorized")
        data = self._body()
        if data is None:
            return
        qid, text = data.get("id"), data.get("text")
        if not isinstance(qid, str) or not isinstance(text, str) or not text.strip():
            return self._send(400, "id and text required")
        try:
            q = self.app.store.answer(qid, text)
        except KeyError:
            return self._send(404, "not found")
        except AlreadyClosed:
            return self._send(409, "already closed")
        self.app.kick(self.app.deliver_pending)
        self._json(200, public(q, self.app.clock()))

    def _otok(self, otok, idx):
        s = strings()
        try:
            i = int(idx)
        except ValueError:
            return self._send(400, "bad option")
        try:
            self.app.store.answer_by_otok(otok, i)
        except KeyError:
            return self._send(404, "not found")
        except AlreadyClosed:
            return self._send(409, _mini(s["replay"], ""), "text/html; charset=utf-8")
        except IndexError:
            return self._send(400, "bad option")
        self.app.kick(self.app.deliver_pending)
        self._send(200, _mini(s["accepted"], s["accepted_text"]), "text/html; charset=utf-8")

    def _page(self):
        lang = pick_lang(STRINGS)
        s = strings_for(STRINGS, lang)
        with open(PAGE_FILE, encoding="utf-8") as f:
            html = f.read()
        nonce = secrets.token_urlsafe(12)
        data = json.dumps(s, ensure_ascii=False).replace("</", "<\\/")
        html = (html.replace("/*STRINGS*/null", data).replace("{{lang}}", lang)
                .replace("{{nonce}}", nonce).replace("{{title}}", s["title"]))
        csp = (f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
               "connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'self'; "
               "frame-ancestors 'none'")
        self._send(200, html, "text/html; charset=utf-8",
                   headers=(("Content-Security-Policy", csp),))


def _mini(title, text):
    return ("<!doctype html><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{title}</title><body style='font:18px system-ui;padding:16px'>"
            f"<h1>{title}</h1><p>{text}</p>")


class Running:
    """Started servers plus their threads; close() stops everything."""

    def __init__(self, servers, threads):
        self.servers, self.threads = servers, threads
        self.ports = [s.server_address[1] for s in servers]

    def close(self):
        for s in self.servers:
            s.shutdown()
            s.server_close()
        for th in self.threads:
            th.join(5)


def _is_wildcard(addr):
    host = addr.strip().strip("[]").strip()
    if host in ("", "*"):
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return False   # not resolvable: the bind will fail on its own
    for info in infos:
        try:
            if ipaddress.ip_address(info[4][0].split("%", 1)[0]).is_unspecified:
                return True
        except ValueError:
            continue
    return False


def _check_listen(listen):
    if not listen:
        raise SystemExit("inbox: listen is empty in the config; set e.g. listen = 127.0.0.1")
    for a in listen:
        if _is_wildcard(a):
            raise SystemExit(f"inbox: refusing to listen on {a!r}: name explicit addresses")


def serve(cfg, app):
    """Bind one ThreadingHTTPServer per address in cfg.listen and start them."""
    _check_listen(cfg.listen)
    token = page_token(cfg.token_file)
    servers, threads = [], []
    local_addrs = frozenset(a.strip().strip("[]") for a in cfg.listen)
    try:
        for addr in cfg.listen:
            host = addr.strip().strip("[]")
            cls = type("InboxServer", (ThreadingHTTPServer,), {
                "address_family": socket.AF_INET6 if ":" in host else socket.AF_INET,
                "daemon_threads": True})
            h = type("InboxHandler", (Handler,), {"app": app, "token": token,
                                                  "local_addrs": local_addrs})
            servers.append(cls((host, cfg.port), h))
    except OSError:
        for s in servers:
            s.server_close()
        raise
    for s in servers:
        th = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        th.start()
        threads.append(th)
    return Running(servers, threads)


def main(argv=None):
    p = argparse.ArgumentParser(prog="agents-inbox")
    p.add_argument("--conf", help="config file (default: AGENTS_INBOX_CONF or ~/.config/agents/inbox.conf)")
    args = p.parse_args(argv)
    cfg = config_mod.load(args.conf)
    os.makedirs(os.path.expanduser(cfg.data_dir), mode=0o700, exist_ok=True)
    app = App(cfg, Store(config_mod.db_path(cfg)))
    running = serve(cfg, app)
    for addr, port in zip(cfg.listen, running.ports):
        _log_stderr(f"inbox: listening on {addr}:{port} (page token in {cfg.token_file})")
    stop = threading.Event()
    loop = threading.Thread(target=app.run_loop, args=(stop,), daemon=True)
    loop.start()
    try:
        import signal
        signal.signal(signal.SIGTERM, lambda *a: stop.set())
        while not stop.is_set():
            stop.wait(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        running.close()
        loop.join(5)
    return 0
