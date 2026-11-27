"""The updater, with a fake Velopack manager and a fake GitHub (no network)."""
import json
import re
import sys
from pathlib import Path

import pytest
from markers import windows_paths

import thechatplace
from thechatplace import updater
from thechatplace.updater import (AVAILABLE, CURRENT, FAILED, NO_RELEASES, NOT_INSTALLED,
                                  UpdateService, data_is_outside_install_dir,
                                  latest_published_version)


class Release:
    def __init__(self, version):
        self.Version = version


class Update:
    def __init__(self, version):
        self.TargetFullRelease = Release(version)


class FakeManager:
    def __init__(self, update=None, portable=False, check_error=None):
        self.update = update
        self.portable = portable
        self.check_error = check_error
        self.downloaded = []
        self.applied = []

    def get_is_portable(self):
        return self.portable

    def check_for_updates(self):
        if self.check_error:
            raise RuntimeError(self.check_error)
        return self.update

    def download_updates(self, update):
        self.downloaded.append(update)

    def apply_updates_and_restart(self, update):
        self.applied.append(update)


def service(manager, latest="0.2.0", urls=None):
    def latest_fn():
        if isinstance(latest, Exception):
            raise latest
        return latest

    def factory(url):
        if urls is not None:
            urls.append(url)
        return manager
    return UpdateService("0.1.0", manager_factory=factory, latest=latest_fn)


def test_update_available_is_not_downloaded_until_asked():
    manager = FakeManager(Update("0.2.0"))
    urls = []
    svc = service(manager, urls=urls)
    result = svc.check()
    assert result.status == AVAILABLE and result.version == "0.2.0"
    assert result.describe() == "The Chat Place 0.2.0 is available. You have 0.1.0."
    # Velopack reads the feed from that one release, not the repo's newest 10.
    assert urls[-1] == ("https://github.com/TheIdeaPlace/AIChat/releases/download/"
                        "v0.2.0/")
    assert manager.downloaded == []            # nothing happens without a yes
    assert svc.apply_and_restart() is False   # can't apply what isn't downloaded
    assert svc.download() is True
    assert svc.apply_and_restart() is True
    assert manager.applied and manager.applied[0].TargetFullRelease.Version == "0.2.0"


def test_up_to_date_never_asks_velopack():
    manager = FakeManager(check_error="should not be called")
    result = service(manager, latest="0.1.0").check()
    assert result.status == CURRENT
    assert result.describe() == "The Chat Place is up to date (version 0.1.0)."


def test_no_releases_yet_is_said_plainly():
    result = service(FakeManager(None), latest=None).check()
    assert result.status == NO_RELEASES
    assert result.describe().startswith("No release of The Chat Place has been published yet.")


def test_newer_release_whose_feed_offers_nothing_is_a_failure_not_up_to_date():
    # The bug this design fixes: other apps' releases hid The Chat Place's feed
    # and Velopack said "nothing newer" while 0.2.0 was out.
    result = service(FakeManager(None), latest="0.2.0").check()
    assert result.status == FAILED
    assert "0.2.0 is published" in result.describe()
    assert "up to date" not in result.describe()


def test_feed_errors_are_reported_briefly_not_hidden():
    result = service(FakeManager(check_error="404 Not Found " * 30)).check()
    assert result.status == FAILED
    assert result.describe().startswith("Couldn't check for updates: 404 Not Found")
    assert len(result.detail) <= 160


def test_github_unreachable_is_a_failure():
    result = service(FakeManager(None), latest=OSError("offline")).check()
    assert result.status == FAILED
    assert "GitHub couldn't be reached (offline)" in result.describe()


def test_offline_copy_that_cannot_update_does_not_claim_there_is_no_release():
    svc = UpdateService("0.1.0", manager_factory=lambda url: FakeManager(portable=True),
                        latest=lambda: (_ for _ in ()).throw(OSError("offline")))
    result = svc.check()
    assert result.status == FAILED and "No release" not in result.describe()


def test_github_is_checked_with_the_windows_certificate_store():
    truststore = pytest.importorskip("truststore")
    assert isinstance(updater._ssl_context(), truststore.SSLContext)


def not_installed(url):
    raise RuntimeError("NotInstalled")  # what Velopack says from source


def test_source_copy_says_it_cannot_update():
    source = UpdateService("0.1.0", manager_factory=not_installed, latest=lambda: "0.3.0").check()
    assert source.status == NOT_INSTALLED and source.version == "0.3.0"
    assert source.describe() == ("This copy of The Chat Place is running from source, so it "
                                 "can't update itself. The latest release is 0.3.0.")
    source = UpdateService("0.1.0", manager_factory=not_installed, latest=lambda: None).check()
    assert source.status == NOT_INSTALLED
    assert "No release has been published yet" in source.describe()


def test_quiet_check_on_a_copy_that_cannot_update_asks_github_nothing():
    def latest():
        raise AssertionError("GitHub was asked")
    source = UpdateService("0.1.0", manager_factory=not_installed, latest=latest)
    assert source.check(manual=False).status == NOT_INSTALLED


def test_source_run_never_touches_velopack(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    svc = UpdateService("0.1.0", latest=lambda: None)
    assert svc.can_update is False
    assert svc.check().status == NOT_INSTALLED


def test_download_failure_is_reported():
    manager = FakeManager(Update("0.2.0"))

    def fail(update):
        raise OSError("disk full")
    manager.download_updates = fail
    svc = service(manager)
    svc.check()
    assert svc.download() is False
    assert svc.apply_and_restart() is False


def test_latest_published_version_filters_by_prefix():
    releases = [
        {"tag_name": "hyperv-manage-v0.9.2"},
        {"tag_name": "v0.1.0"},
        {"tag_name": "v0.10.0"},
        {"tag_name": "v0.2.0"},
        {"tag_name": "v9.0.0", "draft": True},
    ]
    assert latest_published_version(lambda url: json.dumps(releases).encode()) == "0.10.0"
    only_others = [{"tag_name": "hyperv-manage-v0.9.2"}]
    assert latest_published_version(lambda url: json.dumps(only_others).encode()) is None
    assert latest_published_version(lambda url: b"{}") is None
    odd = [None, "text", {"tag_name": "v0.3.0"}]
    assert latest_published_version(lambda url: json.dumps(odd).encode()) == "0.3.0"


def test_a_release_without_this_platforms_feed_is_passed_over():
    """Linux builds don't hold up a release (#230): one that went out
    without them isn't offered to Linux copies, which stay on the last
    release that has their feed."""
    def files(*channels):
        return [{"name": f"releases.{channel}.json"} for channel in channels]
    releases = [
        {"tag_name": "v0.3.0", "assets": files("windows", "osx")},
        {"tag_name": "v0.2.0", "assets": files("windows", "osx", "linux-x64", "linux-arm64")},
    ]
    fetch = lambda url: json.dumps(releases).encode()  # noqa: E731
    assert latest_published_version(fetch, channel="linux-x64") == "0.2.0"
    assert latest_published_version(fetch, channel="windows") == "0.3.0"


# -- data safety ---------------------------------------------------------------------------


def test_data_folder_is_outside_the_folder_velopack_replaces(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setattr(updater.platform_paths.sys, "platform", "win32")
    assert data_is_outside_install_dir()
    assert not data_is_outside_install_dir(tmp_path / "Local" / "TheChatPlace" / "data",
                                           tmp_path / "Local" / "TheChatPlace")
    assert not data_is_outside_install_dir(tmp_path / "Local" / "TheChatPlace",
                                           tmp_path / "Local" / "TheChatPlace")


def test_installed_copy_checks_the_folder_it_really_runs_from(tmp_path, monkeypatch):
    root = tmp_path / "Somewhere" / "TheChatPlace"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(root / "current" / "TheChatPlace.exe"))
    assert data_is_outside_install_dir(tmp_path / "Roaming" / "TheChatPlace")
    assert not data_is_outside_install_dir(root / "data")


def test_apply_is_refused_if_data_would_be_replaced(monkeypatch):
    manager = FakeManager(Update("0.2.0"))
    svc = service(manager)
    svc.check()
    svc.download()
    monkeypatch.setattr(updater, "data_is_outside_install_dir", lambda: False)
    assert svc.apply_and_restart() is False
    assert manager.applied == []


# -- one version ---------------------------------------------------------------------------


def test_version_is_plain_semver_for_vpk():
    assert re.fullmatch(r"\d+\.\d+\.\d+", thechatplace.__version__)


def test_release_notes_exist_for_this_version():
    notes = Path(__file__).resolve().parent.parent / "release-notes" / \
        f"v{thechatplace.__version__}.md"
    assert notes.is_file(), f"write {notes.name} before tagging"
    text = notes.read_text(encoding="utf-8")
    # What changed, under "What's new" (0.1.0, 0.1.1) or the Added, Changed and
    # Fixed sections the release-notes process in CLAUDE.md uses since 0.1.2.
    assert any(h in text for h in ("## What's new", "## Added", "## Changed", "## Fixed"))
    for heading in ("## Downloads", "## Requirements"):
        assert heading in text


@pytest.mark.parametrize("tag,ok", [("v0.1.0", True), ("v9.9.9", False),
                                    ("0.1.0", False)])
def test_tag_check_script(tag, ok):
    import subprocess
    script = Path(__file__).resolve().parent.parent / "tools" / "check_version.py"
    version = thechatplace.__version__
    tag = tag.replace("0.1.0", version) if ok else tag
    result = subprocess.run([sys.executable, str(script), tag], capture_output=True, text=True)
    assert (result.returncode == 0) is ok, result.stdout + result.stderr


# -- the Mac app, which updates itself through Velopack as GHManage's does ---------------


def test_mac_app_updates_although_velopack_calls_it_portable(monkeypatch):
    # Velopack's macOS locator says IsPortable for every .app; heeding that
    # would switch updates off for every Mac user.
    monkeypatch.setattr(updater, "PORTABLE_COPIES_EXIST", False)
    svc = service(FakeManager(Update("0.2.0"), portable=True))
    assert svc.can_update
    assert svc.check().status == AVAILABLE


# -- the Windows portable zip, which updates itself where it can (#747) ------------------


@pytest.fixture
def portable_zip(tmp_path, monkeypatch):
    """A release's portable zip, unzipped: Update.exe beside current\\."""
    root = tmp_path / "TheChatPlace"
    exe = root / "current" / "TheChatPlace.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    (root / "Update.exe").write_text("")
    monkeypatch.setattr(updater, "PORTABLE_COPIES_EXIST", True)
    monkeypatch.setattr(sys, "executable", str(exe))
    from thechatplace import build_info
    monkeypatch.setattr(build_info, "current", lambda: build_info.BuildInfo())
    return root


def test_windows_portable_zip_updates_itself(portable_zip):
    manager = FakeManager(Update("0.2.0"), portable=True)
    svc = service(manager)
    assert updater.portable_update_blocker() == ""
    assert svc.can_update
    assert svc.check().status == AVAILABLE
    assert svc.download() and svc.apply_and_restart()
    assert manager.applied
    # Still not an installed copy: no links, and What's New needs a record.
    assert svc.installed_copy is False
    assert service(FakeManager(Update("0.2.0"))).installed_copy is True


def test_windows_portable_zip_in_a_folder_it_cannot_change_says_so(portable_zip, monkeypatch):
    # A read-only drive, or a folder under Program Files.
    real = updater._can_write
    monkeypatch.setattr(updater, "_can_write",
                        lambda folder: folder != portable_zip.resolve() and real(folder))
    manager = FakeManager(Update("0.2.0"), portable=True)
    svc = service(manager)
    assert svc.can_update is False
    result = svc.check()
    assert result.status == NOT_INSTALLED and manager.downloaded == []
    assert result.describe().startswith(
        "The Chat Place can't update itself here: it can't change the folder it's running from")
    assert "Unzip it to a folder you can change" in result.describe()
    assert result.describe().endswith("The latest release is 0.2.0.")


def test_a_folder_is_writable_only_if_a_file_can_be_made_in_it(tmp_path, monkeypatch):
    assert updater._can_write(tmp_path)
    assert list(tmp_path.iterdir()) == []      # the probe leaves nothing behind
    assert not updater._can_write(tmp_path / "missing")

    def refuse(*a, **k):
        raise PermissionError("Access is denied")
    monkeypatch.setattr(updater.os, "open", refuse)
    assert not updater._can_write(tmp_path)


@windows_paths
def test_the_write_check_gives_up_at_once_in_a_folder_windows_refuses(tmp_path):
    # tempfile retries a refused name about two billion times on Windows, so
    # a check built on it never came back from Program Files.
    import subprocess
    import threading
    folder = tmp_path / "locked"
    folder.mkdir()
    user = subprocess.run(["whoami"], capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(["icacls", str(folder), "/deny", f"{user}:(W)"], capture_output=True,
                   check=True)
    try:
        found = []
        probe = threading.Thread(target=lambda: found.append(updater._can_write(folder)),
                                 daemon=True)
        probe.start()
        probe.join(10)
        assert not probe.is_alive(), "the write check didn't come back"
        assert found == [False]
    finally:
        subprocess.run(["icacls", str(folder), "/remove:d", user], capture_output=True)
    assert updater._can_write(folder)


def test_windows_portable_zip_holding_the_data_folder_does_not_update(portable_zip,
                                                                      monkeypatch):
    # Unzipped straight into the profile folder, where the data folder is too:
    # an update would replace it. Said before anything is downloaded.
    monkeypatch.setattr(updater.platform_paths, "app_data_dir",
                        lambda: portable_zip / "AppData" / "Roaming" / "TheChatPlace")
    manager = FakeManager(Update("0.2.0"), portable=True)
    result = service(manager).check()
    assert result.status == NOT_INSTALLED and manager.downloaded == []
    assert result.describe().startswith("The Chat Place can't update itself here: its "
                                        "settings and sessions are inside the folder")


def test_windows_portable_checks_are_made_once(portable_zip, monkeypatch):
    calls = []
    real = updater._can_write
    monkeypatch.setattr(updater, "_can_write", lambda folder: calls.append(folder) or
                        real(folder))
    manager = FakeManager(Update("0.2.0"), portable=True)
    asked = []
    manager.get_is_portable = lambda: asked.append(1) or True
    svc = service(manager)
    svc.check(manual=False)
    svc.check()
    assert svc.installed_copy is False
    assert len(asked) == 1 and len(calls) == 2   # the folder and current\, once


def test_portable_test_build_never_updates_itself(portable_zip, monkeypatch):
    # Run from its numbered folder (testing\tcp\0.6.4\bld05): updating it to
    # the release would leave the folder and Help, About disagreeing.
    from thechatplace import build_info
    monkeypatch.setattr(build_info, "current", lambda: build_info.BuildInfo(label="bld05"))
    manager = FakeManager(Update("0.2.0"), portable=True)
    svc = service(manager)
    assert svc.can_update is False
    result = svc.check()
    assert result.status == NOT_INSTALLED and manager.downloaded == []
    assert result.describe() == ("This is test build bld05, and test builds don't update "
                                 "themselves. The latest release is 0.2.0.")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert svc.portable_folder == ""             # so it never keeps a What's New record
    # An installed test build still takes the release (build_info).
    assert service(FakeManager(Update("0.2.0"))).check().status == AVAILABLE


def test_built_copy_without_its_updater_says_so_not_running_from_source(portable_zip,
                                                                         monkeypatch):
    # current\ copied on its own, or Update.exe gone.
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    unsure = FakeManager(Update("0.2.0"))

    def cannot_tell():
        raise RuntimeError("Update.exe not found")
    unsure.get_is_portable = cannot_tell
    for svc in (UpdateService("0.1.0", manager_factory=not_installed, latest=lambda: "0.2.0"),
                service(unsure)):
        result = svc.check()
        assert result.status == NOT_INSTALLED
        assert result.describe() == (
            "The Chat Place can't update itself here: it's missing the updater it came with. "
            "Download the installer or the portable zip again. The latest release is 0.2.0.")


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_a_mac_or_linux_copy_without_its_updater_says_to_download_it_again(monkeypatch,
                                                                           platform):
    # No installer or portable zip there, so it names the app instead.
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(updater, "mac_update_blocker", lambda: "")
    result = UpdateService("0.1.0", manager_factory=not_installed, latest=lambda: "0.2.0").check()
    assert result.describe() == (
        "The Chat Place can't update itself here: it's missing the updater it came with. "
        "Download The Chat Place again. The latest release is 0.2.0.")


def test_only_a_built_portable_zip_that_updates_has_a_folder_for_its_record(portable_zip,
                                                                             monkeypatch):
    portable = service(FakeManager(Update("0.2.0"), portable=True))
    assert portable.portable_folder == ""        # a source run (not frozen)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert portable.portable_folder == str(portable_zip.resolve())
    assert service(FakeManager(Update("0.2.0"))).portable_folder == ""   # installed


def test_each_platform_reads_its_own_feed():
    expected = {"win32": "windows", "darwin": "osx"}.get(sys.platform)
    if expected is None:  # Linux: one feed per processor
        import platform
        expected = ("linux-arm64" if platform.machine().lower() in ("aarch64", "arm64")
                    else "linux-x64")
    assert updater.CHANNEL == expected


def test_linux_has_a_feed_for_each_processor():
    assert updater._channel("linux", "x86_64") == "linux-x64"
    assert updater._channel("linux", "aarch64") == "linux-arm64"
    assert updater._channel("win32", "ARM64") == "windows"
    assert updater._channel("darwin", "arm64") == "osx"


def test_mac_app_bundle_is_the_install_folder(tmp_path, monkeypatch):
    app = tmp_path / "Applications" / "TheChatPlace.app"
    exe = app / "Contents" / "MacOS" / "TheChatPlace"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "executable", str(exe))
    support = tmp_path / "Library" / "Application Support" / "TheChatPlace"
    assert updater.data_is_outside_install_dir(support)
    assert not updater.data_is_outside_install_dir(app / "Contents" / "Resources" / "data")


def test_mac_app_that_cannot_replace_itself_says_where_to_put_it(tmp_path, monkeypatch):
    # Run from the mounted disk image: a read-only folder.
    exe = tmp_path / "The Chat Place.app" / "Contents" / "MacOS" / "TheChatPlace"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "executable", str(exe))
    assert updater.mac_update_blocker() == ""
    monkeypatch.setattr(updater.os, "access", lambda path, mode: False)
    assert "Applications" in updater.mac_update_blocker()
    svc = service(FakeManager(Update("0.2.0")))
    assert svc.can_update is False
    result = svc.check()
    assert result.status == NOT_INSTALLED and "0.2.0" in result.describe()
    assert "Drag The Chat Place to Applications" in result.describe()


def test_mac_app_runs_velopack_hooks_but_never_auto_applies_where_it_cannot(monkeypatch):
    import types
    applied = []

    class FakeApp:
        def set_auto_apply_on_startup(self, value):
            applied.append(value)
            return self

        def run(self):
            applied.append("ran")
    monkeypatch.setitem(sys.modules, "velopack", types.SimpleNamespace(App=FakeApp))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater, "mac_update_blocker", lambda: "")
    updater.bootstrap()
    monkeypatch.setattr(updater, "mac_update_blocker", lambda: "read-only")
    updater.bootstrap()
    assert applied == [True, "ran", False, "ran"]


def test_windows_never_auto_applies_over_the_data_folder(monkeypatch):
    # The portable zip unzipped into the profile folder (#747): a waiting
    # update isn't applied at start, as apply_and_restart refuses it too.
    import types
    applied = []

    class FakeApp:
        def set_auto_apply_on_startup(self, value):
            applied.append(value)
            return self

        def run(self):
            pass
    monkeypatch.setitem(sys.modules, "velopack", types.SimpleNamespace(App=FakeApp))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(updater, "data_is_outside_install_dir", lambda: True)
    updater.bootstrap([])
    monkeypatch.setattr(updater, "data_is_outside_install_dir", lambda: False)
    updater.bootstrap([])
    assert applied == [True, False]


def test_velopack_hooks_register_links_only_for_the_installed_copy(monkeypatch):
    # Velopack now runs its update hook for the portable zip too (#747), and
    # links must keep opening the installed copy.
    from thechatplace import links
    asked = []
    monkeypatch.setattr(links, "ensure_registered",
                        lambda installed, exe: asked.append(installed) or "done")
    for installed in (True, False):
        monkeypatch.setattr(updater, "is_installed_copy", lambda version: installed)
        updater._register_links("0.2.0")
    assert asked == [True, False]


def test_smoke_test_reports_whether_velopack_finds_the_updater(tmp_path, monkeypatch):
    from thechatplace import app

    class Manager:
        def get_current_version(self):
            return "0.1.0"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater, "_velopack_manager", lambda url: Manager())
    out = tmp_path / "smoke.json"
    app.smoke_test(str(out))
    assert json.loads(out.read_text())["updater"] == "0.1.0"

    def missing(url):
        raise RuntimeError("UpdateMac does not exist")
    monkeypatch.setattr(updater, "_velopack_manager", missing)
    app.smoke_test(str(out))
    assert json.loads(out.read_text())["updater"].startswith("unavailable: UpdateMac")


# -- "The Chat Place Update Installed" (#141) --------------------------------------------


from thechatplace.updater import InstalledNotice, installed_notice, parse_version  # noqa: E402


def test_notice_is_shown_on_the_first_start_after_an_update():
    assert installed_notice("0.1.3", "0.1.4", installed=True) == InstalledNotice(True, True)
    assert installed_notice("0.1.9", "0.2.0", installed=True).show
    assert installed_notice("0.9.9", "1.0.0", installed=True).show
    # Compared as numbers, not text.
    assert installed_notice("0.1.9", "0.1.10", installed=True).show


def test_notice_is_not_shown_on_a_first_run_but_the_version_is_recorded():
    assert installed_notice("", "0.1.4", installed=True) == InstalledNotice(True, False)


def test_notice_is_not_shown_or_recorded_on_the_same_version():
    assert installed_notice("0.1.4", "0.1.4", installed=True) == InstalledNotice(False, False)


def test_notice_is_not_shown_after_a_downgrade():
    assert installed_notice("0.1.5", "0.1.4", installed=True) == InstalledNotice(True, False)


@pytest.mark.parametrize("previous", ["garbage", "0.1.x", "0.1.3-beta", "1..2", " ", "0.1.3.4.5"])
def test_notice_fails_closed_on_a_version_it_cannot_read(previous):
    decision = installed_notice(previous, "0.1.4", installed=True)
    assert decision.show is False
    assert decision.record is True   # the running version replaces it
    assert installed_notice("0.1.3", previous, installed=True).show is False


def test_notice_is_never_shown_or_recorded_for_a_copy_that_is_not_installed():
    # A source run or the portable zip: swapping copies isn't an update.
    for previous in ("", "0.1.3", "0.1.5"):
        assert installed_notice(previous, "0.1.4", installed=False) == InstalledNotice(False, False)


def test_notice_is_shown_when_the_portable_zip_updated_itself():
    # Its own record says so (#747). Still never recorded as the last run, so
    # an installed copy's own notice is unchanged.
    assert installed_notice("0.1.3", "0.1.4", installed=False, updated_here=True) == \
        InstalledNotice(False, True)
    assert installed_notice("", "0.1.4", installed=False, updated_here=True).show
    assert not installed_notice("0.1.3", "0.1.4", installed=False, updated_here=True,
                                enabled=False).show


# -- the portable zip's own update record (#747) -------------------------------------------


from thechatplace.updater import (portable_update_record,  # noqa: E402
                                  portable_update_started)

HERE = r"D:\Tools\TheChatPlace"
THERE = r"E:\Stick\TheChatPlace"


def test_the_copy_that_updated_says_so_once():
    records = portable_update_record({}, HERE, "0.1.4")
    assert records == {HERE: "0.1.4"}
    assert portable_update_started(records, HERE, "0.1.4") == (True, True)
    # Its folder is compared as Windows does, whatever the case.
    assert portable_update_started(records, HERE.upper(), "0.1.4") == (True, True)


def test_another_copy_on_that_version_says_nothing_and_leaves_the_record():
    records = {HERE: "0.1.4"}
    assert portable_update_started(records, THERE, "0.1.4") == (False, False)
    # ...so the copy that downloaded it still says so when it installs it.
    assert portable_update_started(records, HERE, "0.1.4") == (True, True)
    # Each copy keeps its own; a new download replaces only that copy's.
    both = portable_update_record(records, THERE, "0.1.5")
    assert both == {HERE: "0.1.4", THERE: "0.1.5"}
    assert portable_update_record(both, HERE.lower(), "0.1.6") == {THERE: "0.1.5",
                                                                   HERE.lower(): "0.1.6"}


def test_a_source_run_or_test_build_never_reads_a_record():
    # Neither has a portable folder (UpdateService.portable_folder is "").
    assert portable_update_started({HERE: "0.1.4"}, "", "0.1.4") == (False, False)


def test_an_older_record_is_cleared_and_a_waiting_one_kept():
    # Started on a newer version than the record: long since passed.
    assert portable_update_started({HERE: "0.1.4"}, HERE, "0.1.6") == (False, True)
    # Still on the old version (the update waits for the next start).
    assert portable_update_started({HERE: "0.1.4"}, HERE, "0.1.3") == (False, False)
    assert portable_update_started({HERE: "junk"}, HERE, "0.1.4") == (False, True)


@pytest.mark.parametrize("legacy", ["0.1.4", ["0.1.4"], None, 7, {HERE: 4}, {"": "0.1.4"}])
def test_a_record_it_cannot_read_counts_as_none(legacy):
    # A bare version (an early form of this record) or a hand edit.
    assert updater.portable_update_records(legacy) == {}
    assert portable_update_started(legacy, HERE, "0.1.4") == (False, False)
    assert portable_update_record(legacy, HERE, "0.1.5") == {HERE: "0.1.5"}


def test_notice_turned_off_still_records_the_version():
    assert installed_notice("0.1.3", "0.1.4", installed=True, enabled=False) == \
        InstalledNotice(True, False)


def test_parse_version():
    assert parse_version("0.1.4") == (0, 1, 4, 0)
    assert parse_version("v0.1.4") == (0, 1, 4, 0)
    assert parse_version("1") == (1, 0, 0, 0)
    assert parse_version("0.1.4") == parse_version("0.1.4.0")
    for bad in ("", None, "abc", "0.1.4-rc1", "0.1.", "-1.0"):
        assert parse_version(bad) is None


def test_release_notes_url_is_the_release_tag_page():
    assert updater.release_notes_url("0.1.4") == \
        "https://github.com/TheIdeaPlace/AIChat/releases/tag/v0.1.4"


def test_help_update_item_label_says_the_running_version_or_the_update():
    from thechatplace.ui_text import update_item_label, update_item_spoken
    assert update_item_label("0.1.4") == "Check for &Updates (running 0.1.4)..."
    assert update_item_label("0.1.4", "0.1.5") == "&Update to 0.1.5 Available..."
    # Alt+H, U works either way.
    for label in (update_item_label("0.1.4"), update_item_label("0.1.4", "0.1.5")):
        assert label.count("&") == 1 and label[label.index("&") + 1] == "U"
    assert update_item_spoken("0.1.4") == "Check for Updates"
    assert update_item_spoken("0.1.4", "0.1.5") == "Update to 0.1.5 Available"
