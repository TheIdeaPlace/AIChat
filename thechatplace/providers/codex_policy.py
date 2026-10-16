"""Verified filesystem/approval choices for owned Codex runtimes (#184)."""
from dataclasses import dataclass

from .. import platform_paths
from .codex_rpc import CodexRpcError, CodexTurnRejected


@dataclass(frozen=True)
class CodexPolicy:
    sandbox: str = 'read-only'
    approval: str = 'on-request'
    workspace_root: str = ''

    @classmethod
    def choose(cls, cwd, sandbox='read-only', approval='on-request'):
        if sandbox not in ('read-only', 'workspace-write') or approval not in ('on-request', 'never'):
            raise ValueError('Unsupported Codex sandbox or approval policy')
        root = platform_paths.codex_workspace_root(cwd) if sandbox == 'workspace-write' else ''
        return cls(sandbox, approval, root)

    def config(self):
        if self.sandbox == 'read-only':
            return {}
        # Workspace-write already includes cwd. These are additional roots;
        # pin an empty list instead of inheriting any broader configured roots.
        return {'sandbox_workspace_write': {'writable_roots': [],
            'network_access': False, 'exclude_slash_tmp': True, 'exclude_tmpdir_env_var': True}}

    def turn_policy(self):
        if self.sandbox == 'read-only':
            return {'type': 'readOnly'}
        return {'type': 'workspaceWrite', 'writableRoots': [self.workspace_root],
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
                     and actual.get('writableRoots') in ([], [self.workspace_root]))
            try:
                valid = valid and platform_paths.codex_workspace_root(result.get('cwd')) == self.workspace_root
            except (ValueError, OSError):
                valid = False
        if not valid:
            if (self.sandbox == 'workspace-write' and isinstance(actual, dict)
                    and actual.get('type') == 'readOnly' and result.get('approvalPolicy') == self.approval):
                # Codex downgrades workspace-write to read-only on Windows when
                # its Agent sandbox isn't set up (effective_sandbox_mode in
                # codex-rs config_toml.rs). Still refused, but say why (#257).
                raise CodexTurnRejected('sandbox_unavailable')
            raise CodexRpcError('Codex owned chat policy could not be verified')

    def verify_workspace(self, cwd):
        if self.sandbox == 'workspace-write':
            try:
                if platform_paths.codex_workspace_root(cwd) == self.workspace_root:
                    return
            except (ValueError, OSError):
                pass
            raise CodexRpcError('Codex writable workspace could not be verified')
