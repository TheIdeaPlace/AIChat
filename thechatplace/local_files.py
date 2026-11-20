"""Local references in replies (#253). Recognition and policy need no GUI.

Filesystem checks and shell operations belong to platform_paths. A reference
doesn't grant permission to open it: that policy is checked again on activation.
"""
from __future__ import annotations

import ntpath
import posixpath
import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from urllib.parse import unquote, urlsplit


# Keep executable, script, installer and shortcut types together. Check both
# the displayed name and its resolved target, so a symlink can't disguise one.
BLOCKED_SUFFIXES = frozenset("""
.exe .com .bat .cmd .ps1 .psm1 .psd1 .vbs .vbe .vb .js .jse .wsf .wsh
.msi .msp .mst .lnk .url .scr .hta .cpl .jar .reg .app .sh .command
.dll .ocx .pif .scf .py .pyw .pyc .pyo .rb .pl .php .bash .zsh .fish
.desktop .run .bin .scpt .applescript .workflow .gadget .application .appref-ms
.webloc .inetloc .website .sct .wsc .mjs .cjs .msc .chm .hlp .inf .osax
.plugin .ksh .csh .settingcontent-ms .diagcab .alias
.appx .appxbundle .msix .msixbundle .appinstaller .xll .xla .xlam .wsb .rdp
.ps1xml .psc1 .jnlp .mde .ade .accde .library-ms .search-ms .searchconnector-ms
.theme .themepack .deskthemepack .ins .isp .mshxml .msh .msh1 .msh2
""".split())

WINDOWS_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[/\\]|\\\\[^\\/]+[/\\][^\\/]+)")
_LINE = re.compile(r":\d+(?::\d+)?$")
_RELATIVE = re.compile(r"^[\w .-]+(?:[/\\][\w .-]+)*\.[A-Za-z][\w.-]*$")
MARKDOWN = re.compile(r"!?\[([^\[\]\n]*)\]\(\s*(?:<([^<>\n]+)>|((?:[^()\n]|\([^()\n]*\))+?))\s*\)")
INLINE_CODE = re.compile(r"(?<!`)(`+)([^`\n]+)\1(?!`)")
_DELIMITED = re.compile(r'''<([^<>\n]+)>|"([^"\n]+)"|'([^'\n]+)'|“([^”\n]+)”|‘([^’\n]+)’''')
_BARE = re.compile(
    r"(?:(?<![\w/:@.\\-])|(?<=_))(?:[A-Za-z]:[/\\]|\\\\[^\s\\/]+[/\\]|file:|\.{1,2}[/\\]|[\w.-]+[/\\])"
    r'''[^\s<>\[\]"'`]*''', re.IGNORECASE)
_WEB = re.compile(r'''(?:https?://|mailto:)[^\s<>"'`]+''', re.IGNORECASE)


def markdown_target(match) -> str:
    target = match.group(2) if match.group(2) is not None else match.group(3)
    return re.sub(r'''\s+(?:"[^"\n]*"|'[^'\n]*')\s*$''', '', target).strip()


def trim_path(text: str) -> str:
    """Sentence punctuation isn't a filename; balanced parentheses can be."""
    text = text.rstrip('.,;!?\"\'”’')
    while text.endswith(')') and text.count(')') > text.count('('):
        text = text[:-1].rstrip('.,;!?')
    return text


def local_path(value: str, cwd: str = "") -> str:
    """An absolute path, without a :line suffix, or '' for a non-reference.

    Relative references need a known absolute session folder and a filename
    extension. This deliberately leaves prose such as 'a/b' alone.
    """
    value = value.strip()
    if value.lower().startswith('file:'):
        try:
            url = urlsplit(value)
            if url.query or url.fragment or '@' in url.netloc or ':' in url.netloc:
                return ""
            value = unquote(url.path)
            if url.netloc and url.netloc.lower() != 'localhost':
                value = '\\\\' + url.netloc + value.replace('/', '\\')
            elif re.match(r'^/[A-Za-z]:/', value):
                value = value[1:]
        except ValueError:
            return ""
    value = _LINE.sub('', value)
    if WINDOWS_ABSOLUTE.match(value):
        return ntpath.normpath(value)
    if value.startswith('/') and not value.startswith('//'):
        return posixpath.normpath(value)
    if cwd and _RELATIVE.match(value):
        if WINDOWS_ABSOLUTE.match(cwd):
            return ntpath.normpath(ntpath.join(cwd, value))
        if cwd.startswith('/'):
            return posixpath.normpath(posixpath.join(cwd, value.replace('\\', '/')))
    return ""


def file_url(path: str) -> str:
    return (PureWindowsPath(path).as_uri() if WINDOWS_ABSOLUTE.match(path)
            else PurePosixPath(path).as_uri())


def plain_path(path: str) -> bool:
    """Reject hidden controls, device paths and Windows alternate streams."""
    if any(unicodedata.category(c) in ('Cc', 'Cf') for c in path):
        return False
    if WINDOWS_ABSOLUTE.match(path):
        if path.startswith(('\\\\?\\', '\\\\.\\')) or ':' in path[2:]:
            return False
        if any(c in path for c in '<>"|?*'):
            return False
        for part in path.replace('/', '\\').split('\\'):
            if re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', part):
                return False
    return True


def blocked_type(path: str) -> bool:
    return ntpath.splitext(path.rstrip(' .'))[1].lower() in BLOCKED_SUFFIXES


def network_path(path: str) -> bool:
    """A \\\\server\\share path: on another computer, so touching it may wait."""
    return path.startswith(('\\\\', '//'))


@dataclass(frozen=True)
class FileStatus:
    path: str
    kind: str = 'missing'
    reason: str = "File not found. Copy only."

    @property
    def can_open(self) -> bool:
        # 'network' is a path on another computer, not checked while listing or
        # drawing a page (an unreachable one waits for the OS's network timeout);
        # open_local_file checks it in full before acting.
        return self.kind in ('file', 'network')

    @property
    def can_reveal(self) -> bool:
        return self.kind in ('file', 'blocked', 'folder', 'network')

    @property
    def folder_label(self) -> str:
        return 'Open folder' if self.kind == 'folder' else 'Open containing folder'


@dataclass(frozen=True)
class LocalReference:
    start: int
    end: int
    path: str
    text: str
    words: str = ''
    in_code: bool = False


def local_references(text: str, cwd: str = '', *, markdown: bool = True):
    """References with source spans, shared by Links and formatted reading.

    Delimiters allow spaces; bare references stop at whitespace. Web links
    and their labels are reserved before looking for paths inside them.
    """
    found = []
    taken = list(text)

    def add(start, end, value, words='', code=False):
        if re.fullmatch(r'/[A-Za-z][\w:-]*', value):
            return False  # a closing HTML/XML tag, such as </h1>
        path = local_path(value, cwd)
        if not path:
            return False
        found.append(LocalReference(start, end, path, value, words, code))
        taken[start:end] = ' ' * (end - start)
        return True

    code_ranges = [(m.start(), m.end()) for m in INLINE_CODE.finditer(text)] if markdown else []
    # A moving pointer avoids quadratic work on long runs of inline code.
    code_index = 0
    if markdown:
        for match in MARKDOWN.finditer(text):
            while code_index < len(code_ranges) and code_ranges[code_index][1] <= match.start():
                code_index += 1
            if code_index < len(code_ranges) and code_ranges[code_index][0] <= match.start():
                continue
            add(match.start(), match.end(), markdown_target(match), match.group(1))
            taken[match.start():match.end()] = ' ' * (match.end() - match.start())
        for match in INLINE_CODE.finditer(''.join(taken)):
            value = match.group(2).strip()
            if add(match.start(2), match.end(2), value, code=True):
                continue
    for match in _DELIMITED.finditer(''.join(taken)):
        group = next(i for i in range(1, 6) if match.group(i) is not None)
        add(match.start(group), match.end(group), match.group(group))
    for match in _WEB.finditer(''.join(taken)):
        taken[match.start():match.end()] = ' ' * (match.end() - match.start())
    for match in _BARE.finditer(''.join(taken)):
        value = match.group()
        for marker in ('**', '__', '~~', '*', '_', '~'):
            if text.endswith(marker, 0, match.start()) and value.endswith(marker):
                value = value[:-len(marker)]
                break
        value = trim_path(value)
        add(match.start(), match.start() + len(value), value)
    found.sort(key=lambda ref: ref.start)
    return found
