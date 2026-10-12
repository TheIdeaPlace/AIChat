"""Claude Code's sign-in (#52), from `claude auth status`."""
import json
import subprocess

from thechatplace import signin


def _run(stdout="", error=None, seen=None, stderr="", code=0):
    def run(command, **kwargs):
        assert command[1:] == ["auth", "status", "--json"]
        if seen is not None:
            seen.update(kwargs)
        if error:
            raise error
        return subprocess.CompletedProcess(command, code, stdout=stdout, stderr=stderr)
    return run


def test_signed_in_to_a_subscription():
    out = json.dumps({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "pro",
                      "email": "k@example.com"})
    status = signin.check("claude", run=_run(out))
    assert status.known and status.signed_in and status.subscription
    assert signin.describe(status) == ("Claude Code is signed in to your Claude Pro plan as "
                                       "k@example.com.")


def test_not_signed_in_api_keys_and_no_answer():
    status = signin.check("claude", run=_run(json.dumps({"loggedIn": False})))
    assert status.known and not status.signed_in
    assert signin.describe(status).startswith("Claude Code isn't signed in.")
    key = signin.check("claude", run=_run(json.dumps({"loggedIn": True,
                                                       "authMethod": "api_key"})))
    assert signin.describe(key).startswith(
        "Claude Code is set up to use an Anthropic API key, not a Claude subscription.")
    for run in (_run("not json"), _run(error=OSError("gone")),
                _run(error=subprocess.TimeoutExpired("claude", 20))):
        status = signin.check("claude", run=run)
        assert not status.known
        assert signin.describe(status).startswith("Couldn't tell whether Claude Code is signed in")


def test_terminal_control_codes_around_the_answer_are_ignored():
    """A user's claude ended its answer with ESC [2G (cursor to column 2),
    which made it unreadable; a code just before the "{" must not hide it
    either, and a code is never read out as what Claude Code said."""
    answer = json.dumps({"loggedIn": True, "authMethod": "claude.ai",
                         "apiProvider": "firstParty", "subscriptionType": "pro"}, indent=2)
    for out in (answer + "\x1b[2G",
                "\x1b[?25l" + answer + "\x1b[2G\x1b[K",
                "\x1b]0;claude\x07" + answer + "\n\x1b[?25h",
                "\x1b7" + answer + "\x1b8",
                "\x1b(B" + answer):
        status = signin.check("claude", run=_run(out))
        assert status.known and status.signed_in and status.plan == "pro"
    said = signin.describe(signin.check("/x/claude", run=_run(
        stderr="\x1b[31merror: unknown command 'auth'\x1b[0m\n", code=1)))
    assert "is too old to say" in said and "\x1b" not in said


def test_a_notice_around_the_answer_is_skipped():
    """An install that prints a notice before or after its JSON (an npm
    install did) is still read; it was taken as saying only "{"."""
    answer = json.dumps({"loggedIn": True, "authMethod": "claude.ai",
                         "subscriptionType": "max"}, indent=2)
    for out in (answer + "\nClaude Code has switched from npm to a native installer.\n",
                "Warning: an update is available.\n" + answer + "\n",
                answer + "\n" + answer + "\n",
                "Warning: setting {x} ignored\n" + answer,
                '{"level": "warn"}\n' + answer,
                "\ufeff" + answer):
        status = signin.check("claude", run=_run(out, stderr="npm warn something\n"))
        assert status.known and status.signed_in and status.plan == "max"


def test_an_unreadable_answer_says_where_to_see_it():
    """A cut-off answer's first line is just "{", which said nothing; so does
    JSON that isn't an answer, and it mustn't be taken as "not signed in"."""
    cut_off = '{\n  "loggedIn": true,\n  "authMethod": "claude.ai",\n'
    for out in (cut_off, "\ufeff" + cut_off, "Notice\n" + cut_off, '[{"loggedIn": false}',
                '{"level": "warn"}\n'):
        for stderr in ("", "npm warn something\n"):
            status = signin.check("/x/claude", run=_run(out, stderr=stderr, code=1))
            assert not status.known
            assert signin.describe(status) == (
                "Couldn't tell whether Claude Code is signed in: Claude Code (/x/claude) gave "
                "an answer The Chat Place couldn't read (exit code 1). Run claude auth status "
                "in a terminal to see it.")
    assert signin.describe(signin.check("/x/claude", run=_run("\ufeff[1, 2]\n"))).endswith(
        'said "[1, 2]".')


def test_asked_with_the_environment_turns_get(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-for-other-tools")
    seen = {}
    signin.check("claude", run=_run(json.dumps({"loggedIn": True}), seen=seen))
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert seen["encoding"] == "utf-8"


def test_other_ways_of_signing_in_are_named():
    token = signin.SignIn(True, True, "oauth_token")
    assert token.subscription
    assert signin.describe(token) == ("Claude Code is using a long-lived token from your "
                                      "Claude subscription.")
    bedrock = signin.SignIn(True, True, "third_party", provider="bedrock")
    assert "Amazon Bedrock" in signin.describe(bedrock)
    nobody = signin.SignIn(True, signed_in=False, method="none")
    assert signin.describe(nobody).startswith("Claude Code isn't signed in.")
    assert signin.login_command("claude") == ["claude", "auth", "login", "--claudeai"]


def test_says_why_it_could_not_tell():
    """Each reason Claude Code couldn't be asked is said, not just "didn't
    answer"."""
    def said(run):
        return signin.describe(signin.check("/x/claude", run=run))
    assert said(_run(error=subprocess.TimeoutExpired("claude", 20))).endswith(
        "Claude Code (/x/claude) didn't answer in 20 seconds.")
    assert "couldn't be started: gone" in said(_run(error=OSError("gone")))
    old = said(_run(stdout="setting {x} ignored\n", stderr="error: unknown command 'auth'\n",
                    code=1))
    assert "is too old to say" in old and "claude update" in old
    assert said(_run(stderr="\nenv: node: No such file or directory\n", code=127)).endswith(
        'said "env: node: No such file or directory".')
    assert said(_run(code=3)).endswith("gave no answer (exit code 3).")


def test_no_claude_is_said_with_how_to_install_it(monkeypatch):
    from thechatplace import platform_paths
    monkeypatch.setattr(platform_paths, "find_claude", lambda: platform_paths.ClaudeLookup(
        None, "Claude Code isn't installed.", "To install it, run its native installer."))
    status = signin.check()
    assert status.missing and not status.known
    # The lookup's own words, not "Couldn't tell whether ...: isn't installed".
    assert signin.describe(status) == ("Claude Code isn't installed.\n\nTo install it, run its "
                                       "native installer.")
