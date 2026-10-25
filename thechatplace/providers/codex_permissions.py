"""TCP-only remembered command approvals, with native Allow Once authority (#307).

These are consent records, not a sandbox. Git can run hooks and configured
programs with the authority of the approved command. Never describe this as
confining Git to a folder or host. No native saved/session approval is sent.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import shlex
import threading
import time
import uuid

from .. import platform_paths
from ..provider_storage import atomic_write_json
from .codex_requests import COMMAND, ACCEPT


NOTICE = ('Remembered approvals have the same authority as Allow Once: matching commands '
          'may run outside the sandbox, change files elsewhere, use the network, and run '
          'Git hooks or other configured programs. The folder identifies where the command '
          'starts; it does not confine what it can access. Only this chat in The Chat Place keeps '
          'the approval. No Codex or Claude settings or rules are changed.')


def command_words(command):
    """One captured shell wrapper and one literal command; never interpret code."""
    if (not isinstance(command, str) or not command or len(command) > 8192
            or any(ord(c) < 32 or ord(c) > 126 for c in command)):
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
        script, context = argv[-1], (shell, argv[0].lower(), *[f.lower() for f in flags])
    elif shell == 'posix' and flags in (['-c'], ['-lc']):
        script, context = argv[-1], (shell, argv[0], *flags)
    elif shell:
        return (), ()
    else:
        script, context = command, ('direct',)
    # Whole literal words only. In particular reject PowerShell splatting,
    # zsh =expansion, empty arguments and shell operators inside quotes too.
    token = r'''(?:[A-Za-z0-9_./:=+-]+|'[A-Za-z0-9_./:=+ -]+'|"[A-Za-z0-9_./:=+ -]+")'''
    if not re.fullmatch(r' *' + token + r'(?: +' + token + r')* *', script):
        return (), ()
    words = tuple(w[1:-1] if w[0] in "\"'" else w for w in re.findall(token, script))
    return ((), ()) if any(w.startswith('=') for w in words) else (words, context)


# A deliberately bounded first pilot. Unknown/destructive options still ask.
FLAGS = {
    'status': {'--short', '-s', '--branch', '-b', '--porcelain'},
    'switch': {'--detach', '--track', '--no-track'},
    'add': {'--all', '-A', '--update', '--dry-run', '-n', '--verbose', '-v'},
    'commit': {'--no-edit', '--allow-empty', '--all', '-a', '--verbose', '-v'},
    'push': {'--set-upstream', '--tags', '--dry-run', '-n'},
    'fetch': {'--all', '--prune', '-p', '--tags', '--no-tags', '--dry-run', '-n'},
    'diff': {'--stat', '--name-only', '--name-status', '--cached', '--staged', '--no-ext-diff', '--no-textconv'},
    'log': {'--oneline', '--all', '--decorate', '--graph', '--no-decorate'},
}
VALUES = {'switch': {'-c', '--create'}, 'commit': {'-m', '--message'}, 'log': {'-n', '--max-count'}}


def safe_prefix(prefix):
    return (isinstance(prefix, (list, tuple)) and 2 <= len(prefix) <= 32
            and all(isinstance(w, str) and re.fullmatch(r'[A-Za-z0-9_./:=+-]{1,256}', w) for w in prefix)
            and prefix[0] in ('git', 'git.exe') and prefix[1] in FLAGS)


def reviewed_command(words):
    if not safe_prefix(words[:2]):
        return False
    verb, position, literal = words[1], 2, False
    while position < len(words):
        word = words[position]
        if not literal and word in VALUES.get(verb, ()):
            position += 1
            if position == len(words):
                return False
            value = words[position]
            if value.startswith(('-', '+')) or verb == 'log' and not value.isdecimal():
                return False
        elif word == '--' and not literal:
            literal = True
        elif word.startswith('-'):
            if literal or word not in FLAGS[verb]:
                return False
        elif word.startswith(('/', '+')) or ':' in word or '..' in word.split('/'):
            return False
        position += 1
    return True


@dataclass(frozen=True)
class CodexChatRule:
    rule_id: str
    prefix: tuple
    folder: str
    shell: tuple
    allowed_at_ms: int

    @property
    def description(self):
        return f"Run {' '.join(self.prefix)} with supported arguments, starting in {self.folder}. May run outside the sandbox."


def candidate(request, cwd):
    if (request.method != COMMAND or ACCEPT not in request.offered()
            or not request.outside_sandbox or not safe_prefix(request.proposed_prefix)):
        return None
    words, shell = command_words(request.command)
    if words[:len(request.proposed_prefix)] != request.proposed_prefix or not reviewed_command(words):
        return None
    try:
        folder = platform_paths.codex_approval_folder(request.cwd, cwd)
    except (ValueError, OSError, RuntimeError):
        return None
    return CodexChatRule(uuid.uuid4().hex, request.proposed_prefix, folder, shell, int(time.time() * 1000))


def matches(rule, request, cwd):
    if request.method != COMMAND or ACCEPT not in request.offered() or not request.outside_sandbox:
        return False
    words, shell = command_words(request.command)
    if words[:len(rule.prefix)] != rule.prefix or shell != rule.shell or not reviewed_command(words):
        return False
    try:
        return platform_paths.codex_approval_folder(request.cwd, cwd) == rule.folder
    except (ValueError, OSError, RuntimeError):
        return False


class CodexPermissionStore:
    """Versioned consent in TCP app data. Unknown originals are never replaced."""
    _io_lock = threading.RLock()

    def __init__(self, root=None):
        self.root = Path(root) if root is not None else platform_paths.app_data_dir() / 'provider-permissions' / 'codex'

    def _path(self, native_id):
        return self.root / (hashlib.sha256(native_id.encode('utf-8')).hexdigest() + '.json')

    def load(self, native_id):
        with self._io_lock:
            try:
                raw = json.loads(self._path(native_id).read_text(encoding='utf-8'))
            except FileNotFoundError:
                return ()
            if (not isinstance(raw, dict) or set(raw) != {'version', 'provider', 'native_id', 'rules'}
                    or type(raw['version']) is not int or raw['version'] != 1 or raw['provider'] != 'codex'
                    or raw['native_id'] != native_id or not isinstance(raw['rules'], list) or len(raw['rules']) > 128):
                raise ValueError('Unrecognized remembered approval file; original preserved')
            rules = []
            for row in raw['rules']:
                if (not isinstance(row, dict) or set(row) != {'rule_id', 'prefix', 'folder', 'shell', 'allowed_at_ms'}
                        or not isinstance(row['rule_id'], str) or not re.fullmatch(r'[a-f0-9]{32}', row['rule_id'])
                        or not safe_prefix(row['prefix']) or not isinstance(row['folder'], str) or not row['folder']
                        or not isinstance(row['shell'], list) or not 1 <= len(row['shell']) <= 5
                        or any(not isinstance(s, str) or not s or len(s) > 1024 for s in row['shell'])
                        or type(row['allowed_at_ms']) is not int or row['allowed_at_ms'] <= 0):
                    raise ValueError('Invalid remembered approval; original preserved')
                rules.append(CodexChatRule(row['rule_id'], tuple(row['prefix']), row['folder'],
                                           tuple(row['shell']), row['allowed_at_ms']))
            if len({r.rule_id for r in rules}) != len(rules):
                raise ValueError('Duplicate remembered approvals; original preserved')
            return tuple(rules)

    def save(self, native_id, rules):
        with self._io_lock:
            self.load(native_id)  # Refuse to overwrite a corrupt/newer file.
            if len(rules) > 128:
                raise ValueError('This chat already has 128 remembered approvals')
            atomic_write_json(self._path(native_id), {'version': 1, 'provider': 'codex', 'native_id': native_id,
                                                     'rules': [asdict(r) for r in rules]})

    def clear(self, native_id):
        with self._io_lock:
            try:
                self.save(native_id, ())
            except (ValueError, UnicodeError):
                # A mode change must revoke even a newer format without losing
                # its contents. A failed rename propagates; never claim removal.
                path = self._path(native_id)
                path.rename(path.with_name(path.stem + '.preserved-' + uuid.uuid4().hex + '.json'))

    def remove(self, native_id):
        """Permanent chat deletion also removes its preserved approval files."""
        with self._io_lock:
            path = self._path(native_id)
            path.unlink(missing_ok=True)
            for preserved in self.root.glob(path.stem + '.preserved-*.json'):
                preserved.unlink(missing_ok=True)


class CodexReviewStore(CodexPermissionStore):
    """Bounded decision history in TCP only; never a permission grant (#358)."""
    def __init__(self, root=None):
        super().__init__(root)
        self.root = self.root / 'automatic-reviews'

    def load(self, native_id):
        with self._io_lock:
            try:
                path = self._path(native_id)
                if path.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError('Automatic review history is too large; original preserved')
                raw = json.loads(path.read_text(encoding='utf-8'))
            except FileNotFoundError:
                return ()
            if (not isinstance(raw, dict) or raw.get('version') != 1 or raw.get('provider') != 'codex'
                    or raw.get('native_id') != native_id or not isinstance(raw.get('reviews'), list)
                    or len(raw['reviews']) > 128
                    or any(not isinstance(row, list) or len(row) != 3
                           or any(not isinstance(s, str) or not s or len(s) > 10000 for s in row)
                           for row in raw['reviews'])):
                raise ValueError('Automatic review history could not be read; original preserved')
            return tuple(tuple(row) for row in raw['reviews'])

    def remember(self, native_id, turn_id, reviews):
        with self._io_lock:
            kept = list(self.load(native_id))
            seen = {(row[0], row[1]) for row in kept}
            fresh = [(turn_id, identity, text) for identity, text in reviews
                     if (turn_id, identity) not in seen]
            if fresh:
                atomic_write_json(self._path(native_id), {'version': 1, 'provider': 'codex',
                    'native_id': native_id, 'reviews': (kept + fresh)[-128:]})
