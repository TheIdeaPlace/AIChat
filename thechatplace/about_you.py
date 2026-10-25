"""Local customization files for What AI Knows About You (#92, #305).

Claude Code keeps what it knows about you in plain files: your instructions
(``CLAUDE.md``), the memories it saved for each project, your skills,
subagents, slash commands and output styles, and your settings. This module
finds them and describes each one in a line. It only reads; the dialog's Edit
button hands a file to your own editor, so The Chat Place never writes to
Claude Code's files.

The layout follows Claude Code's documented locations under ``~/.claude``
(``CLAUDE_CONFIG_DIR`` is honoured through ``platform_paths.claude_home``),
plus the project-level ones in the folders your sessions work in.
``collect_codex`` supplies the same list structure for local Codex files.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from . import platform_paths

INSTRUCTIONS = "Instructions"
MEMORIES = "Memories"
SKILLS = "Skills"
SUBAGENTS = "Subagents"
COMMANDS = "Slash commands"
PROMPTS = "Prompts"
OUTPUT_STYLES = "Output styles"
SETTINGS = "Settings"

#: In the order the dialog lists them, each with what it is.
KINDS = [
    (INSTRUCTIONS, "What you've told Claude to do in every session (CLAUDE.md), and in "
                   "each project"),
    (MEMORIES, "What Claude saved about you and your projects to remember next time"),
    (SKILLS, "Packaged instructions Claude uses when a task calls for them"),
    (SUBAGENTS, "Helpers Claude can hand work to, each with its own instructions"),
    (COMMANDS, "Your own slash commands"),
    (OUTPUT_STYLES, "Ways you've asked Claude to shape its answers"),
    (SETTINGS, "Permissions, hooks and other settings"),
]

#: Enough of a file's start to find its front matter.
_HEAD_BYTES = 8192
#: The most of a file the dialog shows; memories and skills are far smaller.
MAX_READ_BYTES = 1_000_000
#: A row's description is cut here.
_DESCRIPTION_CHARS = 160


@dataclass
class KnownItem:
    kind: str
    name: str
    path: Path
    description: str = ""
    #: Whose it is: "" for you everywhere, otherwise the project's name.
    project: str = ""

    def row(self) -> str:
        """The item's line in the dialog's list."""
        text = f"{self.project}: {self.name}" if self.project else self.name
        if self.description:
            text += f" — {self.description}"
        return text


@dataclass
class KnownKind:
    name: str
    about: str
    items: List[KnownItem] = field(default_factory=list)

    def row(self) -> str:
        count = len(self.items)
        return f"{self.name}, {count} item" + ("" if count == 1 else "s")


_FRONT = re.compile(r"\A\ufeff?---[ \t]*\r?\n(?:(.*?)\r?\n)?---[ \t]*(?:\r?\n|\Z)", re.S)
_FIELD = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):[ \t]*(.*)$")


def front_matter(text: str) -> Dict[str, str]:
    """The simple ``key: value`` fields of a Markdown file's front matter.

    Only top-level one-line fields (``name``, ``description``), which is all
    the list needs; nested ones (``metadata:``) are skipped, and quotes round
    a value are taken off. Not a YAML parser, and never fails."""
    match = _FRONT.match(text or "")
    if not match:
        return {}
    fields: Dict[str, str] = {}
    for line in (match.group(1) or "").splitlines():
        found = _FIELD.match(line)
        if not found:
            continue
        key, value = found.group(1).lower(), found.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if value and value not in ("|", ">", "|-", ">-") and key not in fields:
            fields[key] = value
    return fields


def _first_line(text: str) -> str:
    """The first line of prose after any front matter: a heading or sentence."""
    body = _FRONT.sub("", text or "", count=1)
    for line in body.splitlines():
        line = line.strip().lstrip("#").strip()
        if line and line.strip("-*_ "):  # not a rule, or front matter cut short
            return line
    return ""


def _short(text: str) -> str:
    text = " ".join(text.split())
    if len(text) > _DESCRIPTION_CHARS:
        text = text[:_DESCRIPTION_CHARS - 1].rstrip() + "…"
    return text


def _head(path: Path) -> str:
    try:
        with open(path, "rb") as handle:
            return handle.read(_HEAD_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""


def read_text(path: Path) -> str:
    """A file's text for the dialog, cut at ``MAX_READ_BYTES``. Raises
    OSError (the dialog says why it can't show it)."""
    with open(path, "rb") as handle:
        data = handle.read(MAX_READ_BYTES + 1)
    text = data[:MAX_READ_BYTES].decode("utf-8", errors="replace")
    if len(data) > MAX_READ_BYTES:
        text += "\n\n[The rest of this file is too long to show here. Edit opens all of it.]"
    return text


def _markdown_item(kind: str, path: Path, name: str = "", project: str = "") -> KnownItem:
    head = _head(path)
    fields = front_matter(head)
    return KnownItem(kind=kind, name=fields.get("name") or name or path.stem, path=path,
                     description=_short(fields.get("description") or _first_line(head)),
                     project=project)


def _labelled(kind: str, path: Path, label: str, project: str = "") -> KnownItem:
    """A file named by what it is (instructions have no name of their own),
    described by its first line."""
    return KnownItem(kind=kind, name=label, path=path,
                     description=_short(_first_line(_head(path))), project=project)


def _files(folder: Path, pattern: str) -> List[Path]:
    try:
        return sorted((p for p in folder.glob(pattern) if p.is_file()),
                      key=lambda p: str(p).casefold())
    except OSError:
        return []


def _project_names(cwds: Iterable[str]) -> Dict[str, str]:
    """Each session folder's label, by Claude Code's folder name for it:
    its own name, or its whole path when two folders share a name
    (``C:\\a\\App`` and ``D:\\b\\App``)."""
    distinct: Dict[str, str] = {}
    for cwd in cwds:
        cwd = (cwd or "").rstrip("\\/")
        if cwd:
            distinct.setdefault(platform_paths.encode_cwd(cwd), cwd)
    counts = Counter(_folder_name(cwd).casefold() for cwd in distinct.values())
    return {encoded: (_folder_name(cwd) if counts[_folder_name(cwd).casefold()] == 1 else cwd)
            for encoded, cwd in distinct.items()}


def _folder_name(cwd: str) -> str:
    return re.split(r"[\\/]", cwd.rstrip("\\/"))[-1] or cwd


def _decoded(encoded: str, home: str) -> str:
    """A project folder no session knows: its encoded name, without your
    home folder's part at the start (``GitHub-QuickMail``)."""
    prefix = platform_paths.encode_cwd(home) + "-"
    if home and encoded.startswith(prefix) and len(encoded) > len(prefix):
        return encoded[len(prefix):]
    return encoded


def _project_folders(cwds: Iterable[str]) -> List[Path]:
    """The distinct session folders that still exist."""
    seen, folders = set(), []
    for cwd in cwds:
        if not cwd:
            continue
        key = cwd.rstrip("\\/").casefold()
        if key in seen:
            continue
        seen.add(key)
        path = Path(cwd)
        try:
            if path.is_dir():
                folders.append(path)
        except OSError:
            pass
    return sorted(folders, key=lambda p: p.name.casefold())


def _same_folder(a: Path, b: Path) -> bool:
    """Whether two existing paths are one folder, however they are spelled (case, a
    trailing slash, a junction). A path that doesn't exist is nobody's duplicate."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def collect(cwds: Iterable[str] = (), home: Optional[Path] = None,
            user_home: Optional[str] = None) -> List[KnownKind]:
    """Every kind, in ``KINDS`` order, each with what was found (perhaps none).

    ``cwds`` are the folders your sessions work in: their project
    instructions, skills, subagents, commands and settings are included, and
    their names label the memory folders. Never raises for a missing or
    unreadable file; it is just left out."""
    home = Path(home) if home is not None else platform_paths.claude_home()
    user_home = user_home if user_home is not None else str(Path.home())
    cwds = list(cwds)
    folders = _project_folders(cwds)
    names = _project_names(cwds)
    # A session run from your home folder has your own Claude folder as its .claude, and one
    # run from inside your Claude folder is that folder. Reading those as a project's too
    # would list everything of yours twice (#315). A home-folder session's own CLAUDE.md,
    # outside .claude, is still that project's.
    home_exists = home.is_dir()

    def is_claude_home(path: Path) -> bool:
        return home_exists and _same_folder(path, home)

    in_claude_home = {f for f in folders if is_claude_home(f)}
    dot_claude_is_home = {f for f in folders if is_claude_home(f / ".claude")}
    claude_folders = [f for f in folders if f not in dot_claude_is_home | in_claude_home]

    def whose(folder: Path) -> str:
        return names.get(platform_paths.encode_cwd(str(folder)), folder.name)

    kinds = {name: KnownKind(name, about) for name, about in KINDS}

    def add(kind: str, item: KnownItem) -> None:
        kinds[kind].items.append(item)

    # Instructions: yours, then each project's.
    path = home / "CLAUDE.md"
    if path.is_file():
        add(INSTRUCTIONS, _labelled(INSTRUCTIONS, path, "Your instructions for every session"))
    for folder in folders:
        if folder in in_claude_home:
            continue   # its CLAUDE.md is yours, listed above
        for relative, label in (("CLAUDE.md", "Project instructions"),
                                (".claude/CLAUDE.md", "Project instructions (.claude)"),
                                ("CLAUDE.local.md", "Your private project instructions")):
            if relative.startswith(".claude/") and folder in dot_claude_is_home:
                continue
            path = folder / relative
            if path.is_file():
                add(INSTRUCTIONS, _labelled(INSTRUCTIONS, path, label, project=whose(folder)))

    # Memories, a folder per project, the index (MEMORY.md) first.
    projects = home / "projects"
    try:
        memory_dirs = sorted((d / "memory" for d in projects.iterdir()
                              if (d / "memory").is_dir()),
                             key=lambda d: (names.get(d.parent.name)
                                            or _decoded(d.parent.name, user_home)).casefold())
    except OSError:
        memory_dirs = []
    for memory in memory_dirs:
        project = names.get(memory.parent.name) or _decoded(memory.parent.name, user_home)
        files = _files(memory, "*.md")
        files.sort(key=lambda p: (p.name != "MEMORY.md", p.name.casefold()))
        for path in files:
            if path.name == "MEMORY.md":
                add(MEMORIES, KnownItem(MEMORIES, "Index of this project's memories", path,
                                        project=project))
            else:
                add(MEMORIES, _markdown_item(MEMORIES, path, project=project))

    # Skills: yours, the ones synced from your account, then each project's.
    for path in _files(home / "skills", "*/SKILL.md"):
        add(SKILLS, _markdown_item(SKILLS, path, path.parent.name))
    for path in _files(home / "skills" / "synced", "*/*/SKILL.md"):
        add(SKILLS, _markdown_item(SKILLS, path, path.parent.name, project="From your account"))
    for folder in claude_folders:
        for path in _files(folder / ".claude" / "skills", "*/SKILL.md"):
            add(SKILLS, _markdown_item(SKILLS, path, path.parent.name, project=whose(folder)))

    for kind, sub in ((SUBAGENTS, "agents"), (COMMANDS, "commands"),
                      (OUTPUT_STYLES, "output-styles")):
        for path in _files(home / sub, "*.md"):
            add(kind, _markdown_item(kind, path))
        if kind == OUTPUT_STYLES:
            continue
        for folder in claude_folders:
            for path in _files(folder / ".claude" / sub, "*.md"):
                add(kind, _markdown_item(kind, path, project=whose(folder)))

    for name, label in (("settings.json", "Your settings"),
                        ("settings.local.json", "Your local settings")):
        path = home / name
        if path.is_file():
            add(SETTINGS, KnownItem(SETTINGS, label, path))
    for folder in claude_folders:
        for name, label in (("settings.json", "Project settings"),
                            ("settings.local.json", "Your local project settings")):
            path = folder / ".claude" / name
            if path.is_file():
                add(SETTINGS, KnownItem(SETTINGS, label, path, project=whose(folder)))

    return [kinds[name] for name, _about in KINDS]


def collect_codex(cwds: Iterable[str] = (), home: Optional[Path] = None,
                  user_home: Optional[str] = None) -> List[KnownKind]:
    """Inventory local Codex files, not a claim about a turn's active context.

    Only known customization locations are read; never scan auth, histories or
    rollout files. Parent folders and configured alternate filenames are outside
    this inventory, just as parent instructions are for Claude's inventory.
    """
    home = Path(home) if home is not None else platform_paths.codex_home()
    user = Path(user_home) if user_home is not None else Path.home()
    kinds = {name: KnownKind(name, about) for name, about in [
        (INSTRUCTIONS, "Instructions for Codex (AGENTS.md and AGENTS.override.md)"),
        (MEMORIES, "Local Markdown memories saved in Codex's memories folder"),
        (SKILLS, "Packaged instructions for Codex tasks"),
        (PROMPTS, "Saved prompts in Codex's prompts folder"),
        (SETTINGS, "Codex configuration; project settings apply only to trusted projects"),
    ]}
    seen = set()

    def add(kind, path, label="", project=""):
        try:
            key = (kind, path.resolve())
            if not path.is_file() or key in seen:
                return
            seen.add(key)
            kinds[kind].items.append(_markdown_item(kind, path, label, project))
        except OSError:
            return

    folders = [(home, "")]
    cwds = list(cwds)
    names = _project_names(cwds)
    folders += [(p, names.get(platform_paths.encode_cwd(str(p)), p.name))
                for p in _project_folders(cwds) if not _same_folder(p, home)]
    for folder, project in folders:
        for name in ("AGENTS.md", "AGENTS.override.md"):
            add(INSTRUCTIONS, folder / name, name, project)
        add(SETTINGS, (folder / ".codex" if project else folder) / "config.toml",
            "Project settings" if project else "Your settings", project)
    for path in _files(home / "memories", "**/*.md"):
        add(MEMORIES, path)
    for path in _files(home / "prompts", "*.md"):
        add(PROMPTS, path)
    skill_folders = [(user / ".agents/skills", ""), (home / "skills", "")]
    skill_folders += [(folder / ".agents/skills", project)
                      for folder, project in folders if project]
    for folder, project in skill_folders:
        for pattern in ("*/SKILL.md", ".system/*/SKILL.md"):
            for path in _files(folder, pattern):
                add(SKILLS, path, path.parent.name, project)
    return list(kinds.values())


def summary(kinds: List[KnownKind], provider: str = "Claude Code") -> str:
    """One sentence for the dialog to say on opening."""
    found = [k for k in kinds if k.items]
    if not found:
        return f"No local {provider} customization files were found on this computer."
    return "Found: " + ", ".join(f"{k.name} {len(k.items)}" for k in found) + "."
