"""Codex ChatGPT sign-in, from ``codex login status`` (#301)."""
import subprocess

from thechatplace import codex_signin


def _run(stderr="", stdout="", code=0, error=None, seen=None):
    def run(command, **kwargs):
        assert command[1:] == ["login", "status"]
        if seen is not None:
            seen.update(kwargs)
        if error:
            raise error
        return subprocess.CompletedProcess(command, code, stdout=stdout, stderr=stderr)
    return run


def test_chatgpt_sign_in_and_other_credentials():
    status = codex_signin.check("codex", run=_run(stderr="Logged in using ChatGPT\n"))
    assert status.known and status.signed_in and status.method == "ChatGPT"
    assert codex_signin.describe(status) == "Codex is signed in to ChatGPT."

    status = codex_signin.check("codex", run=_run(stderr="Logged in using an API key - sk***\n"))
    assert status.known and status.signed_in and status.method == "an API key"
    assert "not ChatGPT" in codex_signin.describe(status)


def test_not_signed_in_missing_and_unreadable(monkeypatch):
    status = codex_signin.check("codex", run=_run(stderr="Not logged in\n", code=1))
    assert status.known and not status.signed_in
    assert codex_signin.describe(status).startswith("Codex isn't signed in.")

    from thechatplace import platform_paths
    monkeypatch.setattr(platform_paths, "find_codex", lambda: None)
    missing = codex_signin.check()
    assert missing.missing and "Install the Codex app or CLI" in codex_signin.describe(missing)

    unknown = codex_signin.check("codex", run=_run(stderr="private failure", code=1))
    assert not unknown.known
    assert "private failure" in codex_signin.describe(unknown)


def test_status_check_uses_codex_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "not-for-codex")
    seen = {}
    codex_signin.check("codex", run=_run(stderr="Logged in using ChatGPT", seen=seen))
    assert "OPENAI_API_KEY" not in seen["env"]
    assert seen["encoding"] == "utf-8"
    assert codex_signin.login_command("codex") == ["codex", "login"]
