"""Reporting a bug (#28)."""
import sys
import urllib.parse

from thechatplace import bugreport
from thechatplace.speech import SpeechSettings


def _report(**overrides):
    values = dict(summary="F6 skips the reply box", what_happened="It went to the status bar.",
                  expected="The reply box", steps="1. Load a session\n2. Press F6 twice",
                  environment=[("The Chat Place", "0.1.0"), ("Windows", "11")])
    values.update(overrides)
    return bugreport.BugReport(**values)


def test_report_text_has_each_section_and_the_environment():
    text = bugreport.report_text(_report())
    assert text.startswith("### What happened\nIt went to the status bar.\n")
    assert "### What I expected\nThe reply box" in text
    assert "### Steps to reproduce\n1. Load a session\n2. Press F6 twice" in text
    assert "### Environment\n- The Chat Place: 0.1.0\n- Windows: 11" in text


def test_optional_sections_are_left_out_when_empty():
    text = bugreport.report_text(_report(expected=" ", steps=""))
    assert "What I expected" not in text and "Steps to reproduce" not in text


def test_new_issue_url_is_prefilled_and_cut_to_fit():
    url = bugreport.new_issue_url(_report())
    assert url.startswith(f"https://github.com/{bugreport.REPO}/issues/new?")
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert query["title"] == ["F6 skips the reply box"] and query["labels"] == ["bug"]
    for words in ("x " * 5000, "é" * 4000):  # non-ASCII grows a lot when encoded
        long = bugreport.new_issue_url(_report(what_happened=words))
        query = urllib.parse.urlparse(long).query
        body = urllib.parse.parse_qs(query)["body"][0]
        assert len(urllib.parse.quote(body)) <= bugreport.MAX_URL_BODY
        assert "clipboard" in body and "### Environment" in body  # the environment survives


def test_environment_names_no_people_or_paths():
    counts = {"Claude": {"desktop app": 3, "Chat Place": 1, "terminal": 0}}
    facts = bugreport.environment(SpeechSettings(), counts,
                                  claude_version="2.1.286 (Claude Code)")
    labels = [label for label, _value in facts]
    system = {"win32": "Windows", "darwin": "macOS", "linux": "Linux"}.get(sys.platform, "System")
    assert labels[:7] == ["The Chat Place", system, "Python", "wxPython", "Claude Code",
                          "Codex", "Copilot CLI"]
    assert ("Sessions listed", "Claude: 3 desktop app, 1 Chat Place, 0 terminal") in facts
    text = " ".join(str(value) for _label, value in facts)
    assert "Users" not in text and "\\" not in text


class _Out:
    def __init__(self, stdout):
        self.stdout, self.stderr = stdout, ""


def test_only_a_version_number_is_kept(monkeypatch):
    import subprocess
    for finder in ("claude_executable", "find_codex", "find_copilot"):
        monkeypatch.setattr(bugreport.platform_paths, finder, lambda: "cli.exe")
    for provider, printed, kept in (
            ("claude", "2.1.286 (Claude Code)\n", "2.1.286 (Claude Code)"),
            ("claude", "Error: C:\\Users\\kelly\\x not found", "unknown"),
            ("codex", "codex-cli 0.163.0\n", "0.163.0"),
            ("codex", "codex-cli 0.163.0-alpha.2\n", "0.163.0-alpha.2"),
            # #464: a provider whose --version prints a path is "unknown".
            ("codex", "codex-cli C:\\Users\\kelly\\.codex\\bin", "unknown"),
            ("codex", "error: /home/kelly/.codex/config.toml is invalid", "unknown"),
            ("copilot", "GitHub Copilot CLI 1.0.105.\nRun 'copilot update' to check for "
                        "updates.\n", "1.0.105"),
            ("copilot", "GitHub Copilot CLI 1.0.105\n", "1.0.105"),
            ("copilot", "Couldn't read /Users/kelly/.copilot/config.json", "unknown"),
            ("copilot", "GitHub Copilot CLI 1.0 /Users/kelly", "unknown"),
            ("claude", "", "unknown")):
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Out(printed))
        assert bugreport.cli_version(provider) == kept, (provider, printed)
    assert bugreport.claude_code_version() == "unknown"  # the last answer was ""


def test_a_provider_not_installed_says_not_found(monkeypatch):
    monkeypatch.setattr(bugreport.platform_paths, "find_codex", lambda: None)
    assert bugreport.cli_version("codex") == "not found"
    monkeypatch.setattr(bugreport.platform_paths, "find_copilot",
                        lambda: (_ for _ in ()).throw(OSError("C:\\Users\\x")))
    assert bugreport.cli_version("copilot") == "unknown"


def test_each_provider_has_a_line_with_version_state_and_sign_in():
    """#463 gave Claude Code's version only: now each provider says its
    version, whether it's on, and whether it's signed in (#464)."""
    settings = SpeechSettings(codex_enabled=True, copilot_enabled=True)
    facts = dict(bugreport.environment(
        settings, {}, versions={"claude": "2.1.286 (Claude Code)", "codex": "0.163.0",
                                "copilot": "1.0.105"},
        sign_ins={"claude": "signed in (Max plan)", "codex": "not signed in",
                  "copilot": "signed in"}))
    assert facts["Claude Code"] == "2.1.286 (Claude Code); signed in (Max plan)"
    assert facts["Codex"] == "0.163.0; turned on; not signed in"
    assert facts["Copilot CLI"] == "1.0.105; preview on; signed in"
    off = dict(bugreport.environment(
        SpeechSettings(codex_enabled=False), {},
        versions={"claude": "x", "codex": "not found", "copilot": "checking"},
        sign_ins={"codex": "not installed"}))
    assert off["Codex"] == "not found; turned off"
    assert off["Copilot CLI"] == "checking; preview off"
    automatic = dict(bugreport.environment(SpeechSettings(), {}, claude_version="x",
                                           versions={"codex": "0.1"}))
    assert automatic["Codex"] == "0.1; automatic"


def test_a_version_that_cant_be_decoded_is_unknown(monkeypatch):
    import subprocess
    monkeypatch.setattr(bugreport.platform_paths, "find_codex", lambda: "codex.exe")
    calls = []

    def run(*args, **kwargs):
        calls.append(kwargs)
        return _Out("codex-cli 0.1.2�")
    monkeypatch.setattr(subprocess, "run", run)
    assert bugreport.cli_version("codex") == "unknown"
    # Decoded as UTF-8 with replacement, so odd bytes never raise.
    assert calls[0]["encoding"] == "utf-8" and calls[0]["errors"] == "replace"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Out("codex-cli 0.1.2kellyé"))
    assert bugreport.cli_version("codex") == "unknown"


def test_sign_in_words_never_name_the_account():
    from thechatplace import codex_signin, copilot_signin, signin
    claude = signin.SignIn(True, signed_in=True, method="claude.ai", plan="max",
                           email="k@example.com")
    assert bugreport.claude_sign_in(claude) == "signed in (Max plan)"
    claude.plan = "something new"
    assert bugreport.claude_sign_in(claude) == "signed in (subscription)"
    assert bugreport.claude_sign_in(signin.SignIn(True, signed_in=True, method="third_party")) \
        == "signed in, not with a subscription"
    assert bugreport.claude_sign_in(signin.SignIn(True)) == "not signed in"
    assert bugreport.claude_sign_in(signin.SignIn(False, problem="C:\\x")) == "sign-in unknown"
    assert bugreport.claude_sign_in(signin.SignIn(False, missing=True)) == "not installed"
    assert bugreport.codex_sign_in(codex_signin.SignIn(
        True, signed_in=True, method="ChatGPT")) == "signed in (ChatGPT)"
    assert bugreport.codex_sign_in(codex_signin.SignIn(
        True, signed_in=True, method="an API key")) == "signed in, not with ChatGPT"
    assert "another model provider" in bugreport.codex_sign_in(codex_signin.SignIn(
        True, signed_in=True, method="ChatGPT", settings_problem=True))
    assert bugreport.codex_sign_in(codex_signin.SignIn(True)) == "not signed in"
    assert bugreport.codex_sign_in(codex_signin.SignIn(
        False, problem="Codex (C:\\Users\\x) failed")) == "sign-in unknown"
    assert bugreport.copilot_sign_in(copilot_signin.SignIn(
        True, signed_in=True, login="someone", host="https://github.com")) == "signed in"
    # An enterprise host could name the company.
    assert bugreport.copilot_sign_in(copilot_signin.SignIn(
        True, signed_in=True, login="someone", host="https://acme.ghe.com")) \
        == "signed in (another GitHub host)"
    assert bugreport.copilot_sign_in(copilot_signin.SignIn(
        True, signed_in=True, login="someone", host="GitHub.com/")) == "signed in"
    assert bugreport.copilot_sign_in(copilot_signin.SignIn(
        False, missing=True, unusable="C:\\npm\\copilot.cmd")) \
        == "found, but not a copy The Chat Place runs"
    assert bugreport.copilot_sign_in(copilot_signin.SignIn(True)) == "not signed in"


def test_sign_ins_are_asked_at_once_and_a_slow_one_is_unknown():
    import threading
    import time
    release = threading.Event()

    def hangs():
        release.wait(5)
        return "signed in"

    def fails():
        raise RuntimeError("C:\\Users\\private")
    started = time.monotonic()
    found = bugreport.gather_sign_ins(True, timeout=0.3, checks={
        "claude": lambda: "signed in (Max plan)", "codex": hangs, "copilot": fails})
    release.set()
    assert time.monotonic() - started < 3
    assert found == {"claude": "signed in (Max plan)", "codex": "sign-in unknown",
                     "copilot": "sign-in unknown"}


def test_codex_sign_in_is_only_asked_when_codex_is_on(monkeypatch):
    from thechatplace import codex_signin, copilot_signin, signin
    asked = []
    monkeypatch.setattr(signin, "check", lambda: signin.SignIn(True))
    monkeypatch.setattr(copilot_signin, "check", lambda: copilot_signin.SignIn(True))
    monkeypatch.setattr(codex_signin, "check",
                        lambda: asked.append(1) or codex_signin.SignIn(True))
    assert "codex" not in bugreport.gather_sign_ins(False)
    assert not asked
    assert bugreport.gather_sign_ins(True)["codex"] == "not signed in" and asked


def _row(provider, source, **extra):
    from thechatplace.sessions import SessionInfo
    return SessionInfo(source, f"{provider}:{source}", "A private title", "C:\\private",
                       "id", provider=provider, **extra)


def test_session_counts_are_split_by_provider_and_kind():
    rows = ([_row("claude", "desktop")] * 2 + [_row("claude", "own")]
            + [_row("codex", "own")] * 3 + [_row("codex", "editor")]
            + [_row("copilot", "own")] * 2 + [_row("copilot", "terminal")] * 3
            + [_row("agents", "agents"), _row("imported", "imported")]
            + [_row("codex", "own", archived=True)])
    assert bugreport._counts_text(bugreport.session_counts(rows)) == (
        "Claude: 2 desktop app, 1 Chat Place, 0 terminal; Codex: 3 Chat Place, 1 editor; "
        "Copilot: 2 Chat Place, 3 Copilot CLI; Agent conversations: 1; Imported: 1")
    cowork = bugreport.session_counts([_row("claude", "desktop", cowork=True),
                                       _row("claude", "desktop")])
    assert cowork["Claude"] == {"desktop app Code": 1, "desktop app Cowork": 1,
                                "Chat Place": 0, "terminal": 0}
    assert bugreport._counts_text(bugreport.session_counts([])) == \
        "Claude: 0 desktop app, 0 Chat Place, 0 terminal"


def test_how_long_ago():
    assert [bugreport.ago(s) for s in (-5, 0, 59, 60, 179, 3600, 7300, 86400, 3 * 86400)] == [
        "just now", "just now", "just now", "1 minute ago", "2 minutes ago", "1 hour ago",
        "2 hours ago", "1 day ago", "3 days ago"]


def test_install_kind(monkeypatch):
    from thechatplace import updater
    assert bugreport.install_kind(frozen=False) == "run from source"
    monkeypatch.setattr(updater, "PORTABLE_COPIES_EXIST", True)
    monkeypatch.setattr(updater, "CHANNEL", "windows")
    monkeypatch.setattr(updater, "is_installed_copy", lambda version: True)
    assert bugreport.install_kind(frozen=True) == "installed, windows updates"
    monkeypatch.setattr(updater, "is_installed_copy", lambda version: False)
    assert bugreport.install_kind(frozen=True) == "portable, windows updates"
    monkeypatch.setattr(updater, "PORTABLE_COPIES_EXIST", False)
    monkeypatch.setattr(updater, "CHANNEL", "osx")
    monkeypatch.setattr(sys, "platform", "darwin")
    assert bugreport.install_kind(frozen=True) == "app, osx updates"


def test_the_display_and_install_lines():
    facts = dict(bugreport.environment(SpeechSettings(), {}, claude_version="x",
                                       install="portable, windows updates",
                                       display="scale 150%, High Contrast on"))
    assert facts["The Chat Place"].endswith("(portable, windows updates)")
    assert facts["Display"] == "scale 150%, High Contrast on"
    assert "Display" not in dict(bugreport.environment(SpeechSettings(), {}, claude_version="x"))


def test_a_mac_report_says_macos_not_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    facts = dict(bugreport.environment(SpeechSettings(), {}, claude_version="2.1.292 (Claude Code)"))
    assert "macOS" in facts and "Windows" not in facts


def test_the_report_says_what_the_last_announcement_did():
    """#98 hid because nothing said which route spoke; the report now does."""
    route = "not spoken: NVDA is running but didn't answer (Windows error 1722)"
    facts = dict(bugreport.environment(SpeechSettings(), {}, claude_version="x",
                                       speech_route=route))
    assert facts["Last announcement"] == route
    assert "Last announcement" not in dict(
        bugreport.environment(SpeechSettings(), {}, claude_version="x"))


def _wmi_without_product_type():
    raise KeyError("ProductType")


def test_a_report_still_opens_when_windows_cant_say_its_version(monkeypatch):
    # #155: on a freshly reset VM, platform.platform()'s WMI query came back
    # without ProductType and raised, which would have stopped Report a Bug.
    import platform
    monkeypatch.setattr(platform, "platform", _wmi_without_product_type)
    facts = dict(bugreport.environment(SpeechSettings(), {}, claude_version="2.1.292 (Claude Code)"))
    system = facts[{"win32": "Windows", "darwin": "macOS", "linux": "Linux"}[sys.platform]]
    if sys.platform == "win32":
        major, minor, build = sys.getwindowsversion()[:3]
        assert system == f"Windows-{major}.{minor}.{build} (platform.platform failed)"
    else:
        assert system == sys.platform


def test_a_report_says_when_copilot_runs_through_vs_codes_launcher(monkeypatch):
    """#463: whether a launcher is in the way can't be told from a version."""
    from types import SimpleNamespace
    from thechatplace import bugreport, platform_paths
    monkeypatch.setattr(bugreport.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        stdout="GitHub Copilot CLI 1.0.105.\n"))
    monkeypatch.setattr(platform_paths, "find_copilot",
                        lambda: "C:/Code/bin/copilot-shim/copilot.exe")
    assert bugreport.cli_version("copilot") == "1.0.105 (through VS Code's launcher)"
    monkeypatch.setattr(platform_paths, "find_copilot", lambda: "C:/tools/copilot.exe")
    assert bugreport.cli_version("copilot") == "1.0.105"
