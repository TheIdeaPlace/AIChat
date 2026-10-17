"""Codex ChatGPT sign-in through app-server ``account/read`` (#301)."""
from thechatplace import codex_signin


class Connection:
    def __init__(self, executable, answer=None, error=None, seen=None):
        assert executable == "codex"
        self.answer, self.error = answer, error
        if seen is not None:
            seen.append(executable)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def request(self, method, params):
        assert (method, params) == ("account/read", {"refreshToken": True})
        if self.error:
            raise self.error
        return self.answer


def factory(answer=None, error=None, seen=None):
    return lambda executable: Connection(executable, answer, error, seen)


def test_chatgpt_sign_in_and_other_credentials():
    status = codex_signin.check("codex", connection_factory=factory({
        "account": {"type": "chatgpt"}, "requiresOpenaiAuth": True}))
    assert status.known and status.signed_in and status.method == "ChatGPT"
    assert codex_signin.describe(status) == "Codex is signed in to ChatGPT."

    status = codex_signin.check("codex", connection_factory=factory({
        "account": {"type": "apiKey"}, "requiresOpenaiAuth": True}))
    assert status.known and status.signed_in and status.method == "an API key"
    assert "not ChatGPT" in codex_signin.describe(status)


def test_not_signed_in_missing_and_unreadable(monkeypatch):
    status = codex_signin.check("codex", connection_factory=factory({
        "account": None, "requiresOpenaiAuth": True}))
    assert status.known and not status.signed_in
    assert codex_signin.describe(status).startswith("Codex isn't signed in.")

    from thechatplace import platform_paths
    monkeypatch.setattr(platform_paths, "find_codex", lambda: None)
    missing = codex_signin.check()
    assert missing.missing and "Install the Codex app or CLI" in codex_signin.describe(missing)

    unknown = codex_signin.check(
        "codex", connection_factory=factory(error=RuntimeError("safe failure")))
    assert not unknown.known
    assert "safe failure" in codex_signin.describe(unknown)


def test_status_check_starts_app_server_and_login_command():
    seen = []
    codex_signin.check("codex", connection_factory=factory({
        "account": {"type": "chatgpt"}, "requiresOpenaiAuth": True}, seen=seen))
    assert seen == ["codex"]
    assert codex_signin.login_command("codex") == ["codex", "login"]
