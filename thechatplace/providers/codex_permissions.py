"""TCP-owned command grants; never grant a Codex session or saved rule (#307).

This intentionally understands a small common shell grammar. Anything whose
meaning depends on the shell must go through Answer again. Extra permission
profiles must match exactly, including fields not shown in their summary.
"""
from dataclasses import asdict, dataclass
import json
import re
import shlex
import time
import uuid

from .. import platform_paths
from ..provider_storage import PartitionedFile
from .codex_requests import COMMAND, ACCEPT, SANDBOX_NOTE


def simple_words(command):
    if (not isinstance(command, str) or not command or len(command) > 8192
            or any(ord(c) < 32 or ord(c) > 126 for c in command)
            or any(c in command for c in '&|;<>`()$\\%!^{}*?~#')):
        return ()
    try:
        return tuple(shlex.split(command, posix=True))
    except ValueError:
        return ()


def safe_prefix(prefix):
    if (not isinstance(prefix, (list, tuple)) or not 1 <= len(prefix) <= 256
            or any(not isinstance(w, str) or not re.fullmatch(r'[A-Za-z0-9_./:=+@,-]+', w)
                   for w in prefix)):
        return False
    executable = prefix[0].lower()
    if not re.fullmatch(r'[a-z][a-z0-9_-]*(?:\.exe)?', executable):
        return False
    # An allowlist avoids an unbounded blacklist of shells, versioned
    # interpreters, and wrappers. Unrecognized executables still get Allow
    # Once. Expand only after reviewing their command-line grammar.
    return executable.removesuffix('.exe') in {
        'git', 'gh', 'rg', 'curl', 'wget', 'ls', 'cat', 'cp', 'mv', 'mkdir',
        'rm', 'touch', 'head', 'tail', 'wc', 'diff', 'tar', 'zip', 'unzip',
    }


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
        return 'Run ' + ' '.join(self.prefix) + ' (any arguments).'


def access_scope(request):
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
        return json.dumps({'request': raw, 'resolved_paths': resolved}, sort_keys=True, separators=(',', ':'))
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError):
        return ''


def candidate(request, cwd):
    if (request.method != COMMAND or ACCEPT not in request.offered()
            or not safe_prefix(request.proposed_prefix) or not request.permission_scope):
        return None
    words = simple_words(request.command)
    prefix = request.proposed_prefix
    if words[:len(prefix)] != prefix:
        return None
    scope = access_scope(request)
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
    words = simple_words(request.command)
    if (request.method != COMMAND or ACCEPT not in request.offered()
            or words[:len(rule.prefix)] != rule.prefix or rule.scope != access_scope(request)):
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

    def _path(self, native_id):
        import hashlib
        return self.root / (hashlib.sha256(native_id.encode('utf-8')).hexdigest() + '.json')

    def load(self, native_id):
        try:
            raw = json.loads(self._path(native_id).read_text(encoding='utf-8'))
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
        PartitionedFile._write(self._path(native_id), {
            'provider': 'codex', 'native_id': native_id, 'rules': [asdict(r) for r in rules]})
