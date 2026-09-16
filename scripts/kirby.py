#!/usr/bin/env python3
"""kirby: send bulk file reading and boilerplate generation to a cheap worker model instead of your main model.

Default backend ("claude"): Claude Haiku through `claude -p --model haiku`. Works with a Claude Code subscription
alone; no API key, usage billed to your existing plan.
Optional backend ("openai"): any OpenAI-compatible chat-completions endpoint with an API key, e.g. GPT-5.6 Luna.

Subcommands
  hook-read     PreToolUse hook for Read.  Blocks any Read that would put more than the threshold into context,
                whole-file or a large offset/limit window (stdin: hook JSON).
  hook-bash     PreToolUse hook for Bash and PowerShell.  Blocks cat/type/less/more, head/tail with a large -n,
                sed -n ranges and sed without -n. Follows cd inside the command, expands globs, sums several
                files, and maps Git Bash /c/ paths on Windows. Output sent to a pipe or a file is not counted.
  hook-session  SessionStart hook.  Puts a short standing rule into context so the model reaches for kirby
                before it gets blocked.
  mcp           Stdio MCP server exposing kirby_read and kirby_write as tools. Standard library only.
  read          --question Q --paths P [P ...]           Ask the worker about files; prints bullets.
  write         --spec S --reference R [R ...] [--target T] [--context C ...] [--force]
                Generate a file matching the reference's patterns; writes to T or stdout.

Environment (all optional)
  KIRBY_MIN_LINES        line threshold above which reads are redirected (default 200)
  KIRBY_BACKEND          "claude" (default) or "openai"
  KIRBY_DISABLE=1        hooks allow everything and the session rule is not emitted (escape hatch)
  -- claude backend --
  KIRBY_MODEL            worker model passed to `claude --model` (default haiku)
  KIRBY_CLAUDE_BIN       path to the claude executable (default: CLAUDE_CODE_EXECPATH, then `claude` on PATH)
  -- openai backend --
  KIRBY_OPENAI_MODEL     model name (default gpt-5.6-luna)
  KIRBY_OPENAI_EFFORT    reasoning_effort (default low)
  KIRBY_OPENAI_BASE_URL  endpoint (default https://api.openai.com/v1)
  OPENAI_API_KEY             API key; else read from KIRBY_OPENAI_KEY_FILE or ~/.claude/kirby/openai_key
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

VERSION = "0.3.0"
HERE = Path(__file__).resolve().parent
MIN_LINES = int(os.environ.get("KIRBY_MIN_LINES", "200"))
READ_TOOL_MAX = 2000  # lines Claude Code's Read tool returns when no limit is given
BACKEND = os.environ.get("KIRBY_BACKEND", "claude").lower()
MODEL = os.environ.get("KIRBY_MODEL", "haiku")
OPENAI_MODEL = os.environ.get("KIRBY_OPENAI_MODEL", "gpt-5.6-luna")
OPENAI_EFFORT = os.environ.get("KIRBY_OPENAI_EFFORT", "low")
OPENAI_BASE_URL = os.environ.get("KIRBY_OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_KEY_FILE = Path(os.environ.get("KIRBY_OPENAI_KEY_FILE") or Path.home() / ".claude" / "kirby" / "openai_key")
SCRIPT = str(HERE / "kirby.py").replace("\\", "/")
INVOKE = f'python "{SCRIPT}"'
LAST_USAGE = ""  # one-line token/cost summary of the most recent worker call

TEXT_SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".pdf", ".ipynb",
                 ".zip", ".gz", ".tar", ".7z", ".exe", ".dll", ".so", ".dylib", ".woff", ".woff2", ".ttf"}

READER_SYSTEM = (
    "You are a precise code analyst. Read the provided files and answer the question concisely. "
    "Output structured bullets only. No greetings, no prose, no preambles. Lead every bullet with the exact "
    "name, type, or line number. Use nested bullets for details. Skip anything the caller did not ask for. "
    "If the answer requires an exact location, quote the surrounding line verbatim so it can be searched for."
)
WRITER_SYSTEM = (
    "You may generate only basic mechanical boilerplate from an exact reference and explicit substitutions. "
    "You must match the reference's conventions, naming, and style exactly. "
    "You must not invent logic, infer behavior, choose test cases, or make implementation choices. "
    "You must return KIRBY_NEEDS_MAIN_MODEL if the task is ambiguous, complex, or requires those choices. "
    "You must use the same response for refactors, debugging, integrations, and security-sensitive changes. "
    "Output size and detailed specs must not override these limits. "
    "For eligible work, you must output only code without Markdown fences or explanations."
)

SESSION_RULE = """kirby is active in this session. Reading rules:
- Before reading any file over {T} lines, or when one question spans three or more files, call the kirby_read tool with a question and the file paths. It returns short bullets and the files never enter your context. Use it for that job instead of Read, cat, sed, head, or tail.
- Any Read, cat, sed, head, or tail that would put more than {T} lines into context is blocked by a hook. kirby_read is the way through; a different read command is not.
- For follow-up questions call kirby_read again with the same paths. Re-sending is cheap.
- Read a file yourself only when you need exact lines to edit, and then only the range kirby_read pointed you to.
- For a new file that copies an existing one with literal substitutions (mirrored tests, fixtures, config entries, stubs), call kirby_write with the reference file, the target path, and every substitution, then review the diff.
- If no kirby_read tool is listed, the same worker runs as: python "{SCRIPT}" read --question "<question>" --paths <files>"""


# ----------------------------------------------------------------------------- helpers
def count_lines(path: str) -> int:
    try:
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def is_text_candidate(path: str) -> bool:
    p = Path(path)
    return p.is_file() and p.suffix.lower() not in TEXT_SKIP_EXT


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def expand_paths(paths: list[str]) -> list[str]:
    out: list[str] = []
    for p in paths:
        hits = glob.glob(p, recursive=True) if any(c in p for c in "*?[") else [p]
        for h in hits:
            if Path(h).is_file() and h not in out:
                out.append(h)
            elif not Path(h).exists():
                sys.stderr.write(f"kirby: no such file: {h}\n")
    return out


def claude_bin() -> list[str]:
    cand = os.environ.get("KIRBY_CLAUDE_BIN") or os.environ.get("CLAUDE_CODE_EXECPATH") or shutil.which("claude")
    if not cand:
        sys.exit("kirby: cannot find the `claude` executable. Set KIRBY_CLAUDE_BIN.")
    if os.name == "nt" and cand.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", cand]
    return [cand]


def ask_worker(system: str, user: str) -> str:
    if BACKEND == "openai":
        return ask_openai(system, user)
    if BACKEND != "claude":
        sys.exit(f'kirby: unknown KIRBY_BACKEND "{BACKEND}" (use "claude" or "openai")')
    return ask_claude(system, user)


def load_openai_key() -> str:
    if os.environ.get("OPENAI_API_KEY"):
        return os.environ["OPENAI_API_KEY"]
    try:
        key = OPENAI_KEY_FILE.read_text(encoding="utf-8").strip()
        if key:
            return key
    except OSError:
        pass
    sys.exit(f"kirby: no OpenAI key. Set OPENAI_API_KEY, or put the key on one line in {OPENAI_KEY_FILE}")


def note_usage(line: str) -> None:
    global LAST_USAGE
    LAST_USAGE = line
    sys.stderr.write(line + "\n")


def ask_openai(system: str, user: str, max_tokens: int = 16384, retries: int = 3) -> str:
    """One chat-completions call to an OpenAI-compatible endpoint. Standard library only."""
    body = {
        "model": OPENAI_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_completion_tokens": max_tokens,
        "reasoning_effort": OPENAI_EFFORT,
    }
    req = urllib.request.Request(
        f"{OPENAI_BASE_URL}/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {load_openai_key()}", "Content-Type": "application/json"},
    )
    t0 = time.time()
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                data = json.load(r)
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            sys.exit(f"kirby: HTTP {e.code} from {OPENAI_BASE_URL}: {detail}")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            sys.exit(f"kirby: connection failed: {e}")
    u = data.get("usage", {})
    note_usage(
        f"kirby: backend=openai model={OPENAI_MODEL} effort={OPENAI_EFFORT} "
        f"in={u.get('prompt_tokens', '?')} out={u.get('completion_tokens', '?')} ({time.time() - t0:.1f}s)"
    )
    return data["choices"][0]["message"]["content"] or ""


def ask_claude(system: str, user: str) -> str:
    """Run one non-interactive Haiku turn through the Claude Code CLI. No tools, nothing persisted."""
    # --setting-sources "" and --strict-mcp-config matter: without them the nested CLI loads the user's skills,
    # plugins, MCP tool descriptions and CLAUDE.md, which can be >100k tokens per call and would erase the savings.
    cmd = claude_bin() + [
        "-p", "--model", MODEL, "--tools", "", "--max-turns", "1",
        "--setting-sources", "", "--strict-mcp-config",
        "--no-session-persistence", "--output-format", "json",
        "--system-prompt", system,
    ]
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE",)}  # allow nesting inside a session
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, input=user, capture_output=True, text=True, encoding="utf-8",
                              timeout=300, env=env)
    except subprocess.TimeoutExpired:
        sys.exit("kirby: worker timed out after 300s. Send fewer files or a narrower question.")
    if proc.returncode != 0 and not proc.stdout.strip():
        sys.exit(f"kirby: claude exited {proc.returncode}: {proc.stderr.strip()[:500]}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        sys.exit(f"kirby: unexpected output from claude: {proc.stdout[:500]}")
    if data.get("is_error"):
        sys.exit(f"kirby: worker error: {data.get('result', '')[:500]}")
    usage = data.get("usage", {})
    note_usage(
        f"kirby: backend=claude model={MODEL} in={usage.get('input_tokens', '?')} "
        f"cache_write={usage.get('cache_creation_input_tokens', 0)} cache_read={usage.get('cache_read_input_tokens', 0)} "
        f"out={usage.get('output_tokens', '?')} "
        f"cost=${data.get('total_cost_usd', 0):.4f} ({time.time() - t0:.1f}s)"
    )
    return data.get("result") or ""


def wrap_files(paths: list[str]) -> str:
    parts = []
    for p in paths:
        parts.append(f'<file path="{p}" lines="{count_lines(p)}">\n{read_text(p)}\n</file>')
    return "\n\n".join(parts)


def strip_fences(text: str) -> str:
    t = text.strip()
    m = re.match(r"^```[a-zA-Z0-9_+-]*\n(.*?)\n```\s*$", t, re.S)
    return (m.group(1) if m else t) + "\n"


def block(msg: str) -> None:
    sys.stderr.write(msg)
    sys.exit(2)


# ----------------------------------------------------------------------------- hooks
def delegate_hint(paths: list[str]) -> str:
    tool_paths = ", ".join(json.dumps(str(p)) for p in paths)
    cli_paths = " ".join('"' + str(p) + '"' for p in paths)
    return (f'  kirby_read tool: question="<what you need to know>", paths=[{tool_paths}]\n'
            f'  no kirby_read tool listed? run: {INVOKE} read --question "<what you need to know>" --paths {cli_paths}')


def block_message(desc: str, n: int, paths: list[str]) -> str:
    return (f"kirby: {desc} would put {n} lines (> {MIN_LINES}) into context. Delegate it instead:\n"
            f"{delegate_hint(paths)}\n"
            f"Need exact lines to edit? Ask kirby_read where they are, then read only that range "
            f"(under {MIN_LINES} lines).\n")


def hook_read() -> None:
    if os.environ.get("KIRBY_DISABLE"):
        return
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return
    ti = payload.get("tool_input", {}) or {}
    path = ti.get("file_path") or ""
    if not path or not is_text_candidate(path):
        return
    total = count_lines(path)
    offset, limit = ti.get("offset"), ti.get("limit")
    start = max(int(offset or 1), 1)
    want = min(int(limit) if limit else READ_TOOL_MAX, max(total - (start - 1), 0))
    if want <= MIN_LINES:
        return
    if offset is None and limit is None:
        desc = f"reading {path} whole ({total} lines)"
    else:
        desc = f"reading {path} lines {start}-{start + want - 1}"
    block(block_message(desc, want, [path]))


BASH_READERS = {"cat", "less", "more", "type", "head", "tail", "sed", "Get-Content", "gc"}
PREFIX_WORDS = {"do", "then", "else", "time", "sudo", "command", "builtin", "exec"}
SEGMENT_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\r?\n)\s*")
STDERR_REDIRECT = re.compile(r"\d>\s*&\s*\d|2>\s*\S+")  # 2>&1 and 2>/dev/null do not move stdout
SED_RANGE = re.compile(r"(?:^|;)\s*(\d+)(?:,(\d+|\$|\+\d+))?\s*p\b")


def resolve_path(cwd: str, arg: str, windows: bool | None = None) -> Path | None:
    """Turn a shell path argument into an absolute Path, or None when it depends on an unknown variable."""
    windows = (os.name == "nt") if windows is None else windows
    a = arg.strip()
    home = str(Path.home())
    if a.startswith("~"):
        a = home + a[1:]
    a = a.replace("${HOME}", home).replace("$HOME", home)
    if not a or "$" in a:
        return None
    if windows:
        m = re.match(r"^/(?:cygdrive/)?([A-Za-z])(?:/(.*))?$", a)  # Git Bash and Cygwin drive paths
        if m:
            a = f"{m.group(1).upper()}:/{m.group(2) or ''}"
    p = Path(a)
    return p if p.is_absolute() else Path(cwd) / p


def expand_arg(cwd: str, arg: str) -> list[str]:
    p = resolve_path(cwd, arg)
    if p is None:
        return []
    s = str(p)
    if any(c in s for c in "*?["):
        if "**" in s:
            return []  # could be slow inside a 10 s hook; let it through
        return [h for h in glob.glob(s) if Path(h).is_file()]
    return [s] if p.is_file() else []


def change_dir(cwd: str, args: list[str]) -> str:
    if not args or args[0] == "~":
        return str(Path.home())
    if args[0] == "-":
        return cwd
    p = resolve_path(cwd, args[0])
    return str(p) if p is not None and p.is_dir() else cwd


def parse_count(args: list[str]) -> tuple[int, bool] | None:
    """Line count asked for by head/tail/Get-Content flags, plus whether it was a tail +N (from the start)."""
    flags = ("-n", "--lines", "-totalcount", "-head", "-tail")
    for i, a in enumerate(args):
        low = a.lower()
        for f in flags:
            if low == f and i + 1 < len(args) and args[i + 1].lstrip("+-").isdigit():
                return int(args[i + 1].lstrip("+-")), args[i + 1].startswith("+")
            if low.startswith(f + "=") and a[len(f) + 1:].lstrip("+-").isdigit():
                return int(a[len(f) + 1:].lstrip("+-")), a[len(f) + 1:].startswith("+")
        if low.startswith("-n") and low[2:].lstrip("+-").isdigit():  # -n40
            return int(low[2:].lstrip("+-")), low[2:].startswith("+")
        if re.fullmatch(r"-\d+", a):  # head -40
            return int(a[1:]), False
    return None


def sed_lines(args: list[str], total: int) -> int:
    """Lines a `sed -n` script prints, from its numeric ranges. Regex ranges are unknown and count as 0."""
    n = 0
    for s in (a for a in args if not a.startswith("-")):
        for m in SED_RANGE.finditer(s):
            a, b = int(m.group(1)), m.group(2)
            end = a if b is None else total if b == "$" else a + int(b[1:]) if b.startswith("+") else int(b)
            end = min(end, total)
            if end >= a:
                n += end - a + 1
    return n


def lines_emitted(prog: str, args: list[str], cwd: str) -> tuple[int, list[str]]:
    """How many lines this reader command would print, and which files it reads."""
    files: list[str] = []
    for a in args:
        if a.startswith("-") or ">" in a or "<" in a:
            continue
        files += expand_arg(cwd, a)
    files = [f for f in files if is_text_candidate(f)]
    if not files:
        return 0, []
    totals = [count_lines(f) for f in files]
    if prog == "sed":
        if any(a.startswith("-i") or a == "--in-place" for a in args):
            return 0, files
        quiet = any(re.fullmatch(r"-[a-zA-Z]*n[a-zA-Z]*", a) or a in ("--quiet", "--silent") for a in args)
        if quiet:
            return sum(sed_lines(args, t) for t in totals), files
        return sum(totals), files
    count = parse_count(args)
    if prog in ("head", "tail"):
        if any(a.startswith("-c") or a == "--bytes" or a.startswith("--bytes=") for a in args):
            return 0, files
        n, from_start = count if count else (10, False)
        if prog == "tail" and from_start:
            return sum(max(t - n + 1, 0) for t in totals), files
        return sum(min(n, t) for t in totals), files
    if count:
        return sum(min(count[0], t) for t in totals), files
    return sum(totals), files


def hook_bash() -> None:
    if os.environ.get("KIRBY_DISABLE"):
        return
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return
    cmd = (payload.get("tool_input", {}) or {}).get("command") or ""
    if not cmd:
        return
    cwd = payload.get("cwd") or os.getcwd()
    posix = payload.get("tool_name") != "PowerShell"
    for segment in SEGMENT_SPLIT.split(cmd):
        seg = segment.strip()
        if not seg:
            continue
        try:
            toks = shlex.split(seg, posix=posix)
        except ValueError:
            continue
        if not posix:
            toks = [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" else t for t in toks]
        while toks and toks[0] in PREFIX_WORDS:
            toks = toks[1:]
        if not toks:
            continue
        prog = Path(toks[0]).name
        if prog in ("cd", "pushd"):
            cwd = change_dir(cwd, toks[1:])
            continue
        if prog not in BASH_READERS:
            continue
        visible = STDERR_REDIRECT.sub("", seg)
        if "|" in visible or ">" in visible:
            continue  # stdout goes to a pipe or a file, not into context
        n, files = lines_emitted(prog, toks[1:], cwd)
        if n > MIN_LINES:
            block(block_message(f"`{seg[:100]}`", n, files))


def hook_session() -> None:
    if os.environ.get("KIRBY_DISABLE"):
        return
    text = SESSION_RULE.format(T=MIN_LINES, SCRIPT=SCRIPT)
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))


# ----------------------------------------------------------------------------- worker commands
def do_read(question: str, paths: list[str]) -> str:
    files = expand_paths(paths)
    if not files:
        sys.exit("kirby: no readable files given")
    return ask_worker(READER_SYSTEM, f"<question>\n{question}\n</question>\n\n{wrap_files(files)}")


def do_write(spec: str, reference: list[str], context: list[str] | None = None,
             target: str | None = None, force: bool = False) -> str:
    refs = expand_paths(reference)
    if not refs:
        sys.exit("kirby: at least one existing --reference file is required")
    ctx = expand_paths(context) if context else []
    if target and Path(target).exists() and not force:
        sys.exit(f"kirby: {target} exists; pass --force to overwrite")
    user = (
        f"<spec>\n{spec}\n</spec>\n\n"
        f"<reference_files note=\"match these patterns exactly\">\n{wrap_files(refs)}\n</reference_files>\n"
        + (f"\n<context_files note=\"for reference only\">\n{wrap_files(ctx)}\n</context_files>\n" if ctx else "")
        + (f"\nOutput the complete contents of the file {target}." if target else "\nOutput the complete file.")
    )
    code = strip_fences(ask_worker(WRITER_SYSTEM, user))
    if code.strip().startswith("KIRBY_NEEDS_MAIN_MODEL"):
        sys.exit("kirby: worker returned the task to the main model; no file written")
    if target:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_text(code, encoding="utf-8", newline="\n")
        return f"kirby: wrote {target} ({code.count(chr(10))} lines)"
    return code


def cmd_read(a: argparse.Namespace) -> None:
    print(do_read(a.question, a.paths))


def cmd_write(a: argparse.Namespace) -> None:
    out = do_write(a.spec, a.reference, a.context, a.target, a.force)
    if a.target:
        print(out)
    else:
        sys.stdout.write(out)


# ----------------------------------------------------------------------------- MCP server
MCP_TOOLS = [
    {
        "name": "kirby_read",
        "description": (
            f"Read one or more files with a cheap worker model and get back a short bulleted answer. Use this "
            f"instead of Read or cat for any file over {MIN_LINES} lines, and for any question that spans several "
            f"files: what a module does, which functions touch X, where something is implemented, the call graph. "
            f"The files never enter your context, so asking again with the same paths is cheap. Ask for structure "
            f"and for quoted lines you can grep for. Not for debugging subtle logic; read that yourself."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "What you need to know. Be specific; ask for structure."},
                "paths": {"type": "array", "items": {"type": "string"},
                          "description": "Files or globs such as src/**/*.py, absolute or relative to the project root."},
            },
            "required": ["question", "paths"],
        },
    },
    {
        "name": "kirby_write",
        "description": (
            "Generate a new file that copies an existing reference file with explicit substitutions (mirrored tests, "
            "fixtures, config entries, stubs) and write it straight to disk, so the code never enters your context. "
            "Give the reference file(s), the target path, and a spec listing every substitution and literal value. "
            "The worker hands the task back if it would need to invent logic or make design choices. Review the "
            "diff afterwards."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {"type": "string", "description": "What to generate, with every substitution spelled out."},
                "reference": {"type": "array", "items": {"type": "string"},
                              "description": "Existing file(s) whose patterns to copy."},
                "target": {"type": "string", "description": "Output path. Omit to get the file back as text."},
                "context": {"type": "array", "items": {"type": "string"},
                            "description": "Files the output depends on, sent for reference only."},
                "force": {"type": "boolean", "description": "Overwrite the target if it exists."},
            },
            "required": ["spec", "reference"],
        },
    },
]


def mcp_call(name: str, args: dict) -> str:
    if name == "kirby_read":
        text = do_read(str(args.get("question", "")), list(args.get("paths") or []))
    elif name == "kirby_write":
        text = do_write(str(args.get("spec", "")), list(args.get("reference") or []),
                        list(args.get("context") or []), args.get("target") or None, bool(args.get("force")))
    else:
        sys.exit(f"kirby: unknown tool {name}")
    return text + (f"\n\n[{LAST_USAGE}]" if LAST_USAGE else "")


def mcp_dispatch(msg: dict) -> dict | None:
    """Answer one JSON-RPC message. Returns None for notifications."""
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": params.get("protocolVersion") or "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "kirby", "version": VERSION},
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": MCP_TOOLS}}
    if method == "tools/call":
        try:
            text, is_error = mcp_call(str(params.get("name")), params.get("arguments") or {}), False
        except SystemExit as e:
            text, is_error = (e.code if isinstance(e.code, str) else f"kirby: exit {e.code}"), True
        except Exception as e:  # noqa: BLE001 - the server must stay up
            text, is_error = f"kirby: {type(e).__name__}: {e}", True
        return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}],
                                                         "isError": is_error}}
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}


def cmd_mcp(_: argparse.Namespace) -> None:
    out = sys.stdout.buffer
    for raw in sys.stdin.buffer:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        resp = mcp_dispatch(msg)
        if resp is not None:
            out.write((json.dumps(resp) + "\n").encode("utf-8"))
            out.flush()


def main() -> None:
    ap = argparse.ArgumentParser(prog="kirby", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hook-read")
    sub.add_parser("hook-bash")
    sub.add_parser("hook-session")
    sub.add_parser("mcp")
    rd = sub.add_parser("read")
    rd.add_argument("--question", required=True)
    rd.add_argument("--paths", nargs="+", required=True, help="files or globs")
    wr = sub.add_parser("write")
    wr.add_argument("--spec", required=True)
    wr.add_argument("--reference", nargs="+", required=True, help="file(s) whose patterns to match")
    wr.add_argument("--context", nargs="*", help="extra files the generated code depends on")
    wr.add_argument("--target", help="write here instead of stdout")
    wr.add_argument("--force", action="store_true")
    a = ap.parse_args()
    {"hook-read": lambda _: hook_read(), "hook-bash": lambda _: hook_bash(), "hook-session": lambda _: hook_session(),
     "mcp": cmd_mcp, "read": cmd_read, "write": cmd_write}[a.cmd](a)


if __name__ == "__main__":
    main()
