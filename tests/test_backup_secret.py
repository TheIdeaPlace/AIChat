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
