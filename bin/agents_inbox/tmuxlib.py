"""tmux and session discovery, shared by bin/agents and the inbox."""
import glob, json, os, re, subprocess
from datetime import datetime, timezone

SESSIONS_DIR = os.path.expanduser("~/.claude/sessions")
PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
TAIL_BYTES = 4 * 1024 * 1024

# a shell command that runs tests: the panel shows it as 🧪, not ⚙️
TEST_RE = re.compile(r"pytest|unittest|plugin test|npm (run )?test|go test|cargo test")


def tool_hint(name, inp):
    """Short hint about a tool call for the panel: "test" or ""."""
    if name in ("Bash", "PowerShell") and TEST_RE.search(str((inp or {}).get("command", ""))):
        return "test"
    return ""


def tmux(*args):
    r = subprocess.run(["tmux", *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def tmux_panes():
    """pane_pid -> ("=session:index", "session:name"); target by index, names may repeat."""
    out = {}
    for line in tmux("list-panes", "-a", "-F",
                     "#{pane_pid}\t#{session_name}\t#{window_index}\t#{window_name}").splitlines():
        pid, s, wi, wn = line.split("\t")
        out[int(pid)] = (f"={s}:{wi}", f"{s}:{wn}")
    return out


def ancestors(pid):
    seen = []
    for _ in range(10):
        seen.append(pid)
        try:
            with open(f"/proc/{pid}/stat") as f:
                pid = int(f.read().rsplit(")", 1)[1].split()[1])
        except Exception:
            break
        if pid <= 1:
            break
    return seen


def transcript_info(sid):
    """(time of last reply, context tokens, compacted since then, model, last tool, its hint)."""
    paths = glob.glob(os.path.join(PROJECTS_DIR, "*", f"{sid}.jsonl"))
    if not paths:
        return None, 0, False, "", "", ""
    p = paths[0]
    size = os.path.getsize(p)
    with open(p, "rb") as f:
        f.seek(max(0, size - TAIL_BYTES))
        lines = f.read().decode("utf-8", "replace").splitlines()
    last_ts, ctx, model, compacted_after = None, 0, "", False
    tool = hint = ""   # the newest assistant entry: its tool_use name, or "" for text/thinking
    for line in reversed(lines):
        if '"type":"assistant"' not in line:
            continue
        try:
            content = (json.loads(line).get("message") or {}).get("content") or []
        except Exception:
            continue
        uses = [c for c in content if isinstance(c, dict) and c.get("type") == "tool_use"]
        if uses:
            tool = uses[-1].get("name", "")
            hint = tool_hint(tool, uses[-1].get("input"))
        break
    for line in reversed(lines):
        if '"compact_boundary"' in line and last_ts is None and not compacted_after:
            compacted_after = True
            try:  # idle counts from the compaction, not from the reply before it
                bts = datetime.fromisoformat(json.loads(line)["timestamp"].replace("Z", "+00:00"))
            except Exception:
                bts = None
        if '"type":"assistant"' not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        msg = d.get("message") or {}
        u = msg.get("usage") or {}
        if not u:
            continue
        last_ts = datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00"))
        if compacted_after and bts:
            last_ts = bts
        ctx = sum(u.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens",
                                        "cache_creation_input_tokens"))
        model = msg.get("model", "")
        break
    return last_ts, ctx, compacted_after, model, tool, hint


def sessions():
    panes = tmux_panes()
    out = []
    for f in glob.glob(os.path.join(SESSIONS_DIR, "*.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        pid = d.get("pid")
        if not pid or not os.path.exists(f"/proc/{pid}") or d.get("kind") != "interactive":
            continue
        target = label = None
        for a in ancestors(pid):
            if a in panes:
                target, label = panes[a]
                break
        last, ctx, compacted, model, tool, hint = transcript_info(d.get("sessionId", ""))
        try:  # --model on the command line wins: right after a restart there are no replies yet
            argv = open(f"/proc/{pid}/cmdline", "rb").read().decode().split("\0")
            if "--model" in argv:
                model = argv[argv.index("--model") + 1]
        except Exception:
            pass
        idle = (datetime.now(timezone.utc) - last).total_seconds() / 60 if last else None
        out.append(dict(name=d.get("name") or f"(pid {pid})", pid=pid, sid=d.get("sessionId"),
                        cwd=d.get("cwd", ""), status=d.get("status", ""), target=target,
                        label=label, idle=idle, ctx=0 if compacted else ctx, model=model, tool=tool,
                        tool_hint=hint))
    return sorted(out, key=lambda s: (s["label"] or "~", s["name"]))


_SGR = re.compile(r"\x1b\[([0-9;]*)m")


def _is_dim(params):
    """True if an SGR parameter list sets the dim attribute (2). The 2 inside a
    colour (38;2;r;g;b, 38;5;2 and the 48/58 forms) is not dim."""
    p = params.split(";")
    i = 0
    while i < len(p):
        if p[i] in ("38", "48", "58"):
            i += 5 if p[i + 1:i + 2] == ["2"] else 3
            continue
        if p[i] == "2":
            return True
        i += 1
    return False


def prompt_empty(screen):
    """True when the last prompt line (❯) of a captured screen (with escapes) holds no draft.
    Dim text after the prompt is the placeholder hint, not a draft. The hint comes
    either as ESC[2m or, with the cursor on its first letter, as ESC[7m + ESC[0;2m."""
    lines = [l for l in screen.splitlines() if "❯" in l]
    if not lines:
        return False
    rest = lines[-1].split("❯", 1)[1].replace("\xa0", " ")
    if any(_is_dim(m.group(1)) for m in _SGR.finditer(rest)):
        return True
    return re.sub(r"\x1b\[[0-9;]*m|\s", "", rest) == ""


def input_empty(target):
    return prompt_empty(tmux("capture-pane", "-e", "-p", "-t", target))
