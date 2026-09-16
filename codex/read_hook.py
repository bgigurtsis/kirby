#!/usr/bin/env python3
"""PreToolUse guard for recognizable large reads; never executes input.

Denies any Read or shell read that would put more than the threshold into context and points the model at
the native kirby_luna_bulk_reader agent. Follows cd inside a command, expands globs, sums several files,
parses head/tail counts and sed -n ranges, and maps Git Bash /c/ paths on Windows. Output that goes to a
file or to a filtering program is not counted. Luna sessions (the worker) skip the guard.

Environment: KIRBY_CODEX_MIN_LINES (default 200), KIRBY_CODEX_DISABLE=1. The SIDETRACK_CODEX_* names
still work.
"""

import glob
import json
import os
from pathlib import Path
import re
import sys

DEFAULT_THRESHOLD = 200
READ_TOOL_MAX = 2000  # lines a Read tool returns when no limit is given
TOKEN = re.compile(r"'[^']*'|\"(?:`.|\\\"|[^\"])*\"|&&|\|\||[\n|;()]|[^\s|;()]+")
READERS = {"cat", "type", "get-content", "gc", "less", "more", "bat", "head", "tail", "sed"}
PASS_THROUGH = {"cat", "type", "get-content", "gc", "less", "more", "bat", "tee"}  # after a pipe: still a dump
CHDIR = {"cd", "pushd", "chdir", "set-location", "sl"}
PREFIX = {"do", "then", "else", "time", "sudo", "command", "builtin", "exec"}
BOUND_FLAGS = {"-totalcount", "-head", "-tail", "-n", "--lines", "-first", "-last"}
SED_RANGE = re.compile(r"(?:^|;)\s*(\d+)(?:,(\d+|\$|\+\d+))?\s*p\b")
PY_READ = re.compile(r"(?:Path|open)\(\s*['\"]([^'\"]+)['\"]\s*\)\.(?:read_text|read_bytes|read)\(")


def env(name, legacy):
    return os.environ.get(name) if os.environ.get(name) is not None else os.environ.get(legacy)


def unquote(value):
    return value[1:-1] if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'" else value


def resolve(cwd, name):
    """Absolute path for a literal argument, or None for variables, empty, or unsafe values."""
    if not isinstance(name, str) or not name or "\0" in name or "$" in name:
        return None
    if os.name == "nt":
        drive = re.match(r"^/(?:cygdrive/)?([A-Za-z])(?:/(.*))?$", name)  # Git Bash and Cygwin drive paths
        if drive:
            name = f"{drive.group(1).upper()}:/{drive.group(2) or ''}"
    path = Path(name).expanduser()
    return path if path.is_absolute() else cwd / path


def line_count(path, cap):
    """Count lines, stopping once the count exceeds cap so huge files stay cheap."""
    try:
        with path.open("rb") as stream:
            count = 0
            for count, _ in enumerate(stream, 1):
                if count > cap:
                    break
            return count
    except OSError:
        return 0


def files_for(cwd, args):
    out = []
    for arg in args:
        path = resolve(cwd, arg)
        if path is None:
            continue
        text = str(path)
        if any(c in text for c in "*?["):
            if "**" not in text:  # a recursive glob could be slow inside a hook; let it through
                out.extend(Path(h) for h in glob.glob(text) if Path(h).is_file())
        elif path.is_file():
            out.append(path)
    return out


def limit_value(tokens, names):
    """(count, from_start) for -n N, -n +N, -N, -n=N or PowerShell -TotalCount N; None when absent."""
    for i, token in enumerate(tokens):
        low = token.lower()
        if low in names and i + 1 < len(tokens):
            raw = tokens[i + 1]
            try:
                return int(raw), raw.startswith("+")
            except ValueError:
                return None
        for name in names:
            if low.startswith(name + "=") and re.fullmatch(r"[+-]?\d+", token[len(name) + 1:]):
                return int(token[len(name) + 1:]), token[len(name) + 1:].startswith("+")
        m = re.fullmatch(r"-n?([+-]?\d+)", token)
        if m:
            return int(m.group(1)), m.group(1).startswith("+")
    return None


def sed_lines(args, total):
    """Lines a `sed -n` script prints, from its numeric ranges. Regex ranges are unknown and count as 0."""
    n = 0
    for script in (a for a in args if not a.startswith("-")):
        for m in SED_RANGE.finditer(script):
            start, stop = int(m.group(1)), m.group(2)
            end = start if stop is None else total if stop == "$" else start + int(stop[1:]) if stop.startswith("+") else int(stop)
            end = min(end, total)
            if end >= start:
                n += end - start + 1
    return n


def bounded(name, count, totals):
    """Lines printed by head/tail/Get-Content style readers for one (count, from_start) bound."""
    n, from_start = count
    out = 0
    for total in totals:
        if name == "tail" and from_start:
            out += max(total - n + 1, 0)
        elif n < 0:
            out += max(total + n, 0) if name == "head" else min(-n, total)
        else:
            out += min(n, total)
    return out


def reader_lines(name, before, after, cwd, threshold):
    """(lines this reader puts into context, files it reads) for one command segment."""
    args, skip = [], False
    for arg in before:
        if skip:
            skip = False
            continue
        low = arg.lower()
        # `-n` only takes a count for head and tail; for cat it numbers lines.
        if low in {"-encoding", "-totalcount", "-head", "-tail", "-first", "-last"} or (
                low in {"-n", "--lines"} and name in {"head", "tail"}):
            skip = True
        elif not arg.startswith("-") and arg not in {"(", ")", "&"} and not arg.startswith(">"):
            args.append(arg)
    files = files_for(cwd, args)
    if not files:
        return 0, []
    cap = max(threshold, READ_TOOL_MAX)
    totals = [line_count(f, cap) for f in files]
    if name == "sed":
        if any(a.startswith("-i") or a == "--in-place" for a in before):
            return 0, files
        quiet = any(re.fullmatch(r"-[a-zA-Z]*n[a-zA-Z]*", a) or a in ("--quiet", "--silent") for a in before)
        lines = sum(sed_lines(before, t) for t in totals) if quiet else sum(totals)
    elif name in {"head", "tail"}:
        if any(a.startswith("-c") or a.startswith("--bytes") for a in before):
            return 0, files
        lines = bounded(name, limit_value(before, BOUND_FLAGS) or (10, False), totals)
    else:
        count = limit_value(before, BOUND_FLAGS)
        lines = bounded(name, count, totals) if count else sum(totals)
    if after:
        reducer = after[0].lower()
        if reducer in PASS_THROUGH:
            pass  # cat | less: every line still lands in context
        elif reducer in {"head", "tail", "select-object"}:
            count = limit_value(after[1:], BOUND_FLAGS)
            lines = min(lines, bounded(reducer, count or (10, False), totals)) if reducer != "select-object" or count \
                else 0
        else:
            return 0, files  # grep, rg, wc, sort, jq ... consume the output
    return lines, files


def shell_reads(command, threshold, cwd, depth=0):
    """[(lines, [paths])] for every reader invocation found. Conservative literal recognition, not a shell."""
    if depth > 3:
        return []
    tokens = TOKEN.findall(command)
    found = []
    for i, token in enumerate(tokens[:-1]):  # pwsh -Command "..." and sh -c "..." wrappers
        if token.lower() in ("-command", "-c", "-lc"):
            inner = unquote(tokens[i + 1])
            if inner != tokens[i + 1]:
                found.extend(shell_reads(inner, threshold, cwd, depth + 1))
    segments, current = [], []
    for token in tokens:
        if token in (";", "&&", "||", "\n"):
            segments.append(current)
            current = []
        else:
            current.append(token)
    segments.append(current)
    for segment in segments:
        cleaned = [unquote(t) for t in segment]
        while cleaned and cleaned[0].lower() in PREFIX:
            cleaned = cleaned[1:]
        if not cleaned:
            continue
        head = cleaned[0].lower()
        if head in CHDIR:
            target = resolve(cwd, cleaned[1]) if len(cleaned) > 1 and cleaned[1] != "-" else Path.home()
            if target is not None and target.is_dir():
                cwd = target
            continue
        if any(re.fullmatch(r"1?>>?", t) or (t.startswith(">") and not t.startswith(">&")) for t in cleaned):
            continue  # stdout goes to a file
        if head not in READERS:
            continue
        rest = [t for t in cleaned[1:] if not re.match(r"^2>", t)]
        before = rest[:rest.index("|")] if "|" in rest else rest
        after = rest[rest.index("|") + 1:] if "|" in rest else []
        lines, files = reader_lines(head, before, after, cwd, threshold)
        if files:
            found.append((lines, [str(f) for f in files]))
    for literal in PY_READ.findall(command):  # literal Python full reads used instead of cat
        path = resolve(cwd, literal)
        if path is not None and path.is_file():
            found.append((line_count(path, max(threshold, READ_TOOL_MAX)), [str(path)]))
    return found


def read_tool_lines(data, cwd, threshold):
    path = resolve(cwd, data.get("file_path", data.get("path")))
    if path is None or not path.is_file():
        return 0, []
    total = line_count(path, max(threshold, READ_TOOL_MAX))
    start = max(int(data.get("offset") or data.get("start_line") or 1), 1)
    remaining = max(total - start + 1, 0)
    if data.get("limit") is not None:
        want = min(int(data["limit"]), remaining)
    elif data.get("end_line") is not None:
        want = max(min(int(data["end_line"]), total) - start + 1, 0)
    elif data.get("head") is not None:
        want = min(int(data["head"]), total)
    elif data.get("tail") is not None:
        want = min(int(data["tail"]), total)
    else:
        want = min(READ_TOOL_MAX, remaining)
    return want, [str(path)]


def deny(lines, paths, threshold):
    reason = (f"Kirby: this read would put {lines} lines (> {threshold}) into context: {json.dumps(paths)}. "
              "Delegate it instead: spawn the kirby_luna_bulk_reader agent with these paths and a focused question "
              "(what you need to know; ask for structure and for quoted lines you can search for). "
              f"Need exact lines to edit? Ask the reader where they are, then read only that range (under {threshold} "
              "lines). Do not fetch the same file through another command.")
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
            "permissionDecision": "deny", "permissionDecisionReason": reason}}


def decision(event, threshold=DEFAULT_THRESHOLD):
    if env("KIRBY_CODEX_DISABLE", "SIDETRACK_CODEX_DISABLE") == "1" or str(event.get("model", "")).startswith("gpt-5.6-luna"):
        return {}
    if event.get("hook_event_name") != "PreToolUse":
        return {}
    data = event.get("tool_input") or {}
    if not isinstance(data, dict):
        return {}
    name = event.get("tool_name", "")
    cwd = Path(data.get("workdir") or event.get("cwd") or os.getcwd())
    reads = []
    if name in ("Bash", "exec_command", "shell_command"):
        reads = shell_reads(data.get("command", data.get("cmd", "")), threshold, cwd)
    elif name in ("Read", "read_file") or name.endswith("__read_file"):
        reads = [read_tool_lines(data, cwd, threshold)]
    offending = [(lines, paths) for lines, paths in reads if lines > threshold]
    if not offending:
        return {}
    lines = max(item[0] for item in offending)
    paths = list(dict.fromkeys(p for _, ps in offending for p in ps))
    return deny(lines, paths, threshold)


def main():
    try:
        threshold = int(env("KIRBY_CODEX_MIN_LINES", "SIDETRACK_CODEX_MIN_LINES") or DEFAULT_THRESHOLD)
        if threshold < 1:
            threshold = DEFAULT_THRESHOLD
        result = decision(json.load(sys.stdin), threshold)
        print(json.dumps(result))
    except (ValueError, TypeError, AttributeError) as exc:
        # Bad input is visible to Codex; no claim of universal command enforcement.
        print(f"Kirby hook input error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
