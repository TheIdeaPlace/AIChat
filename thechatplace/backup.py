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
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import platform
import socket
import sqlite3
import sys
import tempfile
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence

from . import __version__, platform_paths

FORMAT = "thechatplace-backup"
FORMAT_VERSION = 1
MANIFEST = "manifest.json"
FILE_PREFIX = "TheChatPlace-backup-"
PARTIAL_SUFFIX = ".partial"
_CHUNK = 1024 * 1024

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
    notes: List[str] = field(default_factory=list)
    complete: bool = False


@dataclass
class Check:
    ok: bool
    files: int = 0
    problems: List[str] = field(default_factory=list)
    manifest: Optional[dict] = None


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
    out. Links aren't followed; each is noted on ``inventory``. With
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
    for current, dirs, files in os.walk(reading_root):
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
            if name in CREDENTIAL_NAMES or matches(rel, name, exclude_files):
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

def tcp_inventory() -> Inventory:
    """The Chat Place's own folder: which chats it started, names, groups,
    hidden chats, agent conversations, speech and permissions. Provider
    histories alone can't recreate these."""
    root = platform_paths.app_data_dir()
    inv = Inventory("thechatplace", "The Chat Place", version=__version__)
    inv.areas.append(Area("thechatplace/data", str(root),
                          "The Chat Place's settings, groups and session records"))
    inv.items = walk(root, "thechatplace/data", exclude_files=("*.log", "*.tmp"),
                     inventory=inv)
    inv.skipped.append("*.log (diagnostic logs)")
    return inv


def default_collectors() -> List[Callable[[], Inventory]]:
    """Every provider's inventory, The Chat Place's first. Imported here so a
    provider's module failing to import doesn't stop the others."""
    collectors: List[Callable[[], Inventory]] = [tcp_inventory]
    from .providers import claude_backup
    collectors.append(claude_backup.inventory)
    for name in ("codex_backup", "copilot_backup"):
        try:
            module = __import__(f"thechatplace.providers.{name}", fromlist=["inventory"])
        except ImportError:
            continue
        collectors.append(module.inventory)
    return collectors


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


def same_drive_as_home(dest: Path) -> bool:
    """True when the backup is on the disk it's protecting, so it wouldn't
    survive losing the computer (said, not refused: it's still a copy)."""
    try:
        return os.stat(dest).st_dev == os.stat(Path.home()).st_dev
    except OSError:
        return False


def _hash_into(handle, out, digest) -> int:
    total = 0
    while True:
        chunk = handle.read(_CHUNK)
        if not chunk:
            return total
        digest.update(chunk)
        out.write(chunk)
        total += len(chunk)


def _sqlite_snapshot(source: Path, scratch: Path) -> Path:
    """A consistent copy of one database through SQLite's backup API,
    opened read-only so the provider's file is never changed."""
    target = scratch / (uuid.uuid4().hex + ".sqlite")
    text = str(source)
    if text.startswith("\\\\?\\") and not text.startswith("\\\\?\\UNC\\"):
        text = text[4:]  # a long-form path (Cowork's); SQLite wants a plain URI
    uri = Path(text).resolve().as_uri() + "?mode=ro"
    src = sqlite3.connect(uri, uri=True, timeout=10)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return target


def create(dest_dir: Path, *, collectors: Optional[Sequence[Callable[[], Inventory]]] = None,
           progress: Optional[Callable[[int, int, str], None]] = None,
           cancelled: Optional[Callable[[], bool]] = None,
           filename: Optional[str] = None, now: Optional[datetime] = None) -> Result:
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
        try:
            inventories.append(collect())
        except Exception as exc:  # noqa: BLE001 - one provider can't stop the rest
            name = getattr(collect, "__module__", "a provider").rsplit(".", 1)[-1]
            result.failures.append(f"Couldn't list {name}'s files: {exc}")
    problem = destination_problem(dest_dir, inventories)
    if problem:
        raise ValueError(problem)

    computer = computer_identity()
    now = now or datetime.now()
    final = dest_dir / (filename or default_filename(computer["name"], now))
    partial = final.with_name(final.name + PARTIAL_SUFFIX)
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
                entry = _add(archive, inv, item, scratch, result)
                if entry is not None:
                    files.append(entry)
            manifest = _manifest(inventories, files, result, computer, started)
            archive.writestr(MANIFEST, json.dumps(manifest, indent=1, ensure_ascii=False))
        check = verify(partial)
        if not check.ok:
            raise OSError("The backup didn't check out: " + "; ".join(check.problems[:3]))
        os.replace(partial, final)
    except BaseException:
        try:
            partial.unlink()
        except OSError:
            pass
        raise
    result.path = final
    result.files = len(files)
    result.bytes = sum(f["size"] for f in files)
    result.archive_bytes = final.stat().st_size
    result.providers = [inv.label for inv in inventories if inv.items]
    result.complete = not result.failures
    if same_drive_as_home(dest_dir):
        result.notes.append("This backup is on the same disk as your data, so it won't survive "
                            "losing this computer. Copy it to another drive or a cloud folder.")
    if progress is not None:
        progress(total, total, "")
    return result


def _add(archive: zipfile.ZipFile, inv: Inventory, item: Item, scratch: Path,
         result: Result) -> Optional[dict]:
    source = Path(item.source)
    try:
        if item.sqlite:
            try:
                copy = _sqlite_snapshot(source, scratch)
                kind = "sqlite"
            except sqlite3.Error as exc:
                # Not a database after all, or locked past the timeout: say so.
                result.failures.append(f"{item.arcname}: couldn't copy the database ({exc})")
                return None
            read_from = copy
        else:
            read_from, kind = source, "file"
        before = os.stat(read_from)
        digest = hashlib.sha256()
        info = zipfile.ZipInfo(item.arcname, date_time=time.localtime(
            max(before.st_mtime, 315532800))[:6])
        info.compress_type = zipfile.ZIP_DEFLATED
        with open(read_from, "rb") as handle, archive.open(info, "w", force_zip64=True) as out:
            size = _hash_into(handle, out, digest)
        if kind == "file":
            after = os.stat(read_from)
            if after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
                result.changed.append(item.arcname)
        else:
            try:
                copy.unlink()
            except OSError:
                pass
        return {"path": item.arcname, "provider": inv.provider, "kind": kind,
                "size": size, "sha256": digest.hexdigest(),
                "modified": datetime.fromtimestamp(source.stat().st_mtime, timezone.utc)
                .isoformat(timespec="seconds") if source.exists() else None}
    except FileNotFoundError:
        # Gone between listing and reading (a temp file, a finished turn).
        return None
    except OSError as exc:
        result.failures.append(f"{item.arcname}: {exc.strerror or exc}")
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
        "files": files,
    }


# Checking ------------------------------------------------------------------

def read_manifest(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
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


def verify(path: Path) -> Check:
    """Read the whole archive back and compare every file with the manifest:
    present, the right size, the right SHA-256, nothing unlisted, no unsafe
    names. A newer format version is a problem (this release can't vouch
    for it)."""
    check = Check(ok=False)
    try:
        with zipfile.ZipFile(path) as archive:
            try:
                manifest = json.loads(archive.read(MANIFEST).decode("utf-8"))
            except KeyError:
                check.problems.append("It has no manifest; it isn't a Chat Place backup.")
                return check
            if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
                check.problems.append("It isn't a Chat Place backup.")
                return check
            check.manifest = manifest
            if int(manifest.get("format_version", 0)) > FORMAT_VERSION:
                check.problems.append("It was made by a newer version of The Chat Place.")
                return check
            names = set(archive.namelist())
            listed = set()
            for entry in manifest.get("files", []):
                name = entry.get("path", "")
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
    except (OSError, zipfile.BadZipFile, ValueError, EOFError) as exc:
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
    text = (f"Backed up {result.files} files ({describe_size(result.bytes)}, "
            f"{describe_size(result.archive_bytes)} compressed) from "
            f"{', '.join(result.providers) or 'nothing'} to {result.path.name}.")
    if result.failures:
        text += (f" {len(result.failures)} item{'s' if len(result.failures) != 1 else ''} "
                 "couldn't be copied, so it's incomplete; the details are in its manifest.")
    for note in result.notes:
        text += " " + note
    return text
