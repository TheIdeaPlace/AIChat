"""GitHub Copilot CLI's sign-in, from the account it records (#391)."""
import json
import os

from thechatplace import copilot_signin, platform_paths

HEADER = "// User settings belong in settings.json.\n// This file is managed automatically.\n"


def write_config(home, data, header=HEADER):
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text(header + json.dumps(data), encoding="utf-8")


def test_the_recorded_account_is_the_sign_in(tmp_path):
    write_config(tmp_path, {
        "loggedInUsers": [{"host": "https://github.com", "login": "synthetic-user",
                           "kind": "githubDotCom"}],
        "lastLoggedInUser": {"host": "https://github.com", "login": "synthetic-user"}})
    status = copilot_signin.check("copilot", home=tmp_path)
    assert status.known and status.signed_in and status.login == "synthetic-user"
    assert copilot_signin.describe(status) == (
        "Copilot CLI is signed in to github.com as synthetic-user.")


def test_another_host_is_named(tmp_path):
    """GitHub Enterprise Cloud with data residency (``copilot login --host``)."""
    write_config(tmp_path, {"lastLoggedInUser": {"host": "https://example.ghe.com/",
                                                 "login": "synthetic-user"}})
    status = copilot_signin.check("copilot", home=tmp_path)
    assert copilot_signin.describe(status) == (
        "Copilot CLI is signed in to example.ghe.com as synthetic-user.")


def test_not_signed_in(tmp_path):
    # Never started: no settings file at all.
    status = copilot_signin.check("copilot", home=tmp_path)
    assert status.known and not status.signed_in
    assert copilot_signin.describe(status).startswith("Copilot CLI isn't signed in.")
    # Started, never signed in; or a record with no login.
    write_config(tmp_path, {"firstLaunchAt": "2026-10-26T23:50:26.591Z"})
    assert not copilot_signin.check("copilot", home=tmp_path).signed_in
    write_config(tmp_path, {"lastLoggedInUser": {"host": "https://github.com", "login": " "}})
    assert not copilot_signin.check("copilot", home=tmp_path).signed_in
    # An empty file, as one being written may be.
    (tmp_path / "config.json").write_text("", encoding="utf-8")
    assert copilot_signin.check("copilot", home=tmp_path).known


def test_the_last_account_counts_only_while_copilot_still_lists_it(tmp_path):
    """A sign-out may leave lastLoggedInUser behind: offered a sign-in, not
    told it's signed in (the review of #391)."""
    last = {"host": "https://github.com", "login": "synthetic-user"}
    write_config(tmp_path, {"loggedInUsers": [], "lastLoggedInUser": last})
    assert not copilot_signin.check("copilot", home=tmp_path).signed_in
    write_config(tmp_path, {"loggedInUsers": [{"host": "https://example.ghe.com",
                                               "login": "synthetic-user"}],
                            "lastLoggedInUser": last})
    assert not copilot_signin.check("copilot", home=tmp_path).signed_in
    write_config(tmp_path, {"loggedInUsers": [{"host": "https://github.com/",
                                               "login": "synthetic-user"}],
                            "lastLoggedInUser": last})
    assert copilot_signin.check("copilot", home=tmp_path).signed_in


def test_a_settings_file_saved_with_a_bom_is_read(tmp_path):
    data = {"lastLoggedInUser": {"host": "https://github.com", "login": "synthetic-user"}}
    (tmp_path / "config.json").write_text(HEADER + json.dumps(data), encoding="utf-8-sig")
    assert copilot_signin.check("copilot", home=tmp_path).signed_in


def test_a_settings_file_it_cant_make_sense_of_is_said(tmp_path):
    (tmp_path / "config.json").write_text(HEADER + "{not json", encoding="utf-8")
    status = copilot_signin.check("copilot", home=tmp_path)
    assert not status.known
    assert "isn't in the expected form" in copilot_signin.describe(status)
    write_config(tmp_path, {"lastLoggedInUser": "synthetic-user"})
    assert not copilot_signin.check("copilot", home=tmp_path).known
    (tmp_path / "config.json").write_bytes(b"\xff\xfe\x00")
    status = copilot_signin.check("copilot", home=tmp_path)
    assert not status.known and "couldn't be read" in copilot_signin.describe(status)


def test_the_home_is_copilots_own(tmp_path, monkeypatch):
    """COPILOT_HOME is honoured, as Copilot CLI and the app's chats do."""
    monkeypatch.setenv("COPILOT_HOME", str(tmp_path))
    write_config(tmp_path, {"lastLoggedInUser": {"host": "https://github.com",
                                                 "login": "synthetic-user"}})
    assert copilot_signin.check("copilot").login == "synthetic-user"


def test_not_installed_and_an_npm_copy(monkeypatch):
    monkeypatch.setattr(platform_paths, "find_copilot", lambda: None)
    missing = copilot_signin.check(which=lambda name: None)
    assert missing.missing and not missing.unusable
    text = copilot_signin.describe(missing)
    assert text.startswith("Copilot CLI isn't installed.")
    assert copilot_signin.install_command() in text

    npm = os.path.join("synthetic", "npm", "copilot.cmd")
    found = copilot_signin.check(which=lambda name: npm if name == "copilot" else None)
    assert found.missing and found.unusable == npm
    text = copilot_signin.describe(found)
    assert npm in text and "an npm install" in text and "native Copilot CLI" in text
    assert copilot_signin.install_command() in text

    # Turned down for another reason: not called npm's.
    other = os.path.join("synthetic", "tools", "copilot")
    text = copilot_signin.describe(copilot_signin.check(which=lambda name: other))
    assert other in text and "npm" not in text and "native Copilot CLI" in text


def test_the_install_command_suits_the_computer(monkeypatch):
    monkeypatch.setattr(copilot_signin.sys, "platform", "darwin")
    assert copilot_signin.install_command() == "brew install copilot-cli"
    monkeypatch.setattr(copilot_signin.sys, "platform", "win32")
    assert copilot_signin.install_command() == "winget install GitHub.Copilot"


def test_login_command():
    assert copilot_signin.login_command("copilot") == ["copilot", "login"]


def test_an_odd_config_is_cant_tell_rather_than_an_error(tmp_path):
    """A host that isn't text, or JSON nested too deeply to read, must still
    give an answer: the recheck after signing in runs on a thread of its own
    and would otherwise end silently (#397)."""
    write_config(tmp_path, {
        "loggedInUsers": [{"host": 5, "login": "synthetic-user"}],
        "lastLoggedInUser": {"host": "https://github.com", "login": "synthetic-user"}})
    status = copilot_signin.check("copilot", home=tmp_path)
    assert status.signed_in  # a host that isn't text counts as github.com, as a missing one does
    (tmp_path / "config.json").write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
    status = copilot_signin.check("copilot", home=tmp_path)
    assert not status.known and status.problem


def test_logged_in_users_that_isnt_a_list_is_cant_tell(tmp_path):
    write_config(tmp_path, {
        "loggedInUsers": {"login": "synthetic-user"},
        "lastLoggedInUser": {"host": "https://github.com", "login": "synthetic-user"}})
    status = copilot_signin.check("copilot", home=tmp_path)
    assert not status.known


def test_logins_and_hosts_match_whatever_their_case(tmp_path):
    write_config(tmp_path, {
        "loggedInUsers": [{"host": "https://GitHub.com/", "login": "Synthetic-User"}],
        "lastLoggedInUser": {"host": "https://github.com", "login": "synthetic-user"}})
    status = copilot_signin.check("copilot", home=tmp_path)
    assert status.signed_in
    assert copilot_signin.describe(status) == (
        "Copilot CLI is signed in to github.com as synthetic-user.")


def test_not_signed_in_mentions_the_github_cli(tmp_path):
    """Copilot also uses the GitHub CLI's sign-in, which config.json doesn't
    record (seen on a machine nobody had signed in to, #391)."""
    status = copilot_signin.check("copilot", home=tmp_path)
    assert "GitHub CLI (gh)" in copilot_signin.describe(status)
