import io
import json
import sys
import threading
from pathlib import Path

import pytest

from thechatplace import claude_cli as cli
from thechatplace import platform_paths
from thechatplace.claude_cli import (ResumeRefused, StreamParser, TurnRunner,
                                     build_new_command, build_resume_command,
                                     check_resume_allowed, child_environment)
from markers import windows_paths  # noqa: E402

EXE = "C:\\bin\\claude.exe"
OWN = {"aaaa-1111"}
DESKTOP = {"dddd-2222"}
FLAGS = ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
PROMPTS = ["--permission-prompts", "host", "--permission-prompt-tool", "stdio"]


# -- command construction --------------------------------------------------------


def test_new_command():
    command = build_new_command(EXE, "aaaa-1111", "  My   title ", "auto")
    assert command == [EXE, *FLAGS, "--permission-mode", "auto", *PROMPTS,
                       "--session-id", "aaaa-1111", "--name", "My title"]


def test_new_command_without_title_and_other_modes():
    for mode in ("acceptEdits", "manual", "plan"):
        command = build_new_command(EXE, "aaaa-1111", "", mode)
        assert "--name" not in command
        assert command[command.index("--permission-mode") + 1] == mode


def test_old_default_mode_name_becomes_manual():
    command = build_resume_command(EXE, "aaaa-1111", "default", OWN, DESKTOP)
    assert command[command.index("--permission-mode") + 1] == "manual"


def test_never_bare_prompts_come_to_us_and_prompt_not_on_command_line():
    for command in (build_new_command(EXE, "aaaa-1111", "t", "auto"),
                    build_resume_command(EXE, "aaaa-1111", "auto", OWN, DESKTOP)):
        assert "--bare" not in command
        assert "-p" in command
        assert command[command.index("--permission-prompts") + 1] == "host"
        assert command[command.index("--permission-prompt-tool") + 1] == "stdio"


def test_unknown_permission_mode_refused():
    with pytest.raises(ValueError):
        build_new_command(EXE, "aaaa-1111", "t", "bypassPermissions")
    with pytest.raises(ValueError):
        build_resume_command(EXE, "aaaa-1111", "yolo", OWN, DESKTOP)


def test_new_command_rejects_bad_id():
    with pytest.raises(ValueError):
        build_new_command(EXE, "--resume", "t", "auto")


def test_resume_command_for_own_session():
    assert build_resume_command(EXE, "aaaa-1111", "plan", OWN, DESKTOP) == [
        EXE, *FLAGS, "--permission-mode", "plan", *PROMPTS,
        "--resume", "aaaa-1111"]


def test_model_goes_on_every_turn():
    new = build_new_command(EXE, "aaaa-1111", "t", "auto", "opus")
    resumed = build_resume_command(EXE, "aaaa-1111", "auto", OWN, DESKTOP, model="opus")
    for command in (new, resumed):
        assert command[command.index("--model") + 1] == "opus"
    # A full model name works too.
    command = build_resume_command(EXE, "aaaa-1111", "auto", OWN, DESKTOP,
                                   model="claude-opus-5-5")
    assert command[command.index("--model") + 1] == "claude-opus-5-5"


def test_title_goes_on_every_turn():
    """#123: the name other sessions reach this one by must not change each turn."""
    command = build_resume_command(EXE, "aaaa-1111", "auto", OWN, DESKTOP,
                                   title="  Hub \n on   Clark ")
    assert command[command.index("--name") + 1] == "Hub on Clark"
    assert command[-2:] == ["--resume", "aaaa-1111"]
    assert "--name" not in build_resume_command(EXE, "aaaa-1111", "auto", OWN, DESKTOP)
    # A title that looks like a flag is still only ever the value of --name.
    command = build_resume_command(EXE, "aaaa-1111", "auto", OWN, DESKTOP,
                                   title="--dangerously-skip-permissions")
    assert command[command.index("--name") + 1] == "--dangerously-skip-permissions"
    assert command.count("--dangerously-skip-permissions") == 1


def test_default_model_passes_no_model_flag():
    assert "--model" not in build_new_command(EXE, "aaaa-1111", "t", "auto", "")
    assert "--model" not in build_resume_command(EXE, "aaaa-1111", "auto", OWN, DESKTOP)


@pytest.mark.parametrize("model", ["--dangerously-skip-permissions", "-x", "opus sonnet",
                                   "a;b", "x" * 101, "sonnet[1m]",
                                   "claude-opus-4-1@20250805",
                                   "us.anthropic.claude-opus-4-1-v1:0"])
def test_unsafe_model_name_refused(model):
    with pytest.raises(ValueError):
        build_new_command(EXE, "aaaa-1111", "t", "auto", model)


def test_model_labels():
    assert cli.model_label("") == "the default model"
    assert cli.model_label("opus") == "Opus"
    assert cli.model_label("claude-opus-5-5") == "claude-opus-5-5"
    assert [value for value, _label in cli.MODELS] == ["", "opus", "sonnet", "haiku"]
    # Fable can bill usage credits without asking in -p mode.
    assert "fable" not in cli.MODEL_LABELS


# -- the desktop --resume guard ------------------------------------------------------


def test_resume_refused_for_desktop_cli_id():
    with pytest.raises(ResumeRefused, match="desktop app"):
        build_resume_command(EXE, "dddd-2222", "auto", OWN | DESKTOP, DESKTOP)


def test_resume_refused_for_local_id_even_if_listed_as_own():
    with pytest.raises(ResumeRefused, match="desktop app session id"):
        build_resume_command(EXE, "local_abc", "auto", {"local_abc"}, set())


def test_resume_refused_for_unknown_session():
    with pytest.raises(ResumeRefused, match="only sends"):
        check_resume_allowed("eeee-3333", OWN, DESKTOP)


@pytest.mark.parametrize("bad", ["", "../x", "--print", "a b", None])
def test_resume_refused_for_malformed_ids(bad):
    with pytest.raises(ResumeRefused):
        check_resume_allowed(bad, OWN | {bad} if bad else OWN, DESKTOP)


# -- environment ---------------------------------------------------------------------


def test_child_environment_strips_session_and_billing_vars_only():
    env = child_environment({
        "PATH": "x", "USERPROFILE": "u",
        "ANTHROPIC_API_KEY": "secret", "ANTHROPIC_AUTH_TOKEN": "t",
        "ANTHROPIC_BASE_URL": "http://localhost", "CLAUDE_CODE_USE_BEDROCK": "1",
        "CLAUDECODE": "1", "CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH": "1",
        "CLAUDE_CODE_SESSION_ID": "s", "CLAUDE_CODE_OAUTH_TOKEN": "o",
        "claude_code_entrypoint": "lower",
        # Families a host session sets, including names not in the list yet.
        "CLAUDE_CODE_SDK_FUTURE_THING": "1", "CLAUDE_CODE_HOST_PORT": "1",
        "claude_code_messaging_new": "1",
        "CLAUDE_CODE_GIT_BASH_PATH_EXTRA": "kept",
        # Chosen by the user: kept.
        "CLAUDE_CONFIG_DIR": "D:/c", "CLAUDE_CODE_GIT_BASH_PATH": "C:/Git/bash.exe",
        "HTTPS_PROXY": "http://proxy", "API_TIMEOUT_MS": "60000",
        "ANTHROPIC_MODEL": "opus",
    })
    assert env == {"PATH": "x", "USERPROFILE": "u", "CLAUDE_CONFIG_DIR": "D:/c",
                   "CLAUDE_CODE_GIT_BASH_PATH_EXTRA": "kept",
                   "CLAUDE_CODE_GIT_BASH_PATH": "C:/Git/bash.exe",
                   "HTTPS_PROXY": "http://proxy", "API_TIMEOUT_MS": "60000",
                   "ANTHROPIC_MODEL": "opus"}


# -- finding claude ----------------------------------------------------------------------


def test_find_claude_prefers_native_exe(tmp_path):
    lookup = platform_paths.find_claude(which=lambda n: "C:\\x\\claude.exe",
                                        native_candidates=[])
    assert lookup.path == "C:\\x\\claude.exe"


@pytest.mark.parametrize("script", ["C:\\npm\\claude.CMD", "C:\\npm\\claude.bat",
                                    "C:\\npm\\claude.ps1"])
def test_find_claude_refuses_scripts(script, tmp_path):
    lookup = platform_paths.find_claude(which=lambda n: script, native_candidates=[])
    assert lookup.path is None and "native" in lookup.problem
    native = tmp_path / "claude.exe"
    native.write_bytes(b"")
    lookup = platform_paths.find_claude(which=lambda n: script, native_candidates=[native])
    assert lookup.path == str(native)


def test_find_claude_missing():
    lookup = platform_paths.find_claude(which=lambda n: None, native_candidates=[])
    assert lookup.path is None and "isn't installed" in lookup.problem
    # Short enough to speak, pointing at where it can be installed; the
    # commands for this computer are kept for that dialog.
    assert lookup.problem.endswith("choose Claude Code Sign-in on the Help menu.")
    assert "claude.ai/install" not in lookup.problem
    assert "claude.ai/install" in lookup.install_help


def test_find_claude_uses_the_native_program_behind_an_npm_shim(tmp_path):
    """A newer npm install's claude.cmd starts the native claude.exe, which
    is used directly, never through cmd.exe."""
    npm = tmp_path / "npm"
    exe = npm / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    shim = npm / "claude.cmd"
    shim.write_text("@ECHO off\r\nGOTO start\r\n:find_dp0\r\nSET dp0=%~dp0\r\nEXIT /b\r\n"
                    ":start\r\nSETLOCAL\r\nCALL :find_dp0\r\n"
                    '"%dp0%\\node_modules\\@anthropic-ai\\claude-code\\bin\\claude.exe"   %*\r\n')
    lookup = platform_paths.find_claude(which=lambda n: str(shim), native_candidates=[])
    assert lookup.path is not None and Path(lookup.path).samefile(exe)
    # An old shim that runs cli.js through Node is refused, with the install
    # help, even with node.exe beside it (nvm-windows), which it also names.
    exe.unlink()
    (npm / "node.exe").write_bytes(b"")
    shim.write_text(
        "@ECHO off\r\nGOTO start\r\n:find_dp0\r\nSET dp0=%~dp0\r\nEXIT /b\r\n:start\r\n"
        "SETLOCAL\r\nCALL :find_dp0\r\n\r\n"
        'IF EXIST "%dp0%\\node.exe" (\r\n  SET "_prog=%dp0%\\node.exe"\r\n) ELSE (\r\n'
        '  SET "_prog=node"\r\n  SET PATHEXT=%PATHEXT:;.JS;=;%\r\n)\r\n\r\n'
        'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  '
        '"%dp0%\\node_modules\\@anthropic-ai\\claude-code\\cli.js" %*\r\n')
    lookup = platform_paths.find_claude(which=lambda n: str(shim), native_candidates=[])
    assert lookup.path is None and "older npm install" in lookup.problem
    assert "claude.ai/install" in lookup.install_help


def test_find_claude_refuses_a_node_script(tmp_path):
    """An old npm install on a Mac: a JavaScript file that needs Node, which
    an app started from the Finder can't find."""
    script = tmp_path / "claude"
    script.write_text("#!/usr/bin/env node\nrequire('./cli.js')\n")
    native = tmp_path / "native-claude"
    native.write_bytes(b"\xcf\xfa\xed\xfe")
    lookup = platform_paths.find_claude(which=lambda n: str(script), native_candidates=[])
    assert lookup.path is None and "older npm install" in lookup.problem
    lookup = platform_paths.find_claude(which=lambda n: str(script),
                                        native_candidates=[script, native])
    assert lookup.path == str(native)


def test_claude_is_looked_for_where_installers_put_it(monkeypatch, tmp_path):
    """Off the PATH, as for an app started from the Finder: the native
    installer's, Homebrew's and npm's folders on a Mac, and the native
    installer's and WinGet's on Windows."""
    monkeypatch.setattr(platform_paths.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(platform_paths.sys, "platform", "darwin")
    mac = platform_paths._native_candidates()
    assert mac[0] == tmp_path / ".local" / "bin" / "claude"
    assert Path("/opt/homebrew/bin/claude") in mac and Path("/usr/local/bin/claude") in mac
    monkeypatch.setattr(platform_paths.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    windows = platform_paths._native_candidates()
    assert windows[0] == tmp_path / ".local" / "bin" / "claude.exe"
    assert windows[1] == tmp_path / "Local" / "Microsoft" / "WinGet" / "Links" / "claude.exe"
    assert "irm https://claude.ai/install.ps1 | iex" in platform_paths.claude_install_help()


def test_install_runs_in_a_terminal_window_on_a_mac(monkeypatch, tmp_path):
    """The installer runs from a .command file in the app's own folder, so
    Terminal shows it; no permission to control Terminal is needed."""
    import subprocess
    monkeypatch.setattr(platform_paths.sys, "platform", "darwin")
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path)
    opened = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **k: opened.append(argv))
    assert platform_paths.start_claude_install() is None
    script = tmp_path / "install-claude-code.command"
    assert opened == [["open", "-a", "Terminal", str(script)]]
    text = script.read_text()
    assert text.startswith("#!/bin/bash\n")
    assert "/bin/bash -c 'curl -fsSL https://claude.ai/install.sh | bash'" in text
    assert "status=$?" in text  # a failure isn't reported as finished


def test_a_terminal_window_drops_billing_variables(monkeypatch, tmp_path):
    """Terminal starts the user's own shell, whose profile may export an API
    key: the script unsets what child_environment() would remove."""
    import subprocess
    monkeypatch.setattr(platform_paths.sys, "platform", "darwin")
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **k: None)
    platform_paths.run_in_terminal(["/x/claude", "auth", "login"], "s.command", "Signing in.",
                                   clear=cli.STRIPPED_VARS,
                                   clear_prefixes=cli.SESSION_INJECTED_PREFIXES)
    lines = (tmp_path / "s.command").read_text().splitlines()
    unset = next(line for line in lines if line.startswith("unset "))
    assert "ANTHROPIC_API_KEY" in unset.split() and "CLAUDE_CODE_USE_BEDROCK" in unset.split()
    assert any("CLAUDE_CODE_SDK_*" in line for line in lines)
    assert lines.index(unset) < lines.index("/x/claude auth login")
    monkeypatch.undo()  # the real Popen, for bash
    if sys.platform != "win32":  # the script is valid bash
        out = subprocess.run(["/bin/bash", "-n", str(tmp_path / "s.command")])
        assert out.returncode == 0


def test_codex_terminal_clears_billing_and_exports_its_home(monkeypatch, tmp_path):
    import subprocess
    monkeypatch.setattr(platform_paths.sys, "platform", "darwin")
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **k: None)
    platform_paths.run_in_terminal(
        ["/x/codex", "login"], "codex.command", "Signing in.",
        env={"CODEX_HOME": "/Users/me/Codex Home", "OPENAI_API_KEY": "secret"},
        clear=platform_paths.CODEX_BILLING_VARS,
        clear_prefixes=("OPENAI_", "AZURE_OPENAI_"), export=("CODEX_HOME",))
    lines = (tmp_path / "codex.command").read_text().splitlines()
    assert "export CODEX_HOME='/Users/me/Codex Home'" in lines
    unset = next(line for line in lines if line.startswith("unset "))
    assert set(platform_paths.CODEX_BILLING_VARS) <= set(unset.split())
    assert any("OPENAI_*|AZURE_OPENAI_*" in line for line in lines)
    assert lines.index("export CODEX_HOME='/Users/me/Codex Home'") < lines.index(unset)
    assert lines.index(unset) < lines.index("/x/codex login")
    assert not any("secret" in line or "OPENAI_API_KEY" in line for line in lines)

    platform_paths.run_in_terminal(
        ["/x/codex", "login"], "codex-default-home.command", "Signing in.",
        env={"PATH": "/usr/bin"}, clear=platform_paths.CODEX_BILLING_VARS,
        clear_prefixes=("OPENAI_", "AZURE_OPENAI_"), export=("CODEX_HOME",))
    default_lines = (tmp_path / "codex-default-home.command").read_text().splitlines()
    assert "unset CODEX_HOME" in default_lines
    assert default_lines.index("unset CODEX_HOME") < default_lines.index("/x/codex login")


# -- stream-json events ---------------------------------------------------------------


def ev(**data):
    return json.dumps(data)


def test_stream_parser_happy_path():
    parser = StreamParser()
    events = []
    for line in [
        ev(type="system", subtype="session_title_changed", title="t", session_id="s1"),
        ev(type="system", subtype="init", session_id="s1", apiKeySource="none",
           permissionMode="auto", tools=[]),
        ev(type="assistant", session_id="s1", parent_tool_use_id=None,
           message={"content": [{"type": "thinking", "thinking": ""}]}),
        ev(type="assistant", session_id="s1", parent_tool_use_id=None,
           message={"content": [{"type": "text", "text": "Working on it."}]}),
        ev(type="assistant", session_id="s1", parent_tool_use_id=None,
           message={"content": [{"type": "tool_use", "name": "Bash", "input": {}}]}),
        ev(type="assistant", session_id="s1", parent_tool_use_id="toolu_sub",
           message={"content": [{"type": "text", "text": "subagent chatter"}]}),
        ev(type="rate_limit_event", rate_limit_info={}),
        ev(type="result", subtype="success", is_error=False, result="pong",
           session_id="s1", permission_denials=[]),
    ]:
        events.extend(parser.feed(line))
    assert [(e.kind, e.text) for e in events] == [
        ("started", "auto"), ("text", "Working on it."), ("tool", "Bash"),
        ("finished", "pong")]
    assert parser.session_id == "s1" and parser.api_key_source == "none"
    assert not events[-1].is_error and parser.finished


def test_stream_parser_permission_denials():
    parser = StreamParser()
    denied = parser.feed(ev(type="system", subtype="permission_denied", tool_name="Write",
                            message="Claude requested permissions to write to x.txt"))
    assert denied[0].kind == "denied"
    assert denied[0].text == "Write: Claude requested permissions to write to x.txt"
    result = parser.feed(ev(type="result", subtype="success", is_error=False, result="Done",
                            permission_denials=[
                                {"tool_name": "Write", "tool_use_id": "t",
                                 "tool_input": {"file_path": "C:/x/probe.txt"}},
                                {"tool_name": "Bash", "tool_input": {"command": "rm -rf  /"}},
                                {"tool_name": "WebFetch"}, "junk"]))
    assert result[0].denials == ["Write was refused: C:/x/probe.txt",
                                 "Bash was refused: rm -rf /",
                                 "WebFetch was refused", "A tool was refused."]


def test_stream_parser_error_results():
    parser = StreamParser()
    err = parser.feed(ev(type="result", subtype="error_max_turns", is_error=True))[0]
    assert err.is_error and "error_max_turns" in err.text
    err2 = StreamParser().feed(ev(type="result", subtype="error_during_execution",
                                  errors=["boom", "bang"]))[0]
    assert err2.is_error and err2.text == "boom; bang"


def test_stream_parser_tolerates_garbage():
    parser = StreamParser()
    assert parser.feed("not json") == []
    assert parser.feed("[1]") == []
    assert parser.feed("") == []
    assert parser.feed(ev(type="assistant", message="odd")) == []
    assert parser.feed(ev(type="brand_new_event", x=1)) == []
    assert parser.bad_lines == 2


@pytest.mark.parametrize("source,problem", [
    ("none", False), (None, False), ("", False),
    ("ANTHROPIC_API_KEY", True), ("apiKeyHelper", True)])
def test_api_key_problem(source, problem):
    assert (cli.api_key_problem(source) is not None) is problem


# -- TurnRunner with a fake process -------------------------------------------------------


class FakeProcess:
    """Byte pipes, like the real Popen the runner makes."""

    def __init__(self, stdout_lines, returncode=0, stderr_lines=()):
        self.stdout = io.BytesIO("".join(x + "\n" for x in stdout_lines).encode("utf-8"))
        self.stderr = io.BytesIO("".join(x + "\n" for x in stderr_lines).encode("utf-8"))
        self.stdin = io.BytesIO()
        self.pid = None
        self.returncode = None
        self._final = returncode
        self.killed = False
        self.written = None
        original_close = self.stdin.close

        def close():
            self.written = self.stdin.getvalue()
            original_close()
        self.stdin.close = close

    def poll(self):
        return self.returncode

    def wait(self):
        self.returncode = -9 if self.killed else self._final
        return self.returncode

    def kill(self):
        self.killed = True


def run_turn(process, tmp_path, command=None, prompt="Hello -p --bare", remote_control=None):
    captured = {}
    events = []
    done = threading.Event()

    def popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured.update(kwargs)
        return process

    def on_event(event):
        events.append(event)
        if event.kind in ("finished", "failed"):
            done.set()

    runner = TurnRunner(command or ["claude", "-p"], str(tmp_path), prompt,
                        on_event, popen=popen, env={"PATH": "x"},
                        remote_control=remote_control)
    runner.start()
    assert done.wait(5)
    runner.join(5)
    return runner, events, captured


def test_runner_sends_prompt_on_stdin_and_reports_events(tmp_path):
    process = FakeProcess([
        ev(type="system", subtype="init", session_id="s1", apiKeySource="none"),
        ev(type="assistant", message={"content": [{"type": "text", "text": "pong"}]}),
        ev(type="result", subtype="success", result="pong", session_id="s1"),
    ])
    runner, events, captured = run_turn(process, tmp_path)
    sent = [json.loads(line) for line in process.written.decode("utf-8").splitlines()]
    assert sent[0]["type"] == "control_request"
    assert sent[0]["request"]["subtype"] == "initialize"
    assert sent[1]["type"] == "user"
    assert sent[1]["message"] == {"role": "user", "content": "Hello -p --bare"}
    assert captured["cwd"] == str(tmp_path)
    assert captured["env"] == {"PATH": "x", "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS": "1"}
    assert "encoding" not in captured and "text" not in captured  # byte pipes
    assert [e.kind for e in events] == ["started", "text", "finished"]
    assert events[-1].session_id == "s1"
    assert runner.session_started


def test_runner_stops_when_an_api_key_would_be_billed(tmp_path):
    process = FakeProcess([
        ev(type="system", subtype="init", session_id="s1", apiKeySource="ANTHROPIC_API_KEY"),
        ev(type="assistant", message={"content": [{"type": "text", "text": "billed!"}]}),
        ev(type="result", subtype="success", result="billed!"),
    ])
    _runner, events, _ = run_turn(process, tmp_path)
    assert process.killed
    assert [e.kind for e in events] == ["failed"]
    assert "API key" in events[0].text


@pytest.mark.parametrize("command,stopped", [
    (["claude", "-p"], True),                          # Claude Code's default
    (["claude", "-p", "--model", "opus"], True),       # a fallback to Fable
    (["claude", "-p", "--model", "claude-fable-5-1"], False),  # chosen
])
def test_runner_stops_a_turn_on_an_unchosen_fable(tmp_path, command, stopped):
    process = FakeProcess([
        ev(type="system", subtype="init", session_id="s1", apiKeySource="none",
           model="claude-fable-5-1"),
        ev(type="assistant", message={"content": [{"type": "text", "text": "hi"}]}),
        ev(type="result", subtype="success", result="hi"),
    ])
    _runner, events, _ = run_turn(process, tmp_path, command=command)
    if stopped:
        assert process.killed
        assert [e.kind for e in events] == ["failed"]
        assert "Fable" in events[0].text and "Change Model" in events[0].text
    else:
        assert events[-1].kind == "finished"


def test_runner_reports_exit_without_result(tmp_path):
    process = FakeProcess([], returncode=1, stderr_lines=["Error: not logged in"])
    runner, events, _ = run_turn(process, tmp_path)
    assert [e.kind for e in events] == ["failed"]
    assert "exit code 1" in events[0].text and "not logged in" in events[0].text
    assert not runner.session_started


def test_runner_missing_folder(tmp_path):
    events = []
    done = threading.Event()
    runner = TurnRunner(["claude"], str(tmp_path / "gone"), "hi",
                        lambda e: (events.append(e), done.set()),
                        popen=lambda *a, **k: pytest.fail("must not start"), env={})
    runner.start()
    assert done.wait(5)
    assert events[0].kind == "failed" and "doesn't exist" in events[0].text


def test_runner_claude_not_found(tmp_path):
    events = []
    done = threading.Event()

    def popen(*a, **k):
        raise FileNotFoundError("claude not found")
    runner = TurnRunner(["claude"], str(tmp_path), "hi",
                        lambda e: (events.append(e), done.set()), popen=popen, env={})
    runner.start()
    assert done.wait(5)
    assert events[0].kind == "failed"


def test_runner_cancel(tmp_path):
    process = FakeProcess([])
    runner = TurnRunner(["claude"], str(tmp_path), "hi", lambda e: None,
                        popen=lambda *a, **k: process, env={})
    runner._process = process
    runner.cancel()
    assert process.killed


def test_cancel_during_popen_kills_at_once(tmp_path):
    """cancel() landing while Popen is still starting the process."""
    process = FakeProcess([])
    events = []
    done = threading.Event()
    holder = {}

    def popen(*a, **k):
        holder["runner"].cancel()  # arrives mid-start, before _process is set
        return process
    runner = TurnRunner(["claude"], str(tmp_path), "hi",
                        lambda e: (events.append(e), done.set()), popen=popen, env={})
    holder["runner"] = runner
    runner.start()
    assert done.wait(5)
    assert process.killed
    assert events[-1].kind == "failed" and events[-1].text == "Stopped."


def test_cancel_before_start_never_launches(tmp_path):
    events = []
    done = threading.Event()
    runner = TurnRunner(["claude"], str(tmp_path), "hi",
                        lambda e: (events.append(e), done.set()),
                        popen=lambda *a, **k: pytest.fail("must not start"), env={})
    runner.cancel()
    runner.start()
    assert done.wait(5)
    assert events[0].text == "Stopped."


def test_runner_survives_a_broken_callback(tmp_path):
    process = FakeProcess([ev(type="result", subtype="success", result="ok")])
    calls = []

    def on_event(event):
        calls.append(event.kind)
        raise RuntimeError("UI bug")
    runner = TurnRunner(["claude"], str(tmp_path), "hi", on_event,
                        popen=lambda *a, **k: process, env={})
    runner.start()
    runner.join(5)
    assert calls == ["finished"]


def test_runner_tracks_activity_and_elapsed(tmp_path):
    times = iter([100.0, 165.0])
    process = FakeProcess([
        ev(type="system", subtype="init", session_id="s1", apiKeySource="none"),
        ev(type="assistant", message={"content": [{"type": "tool_use", "name": "Bash"}]}),
        ev(type="result", subtype="success", result="ok"),
    ])
    runner = TurnRunner(["claude"], str(tmp_path), "hi", lambda e: None,
                        popen=lambda *a, **k: process, env={}, clock=lambda: next(times))
    runner.start()
    runner.join(5)
    assert runner.session_started and runner.last_activity == "using Bash"
    assert runner.elapsed() == 65.0


@pytest.mark.parametrize("seconds,words", [
    (0, "0 seconds"), (1, "1 second"), (65, "1 minute 5 seconds"), (120, "2 minutes"),
    (3600, "1 hour"), (3725, "1 hour 2 minutes")])
def test_describe_elapsed(seconds, words):
    assert cli.describe_elapsed(seconds) == words


def test_permission_modes_offered():
    assert cli.PERMISSION_MODE_VALUES == ["auto", "acceptEdits", "manual", "plan"]
    assert cli.DEFAULT_PERMISSION_MODE == "auto"


# -- a real child process ---------------------------------------------------------------------

FAKE_CLAUDE = Path(__file__).with_name("fake_claude.py")


def wait_for_pid_file(path, timeout=20.0):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            return int(path.read_text().strip())
        except (OSError, ValueError):
            time.sleep(0.05)
    raise AssertionError(f"{path} never appeared")


def run_real(tmp_path, prompt, mode="normal", cancel_when_started=False):
    log = tmp_path / "fake.log"
    env = {**child_environment(), "FAKE_CLAUDE_LOG": str(log), "FAKE_CLAUDE_MODE": mode,
           "CLAUDECODE": "1"}
    env = child_environment(env)
    events = []
    done = threading.Event()

    def on_event(event):
        events.append(event)
        if event.kind in ("finished", "failed"):
            done.set()
    runner = TurnRunner([sys.executable, str(FAKE_CLAUDE), "--session-id", "abc-1"],
                        str(tmp_path), prompt, on_event, env=env)
    runner.start()
    if cancel_when_started:
        # Cancel only once the grandchild really exists.
        wait_for_pid_file(tmp_path / "grandchild.pid")
        runner.cancel()
    assert done.wait(30)
    runner.join(10)
    return events, (json.loads(log.read_text(encoding="utf-8")) if log.exists() else None)


def test_real_child_process_pipes_bytes_exactly(tmp_path):
    prompt = "line one\nline two \u2014 caf\u00e9 \u2028 end"
    events, seen = run_real(tmp_path, prompt)
    assert seen["prompt"] == prompt          # no CRLF, no mangled UTF-8
    assert seen["claude_env"] == []           # CLAUDECODE was stripped
    kinds = [e.kind for e in events]
    assert kinds == ["started", "text", "finished"]
    assert seen["received"] == ["control_request", "user"]
    # The reply carries a raw U+2028 inside the JSON line; it must survive.
    assert events[1].text == "reply\u2028with a line separator"
    assert events[-1].text == "done"


def test_real_child_process_cancel_kills_the_tree(tmp_path):
    events, seen = run_real(tmp_path, "wait", mode="hang", cancel_when_started=True)
    assert events[-1].kind == "failed" and events[-1].text == "Stopped."
    grandchild = int((tmp_path / "grandchild.pid").read_text())
    import time
    deadline = time.time() + 5
    while platform_paths.pid_alive(grandchild) and time.time() < deadline:
        time.sleep(0.1)
    assert not platform_paths.pid_alive(grandchild)


def test_real_child_process_permission_request_answered(tmp_path):
    log = tmp_path / "fake.log"
    env = child_environment({**child_environment(), "FAKE_CLAUDE_LOG": str(log),
                             "FAKE_CLAUDE_MODE": "ask"})
    events = []
    done = threading.Event()
    holder = {}

    def on_event(event):
        events.append(event)
        if event.kind == "permission":
            # The UI answers from its own thread.
            threading.Thread(target=lambda: holder["runner"].respond(
                event.request.request_id, cli.deny_response("not today"))).start()
        if event.kind in ("finished", "failed"):
            done.set()
    runner = TurnRunner([sys.executable, str(FAKE_CLAUDE)], str(tmp_path), "push it",
                        on_event, env=env)
    holder["runner"] = runner
    runner.start()
    assert done.wait(30)
    runner.join(10)
    kinds = [e.kind for e in events]
    assert kinds == ["started", "permission", "text", "finished"]
    request = events[1].request
    assert request.tool_name == "Bash" and request.summary() == "Claude wants to run git push"
    assert events[-1].text == "deny"
    answer = json.loads(log.read_text(encoding="utf-8"))["answer"]
    assert answer == {"type": "control_response", "response": {
        "subtype": "success", "request_id": "req-1",
        "response": {"behavior": "deny", "message": "not today"}}}
    assert runner.pending == {}
    assert not runner.respond("req-1", cli.deny_response())  # already answered


# -- permission requests -----------------------------------------------------------------


def control(request_id="r1", **request):
    return ev(type="control_request", request_id=request_id,
              request={"subtype": "can_use_tool", **request})


def test_parser_turns_can_use_tool_into_a_permission_event():
    parser = StreamParser()
    events = parser.feed(control(
        tool_name="PowerShell", input={"command": "npm init -y", "description": "Init"},
        description="Initialize a new npm project",
        permission_suggestions=[{"type": "addRules", "behavior": "allow",
                                 "destination": "localSettings",
                                 "rules": [{"toolName": "PowerShell",
                                            "ruleContent": "npm init -y"}]}],
        tool_use_id="t1"))
    assert [e.kind for e in events] == ["permission"]
    request = events[0].request
    assert request.request_id == "r1"
    assert request.summary() == "Claude wants to run npm init -y"
    assert "PowerShell command:\nnpm init -y" in request.detail()
    assert "Why: Initialize a new npm project" in request.detail()
    assert request.session_rules() == ["PowerShell(npm init -y)"]
    assert request.allow_for_session_label() == (
        "Allow, and don't ask again in this chat for PowerShell(npm init -y)")


def test_parser_refuses_other_control_requests_and_reads_commands():
    parser = StreamParser()
    assert parser.feed(ev(type="control_request", request_id="h1",
                          request={"subtype": "hook_callback"})) == []
    assert parser.unsupported_requests == ["h1"]
    parser.feed(ev(type="control_response", response={
        "subtype": "success", "request_id": cli.INIT_REQUEST_ID,
        "response": {"commands": [{"name": "compact", "description": "Compact"}, "junk"]}}))
    assert parser.commands == [{"name": "compact", "description": "Compact"}]


@windows_paths
def test_allow_responses():
    request = cli.PermissionRequest("r1", "Write", {"file_path": "C:\\x\\a.txt", "content": "hi"},
                                    suggestions=[{"type": "setMode", "mode": "acceptEdits",
                                                  "destination": "session"}])
    assert request.summary() == "Claude wants to write a.txt"
    assert cli.allow_response(request) == {"behavior": "allow", "updatedInput": request.input}
    assert request.allow_for_session_label().startswith("Allow, and accept all file edits")
    assert cli.allow_response(request, for_session=True)["updatedPermissions"] == [
        {"type": "setMode", "mode": "acceptEdits", "destination": "session"}]
    bash = cli.PermissionRequest("r2", "Bash", {"command": "git push"}, suggestions=[
        {"type": "addRules", "behavior": "allow", "destination": "localSettings",
         "rules": [{"toolName": "Bash", "ruleContent": "git push:*"}]}])
    # Always "session": The Chat Place never writes Claude Code's settings files.
    assert cli.allow_response(bash, for_session=True)["updatedPermissions"] == [
        {"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "git push:*"}],
         "behavior": "allow", "destination": "session"}]
    plain = cli.PermissionRequest("r3", "WebFetch", {"url": "https://example.com"})
    assert plain.allow_for_session_label() == ""
    assert "updatedPermissions" not in cli.allow_response(plain, for_session=True)


def test_plan_approval_switches_mode_and_deny_carries_the_reason():
    plan = cli.PermissionRequest("r4", "ExitPlanMode", {"plan": "# Plan\n1. Do it"})
    assert plan.is_plan and plan.plan() == "# Plan\n1. Do it"
    assert plan.summary() == "Claude's plan is ready for you to approve"
    assert cli.allow_response(plan, mode="acceptEdits")["updatedPermissions"] == [
        {"type": "setMode", "mode": "acceptEdits", "destination": "session"}]
    assert cli.deny_response("Keep it smaller") == {"behavior": "deny",
                                                    "message": "Keep it smaller"}
    assert cli.deny_response("  ")["message"]  # never empty


def test_questions_answered_through_updated_input():
    questions = [{"question": "Which color?", "header": "Color", "multiSelect": False,
                  "options": [{"label": "Red", "description": "Warm"},
                              {"label": "Blue", "description": "Cool"}]},
                 {"question": "Which size?", "options": [{"label": "S"}]}]
    request = cli.PermissionRequest("r5", "AskUserQuestion", {"questions": questions})
    assert request.is_question
    assert request.summary() == "Claude asks: Which color? (and 1 more)"
    response = cli.answer_questions_response(request, {"Which color?": "Blue",
                                                       "Which size?": "S"})
    assert response == {"behavior": "allow", "updatedInput": {
        "questions": questions, "answers": {"Which color?": "Blue", "Which size?": "S"}}}


def test_allowed_tools_go_on_every_turn_and_unsafe_rules_are_dropped():
    rules = ["Bash(git push:*)", "Edit", "--dangerously-skip-permissions", "Bash(a\nb)"]
    for command in (build_new_command(EXE, "aaaa-1111", "t", "auto", allowed_tools=rules),
                    build_resume_command(EXE, "aaaa-1111", "auto", OWN, DESKTOP,
                                         allowed_tools=rules)):
        at = command.index("--allowedTools")
        assert command[at + 1: at + 3] == ["Bash(git push:*)", "Edit"]
        assert command[at + 3].startswith("--")  # the list ends at the next flag
        assert "--dangerously-skip-permissions" not in command
    assert "--allowedTools" not in build_new_command(EXE, "aaaa-1111", "t", "auto")


@pytest.mark.parametrize("name,tool_input,words", [
    ("Bash", {"command": "git  status"}, "run git status"),
    pytest.param("Edit", {"file_path": "C:\\r\\main.py"}, "edit main.py",
                 marks=windows_paths),
    ("WebFetch", {"url": "https://x.org"}, "fetch https://x.org"),
    ("WebSearch", {"query": "wx accessible"}, "search the web for wx accessible"),
    ("Task", {}, "use Task"),
    ("mcp__x__y", {"pattern": "*.py"}, "use mcp__x__y: *.py"),
])
def test_describe_tool_use(name, tool_input, words):
    assert cli.describe_tool_use(name, tool_input) == words


# -- continuing another session as a copy (#189) -------------------------------------------


def test_fork_command_reads_the_source_and_writes_a_new_session():
    command = cli.build_fork_command(EXE, "dddd-2222", "nnnn-3333", " Carry  on ", "auto",
                                     "sonnet", taken_ids=OWN | DESKTOP)
    assert command[command.index("--resume") + 1] == "dddd-2222"
    assert "--fork-session" in command
    assert command[command.index("--session-id") + 1] == "nnnn-3333"
    assert command[command.index("--name") + 1] == "Carry on"
    assert command[command.index("--model") + 1] == "sonnet"


@pytest.mark.parametrize("source,new", [
    ("local_abc", "nnnn-3333"),       # a desktop app id, not a Claude Code one
    ("-p", "nnnn-3333"),
    ("dddd-2222", "dddd-2222"),       # the copy needs its own id
    ("dddd-2222", "aaaa-1111"),       # already taken
    ("dddd-2222", "--resume"),
])
def test_fork_command_refuses_bad_ids(source, new):
    with pytest.raises(ValueError):
        cli.build_fork_command(EXE, source, new, "t", "auto", taken_ids=OWN)


def test_fork_command_refuses_a_cowork_session():
    # Its history is in the desktop app's own files (#91): not even a copy.
    with pytest.raises(cli.ResumeRefused):
        cli.build_fork_command(EXE, "cccc-4444", "nnnn-3333", "t", "auto",
                               taken_ids=OWN, cowork_ids={"cccc-4444"})


def test_tool_events_say_what_the_tool_acts_on():
    parser = StreamParser()
    events = parser.feed(ev(type="assistant", message={"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "git  status"}},
        {"type": "tool_use", "name": "Task", "input": {}}]}))
    assert [(e.text, e.detail) for e in events] == [("Bash", "Bash: git status"),
                                                    ("Task", "Task")]


# -- slash commands and skills (#23) --------------------------------------------------------


def test_usable_commands_puts_yours_first_and_hides_internal_ones():
    commands = [{"name": "compact", "builtin": True}, {"name": "zeta"}, {"name": "__remote"},
                {"name": "Alpha"}, {"description": "no name"}, "junk", {"name": ""}]
    assert [c["name"] for c in cli.usable_commands(commands)] == ["Alpha", "zeta", "compact"]


def test_fetch_commands_asks_only_for_initialize_and_closes(tmp_path):
    answer = ev(type="control_response", response={
        "subtype": "success", "request_id": cli.INIT_REQUEST_ID,
        "response": {"commands": [{"name": "context", "builtin": True}, {"name": "mine"}]}})
    process = FakeProcess([answer])
    seen = {}

    def popen(command, **kwargs):
        seen["command"] = command
        seen.update(kwargs)
        return process
    commands = cli.fetch_commands("claude.exe", str(tmp_path), popen=popen)
    assert [c["name"] for c in commands] == ["mine", "context"]
    assert "--resume" not in seen["command"] and "--session-id" not in seen["command"]
    sent = [json.loads(line) for line in process.written.decode("utf-8").splitlines()]
    assert [m["type"] for m in sent] == ["control_request"]  # no user message: no turn
    assert seen["cwd"] == str(tmp_path)


def test_fetch_commands_gives_nothing_for_a_missing_folder_or_no_answer(tmp_path):
    assert cli.fetch_commands("claude.exe", str(tmp_path / "gone"),
                              popen=lambda *a, **k: pytest.fail("must not start")) == []
    assert cli.fetch_commands("claude.exe", str(tmp_path),
                              popen=lambda *a, **k: FakeProcess([])) == []


def test_init_reports_the_model_in_use():
    from thechatplace.claude_cli import model_matches
    parser = StreamParser()
    events = parser.feed(ev(type="system", subtype="init", session_id="s1",
                            apiKeySource="none", model="claude-sonnet-5-5"))
    assert events[0].kind == "started" and events[0].data == {"model": "claude-sonnet-5-5"}
    assert model_matches("sonnet", "claude-sonnet-5-5")
    assert not model_matches("opus", "claude-sonnet-5-5")
    assert model_matches("", "claude-fable-5-1") and model_matches("opus", "")
    assert model_matches("claude-opus-5-5", "claude-opus-5-5")
    assert model_matches("claude-opus-5-5", "claude-opus-5-5-20261001")
    assert not model_matches("claude-opus-5", "claude-opus-55")
    assert model_matches("claude-opus-5-5", "claude-opus-5-5[1m]")
    # No model, or not a string: nothing to compare.
    for model in (None, 5):
        event = StreamParser().feed(ev(type="system", subtype="init", session_id="s1",
                                       apiKeySource="none", model=model))[0]
        assert event.data == {"model": ""}


def test_model_ids_as_words():
    from thechatplace.claude_cli import model_spoken
    assert model_spoken("claude-sonnet-5-5") == "Sonnet 5.5"
    assert model_spoken("claude-haiku-4-5-20251001") == "Haiku 4.5"
    assert model_spoken("claude-opus-5-5[1m]") == "Opus 5.5"
    assert model_spoken("something-else") == "something-else"


def test_a_turn_on_an_unchosen_fable_is_stopped():
    from thechatplace.claude_cli import chosen_model, fable_problem
    assert chosen_model(["claude", "-p", "--model", "opus"]) == "opus"
    assert chosen_model(["claude", "-p"]) == ""
    assert fable_problem("claude-fable-5-1", "") == (
        "Claude Code would have used Fable, Claude Code's default model. Some plans bill Fable "
        "to usage credits, so The Chat Place didn't send your message. Choose another model "
        "with File, Change Model, then send again.")
    assert "instead of Opus, the model this session chose" in fable_problem(
        "claude-fable-5-1", "opus")
    assert "as soon as it started" in fable_problem("claude-fable-5-1", "", sent=True)
    assert fable_problem("claude-fable-5-1", "opus")  # a fallback to Fable: stopped too
    assert fable_problem("claude-fable-5-1", "claude-fable-5-1") is None  # chosen
    assert fable_problem("claude-opus-5-5", "") is None
    assert fable_problem("", "") is None


def test_the_message_waits_for_initialize_and_is_never_sent_to_a_fable_default(tmp_path):
    from thechatplace.claude_cli import INIT_REQUEST_ID
    answer = ev(type="control_response", response={
        "subtype": "success", "request_id": INIT_REQUEST_ID,
        "response": {"commands": [], "models": [
            {"value": "default", "resolvedModel": "claude-fable-5-1"},
            {"value": "opus", "resolvedModel": "claude-opus-5-5"}]}})
    process = FakeProcess([answer, ev(type="system", subtype="init", session_id="s1",
                                      apiKeySource="none", model="claude-fable-5-1")])
    runner, events, _ = run_turn(process, tmp_path)
    assert process.killed and runner.stopped_before_answer
    assert [e.kind for e in events] == ["failed"]
    assert "didn't send your message" in events[0].text
    assert b'"type": "user"' not in process.written  # the message never went
    # Opus chosen: initialize answered, then the message goes.
    process = FakeProcess([answer, ev(type="system", subtype="init", session_id="s1",
                                      apiKeySource="none", model="claude-opus-5-5"),
                           ev(type="result", subtype="success", result="ok")])
    _runner, events, _ = run_turn(process, tmp_path, command=["claude", "-p", "--model", "opus"])
    assert events[-1].kind == "finished"
    assert b'"type": "user"' in process.written


def test_an_old_claude_code_is_told_to_update():
    # Claude Code 2.1.258 on a Mac, which predates --permission-prompts.
    text = cli.exit_message(1, "error: unknown option '--permission-prompts'")
    assert text.startswith("Claude Code is too old for The Chat Place")
    assert "--permission-prompts" in text and "claude update" in text
    assert cli.exit_message(3, "boom") == \
        "Claude exited without finishing the turn (exit code 3). boom"
    assert cli.exit_message(3, "") == "Claude exited without finishing the turn (exit code 3)."


def _with_answer(events_list, runner_box):
    """on_event that answers a permission at once, recording whether it went."""
    def on_event(event):
        if event.kind == "permission":
            runner_box["answered"] = runner_box["runner"].respond(
                event.request.request_id, {"behavior": "allow", "updatedInput": {}})
    return on_event


def test_the_turn_ends_when_claude_code_says_idle_not_at_the_first_result(tmp_path):
    from thechatplace.claude_cli import TurnRunner
    process = FakeProcess([
        ev(type="system", subtype="session_state_changed", state="running"),
        ev(type="system", subtype="init", session_id="s1", apiKeySource="none"),
        # A background agent's notification answered first, as its own result.
        ev(type="result", subtype="success", result="noted",
           permission_denials=[{"tool_name": "Write", "tool_input": {"file_path": "a"}}]),
        ev(type="assistant", message={"content": [{"type": "text", "text": "Real reply."}]}),
        ev(type="control_request", request_id="q1", request={
            "subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "ls"},
            "tool_use_id": "t1"}),
        ev(type="result", subtype="success", result="Real reply."),
        ev(type="system", subtype="session_state_changed", state="idle"),
    ])
    events, box, done = [], {}, threading.Event()
    answer = _with_answer(events, box)

    def on_event(event):
        events.append(event)
        answer(event)
        if event.kind in ("finished", "failed"):
            done.set()
    captured = {}

    def popen(cmd, **kwargs):
        captured.update(kwargs)
        return process
    runner = TurnRunner(["claude", "-p"], str(tmp_path), "Hi", on_event, popen=popen,
                        env={"PATH": "x"})
    box["runner"] = runner
    runner.start()
    assert done.wait(5)
    kinds = [e.kind for e in events]
    assert kinds.count("finished") == 1 and kinds[-1] == "finished"
    assert events[-1].text == "Real reply."
    assert events[-1].denials  # the first result's refusal isn't lost
    assert box["answered"] is True  # stdin still open after the first result
    assert b"control_response" in process.written
    assert "state" not in kinds
    assert captured["env"]["CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS"] == "1"


def test_idle_before_the_result_still_ends_the_turn(tmp_path):
    process = FakeProcess([
        ev(type="system", subtype="session_state_changed", state="running"),
        ev(type="system", subtype="session_state_changed", state="idle"),
        ev(type="result", subtype="success", result="done"),
    ])
    _runner, events, _ = run_turn(process, tmp_path)
    assert [e.kind for e in events][-1] == "finished"


def test_no_idle_after_a_result_ends_at_the_ceiling(tmp_path, monkeypatch):
    from thechatplace import claude_cli
    monkeypatch.setattr(claude_cli, "IDLE_AFTER_RESULT_WAIT", 0.2)

    class Hanging(FakeProcess):
        """stdout stays open until stdin is closed, as the real CLI's does."""
        def __init__(self, lines):
            super().__init__([])
            closed = threading.Event()
            self.stdin.close = closed.set
            queue = [(line + "\n").encode("utf-8") for line in lines]

            class Out:
                def readline(self, *args):
                    if queue:
                        return queue.pop(0)
                    closed.wait(5)
                    return b""
            self.stdout = Out()
    process = Hanging([ev(type="system", subtype="session_state_changed", state="running"),
                       ev(type="result", subtype="success", result="done")])
    _runner, events, _ = run_turn(process, tmp_path)
    assert [e.kind for e in events][-1] == "finished"


def test_an_older_claude_code_without_state_events_ends_at_the_result(tmp_path):
    process = FakeProcess([
        ev(type="system", subtype="init", session_id="s1", apiKeySource="none"),
        ev(type="result", subtype="success", result="done"),
    ])
    _runner, events, _ = run_turn(process, tmp_path)
    assert [e.kind for e in events][-1] == "finished"


def test_remote_control_is_asked_for_after_initialize_and_answered(tmp_path):
    from thechatplace.claude_cli import RC_REQUEST_ID, remote_control_line
    line = json.loads(remote_control_line("Build", "cse_1"))
    assert line["request"] == {"subtype": "remote_control", "enabled": True, "name": "Build",
                               "keep_session_on_exit": True, "reattach_session_id": "cse_1"}
    assert "reattach_session_id" not in json.loads(remote_control_line("Build"))["request"]
    answer = ev(type="control_response", response={
        "subtype": "success", "request_id": RC_REQUEST_ID,
        "response": {"bridge_session_id": "cse_2", "session_url": "https://x"}})
    process = FakeProcess([answer, ev(type="system", subtype="init", session_id="s1",
                                      apiKeySource="none", model="claude-opus-5-5"),
                           ev(type="result", subtype="success", result="ok")])
    _runner, events, _ = run_turn(process, tmp_path, remote_control={"name": "Build",
                                                                      "reattach": ""})
    kinds = [e.kind for e in events]
    assert kinds[0] == "remote_control" and events[0].data["bridge_session_id"] == "cse_2"
    assert kinds[-1] == "finished"
    written = process.written.decode("utf-8").splitlines()
    assert json.loads(written[1])["request"]["subtype"] == "remote_control"
    assert json.loads(written[2])["type"] == "user"  # the message after both


def test_a_state_change_before_initialize_answers_doesnt_send_the_message(tmp_path):
    from thechatplace.claude_cli import INIT_REQUEST_ID
    answer = ev(type="control_response", response={
        "subtype": "success", "request_id": INIT_REQUEST_ID,
        "response": {"commands": [], "models": [
            {"value": "default", "resolvedModel": "claude-fable-5-1"}]}})
    process = FakeProcess([ev(type="system", subtype="session_state_changed", state="running"),
                           answer])
    runner, events, _ = run_turn(process, tmp_path)
    assert runner.stopped_before_answer
    assert b'"type": "user"' not in process.written


def test_a_permission_answered_elsewhere_is_dropped(tmp_path):
    process = FakeProcess([
        ev(type="control_request", request_id="q1", request={
            "subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "ls"},
            "tool_use_id": "t1"}),
        ev(type="control_cancel_request", request_id="q1"),
        ev(type="result", subtype="success", result="ok"),
    ])
    runner, events, _ = run_turn(process, tmp_path)
    kinds = [e.kind for e in events]
    assert kinds.index("permission") < kinds.index("permission_cancelled")
    assert runner.pending == {}


def test_send_now_interrupts_and_the_stopped_work_is_not_a_failure(tmp_path):
    from thechatplace.claude_cli import TurnRunner
    process = FakeProcess([
        ev(type="system", subtype="session_state_changed", state="running"),
        ev(type="system", subtype="init", session_id="s1", apiKeySource="none"),
        ev(type="result", subtype="error_during_execution", is_error=True, result=""),
        ev(type="assistant", message={"content": [{"type": "text", "text": "hi"}]}),
        ev(type="result", subtype="success", result="hi"),
        ev(type="system", subtype="session_state_changed", state="idle"),
    ])
    events, done = [], threading.Event()
    box = {}

    def on_event(event):
        events.append(event)
        if event.kind == "started" and "sent" not in box:
            box["sent"] = box["runner"].send_now("Instead, say hi")
        if event.kind in ("finished", "failed"):
            done.set()
    runner = TurnRunner(["claude", "-p"], str(tmp_path), "Write an essay", on_event,
                        popen=lambda cmd, **k: process, env={"PATH": "x"})
    box["runner"] = runner
    runner.start()
    assert done.wait(5)
    assert box["sent"] is True
    assert events[-1].kind == "finished" and not events[-1].is_error
    written = process.written.decode("utf-8")
    assert '"subtype": "interrupt"' in written and "Instead, say hi" in written


def _send_now_turn(tmp_path, lines, when="started"):
    """A turn whose on_event sends a message now at the first ``when`` event,
    and answers any permission, recording whether the answer got through."""
    from thechatplace.claude_cli import TurnRunner
    process = FakeProcess(lines)
    events, done, box = [], threading.Event(), {}

    def on_event(event):
        events.append(event)
        if event.kind == when and "sent" not in box:
            box["sent"] = box["runner"].send_now("Instead, say hi")
        if event.kind == "permission":
            box["answered"] = box["runner"].respond(
                event.request.request_id, {"behavior": "allow", "updatedInput": {}})
        if event.kind in ("finished", "failed"):
            done.set()
    runner = TurnRunner(["claude", "-p"], str(tmp_path), "Write an essay", on_event,
                        popen=lambda cmd, **k: process, env={"PATH": "x"})
    box["runner"] = runner
    runner.start()
    assert done.wait(5)
    return events, box, process


def test_a_real_error_after_send_now_still_fails_the_turn(tmp_path):
    events, box, _ = _send_now_turn(tmp_path, [
        ev(type="system", subtype="session_state_changed", state="running"),
        ev(type="system", subtype="init", session_id="s1", apiKeySource="none"),
        # Nothing to interrupt: no error result; the new message's turn fails.
        ev(type="result", subtype="error_max_turns", is_error=True, result="Too many turns"),
        ev(type="system", subtype="session_state_changed", state="idle"),
    ])
    assert box["sent"] is True
    assert events[-1].kind == "finished" and events[-1].is_error


def test_idle_between_the_stopped_work_and_the_new_message_keeps_the_turn_open(tmp_path):
    events, box, process = _send_now_turn(tmp_path, [
        ev(type="system", subtype="session_state_changed", state="running"),
        ev(type="system", subtype="init", session_id="s1", apiKeySource="none"),
        ev(type="result", subtype="error_during_execution", is_error=True, result=""),
        ev(type="system", subtype="session_state_changed", state="idle"),
        ev(type="control_response", response={"subtype": "success",
                                               "request_id": "thechatplace-interrupt-x"}),
        ev(type="system", subtype="session_state_changed", state="running"),
        ev(type="control_request", request_id="q1", request={
            "subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "ls"},
            "tool_use_id": "t1"}),
        ev(type="result", subtype="success", result="hi"),
        ev(type="system", subtype="session_state_changed", state="idle"),
    ])
    assert box["answered"] is True  # stdin still open for the new work
    assert events[-1].kind == "finished" and events[-1].text == "hi"
    assert not events[-1].is_error


def test_send_now_refused_while_stopping(tmp_path):
    from thechatplace.claude_cli import TurnRunner
    runner = TurnRunner(["claude", "-p"], str(tmp_path), "x", lambda e: None,
                        popen=lambda cmd, **k: FakeProcess([]), env={"PATH": "x"})
    runner._stdin_open = True
    runner._cancelled = True
    assert runner.send_now("now") is False


# -- Send Now: which result answers which message (#83) ---------------------------------


class FakeTimers:
    """Stands in for threading.Timer: nothing fires by itself; a test fires
    the live ones when it likes, so no test waits out a real ceiling."""

    def __init__(self):
        self.made = []
        self.ceiling_armed = threading.Event()

    def __call__(self, interval, function):
        timers = self

        class Timer:
            daemon = False

            def __init__(self):
                self.interval, self.function = interval, function
                self.started = self.cancelled = False

            def start(self):
                self.started = True
                if self.interval == cli.IDLE_AFTER_RESULT_WAIT:
                    timers.ceiling_armed.set()

            def cancel(self):
                self.cancelled = True
        timer = Timer()
        self.made.append(timer)
        return timer

    def live_ceilings(self):
        return [t for t in self.made if t.started and not t.cancelled
                and t.interval == cli.IDLE_AFTER_RESULT_WAIT]

    def fire_ceilings(self):
        """What 600 s of silence would do."""
        for timer in self.live_ceilings():
            timer.cancelled = True
            timer.function()


class LiveProcess(FakeProcess):
    """Like the real CLI: stdout stays open until stdin is closed. A
    threading.Event among the lines holds stdout there until it's set."""

    def __init__(self, lines):
        super().__init__([])
        self.stdin_closed = threading.Event()
        self.reading = threading.Event()
        queue = [x if isinstance(x, threading.Event) else (x + "\n").encode("utf-8")
                 for x in lines]
        close = self.stdin.close

        def close_and_signal():
            close()
            self.stdin_closed.set()
        self.stdin.close = close_and_signal
        process = self

        class Out:
            def readline(self, *args):
                process.reading.set()
                while queue:
                    item = queue.pop(0)
                    if isinstance(item, threading.Event):
                        assert item.wait(5)
                        continue
                    return item
                process.stdin_closed.wait(5)
                return b""
        self.stdout = Out()

    def sent(self):
        data = self.written if self.written is not None else self.stdin.getvalue()
        return [json.loads(line) for line in data.decode("utf-8").splitlines()]


def _live_send_now_turn(tmp_path, lines, when="started", on_text=None):
    """A turn against a LiveProcess with fake timers. Sends a message now at
    the first ``when`` event, answers any permission (recording whether the
    answer got through) and calls ``on_text(box)`` at each text event."""
    process = LiveProcess(lines)
    timers = FakeTimers()
    events, done, box = [], threading.Event(), {}

    def on_event(event):
        events.append(event)
        if event.kind == when and "sent" not in box:
            box["sent"] = box["runner"].send_now("Instead, say hi")
        if event.kind == "text" and on_text:
            on_text(box)
        if event.kind == "permission":
            box["answered"] = box["runner"].respond(
                event.request.request_id, {"behavior": "allow", "updatedInput": {}})
        if event.kind in ("finished", "failed"):
            done.set()
    runner = TurnRunner(["claude", "-p"], str(tmp_path), "Write an essay", on_event,
                        popen=lambda cmd, **k: process, env={"PATH": "x"}, timer=timers)
    box["runner"], box["timers"], box["done"] = runner, timers, done
    runner.start()
    return events, box, process


RUNNING = ev(type="system", subtype="session_state_changed", state="running")
IDLE = ev(type="system", subtype="session_state_changed", state="idle")
INIT = ev(type="system", subtype="init", session_id="s1", apiKeySource="none")
STOPPED = ev(type="result", subtype="error_during_execution", is_error=True, result="")
ASK = ev(type="control_request", request_id="q1", request={
    "subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "ls"},
    "tool_use_id": "t1"})


def test_an_error_answering_the_send_now_message_is_shown_and_ends_the_turn(tmp_path):
    # #83 item 1: the new message's own result is error_during_execution too.
    events, box, _ = _live_send_now_turn(tmp_path, [
        RUNNING, INIT, STOPPED, RUNNING,
        ev(type="result", subtype="error_during_execution", is_error=True,
           result="It broke"),
        IDLE])
    assert box["done"].wait(5), "the turn waited for the idle ceiling"
    assert box["sent"] is True
    assert events[-1].kind == "finished" and events[-1].is_error


def test_a_long_answer_to_send_now_keeps_stdin_open(tmp_path):
    # #83 item 2: no 600 s ceiling while Claude works on the new message.
    def on_text(box):
        box["live"] = len(box["timers"].live_ceilings())
        box["timers"].fire_ceilings()
    events, box, _ = _live_send_now_turn(tmp_path, [
        RUNNING, INIT, STOPPED, RUNNING,
        ev(type="assistant", message={"content": [{"type": "text", "text": "Hi, so"}]}),
        ASK, ev(type="result", subtype="success", result="hi"), IDLE], on_text=on_text)
    assert box["done"].wait(5)
    assert box["live"] == 0
    assert box["answered"] is True
    assert events[-1].kind == "finished" and events[-1].text == "hi"
    assert not events[-1].is_error


def test_a_ceiling_armed_before_send_now_doesnt_cut_the_new_answer(tmp_path):
    def on_text(box):
        if "fired" not in box:
            box["fired"] = True
            box["timers"].fire_ceilings()
    events, box, _ = _live_send_now_turn(tmp_path, [
        RUNNING, INIT,
        ev(type="result", subtype="success", result="first"),
        # Still running after the result (a hook, say): the ceiling is armed.
        ev(type="assistant", message={"content": [{"type": "text", "text": "more"}]}),
        ASK, ev(type="result", subtype="success", result="second"), IDLE],
        when="text", on_text=on_text)
    assert box["done"].wait(5)
    assert box["sent"] is True and box["answered"] is True
    assert events[-1].text == "second"


def test_the_stopped_works_late_success_doesnt_count_as_the_answer(tmp_path):
    # #83 item 3: the old work finished just as Send Now went, with a success
    # result of its own; the turn must still wait for the new message's.
    events, box, _ = _live_send_now_turn(tmp_path, [
        RUNNING, INIT,
        ev(type="result", subtype="success", result="old answer"),
        IDLE, RUNNING, ASK,
        ev(type="result", subtype="success", result="new answer"), IDLE])
    assert box["done"].wait(5)
    assert box["sent"] is True
    assert box["answered"] is True
    assert events[-1].kind == "finished" and events[-1].text == "new answer"


def test_send_now_before_the_prompt_goes_after_it(tmp_path):
    # #83 item 4: pressed while initialize's answer is awaited.
    from thechatplace.claude_cli import INIT_REQUEST_ID
    gate = threading.Event()
    answer = ev(type="control_response", response={
        "subtype": "success", "request_id": INIT_REQUEST_ID,
        "response": {"commands": [], "models": [
            {"value": "default", "resolvedModel": "claude-opus-5-5"}]}})
    events, box, process = _live_send_now_turn(tmp_path, [
        gate, answer, RUNNING, INIT, STOPPED,
        ev(type="result", subtype="success", result="hi"), IDLE], when="never")
    assert process.reading.wait(5)
    assert box["runner"].send_now("Instead, say hi") is True
    assert [m["type"] for m in process.sent()] == ["control_request"]  # initialize only
    gate.set()
    assert box["done"].wait(5)
    sent = process.sent()
    assert [m.get("request", {}).get("subtype") or m["message"]["content"] for m in sent] \
        == ["initialize", "Write an essay", "interrupt", "Instead, say hi"]
    assert events[-1].kind == "finished" and events[-1].text == "hi"
    assert not events[-1].is_error


def test_idle_with_the_send_now_message_unanswered_still_ends_at_the_ceiling(tmp_path):
    # Claude Code went idle and never answered: the ceiling still ends the
    # turn, as it always did, rather than leaving it open for ever.
    events, box, process = _live_send_now_turn(tmp_path, [RUNNING, INIT, STOPPED, IDLE])
    assert box["timers"].ceiling_armed.wait(5)
    assert not process.stdin_closed.is_set()
    box["timers"].fire_ceilings()
    assert box["done"].wait(5)
    assert events[-1].kind == "finished"


def test_two_send_nows_in_a_row_wait_for_the_second_answer(tmp_path):
    def twice(box):
        if "second" not in box:
            box["second"] = box["runner"].send_now("No, say bye")
    events, box, _ = _live_send_now_turn(tmp_path, [
        RUNNING, INIT, STOPPED,
        ev(type="assistant", message={"content": [{"type": "text", "text": "h"}]}),
        STOPPED, IDLE, RUNNING, ASK,
        ev(type="result", subtype="success", result="bye"), IDLE], on_text=twice)
    assert box["done"].wait(5)
    assert box["sent"] is True and box["second"] is True
    assert box["answered"] is True
    assert events[-1].text == "bye" and not events[-1].is_error


def test_send_now_after_the_turn_ended_is_refused(tmp_path):
    events, box, _ = _live_send_now_turn(tmp_path, [
        RUNNING, INIT, ev(type="result", subtype="success", result="done"), IDLE],
        when="never")
    assert box["done"].wait(5)
    assert box["runner"].send_now("too late") is False


# -- Background work keeps the turn open (#161) ------------------------------------------


def _tasks(*names):
    """background_tasks_changed as Claude Code 2.1.296 sends it."""
    return ev(type="system", subtype="background_tasks_changed",
              tasks=[{"task_id": f"b{i}", "task_type": "local_bash", "description": name}
                     for i, name in enumerate(names)])


def _background_turn(tmp_path, lines, on_event=None):
    process = LiveProcess(lines)
    timers = FakeTimers()
    events, done, box = [], threading.Event(), {}

    def record(event):
        events.append(event)
        if on_event:
            on_event(event, box)
        if event.kind in ("finished", "failed"):
            done.set()
    runner = TurnRunner(["claude", "-p"], str(tmp_path), "Build it", record,
                        popen=lambda cmd, **k: process, env={"PATH": "x"}, timer=timers)
    box["runner"], box["timers"], box["done"] = runner, timers, done
    runner.start()
    return events, box, process


def _wait_for(condition):
    for _ in range(250):
        if condition():
            return True
        threading.Event().wait(0.02)
    return False


def test_the_parser_reads_the_background_task_list():
    parser = StreamParser()
    [event] = parser.feed(_tasks("Windows build", "Watch CI"))
    assert event.kind == "background"
    assert event.data == {"tasks": ["Windows build", "Watch CI"]}
    assert parser.feed(_tasks())[0].data == {"tasks": []}
    odd = ev(type="system", subtype="background_tasks_changed", tasks=[{}, "x"])
    assert parser.feed(odd)[0].data == {"tasks": ["a background task"]}
    assert parser.feed(ev(type="system", subtype="background_tasks_changed"))[0].data == \
        {"tasks": []}


def test_ambient_background_tasks_arent_work():
    """Watchers and tasks kept out of the transcript are ``ambient``: Claude
    Code says they aren't activity, so they mustn't keep a turn open."""
    parser = StreamParser()
    event = ev(type="system", subtype="background_tasks_changed", tasks=[
        {"task_id": "w", "task_type": "monitor", "description": "Watch PR", "ambient": True},
        {"task_id": "b", "task_type": "local_bash", "description": "Build"}])
    assert parser.feed(event)[0].data == {"tasks": ["Build"]}
    only_ambient = ev(type="system", subtype="background_tasks_changed", tasks=[
        {"task_id": "w", "description": "Watch PR", "ambient": True}])
    assert parser.feed(only_ambient)[0].data == {"tasks": []}


def test_background_work_keeps_the_turn_open_until_claude_carries_on(tmp_path):
    # As Claude Code 2.1.296 does it: the reply, idle with the work still
    # running, then, when it finishes, a turn of its own (#161).
    gate = threading.Event()
    seen = {}

    def on_event(event, box):
        if event.kind == "waiting":
            seen["waiting"] = box["runner"].waiting_on_background
            seen["activity"] = box["runner"].last_activity
        if event.kind == "text" and event.text == "Build passed.":
            # Working on what the build brought back: no ceiling, however long.
            seen["ceilings"] = len(box["timers"].live_ceilings())
    events, box, process = _background_turn(tmp_path, [
        RUNNING, INIT, _tasks("Windows build"),
        ev(type="result", subtype="success", result="Started the build."),
        IDLE, gate, _tasks(), RUNNING, INIT,
        ev(type="assistant", message={"content": [{"type": "text", "text": "Build passed."}]}),
        ev(type="result", subtype="success", result="Build passed."), IDLE], on_event)
    # Claude answered and went idle, but the build is still running.
    assert _wait_for(lambda: any(e.kind == "waiting" for e in events))
    [waiting] = [e for e in events if e.kind == "waiting"]
    assert waiting.text == "Started the build."
    assert waiting.data == {"tasks": ["Windows build"]}
    assert seen["waiting"] is True
    assert seen["activity"] == "waiting for background work: Windows build"
    assert not process.stdin_closed.is_set()
    assert not box["timers"].live_ceilings()  # no limit on how long a build takes
    assert not box["done"].is_set()
    gate.set()
    assert box["done"].wait(5)
    assert events[-1].kind == "finished" and events[-1].text == "Build passed."
    assert not events[-1].is_error
    assert seen["ceilings"] == 0
    assert process.stdin_closed.is_set()
    assert box["runner"].waiting_on_background is False


def test_a_message_sent_while_waiting_on_background_work_goes_straight_in(tmp_path):
    gate = threading.Event()

    def on_event(event, box):
        if event.kind == "waiting" and "follow" not in box:
            box["follow"] = box["runner"].send_follow_up("Meanwhile, what is 2+2?")
            box["again"] = box["runner"].send_follow_up("twice")  # not waiting now
    events, box, process = _background_turn(tmp_path, [
        RUNNING, INIT, _tasks("Watch CI"),
        ev(type="result", subtype="success", result="Watching CI."), IDLE,
        RUNNING, ev(type="result", subtype="success", result="4"), IDLE,
        gate, _tasks(), RUNNING, ev(type="result", subtype="success", result="CI passed."),
        IDLE], on_event)
    assert _wait_for(lambda: sum(e.kind == "waiting" for e in events) == 2)
    assert box["follow"] is True and box["again"] is False
    # The answer to the message is said too, with the work still running.
    assert [e.text for e in events if e.kind == "waiting"] == ["Watching CI.", "4"]
    sent = [m for m in process.sent() if m.get("type") == "user"]
    assert len(sent) == 2 and "2+2" in json.dumps(sent[1])
    assert not any(m.get("request", {}).get("subtype") == "interrupt"
                   for m in process.sent())  # nothing to interrupt
    assert not process.stdin_closed.is_set()
    gate.set()
    assert box["done"].wait(5)
    assert events[-1].kind == "finished" and events[-1].text == "CI passed."


def test_send_follow_up_is_refused_unless_waiting_on_background_work(tmp_path):
    events, box, process = _background_turn(tmp_path, [
        RUNNING, INIT, ev(type="result", subtype="success", result="done"), IDLE])
    assert box["done"].wait(5)
    assert box["runner"].send_follow_up("late") is False
    assert "waiting" not in [e.kind for e in events]
    runner = TurnRunner(["claude", "-p"], str(tmp_path), "x", lambda e: None,
                        popen=lambda cmd, **k: FakeProcess([]), env={"PATH": "x"})
    runner._stdin_open = True
    runner.waiting_on_background = True
    runner._cancelled = True
    assert runner.send_follow_up("stopping") is False


def test_background_work_done_with_no_turn_after_it_still_ends_the_turn(tmp_path):
    # Claude Code always starts a turn when the work finishes; if it ever
    # doesn't, the turn ends after a short wait rather than staying open.
    events, box, process = _background_turn(tmp_path, [
        RUNNING, INIT, _tasks("Sleep"),
        ev(type="result", subtype="success", result="Sleeping."), IDLE, _tasks()])
    timers = box["timers"]

    def short_waits():
        return [t for t in timers.made if t.started and not t.cancelled
                and t.interval == cli.BACKGROUND_DONE_WAIT]
    assert _wait_for(short_waits)
    assert not process.stdin_closed.is_set()
    for timer in short_waits():
        timer.cancelled = True
        timer.function()
    assert box["done"].wait(5)
    # Said when Claude went idle; not said again.
    assert events[-1].kind == "finished" and events[-1].text == ""
    assert not events[-1].is_error
    assert events[-1].data == {"after_background": True}  # not notified again


def test_an_old_claude_code_without_the_task_list_ends_at_idle_as_before(tmp_path):
    events, box, process = _background_turn(tmp_path, [
        RUNNING, INIT, ev(type="result", subtype="success", result="Started."), IDLE])
    assert box["done"].wait(5)
    assert [e.kind for e in events][-1] == "finished"


def test_idle_before_the_result_with_background_work_still_waits(tmp_path):
    gate = threading.Event()
    events, box, process = _background_turn(tmp_path, [
        RUNNING, INIT, _tasks("Build"), IDLE,
        ev(type="result", subtype="success", result="Started."), gate,
        _tasks(), RUNNING, ev(type="result", subtype="success", result="Built."), IDLE])
    assert _wait_for(lambda: any(e.kind == "waiting" for e in events))
    assert not process.stdin_closed.is_set()
    gate.set()
    assert box["done"].wait(5)
    assert events[-1].text == "Built."


def test_refusals_said_while_waiting_are_not_said_again_at_the_end(tmp_path):
    gate = threading.Event()
    events, box, _ = _background_turn(tmp_path, [
        RUNNING, INIT, _tasks("Build"),
        ev(type="result", subtype="success", result="Started.",
           permission_denials=[{"tool_name": "Write", "tool_input": {"file_path": "a"}}]),
        IDLE, gate, _tasks(), RUNNING,
        ev(type="result", subtype="success", result="Built."), IDLE])
    assert _wait_for(lambda: any(e.kind == "waiting" for e in events))
    [waiting] = [e for e in events if e.kind == "waiting"]
    assert waiting.denials
    gate.set()
    assert box["done"].wait(5)
    assert events[-1].denials == [] and not events[-1].is_error


def test_stop_while_waiting_on_background_work_says_stopped(tmp_path):
    gate = threading.Event()
    events, box, process = _background_turn(tmp_path, [
        RUNNING, INIT, _tasks("Build"),
        ev(type="result", subtype="success", result="Started."), IDLE, gate])
    assert _wait_for(lambda: any(e.kind == "waiting" for e in events))
    box["runner"].cancel()
    assert process.killed
    gate.set()
    process.stdin_closed.set()  # the killed CLI's output ends
    assert box["done"].wait(5)
    # Not the reply again: it was said already, and Stop ended the work.
    assert events[-1].kind == "failed" and events[-1].text == "Stopped."


def test_claude_code_dying_while_waiting_on_background_work_is_a_failure(tmp_path):
    process = FakeProcess([
        RUNNING, INIT, _tasks("Build"),
        ev(type="result", subtype="success", result="Started."), IDLE],
        returncode=3, stderr_lines=["crashed"])
    _runner, events, _ = run_turn(process, tmp_path)
    assert [e.kind for e in events if e.kind == "waiting"] == ["waiting"]
    assert events[-1].kind == "failed" and events[-1].is_error
    assert "exit code 3" in events[-1].text and "crashed" in events[-1].text


def test_an_empty_result_after_the_answer_keeps_the_answer(tmp_path):
    # After a background agent finishes, Claude Code sends the follow-up's
    # answer and then a result with no text (seen on 2.1.296).
    process = FakeProcess([
        RUNNING, INIT, ev(type="result", subtype="success", result="Agent started."),
        INIT, ev(type="result", subtype="success", result="DONE"),
        INIT, ev(type="result", subtype="success", result=""), IDLE])
    _runner, events, _ = run_turn(process, tmp_path)
    assert events[-1].kind == "finished" and events[-1].text == "DONE"
    # A real error with no text still stands as the error.
    process = FakeProcess([
        RUNNING, INIT, ev(type="result", subtype="success", result="DONE"),
        ev(type="result", subtype="error_during_execution", is_error=True, result=""), IDLE])
    _runner, events, _ = run_turn(process, tmp_path)
    assert events[-1].is_error and events[-1].text != "DONE"



def test_effort_goes_on_every_turn():
    """#189: --effort, like --model, is given to each turn's new process."""
    new = build_new_command(EXE, "aaaa-1111", "t", "auto", effort="high")
    resumed = build_resume_command(EXE, "aaaa-1111", "auto", OWN, DESKTOP, effort="max")
    fork = cli.build_fork_command(EXE, "bbbb-2222", "cccc-3333", "t", "auto", effort="low")
    assert new[new.index("--effort") + 1] == "high"
    assert resumed[resumed.index("--effort") + 1] == "max"
    assert fork[fork.index("--effort") + 1] == "low"
    assert "--effort" not in build_resume_command(EXE, "aaaa-1111", "auto", OWN, DESKTOP)


@pytest.mark.parametrize("effort", ["--help", "huge", "HIGH", " high"])
def test_unknown_effort_refused(effort):
    with pytest.raises(ValueError):
        build_new_command(EXE, "aaaa-1111", "t", "auto", effort=effort)


def test_effort_and_mode_labels():
    assert [value for value, _label in cli.EFFORTS] == ["", "low", "medium", "high", "xhigh",
                                                         "max"]
    assert cli.effort_label("") == "the default effort"
    assert cli.effort_label("xhigh") == "extra high effort"
    assert cli.mode_label("acceptEdits") == "accept edits"
    assert cli.mode_label("default") == "manual"
    assert cli.mode_label("plan") == "plan"


@pytest.mark.parametrize('mode,live', [('compact', True), ('compact-no-boundary', False)])
def test_command_only_compaction_finishes_with_or_without_stream_boundary(tmp_path, mode, live):
    from thechatplace.transcript import read_transcript, COMPACTION, COMMAND_OUTPUT
    events, seen = run_real(tmp_path, '/compact', mode=mode)
    assert events[-1].kind == 'finished' and not events[-1].is_error
    assert not events[-1].text and not any(e.kind == 'text' for e in events)
    compactions = [e for e in events if e.kind == 'compacted']
    assert bool(compactions) is live
    if live:
        assert '968,000 to 9,000' in compactions[0].text
        assert compactions[0].data == {'post_tokens': 9000, 'message_key': 'synthetic-compact'}
    transcript = read_transcript(tmp_path / 'synthetic-compact.jsonl')
    assert [m.kind for m in transcript.visible()] == [COMPACTION, COMMAND_OUTPUT]


def test_local_command_result_can_echo_the_transcript_output_without_assistant_text(tmp_path):
    from thechatplace.transcript import read_transcript, clean_command_output, MAX_COMMAND_OUTPUT
    events, seen = run_real(tmp_path, '/cost', mode='command-result')
    assert events[-1].kind == 'finished' and not events[-1].is_error
    assert not any(event.kind == 'text' for event in events)
    output = read_transcript(tmp_path / 'synthetic-command.jsonl').visible()[0]
    assert output.text == clean_command_output(events[-1].text)
    assert len(output.text) <= MAX_COMMAND_OUTPUT and '\x1b' not in output.text


# -- What Claude Code 2.1 really suggests (#332) ---------------------------------------------
# Shapes captured from real `claude -p` (manual mode, the app's own flags); the
# paths here are synthetic. A command that writes, such as mkdir, comes with a
# rule, the folder it writes in, and a switch to accept edits.


def _captured_mkdir(folder):
    return cli.PermissionRequest("r1", "Bash", {"command": "mkdir capture-dir"}, suggestions=[
        {"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "mkdir capture-dir *"}],
         "behavior": "allow", "destination": "localSettings"},
        {"type": "addDirectories", "directories": [folder], "destination": "session"},
        {"type": "setMode", "mode": "acceptEdits", "destination": "session"}])


def test_a_suggested_rule_comes_before_a_mode_switch_and_keeps_its_folder(tmp_path):
    folder = str(tmp_path)
    request = _captured_mkdir(folder)
    assert request.kept_for_chat() == (["Bash(mkdir capture-dir *)"], [folder], "")
    assert request.allow_for_session_label() == (
        f"Allow, and don't ask again in this chat for Bash(mkdir capture-dir *), "
        f"and let Claude work in the folder {folder}")
    updates = cli.allow_response(request, for_session=True)["updatedPermissions"]
    assert updates == [
        {"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "mkdir capture-dir *"}],
         "behavior": "allow", "destination": "session"},
        {"type": "addDirectories", "directories": [folder], "destination": "session"}]


def test_a_mode_switch_only_without_a_rule_and_a_folder_only_without_either(tmp_path):
    folder = str(tmp_path)
    write = cli.PermissionRequest("r2", "Write", {"file_path": "x"}, suggestions=[
        {"type": "setMode", "mode": "acceptEdits", "destination": "session"}])
    assert write.kept_for_chat() == ([], [], "acceptEdits")
    assert cli.allow_response(write, for_session=True)["updatedPermissions"] == [
        {"type": "setMode", "mode": "acceptEdits", "destination": "session"}]
    both = cli.PermissionRequest("r3", "PowerShell", {"command": "mkdir x"}, suggestions=[
        {"type": "addDirectories", "directories": [folder], "destination": "session"},
        {"type": "setMode", "mode": "acceptEdits", "destination": "session"}])
    # A folder alone doesn't stop the next turn asking; the mode does.
    assert both.kept_for_chat() == ([], [], "acceptEdits")
    only = cli.PermissionRequest("r4", "PowerShell", {"command": "mkdir x"}, suggestions=[
        {"type": "addDirectories", "directories": [folder], "destination": "session"}])
    assert only.allow_for_session_label() == f"Allow, and let Claude work in the folder {folder}"


def test_suggested_folders_that_could_read_as_a_flag_are_dropped(tmp_path):
    request = cli.PermissionRequest("r5", "Bash", {"command": "mkdir x"}, suggestions=[
        {"type": "addDirectories", "destination": "session",
         "directories": ["--dangerously-skip-permissions", "relative", "", 7, str(tmp_path) + "\nx",
                         str(tmp_path), str(tmp_path)]}])
    assert request.session_dirs() == [str(tmp_path)]


def test_kept_folders_go_to_every_turn_after_the_rules(tmp_path):
    folder = str(tmp_path)
    new = cli.build_new_command("claude.exe", "11111111-1111-1111-1111-111111111111", "T", "manual",
                                allowed_tools=["Bash(mkdir capture-dir *)"],
                                allowed_dirs=[folder, "-x"])
    start = new.index("--allowedTools")
    assert new[start:start + 4] == ["--allowedTools", "Bash(mkdir capture-dir *)", "--add-dir", folder]
    assert new[start + 4] == "--session-id"
    resumed = cli.build_resume_command("claude.exe", "own-1", "manual", own_ids={"own-1"},
                                       desktop_ids=set(), allowed_dirs=[folder])
    assert resumed[resumed.index("--add-dir") + 1] == folder
    assert "--add-dir" not in cli.build_new_command(
        "claude.exe", "11111111-1111-1111-1111-111111111111", "T", "manual")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path forms")
def test_kept_folders_are_specific_folders_on_windows():
    for wide in ("C:\\", "C:", "C:folder", "\\\\server", "\\\\server\\share",
                 "\\\\.\\pipe\\x", "\\\\?\\C:\\work", "/c/Users/k"):
        assert not cli.is_safe_dir(wide), wide
    for folder in ("C:\\work", "C:\\Users\\k\\.worktrees", "\\\\server\\share\\work"):
        assert cli.is_safe_dir(folder), folder


def test_a_kept_folder_on_a_mac_is_below_the_root(monkeypatch):
    monkeypatch.setattr(platform_paths.sys, "platform", "darwin")
    assert not cli.is_safe_dir("/") and not cli.is_safe_dir("relative")
    assert cli.is_safe_dir("/Users/k/work")


def test_kellys_mkdir_request_names_its_folder_in_windows_form(tmp_path):
    """Captured: for ``mkdir -p /c/Users/.../.worktrees/x`` Claude Code 2.1.296
    suggested the folder as ``C:\\Users\\...\\.worktrees``, not ``/c/...``,
    so it is kept (#334)."""
    folder = str(tmp_path / ".worktrees")
    request = _captured_mkdir(folder)
    assert request.session_dirs() == [folder]



def test_an_edit_tool_reports_its_file():
    """An agent conversation lists the files a Claude turn edited (#248)."""
    parser = StreamParser()
    events = []
    for name, tool_input in [("Edit", {"file_path": "C:/w/a.py", "old_string": "x"}),
                             ("NotebookEdit", {"notebook_path": "C:/w/n.ipynb"}),
                             ("Bash", {"command": "ls", "file_path": "C:/w/not-an-edit"}),
                             ("Write", {"file_path": 7})]:
        events.extend(parser.feed(ev(type="assistant", session_id="s1", parent_tool_use_id=None,
                                     message={"content": [{"type": "tool_use", "name": name,
                                                           "input": tool_input}]})))
    assert [(e.text, e.data) for e in events] == [
        ("Edit", {"file_path": "C:/w/a.py"}), ("NotebookEdit", {"file_path": "C:/w/n.ipynb"}),
        ("Bash", None), ("Write", None)]
