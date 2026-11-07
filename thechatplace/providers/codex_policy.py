"""Verified filesystem/approval choices for owned Codex runtimes (#184)."""
from dataclasses import dataclass

from .. import platform_paths
from .codex_rpc import CodexRpcError, CodexTurnRejected


@dataclass(frozen=True)
class CodexPolicy:
    sandbox: str = 'read-only'
    approval: str = 'on-request'
    workspace_root: str = ''
    reviewer: str = 'user'
    extra_roots: tuple = ()

    @classmethod
    def choose(cls, cwd, sandbox='read-only', approval='on-request', reviewer='user', extra_roots=()):
        if sandbox not in ('read-only', 'workspace-write') or approval not in ('on-request', 'never'):
            raise ValueError('Unsupported Codex sandbox or approval policy')
        if reviewer not in ('user', 'auto_review') or reviewer == 'auto_review' and approval != 'on-request':
            raise ValueError('Automatic review requires Codex on-request approvals')
        root = platform_paths.codex_workspace_root(cwd) if sandbox == 'workspace-write' else ''
        if not isinstance(extra_roots, (list, tuple)) or len(extra_roots) > 32:
            raise ValueError('Choose at most 32 additional folders')
        roots = tuple(dict.fromkeys(platform_paths.codex_workspace_root(p) for p in extra_roots))
        return cls(sandbox, approval, root, reviewer, roots)

    def config(self):
        if self.sandbox == 'read-only':
            return {}
        # Workspace-write already includes cwd. These are additional roots;
        # pin the chosen list instead of inheriting broader configured roots.
        return {'sandbox_workspace_write': {'writable_roots': list(self.extra_roots),
            'network_access': False, 'exclude_slash_tmp': True, 'exclude_tmpdir_env_var': True}}

    def turn_policy(self):
        if self.sandbox == 'read-only':
            return {'type': 'readOnly'}
        return {'type': 'workspaceWrite', 'writableRoots': list(dict.fromkeys((self.workspace_root, *self.extra_roots))),
                'networkAccess': False, 'excludeSlashTmp': True, 'excludeTmpdirEnvVar': True}

    def verify(self, result):
        actual = result.get('sandbox')
        valid = (isinstance(actual, dict) and result.get('approvalPolicy') == self.approval)
        if self.sandbox == 'read-only':
            valid = valid and actual.get('type') == 'readOnly' and actual.get('networkAccess', False) is False
        else:
            valid = (valid and actual.get('type') == 'workspaceWrite'
                     and actual.get('networkAccess', False) is False
                     and actual.get('excludeSlashTmp') is True and actual.get('excludeTmpdirEnvVar') is True
                     and actual.get('writableRoots') in (list(self.extra_roots), list(dict.fromkeys((self.workspace_root, *self.extra_roots)))))
            try:
                valid = valid and platform_paths.codex_workspace_root(result.get('cwd')) == self.workspace_root
            except (ValueError, OSError):
                valid = False
        if not valid:
            if self._sandbox_downgraded(actual, result):
                # Codex downgrades workspace-write to read-only on Windows when
                # its Windows sandbox isn't set up (effective_sandbox_mode in
                # codex-rs config_toml.rs). Still refused, but say why (#257).
                raise CodexTurnRejected('sandbox_unavailable')
            raise CodexRpcError('Codex owned chat policy could not be verified')

    def _sandbox_downgraded(self, actual, result):
        # Only the exact downgrade: everything else matched, so the advice to
        # set up the sandbox is never given for a wrong folder or approval.
        if (self.sandbox != 'workspace-write' or not platform_paths.codex_downgrades_workspace_write()
                or not isinstance(actual, dict) or actual.get('type') != 'readOnly'
                or actual.get('networkAccess', False) is not False
                or result.get('approvalPolicy') != self.approval):
            return False
        try:
            return platform_paths.codex_workspace_root(result.get('cwd')) == self.workspace_root
        except (ValueError, OSError):
            return False

    def verify_workspace(self, cwd):
        if self.sandbox == 'workspace-write':
            try:
                if (platform_paths.codex_workspace_root(cwd) == self.workspace_root and
                        all(platform_paths.codex_workspace_root(p) == p for p in self.extra_roots)):
                    return
            except (ValueError, OSError):
                pass
            raise CodexRpcError('Codex writable workspace could not be verified')
