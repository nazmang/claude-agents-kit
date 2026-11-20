"""Inbox configuration: `key = value` lines, `#` comments, unknown keys ignored."""
import os
from dataclasses import dataclass, field


@dataclass
class Config:
    owner_name: str = "owner"
    inbox_label: str = "inbox"
    listen: list = field(default_factory=lambda: ["127.0.0.1"])
    port: int = 8787
    base_url: str = "http://127.0.0.1:8787"
    ntfy_url: str = ""
    ntfy_topic: str = ""
    ntfy_token_file: str = ""
    presence_file: str = ""
    away_marker: str = "не дома"
    quiet_hours: tuple = (23, 8)
    data_dir: str = "~/.local/share/agents/inbox"
    token_file: str = "~/.config/agents/inbox.token"


def default_path() -> str:
    return os.environ.get("AGENTS_INBOX_CONF") or os.path.expanduser("~/.config/agents/inbox.conf")


def _parse_hours(v: str) -> tuple:
    a, b = v.replace(":00", "").split("-", 1)
    return (int(a), int(b))


def load(path: str | None = None) -> Config:
    """Read the config file at `path` (default: env AGENTS_INBOX_CONF or
    ~/.config/agents/inbox.conf). A missing file gives defaults."""
    cfg = Config()
    path = path or default_path()
    values = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                if " #" in val:
                    val = val.split(" #", 1)[0]
                values[key.strip()] = val.strip()
    except FileNotFoundError:
        pass
    for key, val in values.items():
        if key in ("owner_name", "inbox_label", "base_url", "ntfy_url", "ntfy_topic",
                   "away_marker"):
            setattr(cfg, key, val)
        elif key == "port":
            cfg.port = int(val)
        elif key == "listen":
            cfg.listen = [p.strip() for p in val.split(",") if p.strip()]
        elif key == "quiet_hours":
            cfg.quiet_hours = _parse_hours(val)
        elif key in ("ntfy_token_file", "presence_file", "data_dir", "token_file"):
            setattr(cfg, key, val)
    for key in ("data_dir", "token_file", "ntfy_token_file", "presence_file"):
        v = getattr(cfg, key)
        if v:
            setattr(cfg, key, os.path.expandvars(os.path.expanduser(v)))
    return cfg
