"""Tests for the verified native Muse history-server launch path."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from thechatplace import platform_paths


def _result(output: str, *, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["muse", "--version"],
        returncode=returncode,
        stdout=output,
        stderr="",
    )


def _patch_verified_lookup(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    monkeypatch.setattr(
        platform_paths,
        "find_muse",
        lambda: platform_paths.MuseLookup(path=path),
    )
    monkeypatch.setattr(platform_paths, "_native_muse", lambda candidate: True)


def test_start_muse_history_server_verifies_muse_1x_and_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = r"C:\Muse\muse.exe"
    _patch_verified_lookup(monkeypatch, executable)
    calls: list[tuple[object, ...]] = []

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return _result("Muse Code 1.4.18\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    started = object()
    monkeypatch.setattr(
        platform_paths,
        "start_muse_server",
        lambda path=None: started if path == executable else None,
    )

    assert platform_paths.start_muse_history_server() is started
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == ([executable, "--version"],)
    assert kwargs["timeout"] == 10
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"
    assert kwargs["creationflags"] == platform_paths.hidden_window_flags()
    assert "META_API_KEY" not in kwargs["env"]


def test_start_muse_history_server_accepts_explicit_verified_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = r"/opt/muse"
    _patch_verified_lookup(monkeypatch, "unused")
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return _result("muse v1.0.0")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        platform_paths,
        "_native_muse",
        lambda candidate: candidate == executable,
    )
    monkeypatch.setattr(
        platform_paths,
        "start_muse_server",
        lambda path=None: path,
    )

    assert platform_paths.start_muse_history_server(executable) == executable
    assert calls == [[executable, "--version"]]


@pytest.mark.parametrize(
    "output",
    ["Other Tool 1.4.18", "Muse Code 2.0.0", "", "Muse Code 1"],
)
def test_start_muse_history_server_rejects_wrong_or_unsupported_version(
    monkeypatch: pytest.MonkeyPatch,
    output: str,
) -> None:
    executable = r"/tmp/muse"
    _patch_verified_lookup(monkeypatch, executable)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: _result(output))
    start_calls: list[str] = []
    monkeypatch.setattr(
        platform_paths,
        "start_muse_server",
        lambda path=None: start_calls.append(path),
    )

    with pytest.raises(OSError):
        platform_paths.start_muse_history_server()
    assert start_calls == []


def test_start_muse_history_server_rejects_nonzero_version_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = r"/tmp/muse"
    _patch_verified_lookup(monkeypatch, executable)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: _result("Muse Code 1.4.18", returncode=1),
    )

    with pytest.raises(OSError):
        platform_paths.start_muse_history_server()


def test_start_muse_history_server_rejects_version_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = r"/tmp/muse"
    _patch_verified_lookup(monkeypatch, executable)

    def timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired([executable, "--version"], timeout=10)

    monkeypatch.setattr(subprocess, "run", timeout)

    with pytest.raises(OSError):
        platform_paths.start_muse_history_server()


def test_start_muse_history_server_rejects_non_native_path_before_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = Path("/tmp/muse-wrapper")
    monkeypatch.setattr(
        platform_paths,
        "_native_muse",
        lambda candidate: False,
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("version probe must not run"),
    )

    with pytest.raises(OSError):
        platform_paths.start_muse_history_server(str(executable))


def test_start_muse_history_server_rejects_missing_muse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform_paths, "find_muse", lambda: platform_paths.MuseLookup())
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("version probe must not run"),
    )

    with pytest.raises(OSError):
        platform_paths.start_muse_history_server()
