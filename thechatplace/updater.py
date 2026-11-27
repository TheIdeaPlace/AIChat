"""Automatic updates through Velopack, adapted from GHManage's ``updater.py``.

How it fits together
--------------------
* ``bootstrap()`` runs first thing in ``main()``, before the single-instance
  check or any window. Velopack's Update.exe starts the app with hook
  arguments when it installs, updates or uninstalls; ``bootstrap()`` handles
  those and exits. A source run does nothing here.
* ``UpdateService.check()`` asks GitHub for a newer release and says what it
  found, as a ``CheckResult`` the UI turns into words. Nothing is downloaded
  until Kelly agrees: ``download()`` then ``apply_and_restart()``.

The Chat Place publishes on four Velopack channels, ``windows``, ``osx``,
``linux-x64`` and ``linux-arm64`` (``CHANNEL`` picks this platform's; Linux
has one per processor, because an AppImage holds programs for one): their
feed files are
``releases.<channel>.json`` and ``assets.<channel>.json``. The updater finds
the newest ``v*`` release itself, rather than through Velopack's own GitHub
source (which reads only the repo's 10 newest releases), and points Velopack
at that one release's files. Each release carries every package its feed names (the workflow
uploads the previous full package with the new one).

On macOS the app updates itself the same way, as GHManage's does. ``vpk pack``
puts Velopack's updater (``Contents/MacOS/UpdateMac``) inside TheChatPlace.app,
and the disk image is made from that packed app, so a copy dragged anywhere
updates itself. Velopack calls every Mac app "portable", so the portable check
is Windows-only (``PORTABLE_COPIES_EXIST``).

On Windows the portable zip updates itself too (#747): it carries Velopack's
Update.exe beside ``current\\``, which an update replaces. It doesn't when it
can't write that folder or holds the data folder (``portable_update_blocker``),
or when it's a test build. It still never registers links, its What's New
notice goes by its own record (``portable_update_started``), and bug reports
call it portable.

On Linux the app is one AppImage file, and Velopack updates it by replacing
that file (asking for a password through pkexec only if it's somewhere you
can't write).

Updating never touches The Chat Place's data. Velopack replaces the app
(``%LOCALAPPDATA%\\TheChatPlace`` on Windows, or the portable folder's
``current\\``; the .app bundle on a Mac; the .AppImage on Linux); sessions, settings and logs live in
``%APPDATA%\\TheChatPlace``, ``~/Library/Application Support/TheChatPlace``
or ``~/.config/TheChatPlace``, which neither an update nor an uninstall goes
near. ``data_is_outside_install_dir`` checks that.
"""
from __future__ import annotations

import json
import logging
import os
import re
import ssl
import sys
import threading
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

from . import platform_paths

logger = logging.getLogger("thechatplace.updater")

REPO_URL = "https://github.com/TheIdeaPlace/AIChat"
RELEASES_API = "https://api.github.com/repos/TheIdeaPlace/AIChat/releases?per_page=100"
TAG_PREFIX = "v"


def _channel(platform: str = sys.platform, machine: str = "") -> str:
    """This platform's Velopack channel."""
    if platform == "win32":
        return "windows"
    if platform == "darwin":
        return "osx"
    import platform as _platform
    machine = (machine or _platform.machine()).lower()
    return "linux-arm64" if machine in ("aarch64", "arm64") else "linux-x64"


CHANNEL = _channel()
INCLUDE_PRERELEASES = True
#: Only Windows has portable copies (the zip). They update themselves (#747),
#: but never register links, and bug reports call them portable. Velopack's
#: macOS locator reports IsPortable for every .app, so checking it there would
#: call every Mac copy portable (GHManage found this).
PORTABLE_COPIES_EXIST = sys.platform == "win32"

# CheckResult.status values
AVAILABLE = "available"
CURRENT = "current"
NO_RELEASES = "no releases"
NOT_INSTALLED = "not installed"
FAILED = "failed"


def feed_url(version: str) -> str:
    """Where one release's feed and packages are downloaded from."""
    return f"{REPO_URL}/releases/download/{TAG_PREFIX}{version}/"


def release_notes_url(version: str) -> str:
    """The GitHub page of one release, with its notes (What's New, #141)."""
    return f"{REPO_URL}/releases/tag/{TAG_PREFIX}{version}"


def parse_version(text: str) -> Optional[Tuple[int, ...]]:
    """``"0.1.4"`` (or ``"v0.1.4"``) as numbers to compare, or None for
    anything else: a pre-release suffix, a blank, text a hand edit left in
    the settings. Unlike ``_version_key`` it never guesses, because the
    update-installed notice must fail closed (#141)."""
    text = str(text or "").strip()
    if text[:1] in ("v", "V"):
        text = text[1:]
    if not re.fullmatch(r"\d+(?:\.\d+){0,3}", text):
        return None
    parts = tuple(int(piece) for piece in text.split("."))
    return parts + (0,) * (4 - len(parts))


@dataclass(frozen=True)
class InstalledNotice:
    """What to do at start about "The Chat Place Update Installed" (#141).

    ``record``: save the running version as the last one run.
    ``show``: show the dialog saying the app was updated to it."""
    record: bool
    show: bool


def installed_notice(previous: str, current: str, installed: bool,
                     enabled: bool = True, updated_here: bool = False) -> InstalledNotice:
    """Whether this start is the first since an update was installed, as
    QuickMail's MaybeShowUpdateInstalledNotice decides it.

    ``previous`` is the version recorded at the last start ("" if none),
    ``installed`` whether this copy is one Velopack installed and updates,
    ``enabled`` the setting that turns the notice off, ``updated_here``
    whether this portable copy is starting on the update it applied itself
    (``portable_update_started``).

    * Only an installed copy records anything: a source run or the portable
      zip changing version is usually someone swapping copies, not an
      update, and recording it would announce updates that never happened
      when an installed copy next starts.
    * The portable zip tells only when it starts on the update it applied
      itself (#747). Unzipping another copy leaves no such record.
    * Nothing on a first run (no record): nothing was updated as far as the
      user knows. Nothing on a downgrade, or when either version can't be
      read: the notice fails closed.
    * The version is recorded even when the setting is off, so turning it
      back on doesn't announce an old update."""
    if not installed:
        return InstalledNotice(record=False, show=bool(updated_here and enabled))
    if str(previous or "").strip() == current:
        return InstalledNotice(record=False, show=False)
    old, new = parse_version(previous), parse_version(current)
    newer = old is not None and new is not None and new > old
    return InstalledNotice(record=True, show=bool(newer and enabled))


def portable_update_record(records, folder: str, version: str) -> dict:
    """``records`` (the ``portable_updates`` setting) with this portable
    copy's update to ``version`` in it: recorded before it's applied (#747).
    Keyed by the copy's folder, as the data folder is shared by every copy."""
    kept = {where: was for where, was in portable_update_records(records).items()
            if where.casefold() != folder.casefold()}
    kept[folder] = version
    return kept


def portable_update_started(records, folder: str, current: str) -> Tuple[bool, bool]:
    """``(show, clear)`` for the portable copy in ``folder`` starting on
    ``current``: whether it's the update this copy applied, and whether its
    record is done with (that version or a newer one has started). Another
    copy's record is left alone, and "" (not a portable copy) never matches."""
    if not folder:
        return False, False
    for where, version in portable_update_records(records).items():
        if where.casefold() != folder.casefold():
            continue
        wanted, now = parse_version(version), parse_version(current)
        if now is None:
            return False, False
        if wanted is None or now > wanted:
            return False, True       # unreadable, or long since passed
        return now == wanted, now == wanted
    return False, False


def portable_update_records(records) -> dict:
    """The readable part of the ``portable_updates`` setting: anything else
    (a bare version string, say) counts as no record."""
    if not isinstance(records, dict):
        return {}
    return {where: version for where, version in records.items()
            if isinstance(where, str) and where and isinstance(version, str)}


def configure_logging() -> None:
    """Update activity goes to %APPDATA%\\TheChatPlace\\update.log, because a
    silently broken updater can't be diagnosed any other way."""
    try:
        path = platform_paths.app_data_dir() / "update.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    except Exception:  # noqa: BLE001 - logging must never stop the app starting
        pass


def mac_update_blocker(executable: Optional[str] = None) -> str:
    """Why this Mac app can't replace itself where it is, or "" if it can.

    Velopack swaps the whole .app, so it needs to write the bundle and the
    folder it's in. It can't when the app runs from the mounted disk image,
    from a quarantined download macOS runs from a read-only copy
    (AppTranslocation), or from a folder another user owns."""
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return ""
    bundle = Path(executable or sys.executable).resolve().parents[2]
    if os.access(bundle, os.W_OK) and os.access(bundle.parent, os.W_OK):
        return ""
    return ("it can't change the folder it's running from (the disk image, say). Drag The Chat "
            "Place to Applications and open it from there, and it will update itself")


def portable_folder(executable: Optional[str] = None) -> Path:
    """The portable copy's folder: the one holding Update.exe and ``current\\``."""
    return Path(executable or sys.executable).resolve().parent.parent


def portable_update_blocker(executable: Optional[str] = None) -> str:
    """Why this portable copy (the Windows zip) can't update itself where it
    is, or "" if it can (#747). Said before an update is offered, rather
    than after it's downloaded, and a blocked copy never applies one at start.

    Velopack replaces ``current\\`` in the folder holding Update.exe, so it
    needs to write there, which it can't on a read-only drive or under
    Program Files. And the data folder mustn't be inside it, as it can be
    when the zip is unzipped straight into the profile folder. (A test build
    never updates either: ``UpdateService`` checks its label.)"""
    root = portable_folder(executable)
    if not data_is_outside_install_dir(install_root=root):
        return ("its settings and sessions are inside the folder it's running from, which an "
                "update replaces. Unzip it to a folder of its own, such as one in Documents, "
                "and it will update itself")
    if _can_write(root) and _can_write(root / "current"):
        return ""
    return ("it can't change the folder it's running from (a read-only drive, say, or one in "
            "Program Files). Unzip it to a folder you can change, such as one in Documents, "
            "and it will update itself")


def _can_write(folder: Path) -> bool:
    """Whether a file can be made in ``folder``: one try, with a name no
    other file has. (os.access can't tell on Windows, where it reads only a
    folder's read-only attribute, and tempfile retries a refused name about
    two billion times there, believing it was taken.)"""
    probe = Path(folder) / f".tcp-write-check-{uuid.uuid4().hex}"
    try:
        os.close(os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except OSError:
        return False
    try:
        probe.unlink()
    except OSError:
        pass
    return True


def bootstrap(argv: Optional[list] = None) -> None:
    """Run Velopack's install/update/uninstall hooks. Call first in main().

    ``argv`` is the command line without the program. Started with a
    thechatplace:// link (#144), Velopack is given no arguments at all: a
    link is the one thing a web page can put on The Chat Place's command
    line, and it must never be read as one of Velopack's hook options."""
    if not getattr(sys, "frozen", False):
        return
    try:
        import velopack
    except ImportError:
        return
    from . import links

    try:
        # A package Kelly agreed to, downloaded but not yet applied (say the
        # restart failed), is applied here before any window appears; never
        # where it can't be, or every start would try again. On Windows, never
        # where the data folder is inside what it replaces (the portable zip
        # unzipped into the profile folder, #747); apply_and_restart refuses
        # that too.
        auto_apply = not mac_update_blocker() and (sys.platform != "win32"
                                                   or data_is_outside_install_dir())
        app = velopack.App().set_auto_apply_on_startup(auto_apply)
        if links.link_argument(sys.argv[1:] if argv is None else argv) is not None:
            app = app.set_args([])
        # Windows: thechatplace:// is registered for this user when the app
        # is installed or updated, and removed when it's uninstalled.
        if sys.platform == "win32":
            # One at a time, so a Velopack without one of them still runs
            # (and still applies a waiting update).
            for name, hook in (("on_after_install_fast_callback", _register_links),
                               ("on_after_update_fast_callback", _register_links),
                               ("on_before_uninstall_fast_callback", _unregister_links)):
                try:
                    app = getattr(app, name)(hook)
                except Exception as exc:  # noqa: BLE001
                    logger.error("Velopack hook %s unavailable: %s", name, exc)
        app.run()
    except Exception as exc:  # noqa: BLE001
        logger.error("Velopack bootstrap failed: %s", exc)


def _register_links(*_version) -> None:
    """Velopack's install and update hooks: register thechatplace:// links
    for this installed copy, never for the portable zip, which Velopack now
    updates too (#747). Never raises (a failed hook fails the install)."""
    try:
        from . import __version__, links

        logger.info("link registration: %s",
                    links.ensure_registered(is_installed_copy(__version__), sys.executable))
    except Exception as exc:  # noqa: BLE001
        logger.error("link registration failed: %s", exc)


def _unregister_links(*_version) -> None:
    """Velopack's uninstall hook: remove this copy's thechatplace:// links."""
    try:
        from . import links

        if links.remove_registration(sys.executable):
            logger.info("link registration removed")
    except Exception as exc:  # noqa: BLE001
        logger.error("removing the link registration failed: %s", exc)


def is_installed_copy(version: str, factory: Optional[Callable[[str], object]] = None,
                      frozen: Optional[bool] = None) -> bool:
    """Whether this is a copy Velopack installed (Windows), not the portable
    zip or a source run: what link registration and bug reports go by.
    Never raises; False when it can't tell."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not frozen:
        return False
    try:
        manager = (factory or _velopack_manager)(feed_url(version))
        return not manager.get_is_portable()
    except Exception as exc:  # noqa: BLE001
        logger.info("couldn't tell whether this copy is installed: %s", exc)
        return False


def data_is_outside_install_dir(data_dir: Optional[Path] = None,
                                install_root: Optional[Path] = None) -> bool:
    """True when The Chat Place's data folder is outside the folder Velopack
    replaces on update and deletes on uninstall."""
    data_dir = Path(data_dir or platform_paths.app_data_dir()).resolve()
    if install_root is None:
        if getattr(sys, "frozen", False):
            executable = Path(sys.executable).resolve()
            # Windows: the "current" folder inside the install root. Mac: the
            # .app bundle (TheChatPlace.app/Contents/MacOS/TheChatPlace).
            # Linux: the folder the AppImage is mounted on while it runs.
            install_root = (executable.parents[2] if sys.platform == "darwin"
                            else executable.parent.parent)
        else:
            local = os.environ.get("LOCALAPPDATA")
            if not local:
                return True
            install_root = Path(local) / "TheChatPlace"
    install_root = Path(install_root).resolve()
    return install_root != data_dir and install_root not in data_dir.parents


@dataclass
class CheckResult:
    status: str
    current: str
    version: str = ""          # the newer version, or the latest published one
    detail: str = ""
    label: str = ""            # a portable test build's label ("bld05"), which never updates

    def describe(self) -> str:
        """The result in words, for the status bar and speech."""
        if self.status == AVAILABLE:
            return (f"The Chat Place {self.version} is available. You have {self.current}.")
        if self.status == CURRENT:
            return f"The Chat Place is up to date (version {self.current})."
        if self.status == NO_RELEASES:
            return (f"No release of The Chat Place has been published yet. You have version "
                    f"{self.current}.")
        if self.status == NOT_INSTALLED:
            latest = (f" The latest release is {self.version}." if self.version else
                      " No release has been published yet.")
            if self.label:
                return (f"This is test build {self.label}, and test builds don't update "
                        f"themselves.{latest}")
            if self.detail:  # can't replace itself where it is, or has no updater
                return f"The Chat Place can't update itself here: {self.detail}.{latest}"
            return ("This copy of The Chat Place is running from source, so it can't update "
                    f"itself.{latest}")
        return f"Couldn't check for updates: {self.detail or 'unknown error'}."


def latest_published_version(fetch: Optional[Callable[[str], bytes]] = None,
                             channel: Optional[str] = None) -> Optional[str]:
    """The newest v* release on GitHub that has this platform's feed, or
    None if there is none. A release whose Linux build failed goes out
    without the Linux files (#230); Linux copies then stay on the release
    before it. Raises on network errors (the caller reports them)."""
    fetch = fetch or _fetch
    feed = f"releases.{channel or CHANNEL}.json"
    releases = json.loads(fetch(RELEASES_API).decode("utf-8"))
    versions = []
    for release in releases if isinstance(releases, list) else []:
        if not isinstance(release, dict):
            continue
        tag = str(release.get("tag_name") or "")
        if release.get("draft") or not tag.startswith(TAG_PREFIX):
            continue
        assets = release.get("assets")
        if isinstance(assets, list) and assets and not any(
                isinstance(asset, dict) and asset.get("name") == feed for asset in assets):
            continue
        versions.append(tag[len(TAG_PREFIX):])
    if not versions:
        return None
    return max(versions, key=_version_key)


def _version_key(version: str):
    """Sorts versions as Velopack does for ours: a test build's pre-release
    ("0.6.4-bld05") comes before its release ("0.6.4"), so an installed
    test build is offered the real release (build_info)."""
    number, _dash, pre = version.partition("-")
    parts = []
    for piece in number.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    parts += [0] * (3 - len(parts))
    return (parts, 0 if pre else 1, pre)


def _ssl_context() -> ssl.SSLContext:
    """Verify certificates the way Windows does. Python's own check fails on a
    PC whose certificate store hasn't yet fetched GitHub's root (seen on a
    fresh Windows install), where Windows itself would fetch it."""
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # noqa: BLE001 - not installed: Python's own check
        return ssl.create_default_context()


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "TheChatPlace-updater"})
    with urllib.request.urlopen(request, timeout=15,  # noqa: S310
                                context=_ssl_context()) as response:
        return response.read()


class UpdateService:
    """Checks for, downloads and applies updates. Every method is safe to
    call from a worker thread and never raises.

    ``manager_factory`` takes the feed URL of the release to update from and
    returns a Velopack UpdateManager (tests pass a fake)."""

    def __init__(self, current_version: str,
                 manager_factory: Optional[Callable[[str], object]] = None,
                 latest: Callable[[], Optional[str]] = latest_published_version) -> None:
        self.current_version = current_version
        self._factory = manager_factory or _velopack_manager
        self._latest = latest
        self._manager = None
        self._pending = None
        self._downloaded = False
        self._lock = threading.Lock()
        self._copy: Optional[_Copy] = None   # worked out once (_this_copy)

    # -- the manager --------------------------------------------------------------

    def _installed(self) -> bool:
        """False for a source run, where Velopack is never touched."""
        return bool(getattr(sys, "frozen", False)) or self._factory is not _velopack_manager

    def _make_manager(self, url: str):
        """An UpdateManager for ``url``, or None if this copy can't update itself.
        Making one reads nothing from the network."""
        return self._manager_and_blocker(url)[0]

    def _manager_and_blocker(self, url: str):
        """``(manager, "", "")``, or ``(None, why, label)`` when this copy
        can't update itself: why in words ("" for a source run), or a
        portable test build's label."""
        if not self._installed():
            return None, "", ""
        blocker = mac_update_blocker()
        if blocker:
            return None, blocker, ""
        try:
            manager = self._factory(url)
        except Exception as exc:  # noqa: BLE001 - not installed, or no velopack
            logger.info("update manager unavailable: %s", exc)
            return None, _missing_updater(), ""
        if not PORTABLE_COPIES_EXIST:
            return manager, "", ""
        copy = self._this_copy(manager)
        if copy is None:
            return None, _missing_updater(), ""
        if copy.label or copy.blocker:
            return None, copy.blocker, copy.label
        return manager, "", ""

    def _this_copy(self, manager) -> Optional["_Copy"]:
        """Whether this Windows copy is the portable zip, and if so what stops
        it updating, worked out once (a write check, a Velopack call). None
        when Velopack can't say."""
        with self._lock:
            if self._copy is not None:
                return self._copy
        try:
            portable = bool(manager.get_is_portable())
        except Exception as exc:  # noqa: BLE001
            logger.info("couldn't tell whether this copy is installed: %s", exc)
            return None
        copy = _Copy(portable=False)
        if portable:
            from . import build_info
            label = build_info.current().label
            # A test build is run from the numbered folder it was unzipped
            # into; updating it to the release would leave the folder and
            # Help, About disagreeing. An installed one still takes it.
            copy = _Copy(portable=True, label=label,
                         blocker="" if label else portable_update_blocker())
            if copy.label or copy.blocker:
                logger.info("portable copy; updates disabled: %s",
                            copy.blocker or f"test build {copy.label}")
        with self._lock:
            self._copy = copy
        return copy

    @property
    def can_update(self) -> bool:
        return self._make_manager(feed_url(self.current_version)) is not None

    @property
    def installed_copy(self) -> bool:
        """Whether this copy updates itself and Velopack installed it, so a
        new version at start is an update it took. False for the portable
        zip, which may be another copy someone unzipped (#747)."""
        manager = self._make_manager(feed_url(self.current_version))
        if manager is None:
            return False
        if not PORTABLE_COPIES_EXIST:
            return True
        copy = self._this_copy(manager)
        return copy is not None and not copy.portable

    @property
    def portable_folder(self) -> str:
        """This copy's folder if it's a built portable zip that updates
        itself, the key of its What's New record (#747); "" for any other
        copy: installed, a test build, blocked or a source run."""
        if not getattr(sys, "frozen", False) or not PORTABLE_COPIES_EXIST:
            return ""
        manager = self._make_manager(feed_url(self.current_version))
        copy = self._this_copy(manager) if manager is not None else None
        if copy is None or not copy.portable:
            return ""
        return str(portable_folder())

    # -- checking ------------------------------------------------------------------

    def check(self, manual: bool = True) -> CheckResult:
        """``manual`` is False for the quiet check at start, which doesn't
        ask GitHub anything on a copy that can't update."""
        if not manual and not self.can_update:
            return CheckResult(NOT_INSTALLED, self.current_version)
        try:
            latest = self._latest()
        except Exception as exc:  # noqa: BLE001
            logger.error("couldn't list releases: %s", exc)
            return CheckResult(FAILED, self.current_version,
                               detail=_short(f"GitHub couldn't be reached ({exc})"))
        manager, blocker, label = self._manager_and_blocker(
            feed_url(latest or self.current_version))
        if manager is None:
            return CheckResult(NOT_INSTALLED, self.current_version, latest or "", blocker, label)
        if latest is None:
            logger.info("no releases published yet")
            return CheckResult(NO_RELEASES, self.current_version)
        if _version_key(latest) <= _version_key(self.current_version):
            logger.info("up to date")
            return CheckResult(CURRENT, self.current_version, latest)
        try:
            update = manager.check_for_updates()
        except Exception as exc:  # noqa: BLE001
            logger.error("update check failed: %s", exc)
            return CheckResult(FAILED, self.current_version, latest, _short(str(exc)))
        if not update:
            # GitHub has a newer release but its feed doesn't offer it. Never
            # call that "up to date".
            logger.error("release %s is published but its feed offered no update", latest)
            return CheckResult(FAILED, self.current_version, latest,
                               f"version {latest} is published, but its update files "
                               "couldn't be read")
        version = str(update.TargetFullRelease.Version)
        logger.info("update %s available", version)
        with self._lock:
            self._manager = manager
            self._pending = update
            self._downloaded = False
        return CheckResult(AVAILABLE, self.current_version, version)

    # -- applying --------------------------------------------------------------------

    def download(self) -> bool:
        """Download the update found by check(). Blocks; run it off the UI thread."""
        with self._lock:
            manager, pending = self._manager, self._pending
        if manager is None or pending is None:
            return False
        try:
            manager.download_updates(pending)
            self._downloaded = True
            logger.info("update downloaded")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("download failed: %s", exc)
            return False

    def apply_and_restart(self) -> bool:
        """Install the downloaded update and start the new version. The
        process exits on success, so a True return is rarely seen."""
        with self._lock:
            manager, pending = self._manager, self._pending
        if manager is None or pending is None or not self._downloaded:
            return False
        if not data_is_outside_install_dir():
            logger.error("refusing to update: the data folder is inside the install folder")
            return False
        try:
            manager.apply_updates_and_restart(pending)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("apply and restart failed: %s", exc)
            return False


@dataclass(frozen=True)
class _Copy:
    """What kind of Windows copy this is, for ``UpdateService``."""
    portable: bool        # Velopack's portable zip
    label: str = ""       # a test build's label: it never updates itself
    blocker: str = ""     # why it can't update where it is (portable_update_blocker)


def _missing_updater() -> str:
    """Why a built copy whose Velopack can't be reached doesn't update:
    copied without its updater (``current\\`` alone, say). "" from source,
    where that's expected."""
    if not getattr(sys, "frozen", False):
        return ""
    again = ("the installer or the portable zip" if sys.platform == "win32"
             else "The Chat Place")
    return f"it's missing the updater it came with. Download {again} again"


def _velopack_manager(url: str):
    import velopack

    options = velopack.UpdateOptions(False, 10, CHANNEL)
    return velopack.UpdateManager(velopack.HttpSource(url), options)


def _short(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
