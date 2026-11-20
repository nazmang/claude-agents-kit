"""`agents ask | inbox`: put a question to the owner, list what is open, withdraw it."""
import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime

from . import config as config_mod
from . import deliver, tmuxlib
from .i18n import tr
from .store import KINDS, AlreadyClosed, Store

STRINGS = {
    "ru": {
        "no_from": "не удалось определить имя сессии (нет tmux-окна Claude среди предков); укажи --from ИМЯ",
        "bad_from": "--from {name}: такого имени нет в sessions.conf",
        "bad_kind": "--kind: допустимо {kinds}",
        "options_kind": "--options только для --kind decision",
        "command_kind": "--command только для --kind command",
        "need_title": "нужен заголовок вопроса",
        "withdrawn": "{id}: отозван",
        "not_yours": "{id}: вопрос чужой, отозвать нельзя",
        "unknown": "{id}: нет такого вопроса",
        "closed": "{id}: вопрос уже закрыт",
        "empty": "открытых вопросов нет",
        "h": ("id", "от кого", "kind", "висит", "заголовок"),
    },
    "en": {
        "no_from": "cannot tell which session this is (no Claude tmux window among the parents); pass --from NAME",
        "bad_from": "--from {name}: no such name in sessions.conf",
        "bad_kind": "--kind: one of {kinds}",
        "options_kind": "--options only works with --kind decision",
        "command_kind": "--command only works with --kind command",
        "need_title": "a question title is required",
        "withdrawn": "{id}: withdrawn",
        "not_yours": "{id}: not your question, cannot withdraw",
        "unknown": "{id}: no such question",
        "closed": "{id}: question is already closed",
        "empty": "no open questions",
        "h": ("id", "from", "kind", "age", "title"),
    },
    "uk": {
        "no_from": "не вдалося визначити ім'я сесії (серед батьківських процесів немає tmux-вікна Claude); вкажи --from ІМ'Я",
        "bad_from": "--from {name}: такого імені немає в sessions.conf",
        "bad_kind": "--kind: можна {kinds}",
        "options_kind": "--options лише для --kind decision",
        "command_kind": "--command лише для --kind command",
        "need_title": "потрібен заголовок питання",
        "withdrawn": "{id}: відкликано",
        "not_yours": "{id}: питання не твоє, відкликати не можна",
        "unknown": "{id}: такого питання немає",
        "closed": "{id}: питання вже закрите",
        "empty": "відкритих питань немає",
        "h": ("id", "від кого", "kind", "висить", "заголовок"),
    },
}


def t(key, **vars):
    return tr(STRINGS, key, **vars)


def detect_from(pid, sessions, conf_names, ancestors=tmuxlib.ancestors):
    """Name of the Claude session `pid` runs under, only if sessions.conf lists it."""
    by_pid = {s.get("pid"): s.get("name") for s in sessions}
    for a in ancestors(pid):
        if a in by_pid:
            name = by_pid[a]
            return name if name in conf_names else None
    return None


def fmt_age(seconds) -> str:
    s = max(0, int(seconds))
    if s >= 86400:
        return f"{s // 86400}d"
    if s >= 3600:
        return f"{s // 3600}h"
    return f"{s // 60}m"


def created_url(cfg):
    """Loopback when the server listens there (the host's own LAN address is not 'local' to it)."""
    if "127.0.0.1" in [a.strip() for a in cfg.listen]:
        return f"http://127.0.0.1:{cfg.port}"
    return cfg.base_url


def post_created(base_url):
    """Nudge the server so the push goes out now; it is fine if nobody listens."""
    try:
        req = urllib.request.Request(base_url.rstrip("/") + "/api/created", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=1).close()
    except Exception:
        pass


def _fail(code, msg):
    print(msg, file=sys.stderr)
    return code


def _parser():
    p = argparse.ArgumentParser(prog="agents ask", add_help=True)
    p.add_argument("title", nargs="?")
    p.add_argument("--kind", default="decision")
    p.add_argument("--blocking", action="store_true")
    p.add_argument("--options")
    p.add_argument("--command")
    p.add_argument("--body", default="")
    p.add_argument("--ref", default="")
    p.add_argument("--from", dest="from_")
    p.add_argument("--withdraw", metavar="ID")
    return p


def _resolve_from(args, sessions, pid, conf_path, ancestors):
    """(name, None) or (None, error exit code)."""
    names = deliver.conf_names(conf_path)
    if args.from_:
        if args.from_ not in names:
            return None, _fail(2, t("bad_from", name=args.from_))
        return args.from_, None
    if sessions is None:
        sessions = tmuxlib.sessions()
    name = detect_from(os.getpid() if pid is None else pid, sessions, names, ancestors=ancestors)
    if not name:
        return None, _fail(2, t("no_from"))
    return name, None


def main(argv, *, sessions=None, pid=None, conf_path=None, cfg=None, post=None,
         ancestors=tmuxlib.ancestors, now=None):
    cfg = cfg or config_mod.load()
    post = post or post_created
    conf_path = conf_path or deliver.conf_path_default()
    if argv and argv[0] == "inbox":
        return _inbox(argv[1:], cfg, now)
    if argv and argv[0] == "ask":
        argv = argv[1:]
    try:
        args = _parser().parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2

    if args.kind not in KINDS:
        return _fail(2, t("bad_kind", kinds=", ".join(KINDS)))
    if args.options is not None and args.kind != "decision":
        return _fail(2, t("options_kind"))
    if args.command is not None and args.kind != "command":
        return _fail(2, t("command_kind"))
    if not args.withdraw and not args.title:
        return _fail(2, t("need_title"))

    name, err = _resolve_from(args, sessions, pid, conf_path, ancestors)
    if err is not None:
        return err
    data_dir = os.path.expanduser(cfg.data_dir)
    os.makedirs(data_dir, mode=0o700, exist_ok=True)
    store = Store(config_mod.db_path(cfg))

    if args.withdraw:
        try:
            store.withdraw(args.withdraw, name)
        except KeyError:
            return _fail(1, t("unknown", id=args.withdraw))
        except PermissionError:
            return _fail(1, t("not_yours", id=args.withdraw))
        except AlreadyClosed:
            return _fail(1, t("closed", id=args.withdraw))
        print(t("withdrawn", id=args.withdraw))
        return 0

    options = [o.strip() for o in (args.options or "").split(",") if o.strip()]
    q = store.create(name, args.kind, args.title, blocking=args.blocking, body=args.body,
                     options=options, command=args.command or "", ref=args.ref)
    print(q.id)
    try:
        post(created_url(cfg))
    except Exception:
        pass
    return 0


def _inbox(argv, cfg, now):
    p = argparse.ArgumentParser(prog="agents inbox")
    p.add_argument("--all", action="store_true")
    try:
        args = p.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    path = config_mod.db_path(cfg)
    qs = Store(path).list() if os.path.exists(path) else []
    if not args.all:
        qs = [q for q in qs if q.status == "open"]
    if not qs:
        print(t("empty"))
        return 0
    now = now or datetime.now()
    rows = [t("h")]
    for q in qs:
        age = fmt_age((now - datetime.fromisoformat(q.created)).total_seconds())
        rows.append((q.id, q.from_, q.kind + ("!" if q.blocking else ""), age, q.title))
    w = [max(len(r[i]) for r in rows) for i in range(4)]
    for r in rows:
        print("  ".join(r[i].ljust(w[i]) for i in range(4)) + "  " + r[4])
    return 0
