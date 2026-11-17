"""The scheduled backups' passphrase in the system's protected store (#442).
Windowless. Windows round-trips through real DPAPI; Linux's secret-tool is
stood in for, so no real keyring is touched."""
import subprocess
import sys

import pytest

from thechatplace import platform_paths


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows'")
def test_dpapi_round_trip_never_keeps_the_text(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path)
    assert platform_paths.backup_secret_available()
    assert platform_paths.load_backup_secret() is None
    platform_paths.save_backup_secret("pässwörd ✓")
    stored = (tmp_path / platform_paths.BACKUP_SECRET_FILE).read_bytes()
    assert "pässwörd".encode() not in stored
    assert platform_paths.load_backup_secret() == "pässwörd ✓"
    platform_paths.save_backup_secret("new one")
    assert platform_paths.load_backup_secret() == "new one"
    platform_paths.delete_backup_secret()
    assert platform_paths.load_backup_secret() is None
    platform_paths.delete_backup_secret()          # twice is harmless


@pytest.mark.skipif(sys.platform == "win32" or sys.platform == "darwin",
                    reason="secret-tool is Linux's")
def test_secret_tool_gets_the_passphrase_on_stdin_never_in_its_arguments(monkeypatch):
    calls = []
    kept = {}

    def fake_run(args, input=None, capture_output=False, timeout=None):
        calls.append((args, input))
        if args[1] == "store":
            kept["pw"] = input
            return subprocess.CompletedProcess(args, 0, b"", b"")
        if args[1] == "lookup":
            return subprocess.CompletedProcess(args, 0 if "pw" in kept else 1,
                                               kept.get("pw", b""), b"")
        kept.pop("pw", None)
        return subprocess.CompletedProcess(args, 0, b"", b"")
    monkeypatch.setattr(subprocess, "run", fake_run)
    platform_paths.save_backup_secret("linux pw")
    assert all("linux pw" not in " ".join(args) for args, _ in calls)
    assert calls[0][1] == b"linux pw"
    assert platform_paths.load_backup_secret() == "linux pw"
    platform_paths.delete_backup_secret()
    assert platform_paths.load_backup_secret() is None


def test_a_store_that_fails_reads_as_no_passphrase(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path)
    if sys.platform == "win32":
        (tmp_path / platform_paths.BACKUP_SECRET_FILE).write_bytes(b"not a dpapi blob")
    else:
        def broken(*a, **k):
            raise OSError("no keyring")
        monkeypatch.setattr(subprocess, "run", broken)
    if sys.platform != "darwin":
        assert platform_paths.load_backup_secret() is None


FAKE_KEY = "sk-ant-test-0000000000000000000000000000"


def test_only_known_secret_names_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path)
    for name in ("", "../x", "backup-passphrase", None, "claude-api-key.dpapi"):
        with pytest.raises(ValueError):
            platform_paths.save_secret(name, "x")
        with pytest.raises(ValueError):
            platform_paths.api_key_helper_command(name)
        assert platform_paths.load_secret(name) is None
        assert platform_paths.has_secret(name) is False
        platform_paths.delete_secret(name)  # never raises
    # The backup passphrase keeps the file and account it always had (#442).
    assert platform_paths.secret_file(platform_paths.BACKUP_SECRET).name == \
        "backup-passphrase.dpapi"
    assert platform_paths.BACKUP_SECRET == "scheduled-backup-passphrase"
    assert platform_paths.secret_file(platform_paths.CLAUDE_API_KEY_SECRET).name == \
        "claude-api-key.dpapi"


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows'")
def test_the_api_key_is_a_secret_of_its_own(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path)
    key = platform_paths.CLAUDE_API_KEY_SECRET
    assert not platform_paths.has_secret(key)
    platform_paths.save_secret(key, FAKE_KEY)
    platform_paths.save_backup_secret("pw")
    assert FAKE_KEY.encode() not in (tmp_path / "claude-api-key.dpapi").read_bytes()
    assert platform_paths.has_secret(key) and platform_paths.load_secret(key) == FAKE_KEY
    assert platform_paths.load_backup_secret() == "pw"
    platform_paths.delete_secret(key)
    assert not platform_paths.has_secret(key) and platform_paths.load_backup_secret() == "pw"


@pytest.mark.skipif(sys.platform == "win32" or sys.platform == "darwin",
                    reason="secret-tool is Linux's")
def test_secret_tool_keeps_the_api_key_under_its_own_account(monkeypatch):
    calls = []

    def fake_run(args, input=None, capture_output=False, timeout=None):
        calls.append((args, input))
        return subprocess.CompletedProcess(args, 0, b"", b"")
    monkeypatch.setattr(subprocess, "run", fake_run)
    platform_paths.save_secret(platform_paths.CLAUDE_API_KEY_SECRET, FAKE_KEY)
    args, given = calls[0]
    assert args[-2:] == ["account", "claude-api-key"] and given == FAKE_KEY.encode()
    assert FAKE_KEY not in " ".join(args)


# -- apiKeyHelper (#658) ------------------------------------------------------------------


def _decoded_script(command):
    import base64
    encoded = command.rsplit(" ", 1)[1]
    return base64.b64decode(encoded).decode("utf-16-le")


def test_windows_helper_command_is_safe_in_any_shell(tmp_path, monkeypatch):
    folder = tmp_path / "O'Brien ‘quoted’ $HOME %PATH% & | ; `x`"
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: folder)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    command = platform_paths.api_key_helper_command()
    assert command.startswith('"C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe" '
                              "-NoLogo -NoProfile -NonInteractive -EncodedCommand ")
    # Nothing from the folder's path is on the command itself: only base64.
    encoded = command.rsplit(" ", 1)[1]
    assert set(encoded) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")
    assert command.count('"') == 2 and "O'Brien" not in command
    script = _decoded_script(command)
    assert "O''Brien ‘‘quoted’’ $HOME %PATH%" in script
    assert "ProtectedData]::Unprotect($b, $null, " in script and "CurrentUser" in script
    assert "claude-api-key.dpapi'" in script


def test_an_odd_systemroot_is_never_put_in_the_command(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SystemRoot", 'C:\\Win" & calc & "')
    assert platform_paths.api_key_helper_command().startswith(
        '"C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe" ')


def test_mac_and_linux_helper_commands_are_quoted_for_sh(monkeypatch):
    import shlex
    import shutil
    monkeypatch.setattr(sys, "platform", "darwin")
    mac = platform_paths.api_key_helper_command()
    assert mac == ("/usr/bin/security find-generic-password -s 'The Chat Place' "
                   "-a claude-api-key -w")
    assert shlex.split(mac)[3] == "The Chat Place"
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/secret-tool")
    linux = platform_paths.api_key_helper_command()
    assert linux == "/usr/bin/secret-tool lookup service 'The Chat Place' account claude-api-key"
    monkeypatch.setattr(platform_paths, "_SECRET_SERVICE", "It's; rm -rf ~")
    assert shlex.split(platform_paths.api_key_helper_command())[3] == "It's; rm -rf ~"


def _git_bash():
    import os
    for candidate in (os.environ.get("CLAUDE_CODE_GIT_BASH_PATH"),
                      r"C:\Program Files\Git\bin\bash.exe"):
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI and PowerShell are Windows'")
def test_the_windows_helper_prints_the_key_through_cmd_and_git_bash(tmp_path, monkeypatch):
    """Runs the real helper: Windows PowerShell 5.1 unprotects what _dpapi
    protected and prints exactly the key, from a folder with an apostrophe
    and a space in its name, through cmd.exe and (if it's here) Git Bash."""
    folder = tmp_path / "O'Brien folder"
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: folder)
    platform_paths.save_secret(platform_paths.CLAUDE_API_KEY_SECRET, FAKE_KEY)
    command = platform_paths.api_key_helper_command()
    done = subprocess.run(command, shell=True, capture_output=True, timeout=60, cwd=tmp_path)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert done.stdout == FAKE_KEY.encode()
    bash = _git_bash()
    if bash:
        done = subprocess.run([bash, "-c", command], capture_output=True, timeout=60)
        assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
        assert done.stdout == FAKE_KEY.encode()
    # Removed, the helper prints nothing and fails, so Claude Code has no key.
    platform_paths.delete_secret(platform_paths.CLAUDE_API_KEY_SECRET)
    done = subprocess.run(command, shell=True, capture_output=True, timeout=60)
    assert done.returncode != 0 and FAKE_KEY.encode() not in done.stdout


@pytest.mark.skipif(sys.platform != "darwin" or not __import__("os").environ.get("CI"),
                    reason="the real keychain, only on a CI Mac (never someone's own)")
def test_keychain_round_trip_on_a_ci_mac(monkeypatch):
    import uuid
    monkeypatch.setattr(platform_paths, "_SECRET_SERVICE", f"The Chat Place test {uuid.uuid4()}")
    try:
        assert platform_paths.load_backup_secret() is None
        platform_paths.save_backup_secret("mac pw")
        assert platform_paths.load_backup_secret() == "mac pw"
        platform_paths.save_backup_secret("changed")          # an existing item is updated
        assert platform_paths.load_backup_secret() == "changed"
    finally:
        platform_paths.delete_backup_secret()
    assert platform_paths.load_backup_secret() is None
