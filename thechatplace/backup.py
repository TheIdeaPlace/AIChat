"""Back Up Now (#433): one zip of the AI history, memory and settings on this
computer, so a lost or replaced machine doesn't take them with it.

The archive is self-contained: ``manifest.json`` plus every file under
``<provider>/<area>/<relative path>``. The manifest records where each area
came from on this computer, the size and SHA-256 of every file, what was
left out and why, and anything that couldn't be read, so a backup that is
incomplete says so instead of looking finished.

Each provider's file knowledge stays with that provider (``providers/
claude_backup.py``, ``codex_backup.py``, ``copilot_backup.py``): which folders
hold history and setup, and which are caches, runtime files or credentials.
This module only copies what they list. It never writes to any source
folder; a SQLite database is copied through SQLite's backup API (a
consistent copy of that one database even while its program has it open,
WAL included), never as its raw main file.

The zip is written under a temporary name in the destination folder and only
renamed into place after it has been read back and every file's checksum
matched, so a failed or interrupted backup never looks like a good one and
never replaces one. Restore is a later step (#433, with #228's import).

With a passphrase (#435) the backup is made as usual, then put whole, as
``backup.zip``, inside a zip whose one entry is AES-256 encrypted the WinZip
way. Encrypting the entries of the backup itself would leave their names in
plain text, and Claude's project folders are named after whole paths. So
nothing shows but that one name; 7-Zip or WinZip opens it with the
passphrase. The passphrase is never stored: forgotten, the backup is lost.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import platform
import shutil
import socket
import sqlite3
import sys
import tempfile
import time
from contextlib import contextmanager
import uuid
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence

import pyzipper

from . import __version__, platform_paths

FORMAT = "thechatplace-backup"
FORMAT_VERSION = 1
MANIFEST = "manifest.json"
FILE_PREFIX = "TheChatPlace-backup-"
PARTIAL_SUFFIX = ".partial"
_CHUNK = 1024 * 1024
# A manifest lists every file; even 100,000 of them is a few tens of MB. One
# far bigger than this isn't ours, and isn't read into memory.
MANIFEST_LIMIT = 256 * 1024 * 1024

# Never copied, whoever lists them: sign-in tokens and keys. Histories can
# still hold secrets someone pasted; the user guide says to treat a backup
# as private.
CREDENTIAL_NAMES = frozenset({
    ".credentials.json", "auth.json", "credentials.json", ".netrc",
    "id_rsa", "id_ed25519", "token.json", "oauth.json",
})
CREDENTIAL_DIRS = frozenset({"mcp-oauth-config", "mcp-secrets"})
# A database's write-ahead log and shared memory go in with the database
# itself (the backup API reads them); copied raw they'd be torn.
SQLITE_SIDECARS = ("-wal", "-shm", "-journal")
SQLITE_SUFFIXES = (".sqlite", ".sqlite3", ".db")
# Half-written files programs rename into place: never history, and they
# come and go while a backup runs.
TEMPORARY_PATTERNS = ("*.tmp", "*.tmp.*", "*.partial", "*~")


@dataclass
class Item:
    """One file to put in the archive."""
    source: Path
    arcname: str          # forward slashes, relative to the archive root
    sqlite: bool = False  # copy through SQLite's backup API


@dataclass
class Area:
    """A folder (or a file's folder) on this computer and what came from it."""
    name: str             # e.g. "claude/home"
    root: str             # where it was on this computer
    description: str


@dataclass
class Inventory:
    """What one provider (or The Chat Place itself) wants backed up."""
    provider: str                     # "thechatplace", "claude", "codex", "copilot"
    label: str                        # for people: "Claude", "Codex", ...
    areas: List[Area] = field(default_factory=list)
    items: List[Item] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)   # "cache/ (rebuilt by Claude)"
    notes: List[str] = field(default_factory=list)
    # What couldn't be read while listing (an unreadable folder, a bad
    # config): any of these makes the backup incomplete.
    failures: List[str] = field(default_factory=list)
    version: Optional[str] = None


@dataclass
class Result:
    path: Optional[Path]
    files: int = 0
    bytes: int = 0
    archive_bytes: int = 0
    providers: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    changed: List[str] = field(default_factory=list)
    gone: List[str] = field(default_factory=list)
    encrypted: bool = False
    # Entries part-written when their source failed mid-read: in the zip,
    # but not a copy of anything.
    unusable: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    complete: bool = False


@dataclass
class Check:
    ok: bool
    files: int = 0
    problems: List[str] = field(default_factory=list)
    manifest: Optional[dict] = None
    # Set when the backup is encrypted and no passphrase, or the wrong one,
    # was given: ask for it and check again.
    needs_passphrase: bool = False
    encrypted: bool = False


class PassphraseError(ValueError):
    """An encrypted backup and no passphrase ("needed"), the wrong one
    ("wrong"), or one that failed only the final check, which a wrong
    passphrase or damage can cause ("wrong-or-damaged")."""


# The one entry of an encrypted backup: the whole backup, encrypted.
INNER = "backup.zip"

PASSPHRASE_PROBLEMS = {
    "needed": "It's encrypted; its passphrase is needed.",
    "wrong": "The passphrase is wrong.",
    "wrong-or-damaged": "The passphrase is wrong, or the backup is damaged.",
}


def is_encrypted(path) -> bool:
    """True for an encrypted backup: one encrypted entry, backup.zip."""
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
    except (OSError, zipfile.BadZipFile, ValueError, EOFError):
        return False
    return len(infos) == 1 and infos[0].filename == INNER and bool(infos[0].flag_bits & 0x1)


def _encrypt(inner: Path, outer: Path, passphrase: str, cancelled=None) -> str:
    """Put ``inner`` whole into ``outer`` as its one AES-256 entry (stored:
    it's compressed already). Returns the SHA-256 of what went in."""
    digest = hashlib.sha256()
    with pyzipper.AESZipFile(outer, "w", compression=pyzipper.ZIP_STORED, allowZip64=True,
                             encryption=pyzipper.WZ_AES) as archive:
        archive.setpassword(passphrase.encode("utf-8"))
        archive.setencryption(pyzipper.WZ_AES, nbits=256)
        info = archive.zipinfo_cls(INNER, date_time=time.localtime()[:6])
        info.compress_type = pyzipper.ZIP_STORED
        with open(inner, "rb") as source, archive.open(info, "w", force_zip64=True) as out:
            _hash_into(source, out, digest, cancelled)
    return digest.hexdigest()


def _decrypt_to(path, passphrase: Optional[str], out, cancelled=None) -> str:
    """Write an encrypted backup's inner zip to ``out`` (a file, or None to
    only read it) and return its SHA-256. Raises PassphraseError. Whether
    it's needed or wrong is told by whether one was given, not by
    pyzipper's wording."""
    if not passphrase:
        raise PassphraseError("needed")
    digest = hashlib.sha256()
    try:
        with pyzipper.AESZipFile(path) as archive:
            archive.setpassword(passphrase.encode("utf-8"))
            with archive.open(INNER) as source:
                while True:
                    if cancelled is not None and cancelled():
                        raise InterruptedError("Backup cancelled.")
                    chunk = source.read(_CHUNK)
                    if not chunk:
                        break
                    digest.update(chunk)
                    if out is not None:
                        out.write(chunk)
    except RuntimeError as exc:
        # The passphrase's own 2-byte check failed.
        raise PassphraseError("wrong") from exc
    except pyzipper.BadZipFile as exc:
        # The final HMAC failed: about 1 wrong passphrase in 65,536 gets
        # this far, and damage ends here too.
        if "hmac" in str(exc).lower():
            raise PassphraseError("wrong-or-damaged") from exc
        raise
    return digest.hexdigest()


@contextmanager
def opened(path, passphrase: Optional[str] = None, cancelled=None):
    """The path of a backup as a plain zip, for reading: the file itself,
    or for an encrypted one its decrypted inner zip in a private temporary
    folder that's deleted afterwards. Raises PassphraseError."""
    if not is_encrypted(path):
        yield Path(path)
        return
    with tempfile.TemporaryDirectory(prefix="tcp-backup-open-") as folder:
        target = Path(folder) / INNER
        with open(target, "wb") as out:
            _decrypt_to(path, passphrase, out, cancelled)
        yield target


def _arc(*parts: str) -> str:
    return "/".join(p.strip("/").replace("\\", "/") for p in parts if p)


def _is_link(path: Path) -> bool:
    """A symbolic link or a Windows junction: never followed, so a backup
    can't wander out of its folder or loop (the user is told instead)."""
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError:
        return True


def walk(root: Path, area: str, *, exclude_dirs: Iterable[str] = (),
         exclude_files: Iterable[str] = (), sqlite: bool = True,
         inventory: Optional[Inventory] = None) -> List[Item]:
    """Every file under ``root`` as an Item named ``area/<relative path>``.

    ``exclude_dirs`` and ``exclude_files`` are glob patterns matched against
    the path relative to ``root`` (forward slashes), so ``"cache"`` leaves
    out only the top-level cache folder, ``"plugins/marketplaces"`` only that
    one, and ``"*.log"`` (``*`` crosses folders) every log. Credentials are always left
    out. Links inside ``root`` aren't followed; each is noted on
    ``inventory``. A ``root`` that is itself a link is read where it points
    (someone who moved ~/.claude elsewhere wants that backed up), and noted.
    A folder that can't be read is a failure on ``inventory``. With
    ``sqlite``, SQLite databases are marked to be copied safely and their
    -wal/-shm files left to that copy.
    """
    root = Path(root)
    exclude_dirs = tuple(exclude_dirs)
    exclude_files = tuple(exclude_files)
    items: List[Item] = []
    if not root.is_dir():
        return items

    def matches(rel: str, _name: str, patterns) -> bool:
        return any(fnmatch.fnmatchcase(rel, p) for p in patterns)

    reading_root = root
    if _is_link(root):
        reading_root = root.resolve()
        if inventory is not None:
            inventory.notes.append(f"{root} is a link to {reading_root}; backed up from there.")
    # Claude's project folders encode whole paths into their names, so a
    # transcript's path can pass Windows' 260 characters; the long form has
    # no limit. Names in the archive are relative, so they don't change.
    reading_root = platform_paths.long_path(Path(os.path.abspath(reading_root)))

    def unreadable(error: OSError):
        if inventory is not None:
            inventory.failures.append(f"Couldn't read {error.filename}: {error.strerror or error}")

    for current, dirs, files in os.walk(reading_root, onerror=unreadable):
        here = Path(current)
        rel_dir = here.relative_to(reading_root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        kept = []
        for name in sorted(dirs):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if name in CREDENTIAL_DIRS or matches(rel, name, exclude_dirs):
                continue
            if _is_link(here / name):
                if inventory is not None:
                    inventory.notes.append(f"Not followed (a link): {area}/{rel}")
                continue
            kept.append(name)
        dirs[:] = kept
        names = set(files)
        for name in sorted(files):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if (name in CREDENTIAL_NAMES or matches(rel, name, exclude_files)
                    or any(fnmatch.fnmatchcase(name, p) for p in TEMPORARY_PATTERNS)):
                continue
            path = here / name
            if _is_link(path):
                if inventory is not None:
                    inventory.notes.append(f"Not followed (a link): {area}/{rel}")
                continue
            if sqlite and name.endswith(SQLITE_SIDECARS):
                base = name[: name.rindex("-")]
                if base in names and is_sqlite(here / base):
                    continue
            items.append(Item(path, _arc(area, rel),
                              sqlite=sqlite and is_sqlite(path)))
    return items


def is_sqlite(path: Path) -> bool:
    """True for a SQLite database, by its header (not just its name)."""
    if not str(path).lower().endswith(SQLITE_SUFFIXES):
        return False
    try:
        with open(path, "rb") as handle:
            return handle.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


# The Chat Place's own data -------------------------------------------------

# The session tools' live address and key (session_tools.CONNECTION_FILE),
# kept readable only by you on purpose: never put in a backup.
STATE_FILE_RUNTIME = "session-tools.json"


def tcp_inventory() -> Inventory:
    """The Chat Place's own folder: which chats it started, names, groups,
    hidden chats, agent conversations, speech and permissions. Provider
    histories alone can't recreate these."""
    root = platform_paths.app_data_dir()
    inv = Inventory("thechatplace", "The Chat Place", version=__version__)
    inv.areas.append(Area("thechatplace/data", str(root),
                          "The Chat Place's settings, groups and session records"))
    inv.items = walk(root, "thechatplace/data",
                     exclude_files=("*.log", STATE_FILE_RUNTIME, "computer.json"),
                     inventory=inv)
    inv.skipped += ["*.log (diagnostic logs)",
                    "session-tools.json (a running chat's connection key, made anew)",
                    "computer.json (this computer's id; another computer has its own)"]
    return inv


def default_collectors() -> List[Callable[[], Inventory]]:
    """Every provider's inventory, The Chat Place's first. Imported here
    (plainly, so PyInstaller bundles them) because they import this module."""
    from .providers import claude_backup, codex_backup, copilot_backup
    return [tcp_inventory, claude_backup.inventory, codex_backup.inventory,
            copilot_backup.inventory]


# Identity ------------------------------------------------------------------

def computer_identity() -> dict:
    """A stable id for this computer (kept in The Chat Place's folder, made
    once), plus names for people. A computer's name can change; the id is
    what tells two backups came from the same machine (#228)."""
    path = platform_paths.app_data_dir() / "computer.json"
    ident = None
    try:
        ident = json.loads(path.read_text(encoding="utf-8")).get("id")
    except (OSError, ValueError, AttributeError):
        pass
    if not ident:
        ident = str(uuid.uuid4())
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            from .provider_storage import atomic_write_json
            atomic_write_json(path, {"id": ident})
        except OSError:
            pass
    return {"id": ident, "name": socket.gethostname(), "os": sys.platform,
            "os_version": platform.platform(), "home": str(Path.home())}


# Writing -------------------------------------------------------------------

def default_filename(computer: str, now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in computer).strip("-") or "computer"
    return f"{FILE_PREFIX}{safe}-{now:%Y-%m-%d-%H%M%S}.zip"


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def destination_problem(dest: Path, inventories: Sequence[Inventory]) -> Optional[str]:
    """Why a folder can't hold the backup, or None. A backup inside a folder
    it copies would copy itself next time."""
    dest = Path(dest)
    if not dest.is_dir():
        return f"{dest} isn't a folder."
    for inv in inventories:
        for area in inv.areas:
            if area.root and _inside(dest, Path(area.root)):
                return (f"{dest} is inside {area.root}, which the backup copies. "
                        "Choose a folder outside it, ideally on another drive.")
    return None


def cloud_folders() -> List[Path]:
    """Folders a sync service copies off this computer: OneDrive's (Windows
    says where), Dropbox, Google Drive, iCloud Drive and macOS's
    CloudStorage."""
    home = Path.home()
    found = [Path(os.environ[name]) for name in ("OneDrive", "OneDriveCommercial",
                                                 "OneDriveConsumer") if os.environ.get(name)]
    found += [home / "Dropbox", home / "Google Drive", home / "iCloudDrive",
              home / "Library" / "CloudStorage", home / "Library" / "Mobile Documents"]
    return found


def same_drive_as_home(dest: Path) -> bool:
    """True when the backup is on the disk it's protecting and not in a
    folder that syncs to the cloud, so it wouldn't survive losing the
    computer (said, not refused: it's still a copy)."""
    if any(_inside(Path(dest), folder) for folder in cloud_folders() if folder.is_dir()):
        return False
    try:
        return os.stat(dest).st_dev == os.stat(Path.home()).st_dev
    except OSError:
        return False


class SourceError(OSError):
    """Reading a file being backed up failed partway (a lock, a cloud
    placeholder that won't download): that file is lost to this backup,
    the rest goes on. A failure writing the backup itself is a plain
    OSError, and stops it."""


def _hash_into(handle, out, digest, cancelled=None) -> int:
    total = 0
    while True:
        if cancelled is not None and cancelled():
            raise InterruptedError("Backup cancelled.")
        try:
            chunk = handle.read(_CHUNK)
        except OSError as exc:
            raise SourceError(exc.errno, exc.strerror or str(exc), exc.filename) from exc
        if not chunk:
            return total
        digest.update(chunk)
        out.write(chunk)
        total += len(chunk)


def _sqlite_snapshot(source: Path, scratch: Path, cancelled=None) -> Path:
    """A consistent copy of one database through SQLite's backup API that
    never writes beside the provider's file.

    Opening a WAL database even read-only makes SQLite create -wal and -shm
    files when they aren't there, which would be writing in another app's
    folder. So, by what's beside it:

    * nothing: no uncommitted changes, so it's opened as immutable (no
      locks, no new files);
    * -wal and -shm: its program has it open, both files exist, and a
      read-only open reads the WAL through them;
    * -wal without -shm, or a -journal: no program has it open (-shm lives
      as long as a connection does) but changes were left behind. Even a
      read-only open would create -shm or need to roll the journal back,
      so the database and that file are copied as they are into the
      scratch folder and opened there, where SQLite finishes the job
      (#434 review).

    The copy is then checked with ``quick_check``, so a database changed
    under an immutable open is caught rather than kept torn."""
    target = scratch / (uuid.uuid4().hex + ".sqlite")
    text = str(source)
    if text.startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\"):
        text = text[4:]  # the long form; SQLite wants a plain URI
    plain = Path(text)
    beside = {suffix: Path(str(source) + suffix).exists()
              for suffix in ("-wal", "-shm", "-journal")}
    if beside["-journal"] or (beside["-wal"] and not beside["-shm"]):
        left = "-journal" if beside["-journal"] else "-wal"
        work = scratch / uuid.uuid4().hex
        work.mkdir()
        shutil.copyfile(source, work / "db.sqlite")
        shutil.copyfile(str(source) + left, str(work / "db.sqlite") + left)
        uri = (work / "db.sqlite").as_uri()
    else:
        uri = plain.resolve().as_uri() + ("?mode=ro" if beside["-wal"]
                                          else "?mode=ro&immutable=1")
    src = sqlite3.connect(uri, uri=True, timeout=10)
    try:
        dst = sqlite3.connect(target)
        try:
            def step(_status, _remaining, _total):
                if cancelled is not None and cancelled():
                    raise InterruptedError("Backup cancelled.")
            src.backup(dst, pages=256, progress=step)
            result = dst.execute("pragma quick_check").fetchone()
            if not result or result[0] != "ok":
                raise sqlite3.DatabaseError(f"the copy failed its check ({result and result[0]})")
        finally:
            dst.close()
    finally:
        src.close()
    return target


def create(dest_dir: Path, *, collectors: Optional[Sequence[Callable[[], Inventory]]] = None,
           progress: Optional[Callable[[int, int, str], None]] = None,
           cancelled: Optional[Callable[[], bool]] = None,
           filename: Optional[str] = None, now: Optional[datetime] = None,
           passphrase: Optional[str] = None) -> Result:
    """Write one backup into ``dest_dir`` and return what happened.

    ``progress(done, total, arcname)`` is called from this (background)
    thread. A file that can't be read is recorded as a failure and the
    backup is marked incomplete; one that changes while it's read is kept
    and noted. Raises OSError (or ValueError for a bad destination) when no
    backup could be written; the destination then has no new file.
    """
    dest_dir = Path(dest_dir)
    collectors = list(collectors or default_collectors())
    inventories: List[Inventory] = []
    result = Result(path=None)
    for collect in collectors:
        if cancelled is not None and cancelled():
            raise InterruptedError("Backup cancelled.")
        try:
            inventories.append(collect())
        except Exception as exc:  # noqa: BLE001 - one provider can't stop the rest
            name = getattr(collect, "__module__", "a provider").rsplit(".", 1)[-1]
            result.failures.append(f"Couldn't list {name}'s files: {exc}")
    for inv in inventories:
        result.failures.extend(f"{inv.label}: {failure}" for failure in inv.failures)
    problem = destination_problem(dest_dir, inventories)
    if problem:
        raise ValueError(problem)
    if not any(inv.items for inv in inventories):
        # An empty zip would look like a backup and hold nothing.
        reason = f" ({result.failures[0]})" if result.failures else ""
        raise ValueError("There's nothing to back up: no Claude, Codex, Copilot CLI or "
                         f"Chat Place files were found on this computer{reason}.")

    computer = computer_identity()
    now = now or datetime.now()
    final = dest_dir / (filename or default_filename(computer["name"], now))
    partial = final.with_name(f"{final.name}.{uuid.uuid4().hex[:8]}{PARTIAL_SUFFIX}")
    made = [partial]   # unfinished files, removed if anything goes wrong
    items = [(inv, item) for inv in inventories for item in inv.items]
    total = len(items)
    files: List[dict] = []
    started = datetime.now(timezone.utc)
    seen = set()
    try:
        with tempfile.TemporaryDirectory(prefix="tcp-backup-") as scratch_dir, \
                zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, compresslevel=6,
                                allowZip64=True) as archive:
            scratch = Path(scratch_dir)
            for done, (inv, item) in enumerate(items):
                if cancelled is not None and cancelled():
                    raise InterruptedError("Backup cancelled.")
                if item.arcname in seen or item.arcname == MANIFEST:
                    continue
                seen.add(item.arcname)
                if progress is not None:
                    progress(done, total, item.arcname)
                entry = _add(archive, inv, item, scratch, result, cancelled)
                if entry is not None:
                    files.append(entry)
            manifest = _manifest(inventories, files, result, computer, started)
            manifest["encrypted"] = bool(passphrase)
            archive.writestr(MANIFEST, json.dumps(manifest, indent=1, ensure_ascii=False))
        # On the disk, not just in the cache, before it's checked and named:
        # a USB stick pulled after "finished" mustn't hold half a backup.
        with open(partial, "rb+") as handle:
            os.fsync(handle.fileno())
        check = verify(partial, cancelled=cancelled)
        if not check.ok:
            raise OSError("The backup didn't check out: " + "; ".join(check.problems[:3]))
        if passphrase:
            # The checked backup goes whole into the encrypted one, which is
            # then read back and compared before the plain one is deleted.
            sealed = partial.with_name(partial.name.replace(PARTIAL_SUFFIX, ".enc" + PARTIAL_SUFFIX))
            made.append(sealed)
            written = _encrypt(partial, sealed, passphrase, cancelled)
            with open(sealed, "rb+") as handle:
                os.fsync(handle.fileno())
            if _decrypt_to(sealed, passphrase, None, cancelled) != written:
                raise OSError("The encrypted backup didn't read back the same.")
            partial.unlink()
            final = _publish(sealed, final, result)
        else:
            final = _publish(partial, final, result)
    except BaseException:
        for leftover in made:
            try:
                leftover.unlink()
            except OSError:
                pass
        raise
    result.path = final
    result.files = len(files)
    result.bytes = sum(f["size"] for f in files)
    result.archive_bytes = final.stat().st_size
    result.providers = [inv.label for inv in inventories if inv.items]
    result.complete = not result.failures
    result.encrypted = bool(passphrase)
    if same_drive_as_home(dest_dir):
        result.notes.append("This backup is on the same disk as your data, so it won't survive "
                            "losing this computer. Copy it to another drive or a cloud folder.")
    if progress is not None:
        progress(total, total, "")
    return result


def _publish(partial: Path, final: Path, result: Optional[Result] = None) -> Path:
    """Give the checked archive its real name without ever replacing an
    existing file: two backups in the same second, or a name the user already
    has, get " (2)", " (3)"... A hard link fails if the name exists, which
    rename doesn't promise everywhere."""
    stem, suffix = final.stem, final.suffix
    for attempt in range(1, 100):
        target = final if attempt == 1 else final.with_name(f"{stem} ({attempt}){suffix}")
        try:
            os.link(partial, target)
        except FileExistsError:
            continue
        except OSError:
            # No hard links on this drive (FAT, some network shares): an
            # exclusive create reserves the name, then the archive replaces
            # that empty placeholder.
            try:
                with open(target, "xb"):
                    pass
            except FileExistsError:
                continue
            try:
                os.replace(partial, target)
            except OSError:
                try:
                    target.unlink()   # never leave an empty file with a backup's name
                except OSError:
                    pass
                raise
            return target
        try:
            partial.unlink()
        except OSError:
            # The backup is safely in place under its name; a sync client or
            # virus scanner holding the temporary one mustn't undo that.
            if result is not None:
                result.notes.append(f"{partial.name} couldn't be removed; it's a second copy "
                                    "and can be deleted.")
        return target
    raise OSError(f"Couldn't find a free name for the backup in {final.parent}.")


def _add(archive, inv: Inventory, item: Item, scratch: Path,
         result: Result, cancelled=None) -> Optional[dict]:
    source = Path(item.source)
    try:
        if item.sqlite:
            try:
                copy = _sqlite_snapshot(source, scratch, cancelled)
                kind = "sqlite"
            except sqlite3.Error as exc:
                # Not a database after all, or locked past the timeout: say so.
                result.failures.append(f"{item.arcname}: couldn't copy the database ({exc})")
                return None
            read_from = copy
        else:
            read_from, kind = source, "file"
        try:
            before = os.stat(read_from)
            handle = open(read_from, "rb")
        except OSError as exc:
            raise SourceError(exc.errno, exc.strerror or str(exc), exc.filename) from exc
        digest = hashlib.sha256()
        info = zipfile.ZipInfo(item.arcname, date_time=time.localtime(
            max(before.st_mtime, 315532800))[:6])
        info.compress_type = zipfile.ZIP_DEFLATED
        with handle, archive.open(info, "w", force_zip64=True) as out:
            try:
                size = _hash_into(handle, out, digest, cancelled)
            except SourceError:
                # zipfile has already written part of this entry, and can't
                # take it back: it's listed as unusable, so it's known.
                result.unusable.append(item.arcname)
                raise
        if kind == "file":
            try:
                after = os.stat(read_from)
                changed = (after.st_size != before.st_size
                           or after.st_mtime_ns != before.st_mtime_ns)
            except OSError:
                changed = True   # gone or locked just after: it was read whole
            if changed:
                result.changed.append(item.arcname)
        else:
            try:
                copy.unlink()
            except OSError:
                pass
        try:
            modified = datetime.fromtimestamp(os.stat(source).st_mtime, timezone.utc)                 .isoformat(timespec="seconds")
        except (OSError, ValueError, OverflowError):
            modified = None
        return {"path": item.arcname, "provider": inv.provider, "kind": kind,
                "size": size, "sha256": digest.hexdigest(), "modified": modified}
    except SourceError as exc:
        if exc.errno == 2 and item.arcname not in result.unusable:
            result.gone.append(item.arcname)
            result.failures.append(f"{item.arcname}: deleted before it could be copied")
        else:
            result.failures.append(f"{item.arcname}: {exc.strerror or exc}")
        return None
    except FileNotFoundError:
        # Gone between listing and reading. Temporary files are never
        # listed, so this is history or setup that's now lost, perhaps to a
        # provider's own cleanup, the very loss a backup is for: the backup
        # is incomplete, and says which (#433).
        result.gone.append(item.arcname)
        result.failures.append(f"{item.arcname}: deleted before it could be copied")
        return None


def _manifest(inventories, files, result, computer, started) -> dict:
    by_provider = {}
    for entry in files:
        stats = by_provider.setdefault(entry["provider"], [0, 0])
        stats[0] += 1
        stats[1] += entry["size"]
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "app": {"name": "The Chat Place", "version": __version__},
        "created": started.isoformat(timespec="seconds"),
        "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "computer": computer,
        "complete": not result.failures,
        "consistency": ("Each database is a consistent copy of itself. Files and databases "
                        "were copied one after another while programs may have been running, "
                        "so the backup as a whole is not one instant."),
        "providers": [{
            "id": inv.provider, "label": inv.label, "version": inv.version,
            "areas": [{"name": a.name, "root": a.root, "description": a.description}
                      for a in inv.areas],
            "files": by_provider.get(inv.provider, [0, 0])[0],
            "bytes": by_provider.get(inv.provider, [0, 0])[1],
            "skipped": inv.skipped, "notes": inv.notes,
        } for inv in inventories],
        "failures": result.failures,
        "changed_while_copying": result.changed,
        "gone_while_copying": result.gone,
        "unusable": result.unusable,
        "files": files,
    }


# Checking ------------------------------------------------------------------

def read_manifest(path: Path, passphrase: Optional[str] = None) -> dict:
    """A backup's manifest. Raises PassphraseError for an encrypted backup
    without its passphrase, or with the wrong one."""
    with opened(path, passphrase) as plain, zipfile.ZipFile(plain) as archive:
        manifest = json.loads(archive.read(MANIFEST).decode("utf-8"))
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise ValueError(f"{Path(path).name} isn't a Chat Place backup.")
    return manifest


def _safe_name(name: str) -> bool:
    """No absolute paths, drive letters or .. in an archive name, so a
    restore can never write outside where it's told to."""
    if not name or name.startswith(("/", "\\")) or ":" in name:
        return False
    return ".." not in name.replace("\\", "/").split("/")


def verify(path: Path, cancelled: Optional[Callable[[], bool]] = None,
           passphrase: Optional[str] = None) -> Check:
    """Read the whole archive back and compare every file with the manifest:
    present, the right size, the right SHA-256, nothing unlisted, no unsafe
    names. A newer format version is a problem (this release can't vouch
    for it). An encrypted backup needs its passphrase: without it, or with
    the wrong one, ``needs_passphrase`` is set. It's decrypted to a private
    temporary folder to be checked, and that copy deleted."""
    if not is_encrypted(path):
        return _verify_plain(path, cancelled)
    try:
        with opened(path, passphrase, cancelled) as plain:
            check = _verify_plain(plain, cancelled)
    except InterruptedError:
        raise
    except PassphraseError as exc:
        check = Check(ok=False, needs_passphrase=True,
                      problems=[PASSPHRASE_PROBLEMS.get(str(exc), str(exc))])
    except (OSError, pyzipper.BadZipFile, zlib.error, ValueError, EOFError) as exc:
        check = Check(ok=False, problems=[f"Couldn't read it: {exc}"])
    check.encrypted = True
    return check


def _verify_plain(path, cancelled=None) -> Check:
    check = Check(ok=False)
    try:
        with zipfile.ZipFile(path) as archive:
            try:
                if archive.getinfo(MANIFEST).file_size > MANIFEST_LIMIT:
                    check.problems.append("Its manifest is far too big; it isn't a Chat Place "
                                          "backup.")
                    return check
                manifest = json.loads(archive.read(MANIFEST).decode("utf-8"))
            except KeyError:
                check.problems.append("It has no manifest; it isn't a Chat Place backup.")
                return check
            if (not isinstance(manifest, dict) or manifest.get("format") != FORMAT
                    or not isinstance(manifest.get("files", []), list)):
                check.problems.append("It isn't a Chat Place backup.")
                return check
            check.manifest = manifest
            if int(manifest.get("format_version", 0)) > FORMAT_VERSION:
                check.problems.append("It was made by a newer version of The Chat Place.")
                return check
            names = set(archive.namelist())
            listed = {name for name in manifest.get("unusable") or [] if isinstance(name, str)}
            for entry in manifest.get("files", []):
                if cancelled is not None and cancelled():
                    raise InterruptedError("Backup cancelled.")
                if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                    check.problems.append("Its manifest has an entry that isn't a file.")
                    continue
                name = entry["path"]
                listed.add(name)
                if not _safe_name(name):
                    check.problems.append(f"Unsafe name in the backup: {name}")
                    continue
                if name not in names:
                    check.problems.append(f"Missing: {name}")
                    continue
                digest = hashlib.sha256()
                size = 0
                with archive.open(name) as handle:
                    while True:
                        chunk = handle.read(_CHUNK)
                        if not chunk:
                            break
                        digest.update(chunk)
                        size += len(chunk)
                if size != entry.get("size") or digest.hexdigest() != entry.get("sha256"):
                    check.problems.append(f"Damaged: {name}")
                    continue
                check.files += 1
            extra = sorted(names - listed - {MANIFEST})
            for name in extra[:20]:
                check.problems.append(f"Not in the manifest: {name}")
    except InterruptedError:
        raise   # a cancel, not damage (it's an OSError too)
    except (OSError, zipfile.BadZipFile, zlib.error, ValueError, TypeError, EOFError,
            RuntimeError, NotImplementedError) as exc:
        # zlib.error is damaged compressed data; RuntimeError an encrypted
        # entry; NotImplementedError a compression this Python can't read.
        check.problems.append(f"Couldn't read it: {exc}")
        return check
    check.ok = not check.problems
    return check


def describe_size(count: int) -> str:
    size = float(count)
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{count} bytes"


def summary(result: Result) -> str:
    """What a finished backup says, in a sentence or two."""
    files = "1 file" if result.files == 1 else f"{result.files} files"
    text = (f"Backed up {files} ({describe_size(result.bytes)}, "
            f"{describe_size(result.archive_bytes)} compressed) from "
            f"{', '.join(result.providers) or 'nothing'} to {result.path.name}.")
    if result.failures:
        text += (f" {len(result.failures)} item{'s' if len(result.failures) != 1 else ''} "
                 "couldn't be copied, so it's incomplete; the details are in its manifest.")
    if result.encrypted:
        text += (" It's encrypted with your passphrase. Without it the backup can't be opened, "
                 "so keep the passphrase safe.")
    else:
        text += (" It's not encrypted and holds your conversations, so keep it somewhere "
                 "private.")
    for note in result.notes:
        text += " " + note
    return text


# What Back Up Now remembers -------------------------------------------------

STATE_FILE = "backup.json"


def load_state() -> dict:
    """The last folder chosen and the last good backup (where, when, how
    big, whether complete), from The Chat Place's own folder."""
    try:
        state = json.loads((platform_paths.app_data_dir() / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def record(result: Result) -> None:
    """Remember a finished backup. A failure to save is not the backup's
    failure, so it's swallowed."""
    from .provider_storage import atomic_write_json
    state = load_state()
    state.update({"folder": str(result.path.parent), "last_path": str(result.path),
                  "last_time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "last_files": result.files, "last_bytes": result.archive_bytes,
                  "last_complete": result.complete})
    try:
        platform_paths.app_data_dir().mkdir(parents=True, exist_ok=True)
        atomic_write_json(platform_paths.app_data_dir() / STATE_FILE, state)
    except OSError:
        pass


def last_backup_text(state: Optional[dict] = None) -> str:
    """What the folder question says about the last backup made here:
    when, how big and where, or that there hasn't been one."""
    state = load_state() if state is None else state
    when = state.get("last_time")
    try:
        moment = datetime.fromisoformat(when).astimezone()
    except (TypeError, ValueError):
        return "No backup made here yet."
    text = (f"Last backup: {moment.day} {moment:%B %Y} at {moment:%H:%M}, "
            f"{describe_size(int(state.get('last_bytes') or 0))}, in {state.get('folder', '?')}")
    if state.get("last_complete") is False:
        text += " (incomplete)"
    return text + "."


def check_text(path: Path, check: Check) -> str:
    """What Check Backup says about one archive."""
    name = Path(path).name
    manifest = check.manifest or {}
    if check.ok:
        computer = (manifest.get("computer") or {}).get("name", "an unknown computer")
        try:
            moment = datetime.fromisoformat(manifest.get("created", "")).astimezone()
            when = f" on {moment.day} {moment:%B %Y} at {moment:%H:%M}"
        except ValueError:
            when = ""
        match = "its 1 file matches" if check.files == 1 else f"all {check.files} files match"
        text = (f"{name} is good: {match}. It was made on "
                f"{computer}{when}.")
        if check.encrypted:
            text += " It's encrypted."
        if manifest.get("complete") is False:
            failures = manifest.get("failures") or []
            text += (f" It's incomplete: {len(failures)} item"
                     f"{'s' if len(failures) != 1 else ''} couldn't be copied when it was made.")
        return text
    shown = check.problems[:10]
    more = len(check.problems) - len(shown)
    text = f"{name} has problems:\n" + "\n".join(shown)
    if more > 0:
        text += f"\n...and {more} more."
    return text
