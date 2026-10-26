"""Owned thread lifecycle with explicit runtime billing/policy overrides.

Creation, fork and owned resume establish the runtime configuration that the
turn executor validates independently before inference (#184) (#171).
"""
from dataclasses import dataclass, field, replace

from .. import platform_paths
from ..own_store import OwnSession
from ..sessions import SessionInfo
from .codex_rpc import (verify_codex_rule_layers, codex_tool_overrides, verify_codex_tools,
                        is_chatgpt_account, CodexRpcError, CodexTurnRejected,
                        ReadOnlyCodexConnection)
from .codex_models import read_models
from .codex_policy import CodexPolicy

CHATGPT_BASE_URL = "https://chatgpt.com/backend-api/"


class CodexLifecycleConnection(ReadOnlyCodexConnection):
    METHODS = ReadOnlyCodexConnection.METHODS | {"thread/start", "thread/fork", "thread/resume"}


@dataclass(frozen=True)
class CodexOwnedThread:
    native_id: str
    cwd: str
    model: str
    title: str
    created_ms: int
    connection: object = field(repr=False)
    ephemeral: bool = False
    effort: str | None = None
    policy: CodexPolicy = field(default_factory=CodexPolicy)

    def record(self):
        # Return a fresh record: persisted/mutable UI metadata cannot change
        # the runtime identity/configuration proof used by an executor.
        if self.ephemeral:
            raise ValueError("Ephemeral Codex chats cannot be saved for later resume")
        options = {"model": self.model, "sandbox": self.policy.sandbox, "approval_policy": self.policy.approval}
        if self.policy.reviewer != 'user':
            options['approvals_reviewer'] = self.policy.reviewer
        if self.policy.workspace_root:
            options['workspace_root'] = self.policy.workspace_root
        if self.effort is not None:
            options["effort"] = self.effort
        return OwnSession(self.native_id, self.title, self.cwd, provider="codex",
                          created_ms=self.created_ms, started=True,
                          provider_options=options)


class CodexOwnedThreads:
    def __init__(self, connection):
        self.connection = connection

    def _preflight(self, cwd):
        if not isinstance(cwd, str) or not cwd.strip():
            raise ValueError("Codex working directory required")
        account = self.connection.request("account/read", {"refreshToken": True})
        if not is_chatgpt_account(account):
            raise CodexRpcError("Codex owned chats require ChatGPT sign-in")
        result = self.connection.request("config/read", {"cwd": cwd, "includeLayers": True})
        config = result.get("config")
        if not isinstance(config, dict):
            raise CodexRpcError("Codex configuration unavailable")
        tools = codex_tool_overrides(config)
        providers = config.get("model_providers", {})
        if (config.get("model_provider") not in (None, "openai")
                or config.get("forced_login_method") not in (None, "chatgpt")
                or config.get("openai_base_url") is not None
                or config.get("chatgpt_base_url") not in (None, CHATGPT_BASE_URL)
                or not isinstance(providers, dict) or "openai" in providers):
            raise CodexRpcError("Codex owned chats require the default ChatGPT route")

        verify_codex_rule_layers(result)
        return tools

    def _verify_idle(self, native_id, *, loaded=False, empty=False):
        # Inspect native state before changing or recovering a grant. A new,
        # still-owned empty thread has no persisted rollout to list yet.
        metadata = self.connection.request('thread/read', {'threadId': native_id, 'includeTurns': False})
        thread = metadata.get('thread')
        statuses = ('idle',) if loaded else ('idle', 'notLoaded')
        if (not isinstance(thread, dict) or thread.get('id') != native_id
                or not isinstance(thread.get('status'), dict)
                or thread['status'].get('type') not in statuses):
            raise CodexTurnRejected('not_idle')
        if empty:
            return
        latest = self.connection.request('thread/turns/list', {
            'threadId': native_id, 'limit': 1, 'sortDirection': 'desc', 'itemsView': 'notLoaded'})
        turns = latest.get('data')
        if (not isinstance(turns, list) or len(turns) > 1
                or any(not isinstance(turn, dict) or turn.get('status') not in
                       ('completed', 'failed', 'interrupted') for turn in turns)):
            raise CodexTurnRejected('not_idle')

    def verify_loaded(self, proof, *, empty=False):
        """Recheck retained ownership without resuming or replacing the thread."""
        if not self.connection.owns_runtime(proof):
            raise CodexRpcError('Codex execution requires a verified owned chat')
        proof.policy.verify_workspace(proof.cwd)
        self._verify_idle(proof.native_id, loaded=True, empty=empty)
        verify_codex_tools(self.connection, proof.native_id)
        if not self.connection.owns_runtime(proof):
            raise CodexRpcError('Codex connection changed while checking permissions')

    def recheck_saved(self, proof, record, *, empty=False):
        options = self._saved_options(record)
        checked = self.start(record.cwd, record.title, ephemeral=True,
            model=options.get('model'), effort=options.get('effort'),
            sandbox=options.get('sandbox', 'read-only'),
            approval_policy=options.get('approval_policy', 'on-request'),
            approvals_reviewer=options.get('approvals_reviewer', 'user'))
        if checked.cwd != record.cwd:
            raise CodexRpcError('Saved Codex workspace scope could not be verified')
        self.verify_loaded(proof, empty=empty)
        return replace(proof, cwd=checked.cwd, model=checked.model, effort=checked.effort,
                       policy=checked.policy, title=record.title)

    def _create(self, method, cwd, title, *, native_id=None, ephemeral=False, model=None, effort=None,
                sandbox='read-only', approval_policy='on-request', approvals_reviewer='user'):
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Codex chat title required")
        if type(ephemeral) is not bool:
            raise ValueError("Codex ephemeral selection must be boolean")
        if model is not None and (not isinstance(model, str) or not model.strip()
                                  or len(model) > 256 or any(ord(c) < 32 for c in model)):
            raise ValueError("Invalid Codex model")
        if effort is not None and (model is None or not isinstance(effort, str) or not effort.strip()
                                   or len(effort) > 256 or any(ord(c) < 32 for c in effort)):
            raise ValueError("Codex reasoning effort requires a selected model")
        policy = CodexPolicy.choose(cwd, sandbox, approval_policy, approvals_reviewer)
        if policy.workspace_root:
            cwd = policy.workspace_root
        tools = self._preflight(cwd)
        if method == 'thread/resume':
            # Resume changes the loaded thread. Inspect persisted turn state
            # first, even after restarting TCP; never overwrite unfinished work.
            self._verify_idle(native_id)
        if (model is not None and method != "thread/resume") or effort is not None:
            choice = next((choice for choice in read_models(self.connection) if choice.model == model), None)
            if choice is None or (effort is not None and effort not in choice.efforts):
                raise ValueError("Codex model or reasoning effort is no longer available")
        params = {"cwd": cwd, "modelProvider": "openai", "sandbox": policy.sandbox,
                  "approvalPolicy": policy.approval, "config": {
                      "model_provider": "openai", "model_providers": {},
                      "forced_login_method": "chatgpt", "openai_base_url": None,
                      "chatgpt_base_url": CHATGPT_BASE_URL, "approvals_reviewer": policy.reviewer}}
        params['config'].update(policy.config())
        params['config'].update(tools)
        if native_id is not None:
            params["threadId"] = native_id
        if model is not None:
            params["model"] = model
        if effort is not None:
            params["config"]["model_reasoning_effort"] = effort
        if method != "thread/resume":
            params["ephemeral"] = ephemeral
        try:
            result = self.connection.request(method, params)
        except CodexRpcError as error:
            if policy.reviewer == 'auto_review':
                raise CodexTurnRejected('auto_review_unavailable') from error
            raise
        if policy.reviewer == 'auto_review' and result.get('approvalsReviewer') != policy.reviewer:
            raise CodexTurnRejected('auto_review_unavailable')
        thread = result.get("thread")
        actual_id = thread.get("id") if isinstance(thread, dict) else None
        status = thread.get("status") if isinstance(thread, dict) else None
        policy.verify(result)
        if (not isinstance(actual_id, str) or not platform_paths.is_safe_id(actual_id)
                or result.get("modelProvider") != "openai"
                or not isinstance(result.get("cwd"), str) or not result["cwd"].strip()
                or not isinstance(result.get("model"), str) or not result["model"].strip()
                or len(result["model"]) > 256
                or (model is not None and result["model"] != model)
                or (effort is not None and result.get("reasoningEffort") != effort)
                or result.get("approvalsReviewer") != policy.reviewer
                or not isinstance(status, dict) or status.get("type") != "idle"
                or thread.get("ephemeral") is not ephemeral
                or (method == "thread/resume" and actual_id != native_id)
                or (method == "thread/fork" and actual_id == native_id)):
            # Never grant a runtime proof from a partial or mismatched response.
            # No prompt has been sent; uncertain creation is never replayed.
            raise CodexRpcError("Codex owned chat configuration could not be verified")
        # Check again after lifecycle loading before granting any runtime proof.
        # A proof from an earlier, ungated process must never be reused.
        verified_config = self.connection.request('config/read', {'cwd': cwd, 'includeLayers': True})
        verify_codex_rule_layers(verified_config)
        verify_codex_tools(self.connection, actual_id)
        created = thread.get("createdAt")
        created_ms = created * 1000 if type(created) is int and created >= 0 else 0
        proof = CodexOwnedThread(actual_id, policy.workspace_root or result["cwd"], result["model"], title,
                                 created_ms, self.connection, ephemeral, effort, policy)
        accept = getattr(self.connection, "_accept_owned_thread", None)
        if callable(accept) and not ephemeral:
            accept(proof)
        return proof

    def start(self, cwd, title, *, ephemeral=False, model=None, effort=None,
              sandbox='read-only', approval_policy='on-request', approvals_reviewer='user'):
        return self._create("thread/start", cwd, title, ephemeral=ephemeral, model=model, effort=effort,
                            sandbox=sandbox, approval_policy=approval_policy, approvals_reviewer=approvals_reviewer)

    def fork(self, source: SessionInfo, title, *, ephemeral=False, model=None, effort=None,
             sandbox='read-only', approval_policy='on-request', approvals_reviewer='user'):
        if (not isinstance(source, SessionInfo) or source.provider != "codex"
                or not isinstance(source.cli_session_id, str)
                or not platform_paths.is_safe_id(source.cli_session_id)):
            raise ValueError("Codex fork requires a valid source chat")
        return self._create("thread/fork", source.cwd, title,
                            native_id=source.cli_session_id, ephemeral=ephemeral, model=model, effort=effort,
                            sandbox=sandbox, approval_policy=approval_policy, approvals_reviewer=approvals_reviewer)

    @staticmethod
    def _saved_options(record):
        if (not isinstance(record, OwnSession) or record.provider != "codex"
                or record.started is not True or not isinstance(record.cli_session_id, str)
                or not platform_paths.is_safe_id(record.cli_session_id)):
            raise ValueError("Only a started TCP-owned Codex chat can be resumed")
        options = record.provider_options
        if (not isinstance(options, dict) or set(options) - {"model", "sandbox", "approval_policy", "effort", "workspace_root", "approvals_reviewer"}
                or options.get("sandbox", "read-only") not in ('read-only', 'workspace-write')
                or options.get("approval_policy", "on-request") not in ('on-request', 'never')):
            raise ValueError("Codex owned chat options are not supported yet")
        if options.get('sandbox', 'read-only') == 'workspace-write':
            # The saved grant binds permission to the original canonical folder.
            # Re-resolving a replaced path must not silently grant a new scope.
            root = options.get('workspace_root')
            if not isinstance(root, str) or not root:
                raise ValueError('Codex writable workspace grant is missing')
            CodexPolicy('workspace-write', options.get('approval_policy', 'on-request'), root).verify_workspace(record.cwd)
        elif 'workspace_root' in options:
            raise ValueError('Read-only Codex chats cannot retain a writable workspace grant')
        return options

    def resume(self, record: OwnSession):
        options = self._saved_options(record)
        return self._create("thread/resume", record.cwd, record.title,
                            native_id=record.cli_session_id, model=options.get("model"), effort=options.get("effort"),
                            sandbox=options.get('sandbox', 'read-only'),
                            approval_policy=options.get('approval_policy', 'on-request'),
                            approvals_reviewer=options.get('approvals_reviewer', 'user'))
