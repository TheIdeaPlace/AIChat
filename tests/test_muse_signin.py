import subprocess
from pathlib import Path

from thechatplace import muse_signin, platform_paths


def native(path: Path, header: bytes = b"MZ") -> str:
    path.write_bytes(header + b"synthetic")
    try:
        path.chmod(0o755)
    except OSError:
        pass
    return str(path)


def test_find_muse_accepts_a_native_path_and_rejects_wrappers(tmp_path):
    exe = native(tmp_path / "muse.exe")
    assert platform_paths.find_muse(which=lambda _: exe, native_candidates=[]).path == exe

    script = tmp_path / "muse.cmd"
    script.write_text("@echo off", encoding="utf-8")
    lookup = platform_paths.find_muse(which=lambda _: str(script), native_candidates=[])
    assert lookup.path is None
    assert lookup.unusable and str(script) in lookup.reason


def test_find_muse_uses_verified_fallback_and_reports_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_paths.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    fallback = tmp_path / "Local" / "Microsoft" / "WinGet" / "Links" / "muse.exe"
    fallback.parent.mkdir(parents=True)
    native(fallback)
    assert platform_paths.find_muse(which=lambda _: None,
                                    native_candidates=[fallback]).path == str(fallback)
    assert platform_paths.find_muse(which=lambda _: None).path is None

    lookup = platform_paths.find_muse(which=lambda _: None, native_candidates=[])
    assert lookup.path is None and not lookup.unusable
    assert "Muse Code isn't installed" in lookup.reason
    assert "install.ps1" in lookup.install_help


def test_windows_muse_launcher_resolves_its_active_native_binary(tmp_path):
    launcher = tmp_path / "muse.cmd"
    launcher.write_text("@echo off\n", encoding="utf-8")
    (tmp_path / ".muse-version").write_text("1.4.18-R8587.1\n", encoding="utf-8")
    active = native(tmp_path / "muse-bin-1.4.18-R8587.1.exe")
    native(tmp_path / "muse-bin-9.9.9.exe")  # do not guess the newest filename
    lookup = platform_paths.find_muse(which=lambda _: str(launcher), native_candidates=[])
    assert lookup.path == active


def test_windows_muse_install_is_found_when_desktop_path_is_stale(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_paths.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    installed = tmp_path / "Programs" / "muse"
    installed.mkdir(parents=True)
    (installed / ".muse-version").write_text("1.4.18-R8587.1", encoding="utf-8")
    active = native(installed / "muse-bin-1.4.18-R8587.1.exe")
    assert platform_paths.find_muse(which=lambda _: None).path == active


def test_muse_launcher_never_uses_missing_or_unsafe_version_targets(tmp_path):
    launcher = tmp_path / "muse.cmd"
    launcher.write_text("@echo off\n", encoding="utf-8")
    state = tmp_path / ".muse-version"
    for version in ("../../other", "1.4.18 & echo bad", "1.4.18-R8587.1"):
        state.write_text(version, encoding="utf-8")
        lookup = platform_paths.find_muse(which=lambda _: str(launcher), native_candidates=[])
        assert lookup.path is None and lookup.unusable


def test_muse_native_candidates_use_documented_unix_launcher(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_paths.sys, "platform", "linux")
    monkeypatch.setattr(platform_paths.Path, "home", classmethod(lambda cls: tmp_path))
    assert platform_paths.muse_native_candidates() == [tmp_path / ".local/bin/muse"]


def test_muse_install_help_is_platform_specific(monkeypatch):
    monkeypatch.setattr(platform_paths.sys, "platform", "darwin")
    assert "curl -fsSL https://dev.meta.ai/install.sh | sh" in platform_paths.muse_install_help()
    monkeypatch.setattr(platform_paths.sys, "platform", "win32")
    assert "irm https://dev.meta.ai/install.ps1 | iex" in platform_paths.muse_install_help()
    assert "https://dev.meta.ai/docs/muse-code" in platform_paths.muse_install_help()
    assert "restart" in platform_paths.muse_install_help()
    assert "Tools, Sign In, Muse Code" in platform_paths.muse_install_help()


def test_check_reports_installed_but_unknown_account_without_inference(tmp_path):
    exe = native(tmp_path / "muse")
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="muse 1.4.0\n", stderr="")

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def request(self, method, params=None): return {"state": "future", "credentialRequired": True}

    status = muse_signin.check(executable=exe, run=run,
                               connection_factory=lambda _exe: Connection())
    assert not status.known and status.signed_in is None
    assert status.version == "1.4.0"
    assert calls[0][0] == [exe, "--version"]
    assert "META_API_KEY" not in calls[0][1].get("env", {})
    assert "unknown account status" in muse_signin.describe(status)


def test_check_missing_gives_instructions_without_starting_a_process(monkeypatch):
    monkeypatch.setattr(platform_paths, "find_muse", lambda: platform_paths.MuseLookup(
        reason="Muse Code isn't installed", install_help="run the official installer"))
    called = []
    status = muse_signin.check(run=lambda *a, **k: called.append(a))
    assert status.missing and not called
    assert "official installer" in muse_signin.describe(status)


def test_check_rejects_version_failure_as_unknown(tmp_path):
    exe = native(tmp_path / "muse")

    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 2, stdout="", stderr="bad version")

    status = muse_signin.check(executable=exe, run=run)
    assert not status.known and not status.missing
    assert "couldn't report its version" in muse_signin.describe(status)


def test_check_rejects_an_unrelated_program_with_a_number(tmp_path):
    exe = native(tmp_path / "muse")

    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="Other Tool 1.4.0\n", stderr="")

    status = muse_signin.check(executable=exe, run=run)
    assert not status.known


def test_check_does_not_speak_raw_process_errors(tmp_path):
    exe = native(tmp_path / "muse")

    def run(argv, **kwargs):
        raise OSError("secret path and token")

    status = muse_signin.check(executable=exe, run=run)
    assert not status.known
    assert "secret path" not in muse_signin.describe(status)


def test_login_command_starts_interactive_muse_only_after_detection():
    assert muse_signin.login_command("muse.exe") == ["muse.exe"]


def test_check_maps_documented_account_states(tmp_path):
    exe = native(tmp_path / "muse")

    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="muse 1.4.0\n", stderr="")

    class Connection:
        def __init__(self, state):
            self.state = state
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def request(self, method, params=None):
            assert method == "account/read"
            return self.state

    for state, signed_in, method in (
        ({"state": "accountLogin", "credentialRequired": True, "label": "Meta account"}, True, "Meta account"),
        ({"state": "loggedOut", "credentialRequired": True}, False, ""),
        ({"state": "apiKey", "credentialRequired": True}, False, "an API key"),
        ({"state": "envKey", "credentialRequired": True}, False, "META_API_KEY"),
    ):
        status = muse_signin.check(executable=exe, run=run,
                                    connection_factory=lambda _exe, s=state: Connection(s))
        assert status.known and status.signed_in is signed_in
        assert status.method == method
        assert status.needs_login is (state["state"] == "loggedOut" and state["credentialRequired"])


def test_check_rejects_unknown_account_state(tmp_path):
    exe = native(tmp_path / "muse")

    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="muse 1.4.0\n", stderr="")

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def request(self, method, params=None): return {"state": "futureState"}

    status = muse_signin.check(executable=exe, run=run,
                               connection_factory=lambda _exe: Connection())
    assert not status.known and "account status" in status.problem


def test_login_flow_starts_waits_and_cancels_on_one_connection():
    class Connection:
        def __init__(self):
            self.requests = []
            self.events = [{"method": "account/loginCompleted",
                            "params": {"outcome": "granted"}}]
            self.closed = False
        def request(self, method, params=None):
            self.requests.append((method, params))
            return {"verificationUrl": "https://example.test/verify", "userCode": "ABC"}
        def next_notification(self, timeout=1):
            return self.events.pop(0) if self.events else None
        def close(self):
            self.closed = True

    connection = Connection()
    flow = muse_signin.LoginFlow("muse", connection_factory=lambda _exe: connection)
    prompt = flow.start()
    assert prompt.verification_url.endswith("verify") and prompt.user_code == "ABC"
    assert flow.wait(timeout=1) == "granted"
    assert connection.requests == [("account/loginStart", {"type": "deviceCode"})]
    flow.close()
    assert connection.closed


def test_login_flow_cancel_is_safe_and_fixed():
    class Connection:
        def request(self, method, params=None):
            if method == "account/loginStart":
                return {"verificationUrl": "https://example.test", "userCode": "ABC"}
            return {}
        def next_notification(self, timeout=1):
            return None
        def close(self):
            pass

    flow = muse_signin.LoginFlow("muse", connection_factory=lambda _exe: Connection())
    flow.start()
    assert flow.cancel() == "cancelled"
    assert flow.wait(timeout=0.01) == "cancelled"


def test_login_flow_rejects_unsafe_device_prompt():
    class Connection:
        def request(self, method, params=None):
            return {"verificationUrl": "http://example.test", "userCode": "ABC"}
        def close(self): pass
    flow = muse_signin.LoginFlow("muse", connection_factory=lambda _exe: Connection())
    try:
        flow.start()
    except muse_signin.MuseAccountError as error:
        assert "sign-in code" in str(error)
    else:
        raise AssertionError("unsafe URL was accepted")


def test_missing_credential_required_is_unknown_not_signed_out(tmp_path):
    exe = native(tmp_path / "muse")

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def request(self, method): return {"state": "loggedOut"}

    status = muse_signin.check(executable=exe,
        run=lambda *a, **k: subprocess.CompletedProcess(a, 0, "muse 1.4.0", ""),
        connection_factory=lambda exe: Connection())
    assert not status.known and not status.needs_login


def test_unsupported_account_interface_explains_update_and_recheck(tmp_path):
    exe = native(tmp_path / "muse")

    def unsupported(exe):
        raise muse_signin.MuseAccountError("PRIVATE RPC ERROR")

    status = muse_signin.check(executable=exe,
        run=lambda *a, **k: subprocess.CompletedProcess(a, 0, "muse 1.4.0", ""),
        connection_factory=unsupported)
    text = muse_signin.describe(status)
    assert "Update Muse Code" in text and "PRIVATE" not in text


def test_api_key_guidance_explains_precedence_without_changing_credentials():
    text = muse_signin.describe(muse_signin.SignIn(True, signed_in=False,
        method="an API key", credential_required=True, account_state="apiKey"))
    assert "takes precedence" in text
    assert "Muse Code" in text


def test_malformed_login_url_closes_connection_and_has_fixed_diagnostic():
    class Connection:
        closed = False
        def request(self, method, params=None):
            return {"verificationUrl": "https://[PRIVATE", "userCode": "ABC"}
        def close(self): self.closed = True

    connection = Connection()
    flow = muse_signin.LoginFlow("muse", connection_factory=lambda exe: connection)
    try:
        flow.start()
    except muse_signin.MuseAccountError as error:
        assert "PRIVATE" not in str(error)
    else:
        raise AssertionError("malformed URL accepted")
    assert connection.closed
