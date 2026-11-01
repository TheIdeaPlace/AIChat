"""Owned thread lifecycle with explicit runtime billing/policy overrides.

Creation, fork and owned resume establish the runtime configuration that the
turn executor validates independently before inference (#184) (#171).
"""
from dataclasses import dataclass, field, replace

from .. import platform_paths
from ..own_store import OwnSession
from ..sessions import SessionInfo
from .codex_rpc import (check_startable, verify_codex_rule_layers, codex_tool_overrides, verify_codex_tools,
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
    mcp_servers: tuple = ()
    collaboration: str | None = None

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
        if self.mcp_servers:
            options['mcp_servers'] = list(self.mcp_servers)
        if self.collaboration is not None:
            options['collaboration'] = self.collaboration
        return OwnSession(self.native_id, self.title, self.cwd, provider="codex",
                          created_ms=self.created_ms, started=True,
                          provider_options=options)


class CodexOwnedThreads:
    def __init__(self, connection):
        self.connection = connection

    def _preflight(self, cwd, mcp_servers=()):
        if not isinstance(cwd, str) or not cwd.strip():
            raise ValueError("Codex working directory required")
        account = self.connection.request("account/read", {"refreshToken": True})
        if not is_chatgpt_account(account):
            raise CodexRpcError("Codex owned chats require ChatGPT sign-in")
        result = self.connection.request("config/read", {"cwd": cwd, "includeLayers": True})
        config = result.get("config")
        if not isinstance(config, dict):
            raise CodexRpcError("Codex configuration unavailable")
        tools = codex_tool_overrides(config, mcp_servers)
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
        if not isinstance(thread, dict) or thread.get('id') != native_id:
            raise CodexTurnRejected('status_unclear')
        # A thread whose last turn failed reports systemError until a new
        # turn starts, so reopening or rechecking it must accept that (#448).
        check_startable(self.connection, native_id, thread, not_loaded=not loaded,
                        check_turns=not empty)

    def verify_loaded(self, proof, *, empty=False):
        """Recheck retained ownership without resuming or replacing the thread."""
        if not self.connection.owns_runtime(proof):
            raise CodexRpcError('Codex execution requires a verified owned chat')
        proof.policy.verify_workspace(proof.cwd)
        self._verify_idle(proof.native_id, loaded=True, empty=empty)
        verify_codex_tools(self.connection, proof.native_id, proof.mcp_servers)
        if not self.connection.owns_runtime(proof):
            raise CodexRpcError('Codex connection changed while checking permissions')

    def recheck_saved(self, proof, record, *, empty=False):
        options = self._saved_options(record)
        checked = self.start(record.cwd, record.title, ephemeral=True,
            model=options.get('model'), effort=options.get('effort'),
            sandbox=options.get('sandbox', 'read-only'),
            approval_policy=options.get('approval_policy', 'on-request'),
            approvals_reviewer=options.get('approvals_reviewer', 'user'),
            mcp_servers=options.get('mcp_servers', ()))
        if checked.cwd != record.cwd:
            raise CodexRpcError('Saved Codex workspace scope could not be verified')
        self.verify_loaded(proof, empty=empty)
        return replace(proof, cwd=checked.cwd, model=checked.model, effort=checked.effort,
                       policy=checked.policy, title=record.title)

    def _create(self, method, cwd, title, *, native_id=None, ephemeral=False, model=None, effort=None,
                sandbox='read-only', approval_policy='on-request', approvals_reviewer='user',
                last_turn_id=None, before_turn_id=None, passive=False, mcp_servers=(), collaboration=None):
        from .codex_tools import server_names
        mcp_servers = server_names(mcp_servers)
        if collaboration not in (None, 'default', 'plan'):
            raise ValueError('Invalid Codex collaboration mode')
        if collaboration is not None:
            presets = self.connection.request('collaborationMode/list', {})
            if not isinstance(presets.get('data'), list) or not any(
                    isinstance(p, dict) and p.get('mode') == collaboration for p in presets['data']):
                from .codex_features import CodexFeatureError
                raise CodexFeatureError('unsupported')
        if passive and (not ephemeral or sandbox != 'read-only' or approval_policy != 'never'):
            raise ValueError('Passive Codex work requires an ephemeral read-only fork')
        if last_turn_id is not None or before_turn_id is not None:
            if (method != 'thread/fork' or (last_turn_id is not None and before_turn_id is not None)
                    or not platform_paths.is_safe_id(last_turn_id or before_turn_id)):
                raise ValueError('Invalid Codex branch point')
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
        if passive and mcp_servers:
            raise ValueError('Side questions cannot enable external tools')
        tools = self._preflight(cwd, mcp_servers)
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
        # TCP must authorize every inferred turn. Native Goal tools would
        # schedule continuations without going through that authorization.
        params['config']['features']['goals'] = False
        if passive:
            params['config']['features'].update(hooks=False, goals=False, memories=False,
                shell_tool=False, unified_exec=False, js_repl=False, multi_agent=False)
        if native_id is not None:
            params["threadId"] = native_id
        if last_turn_id is not None:
            params['lastTurnId'] = last_turn_id
        if before_turn_id is not None:
            params['beforeTurnId'] = before_turn_id
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
                or not isinstance(status, dict)
                or status.get("type") not in (("idle", "systemError") if method == "thread/resume" else ("idle",))
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
        verify_codex_tools(self.connection, actual_id, mcp_servers)
        effective_effort = result.get('reasoningEffort')
        if effective_effort is not None and (not isinstance(effective_effort, str)
                or not effective_effort.strip() or len(effective_effort) > 256
                or any(ord(c) < 32 for c in effective_effort)):
            raise CodexRpcError('Codex reasoning effort could not be verified')
        created = thread.get("createdAt")
        created_ms = created * 1000 if type(created) is int and created >= 0 else 0
        proof = CodexOwnedThread(actual_id, policy.workspace_root or result["cwd"], result["model"], title,
                                 created_ms, self.connection, ephemeral, effective_effort, policy, mcp_servers, collaboration)
        accept = getattr(self.connection, "_accept_owned_thread", None)
        if callable(accept) and not ephemeral:
            accept(proof)
        return proof

    def start(self, cwd, title, *, ephemeral=False, model=None, effort=None,
              sandbox='read-only', approval_policy='on-request', approvals_reviewer='user', mcp_servers=(), collaboration=None):
        return self._create("thread/start", cwd, title, ephemeral=ephemeral, model=model, effort=effort,
                            sandbox=sandbox, approval_policy=approval_policy, approvals_reviewer=approvals_reviewer,
                            mcp_servers=mcp_servers, collaboration=collaboration)

    def fork(self, source: SessionInfo, title, *, ephemeral=False, model=None, effort=None,
             sandbox='read-only', approval_policy='on-request', approvals_reviewer='user',
             last_turn_id=None, before_turn_id=None, passive=False, mcp_servers=()):
        if (not isinstance(source, SessionInfo) or source.provider != "codex"
                or not isinstance(source.cli_session_id, str)
                or not platform_paths.is_safe_id(source.cli_session_id)):
            raise ValueError("Codex fork requires a valid source chat")
        return self._create("thread/fork", source.cwd, title,
                            native_id=source.cli_session_id, ephemeral=ephemeral, model=model, effort=effort,
                            sandbox=sandbox, approval_policy=approval_policy, approvals_reviewer=approvals_reviewer,
                            last_turn_id=last_turn_id, before_turn_id=before_turn_id, passive=passive,
                            mcp_servers=mcp_servers)

    @staticmethod
    def _saved_options(record):
        if (not isinstance(record, OwnSession) or record.provider != "codex"
                or record.started is not True or not isinstance(record.cli_session_id, str)
                or not platform_paths.is_safe_id(record.cli_session_id)):
            raise ValueError("Only a started TCP-owned Codex chat can be resumed")
        options = record.provider_options
        if (not isinstance(options, dict) or set(options) - {"model", "sandbox", "approval_policy", "effort", "workspace_root", "approvals_reviewer", "mcp_servers", "collaboration"}
                or options.get("sandbox", "read-only") not in ('read-only', 'workspace-write')
                or options.get("approval_policy", "on-request") not in ('on-request', 'never')):
            raise ValueError("Codex owned chat options are not supported yet")
        from .codex_tools import server_names
        server_names(options.get('mcp_servers', ()))
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
                            approvals_reviewer=options.get('approvals_reviewer', 'user'),
                            mcp_servers=options.get('mcp_servers', ()), collaboration=options.get('collaboration'))
