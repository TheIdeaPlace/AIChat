"""TCP-owned command grants; never grant a Codex session or saved rule (#307).

This intentionally understands a small common shell grammar. Anything whose
meaning depends on the shell must go through Answer again. Extra permission
profiles must match exactly, including fields not shown in their summary.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import re
import shlex
import time
import uuid

from .. import platform_paths
from ..provider_storage import atomic_write_json
from .codex_requests import COMMAND, ACCEPT, SANDBOX_NOTE


def simple_words(command):
    if (not isinstance(command, str) or not command or len(command) > 8192
            or any(ord(c) < 32 or ord(c) > 126 for c in command)
            or any(c in command for c in '&|;<>`()$\\%!^{}*?~#')):
        return ()
    # Accept only whole unquoted words or whole quoted literals. Adjacent or
    # embedded quotes mean different things in PowerShell and POSIX shells.
    token = r'''(?:[A-Za-z0-9_./:@=+,-]+|'[A-Za-z0-9_./:@=+, -]*'|"[A-Za-z0-9_./:@=+, -]*")'''
    if not re.fullmatch(r' *' + token + r'(?: +' + token + r')* *', command):
        return ()
    return tuple(w[1:-1] if w[0] in "\"'" else w for w in re.findall(token, command))


def command_words(command):
    """Decode app-server's shlex-joined exec argv, then inspect one inner script.

    This is not recursive shell interpretation: only exact trusted wrappers
    are unwrapped. The shell identity/flags become part of the kept scope.
    """
    if not isinstance(command, str) or any(ord(c) < 32 or ord(c) > 126 for c in command):
        return (), ()
    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        return (), ()
    if not argv:
        return (), ()
    shell = platform_paths.codex_approval_shell(argv[0])
    flags = argv[1:-1]
    if shell == 'powershell' and [f.lower() for f in flags] in (
            ['-command'], ['-noprofile', '-command']):
        return simple_words(argv[-1]), (shell, argv[0].lower(), *[f.lower() for f in flags])
    if shell == 'posix' and flags in (['-c'], ['-lc']):
        return simple_words(argv[-1]), (shell, argv[0], *flags)
    if shell:
        return (), ()
    return simple_words(command), ('direct',)


# Only these Git operations/options are reviewed. Unknown options still ask;
# matching a prefix never bypasses checking the rest of the command (#330).
GIT_FLAGS = {
    'status': {'--short', '-s', '--branch', '-b', '--porcelain'},
    'switch': {'--detach', '-d', '--force', '-f', '--track', '--no-track'},
    'add': {'--all', '-A', '--update', '--force', '-f', '--dry-run', '-n', '--verbose', '-v'},
    'commit': {'--amend', '--no-edit', '--allow-empty', '--no-verify', '--all', '-a', '--verbose', '-v'},
    'push': {'--force', '-f', '--force-with-lease', '--set-upstream', '--delete', '-d', '--tags', '--dry-run', '-n'},
    'fetch': {'--all', '--prune', '-p', '--tags', '--no-tags', '--dry-run', '-n'},
    'diff': {'--stat', '--name-only', '--name-status', '--cached', '--staged', '--no-ext-diff', '--no-textconv'},
    'log': {'--oneline', '--all', '--decorate', '--graph', '--no-decorate'},
}
GIT_VALUES = {'switch': {'-c', '--create'}, 'commit': {'-m', '--message'}, 'log': {'-n', '--max-count'}}


def reviewed_git(words, cwd, root):
    if not safe_prefix(words[:2]):
        return False
    verb = words[1]
    position = 2
    literal_paths = False
    while position < len(words):
        word = words[position]
        if not literal_paths and word == '--':
            literal_paths = True
        elif not literal_paths and word in GIT_VALUES.get(verb, ()):
            position += 1
            if position >= len(words):
                return False
            value = words[position]
            if verb == 'commit':
                pass  # A message is data, never a path or a command option.
            elif verb == 'log':
                if not value.isdecimal():
                    return False
            elif value.startswith('-') or not platform_paths.codex_permission_argument(value, cwd, root):
                return False
        elif word.startswith('-'):
            if literal_paths or word not in GIT_FLAGS[verb]:
                return False
        elif ':' in word or not platform_paths.codex_permission_argument(word, cwd, root):
            # No explicit transports (especially ext::), drive paths, Git
            # pathspec magic or paths escaping the chat, even after --.
            return False
        position += 1
    return True


def safe_prefix(prefix):
    if (not isinstance(prefix, (list, tuple)) or not 2 <= len(prefix) <= 256
            or any(not isinstance(w, str) or not re.fullmatch(r'[A-Za-z0-9_./:=+@,-]+', w)
                   for w in prefix)):
        return False
    return prefix[0] in ('git', 'git.exe') and prefix[1] in GIT_FLAGS


@dataclass(frozen=True)
class CodexChatRule:
    rule_id: str
    prefix: tuple
    folder: str
    scope: str
    allowed_at_ms: int
    access: tuple = ()

    @property
    def description(self):
        return 'Run ' + ' '.join(self.prefix) + ' (simple commands with reviewed options).'


def access_scope(request, context):
    """Bind literal extra paths to their current targets as well as their text.

    Relative paths, globs and contextual special paths need more interpretation
    than this matcher supports. They can still be allowed once, never kept.
    """
    try:
        raw = json.loads(request.permission_scope)
        files = (raw.get('permissions') or {}).get('fileSystem') or {}
        paths = list(files.get('read') or ()) + list(files.get('write') or ())
        for entry in files.get('entries') or ():
            path = entry['path']
            if path.get('type') != 'path':
                return ''
            paths.append(path['path'])
        resolved = [platform_paths.codex_permission_path(p) for p in paths]
        return json.dumps({'request': raw, 'resolved_paths': resolved, 'wrapper': context},
                          sort_keys=True, separators=(',', ':'))
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError):
        return ''


def candidate(request, cwd):
    if (request.method != COMMAND or ACCEPT not in request.offered()
            or not safe_prefix(request.proposed_prefix) or not request.permission_scope):
        return None
    words, context = command_words(request.command)
    prefix = request.proposed_prefix
    if words[:len(prefix)] != prefix or not reviewed_git(words, request.cwd, cwd):
        return None
    scope = access_scope(request, context)
    if not scope:
        return None
    try:
        folder = platform_paths.codex_permission_folder(request.cwd, cwd)
    except (OSError, ValueError, RuntimeError):
        return None
    access = request.extra_access
    if request.network_host:
        access += (f'Network access to {request.network_host} ({request.network_protocol})',)
    if request.outside_sandbox:
        access += (SANDBOX_NOTE,)
    return CodexChatRule(uuid.uuid4().hex, prefix, folder, scope,
                         int(time.time() * 1000), access)


def matches(rule, request, cwd):
    # Later requests need not repeat the proposed amendment. Match the command
    # itself, but keep every normal decoder and one-time decision guard.
    words, context = command_words(request.command)
    if (request.method != COMMAND or ACCEPT not in request.offered()
            or not safe_prefix(rule.prefix) or words[:len(rule.prefix)] != rule.prefix
            or not reviewed_git(words, request.cwd, cwd) or rule.scope != access_scope(request, context)):
        return False
    try:
        return platform_paths.codex_permission_folder(request.cwd, cwd) == rule.folder
    except (OSError, ValueError, RuntimeError):
        return False


class CodexPermissionStore:
    """Sibling provider store keyed by native chat ID, never copied on fork.

    The runtime serializes saves/removals with automatic answers. A corrupt or
    unreadable file never enables approvals; a failed save propagates to UI.
    """
    def __init__(self, root=None):
        self.root = root if root is not None else platform_paths.app_data_dir() / 'provider-permissions' / 'codex'

    VERSION = 1

    def _path(self, native_id):
        return self.root / (hashlib.sha256(native_id.encode('utf-8')).hexdigest() + '.json')

    def load(self, native_id):
        try:
            raw = json.loads(self._path(native_id).read_text(encoding='utf-8'))
            if (not isinstance(raw, dict) or type(raw.get('version')) is not int
                    or raw['version'] != self.VERSION
                    or set(raw) != {'version', 'provider', 'native_id', 'rules'}):
                return ()
            if (raw['provider'] != 'codex' or raw['native_id'] != native_id
                    or not isinstance(raw['rules'], list) or len(raw['rules']) > 128):
                return ()
            rules = []
            for row in raw['rules']:
                rule = CodexChatRule(**row)
                if (not isinstance(rule.rule_id, str) or not re.fullmatch('[a-f0-9]{32}', rule.rule_id)
                        or not safe_prefix(rule.prefix) or not isinstance(rule.folder, str)
                        or not isinstance(rule.scope, str) or not rule.scope or len(rule.scope) > 131072
                        or type(rule.allowed_at_ms) is not int or rule.allowed_at_ms <= 0
                        or not isinstance(rule.access, list)
                        or any(not isinstance(s, str) for s in rule.access)):
                    return ()
                rules.append(CodexChatRule(rule.rule_id, tuple(rule.prefix), rule.folder,
                                           rule.scope, rule.allowed_at_ms, tuple(rule.access)))
            return tuple(rules) if len({r.rule_id for r in rules}) == len(rules) else ()
        except (OSError, ValueError, TypeError, KeyError):
            return ()

    def save(self, native_id, rules):
        path = self._path(native_id)
        exists = True
        try:
            previous = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            previous = None
            exists = False
        except (ValueError, TypeError):
            raise ValueError('Unreadable chat rules must be preserved') from None
        if exists and (not isinstance(previous, dict)
                or type(previous.get('version')) is not int or previous['version'] != self.VERSION):
            raise ValueError('Unrecognized chat rule version; use the release that wrote it')
        if exists and (set(previous) != {'version', 'provider', 'native_id', 'rules'}
                or previous['provider'] != 'codex' or previous['native_id'] != native_id
                or not isinstance(previous.get('rules'), list)
                or any(not isinstance(row, dict) or set(row) != set(CodexChatRule.__dataclass_fields__)
                       for row in previous['rules'])):
            raise ValueError('Unrecognized chat rule fields must be preserved')
        atomic_write_json(path, {
            'version': self.VERSION, 'provider': 'codex', 'native_id': native_id,
            'rules': [asdict(r) for r in rules]})
