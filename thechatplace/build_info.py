"""Which build this is: its label, commit and branch, for the About box,
bug reports and the smoke test, so Kelly can tell one test build from the
next (2026-11-26).

A build writes ``thechatplace/_build.py`` before PyInstaller runs
(``tools/write_build_info.py``; never committed). A test build has a label
such as "bld05", matching its folder under testing\\tcp, and is packed as
``<version>-<label>``, which Velopack sorts before ``<version>``: an
installed test build still takes the real release. A release has no label.
A source run has no ``_build.py`` and reads the commit from ``.git``.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import __version__

#: A test build's label: letters and digits, as in "bld05". Kept to what a
#: Velopack (semver) pre-release part allows.
LABEL = re.compile(r"[a-z][a-z0-9]{0,19}")


@dataclass(frozen=True)
class BuildInfo:
    label: str = ""     # "bld05" for a test build, "" for a release or a source run
    commit: str = ""    # the full commit id
    branch: str = ""    # "main", "build/bld05"
    built: str = ""     # "2026-11-26", the day it was built
    source: bool = False  # running from source, not a build

    @property
    def short_commit(self) -> str:
        return self.commit[:8]

    @property
    def version(self) -> str:
        """The version as packed: "0.6.4-bld05" for a test build."""
        return f"{__version__}-{self.label}" if self.label else __version__


def _from_build(frozen: Optional[bool] = None) -> Optional[BuildInfo]:
    """The build's own record, only in a built copy: a source checkout keeps
    the last local build's _build.py (it's never removed), which says
    nothing about what a source run is on now."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not frozen:
        return None
    try:
        from . import _build  # written by tools/write_build_info.py
    except ImportError:
        return None
    label = str(getattr(_build, "LABEL", "") or "")
    return BuildInfo(label=label if LABEL.fullmatch(label) else "",
                     commit=str(getattr(_build, "COMMIT", "") or ""),
                     branch=str(getattr(_build, "BRANCH", "") or ""),
                     built=str(getattr(_build, "BUILT", "") or ""))


def _git_dir(root: Path) -> Optional[Path]:
    """The checkout's git folder; a worktree's ``.git`` is a file naming it."""
    dot_git = root / ".git"
    if dot_git.is_dir():
        return dot_git
    try:
        text = dot_git.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if text.startswith("gitdir:"):
        path = Path(text[len("gitdir:"):].strip())
        return path if path.is_absolute() else (root / path).resolve()
    return None


def _from_git(root: Path) -> BuildInfo:
    """The commit a source run is on, read from the files (no git needed)."""
    git = _git_dir(root)
    if git is None:
        return BuildInfo(source=True)
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return BuildInfo(source=True)
    if not head.startswith("ref:"):
        return BuildInfo(commit=head, source=True)
    ref = head[len("ref:"):].strip()
    branch = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
    # A worktree keeps its HEAD alone; refs live in the main git folder.
    common = git
    try:
        common = (git / (git / "commondir").read_text(encoding="utf-8").strip()).resolve()
    except OSError:
        pass
    commit = ""
    for folder in (git, common):
        try:
            commit = (folder / ref).read_text(encoding="utf-8").strip()
            break
        except OSError:
            continue
    if not commit:
        try:
            for line in (common / "packed-refs").read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    commit = parts[0]
                    break
        except OSError:
            pass
    return BuildInfo(commit=commit, branch=branch, source=True)


def current() -> BuildInfo:
    """This copy's build, or what a source run is on. Never raises."""
    try:
        found = _from_build()
        if found is not None:
            return found
        return _from_git(Path(__file__).resolve().parent.parent)
    except Exception:  # noqa: BLE001 - an About box must always open
        return BuildInfo()


def describe(info: Optional[BuildInfo] = None) -> str:
    """The version and build in words: "0.6.4, build bld05 (commit 487a6d67
    on build/bld05, built 2026-11-26)"; "0.6.4 (commit 1512fbe2 on main,
    built 2026-12-01)" for a release; "0.6.4, from source (commit 1512fbe2
    on main)"."""
    info = info or current()
    head = __version__
    if info.label:
        head += f", build {info.label}"
    elif info.source:
        head += ", from source"
    details = []
    if info.short_commit:
        details.append(f"commit {info.short_commit}" + (f" on {info.branch}" if info.branch else ""))
    if info.built:
        details.append(f"built {info.built}")
    return f"{head} ({', '.join(details)})" if details else head


def packed_version() -> str:
    """The version this copy was packed as: what Velopack reports for it."""
    return current().version
