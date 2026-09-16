import importlib.util
import json
from io import StringIO
from pathlib import Path

import pytest


def load_kirby(monkeypatch):
    """Load scripts/kirby.py fresh so module-level env reads pick up monkeypatched values."""
    kirby_path = Path(__file__).resolve().parent.parent / "scripts" / "kirby.py"
    spec = importlib.util.spec_from_file_location("kirby", str(kirby_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_payload(path, **extra):
    return StringIO(json.dumps({"tool_name": "Read", "tool_input": {"file_path": str(path), **extra}}))


def _bash_payload(command, cwd=None, tool="Bash"):
    payload = {"tool_name": tool, "tool_input": {"command": command}}
    if cwd is not None:
        payload["cwd"] = str(cwd)
    return StringIO(json.dumps(payload))


def blocks(fn):
    with pytest.raises(SystemExit) as exc_info:
        fn()
    assert exc_info.value.code == 2


@pytest.fixture
def big_file(tmp_path):
    f = tmp_path / "big.py"
    f.write_text("\n" * 20)
    return f


@pytest.fixture
def small(monkeypatch):
    """kirby loaded with a 10-line threshold."""
    monkeypatch.setenv("KIRBY_MIN_LINES", "10")
    return load_kirby(monkeypatch)


# ----------------------------------------------------------------------------- Read hook
def test_hook_read_blocks_large_files(small, monkeypatch, big_file):
    monkeypatch.setattr("sys.stdin", _read_payload(big_file))
    blocks(small.hook_read)


def test_hook_read_allows_small_window(small, monkeypatch, big_file):
    monkeypatch.setattr("sys.stdin", _read_payload(big_file, offset=1, limit=5))
    small.hook_read()  # must not raise


def test_hook_read_blocks_window_over_threshold(small, monkeypatch, big_file):
    monkeypatch.setattr("sys.stdin", _read_payload(big_file, offset=1, limit=15))
    blocks(small.hook_read)


def test_hook_read_offset_only_counts_remaining_lines(small, monkeypatch, big_file):
    monkeypatch.setattr("sys.stdin", _read_payload(big_file, offset=15))  # 6 lines remain
    small.hook_read()


def test_hook_read_allows_small_files(small, monkeypatch, tmp_path):
    f = tmp_path / "small.py"
    f.write_text("\n" * 5)
    monkeypatch.setattr("sys.stdin", _read_payload(f))
    small.hook_read()


def test_hook_read_respects_kirby_disable(monkeypatch, big_file):
    monkeypatch.setenv("KIRBY_MIN_LINES", "10")
    monkeypatch.setenv("KIRBY_DISABLE", "1")
    kirby = load_kirby(monkeypatch)
    monkeypatch.setattr("sys.stdin", _read_payload(big_file))
    kirby.hook_read()


def test_block_message_leads_with_the_tool(small, big_file):
    msg = small.block_message("reading x whole (20 lines)", 20, [str(big_file)])
    lines = msg.splitlines()
    assert lines[0].endswith("Delegate it instead:")
    assert lines[1].strip().startswith("kirby_read tool:")
    assert json.dumps(str(big_file)) in lines[1]  # JSON-encoded so backslashes survive a copy
    assert "no kirby_read tool listed? run:" in lines[2]
    assert f'"{big_file}"' in lines[2]


# ----------------------------------------------------------------------------- Bash hook
def test_hook_bash_blocks_cat_of_large_file(small, monkeypatch, big_file):
    monkeypatch.setattr("sys.stdin", _bash_payload(f'cat "{big_file}"'))
    blocks(small.hook_bash)


@pytest.mark.parametrize("template", [
    'cat "{f}" | grep x',    # piped: output goes elsewhere
    'cat "{f}" > out.txt',   # redirected to a file
    'head -n 5 "{f}"',       # bounded head
    'tail -n 5 "{f}"',       # bounded tail
    'sed -n 1,5p "{f}"',     # small range
    'sed -i s/a/b/ "{f}"',   # in-place edit prints nothing
    'wc -l "{f}"',           # not a reader
    'git status',            # unrelated
])
def test_hook_bash_allows_targeted_commands(small, monkeypatch, big_file, template):
    monkeypatch.setattr("sys.stdin", _bash_payload(template.format(f=big_file.as_posix())))
    small.hook_bash()  # must not raise


@pytest.mark.parametrize("template", [
    'cat "{f}" 2>&1',        # stderr redirect leaves stdout in context
    'head -n 15 "{f}"',      # head over the threshold
    'head -15 "{f}"',
    'tail -n +3 "{f}"',      # line 3 to the end: 18 lines
    'sed -n 1,15p "{f}"',    # sed range over the threshold
    "sed -n '3,$p' \"{f}\"",  # range to end of file
    'sed s/a/b/ "{f}"',      # sed without -n prints every line
    'time cat "{f}"',        # prefix word
    'echo start; cat "{f}"',  # later segment
])
def test_hook_bash_blocks_large_dumps(small, monkeypatch, big_file, template):
    monkeypatch.setattr("sys.stdin", _bash_payload(template.format(f=big_file.as_posix())))
    blocks(small.hook_bash)


def test_hook_bash_follows_cd(small, monkeypatch, big_file, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setattr("sys.stdin", _bash_payload(f'cd "{tmp_path.as_posix()}" && cat big.py', cwd=elsewhere))
    blocks(small.hook_bash)


def test_hook_bash_resolves_relative_to_hook_cwd(small, monkeypatch, big_file, tmp_path):
    monkeypatch.setattr("sys.stdin", _bash_payload("cat big.py", cwd=tmp_path))
    blocks(small.hook_bash)


def test_hook_bash_sums_multiple_files(small, monkeypatch, tmp_path):
    for name in ("a.py", "b.py"):
        (tmp_path / name).write_text("\n" * 6)
    monkeypatch.setattr("sys.stdin", _bash_payload(f'cat "{tmp_path.as_posix()}/a.py" "{tmp_path.as_posix()}/b.py"'))
    blocks(small.hook_bash)


def test_hook_bash_expands_globs(small, monkeypatch, tmp_path):
    for name in ("a.py", "b.py"):
        (tmp_path / name).write_text("\n" * 6)
    monkeypatch.setattr("sys.stdin", _bash_payload(f"cat {tmp_path.as_posix()}/*.py"))
    blocks(small.hook_bash)


def test_hook_bash_powershell_get_content(small, monkeypatch, big_file):
    monkeypatch.setattr("sys.stdin", _bash_payload(f'Get-Content "{big_file}"', tool="PowerShell"))
    blocks(small.hook_bash)


def test_hook_bash_powershell_bounded(small, monkeypatch, big_file):
    monkeypatch.setattr("sys.stdin", _bash_payload(f'Get-Content "{big_file}" -TotalCount 5', tool="PowerShell"))
    small.hook_bash()


def test_resolve_path_maps_git_bash_drive_letters(small):
    assert small.resolve_path("/x", "/c/Users/me/f.py", windows=True) == Path("C:/Users/me/f.py")
    assert small.resolve_path("/x", "/cygdrive/d/f.py", windows=True) == Path("D:/f.py")
    assert small.resolve_path("/x", "$UNKNOWN/f.py") is None
    assert small.resolve_path("/base", "rel/f.py", windows=False) == Path("/base/rel/f.py")


# ----------------------------------------------------------------------------- SessionStart hook
def test_hook_session_emits_rule(small, capsys):
    small.hook_session()
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "kirby_read" in ctx
    assert "over 10 lines" in ctx


def test_hook_session_silent_when_disabled(monkeypatch, capsys):
    monkeypatch.setenv("KIRBY_DISABLE", "1")
    kirby = load_kirby(monkeypatch)
    kirby.hook_session()
    assert capsys.readouterr().out == ""


# ----------------------------------------------------------------------------- MCP server
def _call(kirby, mid, name, arguments):
    return kirby.mcp_dispatch({"jsonrpc": "2.0", "id": mid, "method": "tools/call",
                               "params": {"name": name, "arguments": arguments}})


def test_mcp_initialize_and_tools_list(small):
    init = small.mcp_dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                               "params": {"protocolVersion": "2025-06-18"}})
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["capabilities"] == {"tools": {}}
    assert small.mcp_dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    tools = small.mcp_dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    assert [t["name"] for t in tools] == ["kirby_read", "kirby_write"]
    assert "10 lines" in tools[0]["description"]
    assert tools[0]["inputSchema"]["required"] == ["question", "paths"]


def test_mcp_read_sends_files_to_worker(small, monkeypatch, big_file):
    seen = {}

    def fake(system, user):
        seen["system"], seen["user"] = system, user
        return "- bullet"

    monkeypatch.setattr(small, "ask_worker", fake)
    resp = _call(small, 3, "kirby_read", {"question": "what?", "paths": [str(big_file)]})
    assert resp["result"]["isError"] is False
    assert resp["result"]["content"][0]["text"].startswith("- bullet")
    assert seen["system"] == small.READER_SYSTEM
    assert "<question>\nwhat?" in seen["user"]
    assert str(big_file) in seen["user"]


def test_mcp_errors_come_back_as_results(small):
    resp = _call(small, 4, "kirby_read", {"question": "q", "paths": ["/nonexistent/file.py"]})
    assert resp["result"]["isError"] is True
    assert "no readable files" in resp["result"]["content"][0]["text"]
    unknown = small.mcp_dispatch({"jsonrpc": "2.0", "id": 5, "method": "nope"})
    assert unknown["error"]["code"] == -32601


def test_mcp_write_refuses_existing_target(small, monkeypatch, big_file, tmp_path):
    monkeypatch.setattr(small, "ask_worker", lambda s, u: "x = 1")
    target = tmp_path / "exists.py"
    target.write_text("keep")
    resp = _call(small, 6, "kirby_write", {"spec": "s", "reference": [str(big_file)], "target": str(target)})
    assert resp["result"]["isError"] is True
    assert "exists" in resp["result"]["content"][0]["text"]
    assert target.read_text() == "keep"


def test_mcp_write_writes_target(small, monkeypatch, big_file, tmp_path):
    monkeypatch.setattr(small, "ask_worker", lambda s, u: "```python\nx = 1\n```")
    target = tmp_path / "new" / "gen.py"
    resp = _call(small, 7, "kirby_write", {"spec": "s", "reference": [str(big_file)], "target": str(target)})
    assert resp["result"]["isError"] is False
    assert target.read_text() == "x = 1\n"


def test_mcp_write_hands_back_when_worker_declines(small, monkeypatch, big_file, tmp_path):
    monkeypatch.setattr(small, "ask_worker", lambda s, u: "KIRBY_NEEDS_MAIN_MODEL")
    target = tmp_path / "gen.py"
    resp = _call(small, 8, "kirby_write", {"spec": "s", "reference": [str(big_file)], "target": str(target)})
    assert resp["result"]["isError"] is True
    assert not target.exists()


# ----------------------------------------------------------------------------- backends
def test_backend_defaults_to_claude(monkeypatch):
    monkeypatch.delenv("KIRBY_BACKEND", raising=False)
    kirby = load_kirby(monkeypatch)
    assert kirby.BACKEND == "claude"


def test_backend_openai_is_selected_and_routed(monkeypatch):
    monkeypatch.setenv("KIRBY_BACKEND", "openai")
    kirby = load_kirby(monkeypatch)
    assert kirby.BACKEND == "openai"
    monkeypatch.setattr(kirby, "ask_openai", lambda s, u: f"openai:{u}")
    monkeypatch.setattr(kirby, "ask_claude", lambda s, u: "claude")
    assert kirby.ask_worker("sys", "hi") == "openai:hi"


def test_backend_unknown_exits(monkeypatch):
    monkeypatch.setenv("KIRBY_BACKEND", "bogus")
    kirby = load_kirby(monkeypatch)
    with pytest.raises(SystemExit):
        kirby.ask_worker("sys", "hi")


def test_openai_key_from_file(monkeypatch, tmp_path):
    keyfile = tmp_path / "k"
    keyfile.write_text("sk-test\n")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("KIRBY_OPENAI_KEY_FILE", str(keyfile))
    kirby = load_kirby(monkeypatch)
    assert kirby.load_openai_key() == "sk-test"


def test_strip_fences(monkeypatch):
    kirby = load_kirby(monkeypatch)
    assert kirby.strip_fences("```python\nx = 1\n```") == "x = 1\n"
    assert kirby.strip_fences("x = 1") == "x = 1\n"
