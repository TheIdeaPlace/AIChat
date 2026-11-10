"""Owned thread lifecycle with explicit runtime billing/policy overrides.

Creation, fork and owned resume establish the runtime configuration that the
turn executor validates independently before inference (#184) (#171).
"""
from dataclasses import dataclass, field, replace

from .. import platform_paths
from ..own_store import OwnSession
from ..sessions import SessionInfo
from .codex_features import CodexFeatureError
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
    #: The effort the chat asked for, which is what's saved. ``effort`` is what
    #: Codex reports it's using, pinned on each turn. Keeping them apart stops a
    #: chat that follows its configured effort from looking changed (#467).
    saved_effort: str | None = None
    #: A saved mode this Codex couldn't offer when the chat resumed. Its turns
    #: run without it, in Default, but the choice stays saved for a later Codex.
    unavailable_mode: str | None = None
    instructions: str | None = None
    apps: tuple = ()
    session_tool_caller: str = field(default="", repr=False)
    #: The model the chat asked for, which is what's saved; None is Codex's
    #: default, followed at each turn (codex_runtime._follow_default), as
    #: ``saved_effort`` is for effort. ``model`` is the one Codex reports and
    #: each turn pins. Chats saved before 0.5 always named a model and keep it
    #: (#596, decision 19).
    saved_model: str | None = None

    @property
    def allowed_tools(self):
        return self.mcp_servers + (("ccd_session_mgmt",) if self.session_tool_caller else ()) + (("codex_apps",) if self.apps else ())

    def record(self):
        # Return a fresh record: persisted/mutable UI metadata cannot change
        # the runtime identity/configuration proof used by an executor.
        if self.ephemeral:
            raise ValueError("Ephemeral Codex chats cannot be saved for later resume")
        options = {"sandbox": self.policy.sandbox, "approval_policy": self.policy.approval}
        if self.saved_model is not None:
            options["model"] = self.saved_model
        if self.policy.reviewer != 'user':
            options['approvals_reviewer'] = self.policy.reviewer
        if self.policy.workspace_root:
            options['workspace_root'] = self.policy.workspace_root
        if self.instructions is not None:
            options['instructions'] = self.instructions
        if self.policy.extra_roots:
            options['extra_roots'] = list(self.policy.extra_roots)
        if self.saved_effort is not None:
            options["effort"] = self.saved_effort
        if self.apps:
            options['apps'] = list(self.apps)
        if self.mcp_servers:
            options['mcp_servers'] = list(self.mcp_servers)
        mode = self.collaboration if self.collaboration is not None else self.unavailable_mode
        if mode is not None:
            options['collaboration'] = mode
        return OwnSession(self.native_id, self.title, self.cwd, provider="codex",
                          created_ms=self.created_ms, started=True,
                          provider_options=options)


class CodexOwnedThreads:
    def __init__(self, connection):
        self.connection = connection

    def _preflight(self, cwd, mcp_servers=(), apps=()):
        if not isinstance(cwd, str) or not cwd.strip():
            raise ValueError("Codex working directory required")
        account = self.connection.request("account/read", {"refreshToken": True})
        if not is_chatgpt_account(account):
            raise CodexRpcError("Codex owned chats require ChatGPT sign-in")
        result = self.connection.request("config/read", {"cwd": cwd, "includeLayers": True})
        config = result.get("config")
        if not isinstance(config, dict):
            raise CodexRpcError("Codex configuration unavailable")
        if getattr(self.connection, 'session_tools', False) and 'ccd_session_mgmt' in (config.get('mcp_servers') or {}):
            raise CodexRpcError('Remove or rename the configured ccd_session_mgmt server before opening this chat here')
        tools = codex_tool_overrides(config, mcp_servers, apps)
        providers = config.get("model_providers", {})
        if (config.get("model_provider") not in (None, "openai")
                or config.get("forced_login_method") not in (None, "chatgpt")
                or config.get("openai_base_url") is not None
                or config.get("chatgpt_base_url") not in (None, CHATGPT_BASE_URL)
                or not isinstance(providers, dict) or "openai" in providers):
            raise CodexRpcError("Codex owned chats require the default ChatGPT route")

        verify_codex_rule_layers(result)
        return tools

    def _thread_gone(self, native_id):
        """Whether Codex's own thread list, active and archived, has no such
        thread, so there is nothing to resume however often TCP retries.
        Any doubt (a failed or malformed list) is not proof: return False."""
        try:
            for archived in (False, True):
                cursor, cursors = None, set()
                while True:
                    params = {'archived': archived, 'limit': 100,
                              'sourceKinds': ['cli', 'vscode', 'exec', 'appServer', 'unknown']}
                    if cursor is not None:
                        params['cursor'] = cursor
                    result = self.connection.request('thread/list', params)
                    data = result.get('data') if isinstance(result, dict) else None
                    if not isinstance(data, list):
                        return False
                    if any(isinstance(thread, dict) and thread.get('id') == native_id
                           for thread in data):
                        return False
                    cursor = result.get('nextCursor')
                    if cursor is None:
                        break
                    if not isinstance(cursor, str) or not cursor or cursor in cursors:
                        return False
                    cursors.add(cursor)
        except Exception:  # noqa: BLE001 - an unreadable list proves nothing
            return False
        return True

    def _verify_idle(self, native_id, *, loaded=False, empty=False):
        # Inspect native state before changing or recovering a grant. A new,
        # still-owned empty thread has no persisted rollout to list yet.
        try:
            metadata = self.connection.request('thread/read', {'threadId': native_id, 'includeTurns': False})
        except CodexRpcError:
            # A failed read alone doesn't prove the thread is gone (the
            # connection may just be unhealthy). Only say so when Codex's
            # thread list confirms it; a loaded thread is Codex's own live
            # state, never judged by the list.
            if not loaded and self._thread_gone(native_id):
                raise CodexTurnRejected('thread_gone') from None
            raise
        thread = metadata.get('thread')
        if not isinstance(thread, dict) or thread.get('id') != native_id:
            raise CodexTurnRejected('status_unclear')
        # A thread whose last turn failed reports systemError until a new
        # turn starts, so reopening or rechecking it must accept that (#448).
        check_startable(self.connection, native_id, thread, not_loaded=not loaded,
                        check_turns=not empty)

    def verify_loaded(self, proof, *, empty=False, retained_roots=None):
        """Recheck retained ownership without resuming or replacing the thread."""
        if not self.connection.owns_runtime(proof):
            raise CodexRpcError('Codex execution requires a verified owned chat')
        policy = proof.policy if retained_roots is None else replace(proof.policy, extra_roots=retained_roots)
        policy.verify_workspace(proof.cwd)
        self._verify_idle(proof.native_id, loaded=True, empty=empty)
        verify_codex_tools(self.connection, proof.native_id, proof.allowed_tools)
        if not self.connection.owns_runtime(proof):
            raise CodexRpcError('Codex connection changed while checking permissions')

    def recheck_saved(self, proof, record, *, empty=False):
        options = self._saved_options(record)
        checked = self.start(record.cwd, record.title, ephemeral=True,
            model=options.get('model'), effort=options.get('effort'),
            sandbox=options.get('sandbox', 'read-only'),
            approval_policy=options.get('approval_policy', 'on-request'),
            approvals_reviewer=options.get('approvals_reviewer', 'user'),
            mcp_servers=options.get('mcp_servers', ()), allow_retired=True,
            instructions=options.get('instructions'), extra_roots=options.get('extra_roots', ()), apps=options.get('apps', ()))
        if checked.cwd != record.cwd:
            raise CodexRpcError('Saved Codex workspace scope could not be verified')
        self.verify_loaded(proof, empty=empty)
        return replace(proof, cwd=checked.cwd, model=checked.model, effort=checked.effort,
                       saved_effort=checked.saved_effort, saved_model=checked.saved_model,
                       policy=checked.policy, instructions=checked.instructions, title=record.title)

    def _require_mode(self, mode):
        try:
            presets = self.connection.request('collaborationMode/list', {})
        except CodexRpcError as error:
            # Only "no such method" means no Plan. Any other invalid request is
            # a real failure, as in feature(), and must not hide a saved mode.
            if getattr(error, 'rpc_code', None) == -32601 or getattr(error, 'unknown_method', False):
                raise CodexFeatureError('unsupported') from None
            raise
        if not isinstance(presets.get('data'), list) or not any(
                isinstance(p, dict) and p.get('mode') == mode for p in presets['data']):
            raise CodexFeatureError('unsupported')

    def _create(self, method, cwd, title, *, native_id=None, ephemeral=False, model=None, effort=None,
                sandbox='read-only', approval_policy='on-request', approvals_reviewer='user',
                last_turn_id=None, before_turn_id=None, passive=False, mcp_servers=(), collaboration=None,
                allow_retired=False, instructions=None, extra_roots=(), apps=(), keep_mode=False):
        """``keep_mode``: a mode kept from another chat (a copy), which, like
        a saved one, stays saved if this Codex can't offer it."""
        from .codex_tools import server_names, app_ids
        mcp_servers = server_names(mcp_servers)
        apps = app_ids(apps)
        if collaboration not in (None, 'default', 'plan'):
            raise ValueError('Invalid Codex collaboration mode')
        unavailable_mode = None
        if collaboration is not None:
            try:
                self._require_mode(collaboration)
            except CodexFeatureError:
                if method not in ('thread/resume', 'thread/fork') and not keep_mode:
                    raise
                # A saved mode must not lock a chat out on a Codex without it,
                # nor stop a copy that keeps the original's mode (#596).
                # Choosing a mode (thread/start) still says it isn't available.
                collaboration, unavailable_mode = None, collaboration
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
        if instructions is not None and (not isinstance(instructions, str) or len(instructions) > 8000 or '\x00' in instructions):
            raise ValueError('Chat instructions must be text of at most 8000 characters')
        policy = CodexPolicy.choose(cwd, sandbox, approval_policy, approvals_reviewer, extra_roots)
        if policy.workspace_root:
            cwd = policy.workspace_root
        if passive and (mcp_servers or apps):
            raise ValueError('Side questions cannot enable external tools')
        tools = self._preflight(cwd, mcp_servers, apps)
        if method == 'thread/resume':
            # Resume changes the loaded thread. Inspect persisted turn state
            # first, even after restarting TCP; never overwrite unfinished work.
            self._verify_idle(native_id)
        if (model is not None and method != "thread/resume") or effort is not None:
            models = read_models(self.connection)
            choice = next((choice for choice in models if choice.model == model), None)
            # The catalog hides retired models. A saved chat on one can still
            # open, and Codex's own answer is checked below. Trying the chat's
            # own model in a temporary thread, for a permission or mode change
            # or Stop's recheck, is the same chat staying where it is, so it
            # counts too; otherwise the failed check locks the chat with Stop
            # refused the same way. Only choosing a model needs the catalog (#467).
            # A catalog with nothing in it hasn't retired a model, though: it
            # can't vouch for anything, so a grant recheck still refuses (#184).
            retired = choice is None and (method == 'thread/resume' or (allow_retired and bool(models)))
            if not retired and (choice is None or (effort is not None and effort not in choice.efforts)):
                raise ValueError("Codex model or reasoning effort is no longer available")
        params = {"cwd": cwd, "modelProvider": "openai", "sandbox": policy.sandbox,
                  "approvalPolicy": policy.approval, "config": {
                      "model_provider": "openai", "model_providers": {},
                      "forced_login_method": "chatgpt", "openai_base_url": None,
                      "chatgpt_base_url": CHATGPT_BASE_URL, "approvals_reviewer": policy.reviewer}}
        params['config'].update(policy.config())
        params['config'].update(tools)
        caller = ''
        if getattr(self.connection, 'session_tools', False) and not ephemeral:
            import secrets
            from ..session_tools import server_command, SERVER_NAME
            caller = (self.connection.loaded_caller(native_id) if method == 'thread/resume' else '')
            caller = caller or 'codex:' + secrets.token_hex(24)
            command, args, env = server_command(caller)
            params['config']['mcp_servers'][SERVER_NAME] = {
                'enabled': True, 'command': command, 'args': args, 'env': env}

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
        if method == "thread/fork" and ephemeral:
            # Codex refuses an ephemeral fork of a paginated thread without
            # this ("requires `excludeTurns: true`", 0.163), which every Ask
            # Aside on a new chat is. It only leaves the turns out of the
            # reply: nothing here reads them, and the copy still has them.
            params["excludeTurns"] = True
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
        verify_codex_tools(self.connection, actual_id, mcp_servers + (("ccd_session_mgmt",) if caller else ()) + (("codex_apps",) if apps else ()))
        from .codex_tools import verify_apps
        verify_apps(self.connection, actual_id, apps)
        effective_effort = result.get('reasoningEffort')
        if effective_effort is not None and (not isinstance(effective_effort, str)
                or not effective_effort.strip() or len(effective_effort) > 256
                or any(ord(c) < 32 for c in effective_effort)):
            raise CodexRpcError('Codex reasoning effort could not be verified')
        created = thread.get("createdAt")
        created_ms = created * 1000 if type(created) is int and created >= 0 else 0
        proof = CodexOwnedThread(actual_id, policy.workspace_root or result["cwd"], result["model"], title,
                                 created_ms, self.connection, ephemeral, effective_effort, policy, mcp_servers, collaboration,
                                 saved_effort=effort, saved_model=model, unavailable_mode=unavailable_mode, instructions=instructions, session_tool_caller=caller, apps=apps)
        accept = getattr(self.connection, "_accept_owned_thread", None)
        if callable(accept) and not ephemeral:
            accept(proof)
        return proof

    def start(self, cwd, title, *, ephemeral=False, model=None, effort=None,
              sandbox='read-only', approval_policy='on-request', approvals_reviewer='user', mcp_servers=(), collaboration=None,
              allow_retired=False, instructions=None, extra_roots=(), apps=(), keep_mode=False):
        return self._create("thread/start", cwd, title, ephemeral=ephemeral, model=model, effort=effort,
                            sandbox=sandbox, approval_policy=approval_policy, approvals_reviewer=approvals_reviewer,
                            mcp_servers=mcp_servers, collaboration=collaboration, allow_retired=allow_retired, instructions=instructions, extra_roots=extra_roots, apps=apps,
                            keep_mode=keep_mode)

    def fork(self, source: SessionInfo, title, *, ephemeral=False, model=None, effort=None,
             sandbox='read-only', approval_policy='on-request', approvals_reviewer='user',
             last_turn_id=None, before_turn_id=None, passive=False, mcp_servers=(), instructions=None, extra_roots=(), apps=(),
             collaboration=None, allow_retired=False):
        if (not isinstance(source, SessionInfo) or source.provider != "codex"
                or not isinstance(source.cli_session_id, str)
                or not platform_paths.is_safe_id(source.cli_session_id)):
            raise ValueError("Codex fork requires a valid source chat")
        return self._create("thread/fork", source.cwd, title,
                            native_id=source.cli_session_id, ephemeral=ephemeral, model=model, effort=effort,
                            sandbox=sandbox, approval_policy=approval_policy, approvals_reviewer=approvals_reviewer,
                            last_turn_id=last_turn_id, before_turn_id=before_turn_id, passive=passive,
                            mcp_servers=mcp_servers, instructions=instructions, extra_roots=extra_roots, apps=apps,
                            collaboration=collaboration, allow_retired=allow_retired)

    @staticmethod
    def _saved_options(record):
        if (not isinstance(record, OwnSession) or record.provider != "codex"
                or record.started is not True or not isinstance(record.cli_session_id, str)
                or not platform_paths.is_safe_id(record.cli_session_id)):
            raise ValueError("Only a started TCP-owned Codex chat can be resumed")
        options = record.provider_options
        if (not isinstance(options, dict) or set(options) - {"model", "sandbox", "approval_policy", "effort", "workspace_root", "approvals_reviewer", "mcp_servers", "collaboration", "instructions", "extra_roots", "apps"}
                or options.get("sandbox", "read-only") not in ('read-only', 'workspace-write')
                or options.get("approval_policy", "on-request") not in ('on-request', 'never')):
            raise ValueError("Codex owned chat options are not supported yet")
        from .codex_tools import server_names
        server_names(options.get('mcp_servers', ()))
        roots = options.get('extra_roots', ())
        if (not isinstance(roots, (list, tuple)) or len(roots) > 32 or
                any(platform_paths.codex_workspace_root(p) != p for p in roots)):
            raise ValueError('Saved additional folders could not be verified')
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
                            mcp_servers=options.get('mcp_servers', ()), collaboration=options.get('collaboration'),
                            instructions=options.get('instructions'), extra_roots=options.get('extra_roots', ()), apps=options.get('apps', ()))
