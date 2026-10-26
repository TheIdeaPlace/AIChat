"""Worker-owned Codex lifecycle, streaming and queued sends for the controller."""
from dataclasses import dataclass, field, replace
from contextlib import contextmanager
import threading

from .codex_connection import ManagedCodexConnection
from .codex_execution import CodexExecutionConnection, CodexTurnExecutor
from .codex_owned import CodexOwnedThreads
from .codex_policy import CodexPolicy
from .codex_rpc import CodexRpcError, CodexTurnRejected, CodexConnectionFailure, CodexApprovalNotSent, CodexRememberedApprovalError
from .codex_permissions import CodexPermissionStore, CodexReviewStore, candidate, matches
from .codex_tokens import CodexTokenUsage
from ..codex_diagnostics import record_failure, safe_reason
from ..sessions import IDLE, NEEDS_YOU, WORKING, STATUS_UNAVAILABLE
from ..own_store import OwnSession


@dataclass(frozen=True)
class CodexChatState:
    native_id: str
    state: str
    turn_id: str = ''
    outcome: str = ''
    replies: tuple = field(default=(), repr=False)
    requests: tuple = field(default=(), repr=False)
    queued: tuple = field(default=(), repr=False)
    unsent: tuple = field(default=(), repr=False)
    problem: str = ''
    file_changes: tuple = field(default=(), repr=False)
    started_at: float = 0
    activity: str = field(default='', repr=False)
    notice: str = ''
    token_usage: CodexTokenUsage | None = None
    context_epoch: int = 0
    tasks: tuple = field(default=(), repr=False)
    plan_received: bool = False
    permission_notices: tuple = field(default=(), repr=False)
    changed_paths: tuple = field(default=(), repr=False)


@dataclass(frozen=True)
class PreparedCodexPolicy:
    proof: object = field(repr=False)

    def record(self):
        return self.proof.record()


class CodexChatRuntime:
    """No wx calls: callers run actions and plan persistence off the UI thread.

    Only explicit lifecycle actions acquire/reconnect a server. Stream failure
    suspends queues; it cannot resume or replay any prompt automatically.
    """
    def __init__(self, on_update=lambda state: None, *, factory=None, executable=None):
        self._managed = ManagedCodexConnection(executable, factory=factory or (
            lambda exe: CodexExecutionConnection(exe, initialize=False)))
        self._on_update = on_update
        self._lock = threading.Lock()
        self._binding_lock = threading.Lock()
        self._connection = self._executor = None
        self._proofs = {}
        self._new_threads = set()
        self._queues = {}
        self._sending = set()
        self._stopping = set()
        self._flushed_turns = {}
        self._reopening = set()
        self._policy_changes = {}
        self._pending_dispatch = {}
        self._problems = {}
        self._stop = threading.Event()
        self._listeners = []
        from ..provider_storage import CodexPlanStore
        self.plan_store = CodexPlanStore()
        self._saved_plans = {}
        self._plan_lock = threading.Lock()
        self.permission_store = CodexPermissionStore()
        self.review_store = CodexReviewStore()
        self._saved_reviews = {}
        self._permission_lock = threading.RLock()
        self._permission_evaluated = set()
        self._permission_pending = set()
        self._manual_permission_locks = {}
        self._permission_notices = {}

    def _binding(self):
        with self._binding_lock:
            if self._stop.is_set():
                raise CodexRpcError('Codex chats are closing')
            connection = self._managed._get()
            if connection is not self._connection:
                executor = CodexTurnExecutor(connection)
                with self._lock:
                    self._connection, self._executor = connection, executor
                listener = threading.Thread(target=self._listen, args=(connection, executor),
                                            name='codex-events', daemon=True)
                self._listeners.append(listener)
                listener.start()
            return connection, self._executor

    def _remember(self, proof, *, new=False):
        with self._lock:
            if self._stop.is_set():
                raise CodexRpcError('Codex chats are closing')
            self._proofs[proof.native_id] = proof
            if new:
                self._new_threads.add(proof.native_id)
            else:
                self._new_threads.discard(proof.native_id)
            self._problems.pop(proof.native_id, None)
        self._notify(proof.native_id)
        return proof.record()

    def create(self, cwd, title, *, model=None, effort=None, sandbox='read-only', approval_policy='on-request', approvals_reviewer='user'):
        connection, _ = self._binding()
        proof = CodexOwnedThreads(connection).start(cwd, title, model=model, effort=effort,
            sandbox=sandbox, approval_policy=approval_policy, approvals_reviewer=approvals_reviewer)
        return self._remember(proof, new=True)

    def history_route(self, native_id):
        """Where a chat created here reads its history (#285).

        Before the first turn the thread is empty. During the first turn it
        exists only in this runtime's process, so it is read there. After
        that, and whenever this process is gone or had a problem, history is
        read through the reconnecting discovery connection as before, so a
        large history page can't break the stream running turns. That relies
        on Codex saving the transcript before it reports a turn finished."""
        from .codex import EMPTY_HISTORY
        with self._lock:
            proof, executor = self._proofs.get(native_id), self._executor
            new = native_id in self._new_threads
            problem = self._problems.get(native_id)
        if not new or proof is None or executor is None or problem:
            return None
        if not proof.connection.owns_runtime(proof):
            return None
        state = executor.snapshot(proof)
        if not state.turn_id:
            return EMPTY_HISTORY
        if state.status == 'inProgress':
            return proof.connection
        return None

    def fork(self, source, title, *, model=None, effort=None, sandbox='read-only', approval_policy='on-request', approvals_reviewer='user'):
        connection, _ = self._binding()
        return self._remember(CodexOwnedThreads(connection).fork(source, title, model=model, effort=effort,
            sandbox=sandbox, approval_policy=approval_policy, approvals_reviewer=approvals_reviewer))

    def reopen(self, record):
        # Explicit user action only, after the caller has reconciled history.
        with self._lock:
            proof = self._proofs.get(record.cli_session_id)
            if (record.cli_session_id in self._reopening
                    or record.cli_session_id in self._sending or record.cli_session_id in self._stopping
                    or (proof is not None and self._executor is not None
                        and self._executor.snapshot(proof).status == 'inProgress')):
                raise CodexRpcError('This Codex chat already has work in progress')
            self._reopening.add(record.cli_session_id)
            unsent = tuple(self._queues.pop(record.cli_session_id, ()))
        try:
            connection, _ = self._binding()
            return self._remember(CodexOwnedThreads(connection).resume(record))
        finally:
            with self._lock:
                self._reopening.discard(record.cli_session_id)
            if unsent:
                self._notify(record.cli_session_id, unsent=unsent)

    def prepare_policy(self, record, sandbox, approval_policy, approvals_reviewer='user'):
        """Verify an idle chat's new grant, keeping sends blocked until saved.

        The UI persists the returned record on its own thread and then confirms
        it. A failed save must never enable a broader runtime permission (#184).
        """
        if not isinstance(record, OwnSession) or record.provider != 'codex' or record.started is not True:
            raise CodexRpcError('Only owned Codex chats can change permissions')
        if record.provider_options.get('sandbox') == 'workspace-write':
            CodexPolicy('workspace-write', workspace_root=record.provider_options.get('workspace_root', '')).verify_workspace(record.cwd)
        policy = CodexPolicy.choose(record.cwd, sandbox, approval_policy, approvals_reviewer)
        native_id = record.cli_session_id
        with self._lock:
            proof = self._proofs.get(native_id)
            if (self._stop.is_set() or native_id in self._reopening or native_id in self._sending
                    or native_id in self._stopping or native_id in self._pending_dispatch
                    or self._queues.get(native_id)
                    or (proof is not None and self._executor is not None
                        and self._executor.snapshot(proof).status == 'inProgress')):
                raise CodexRpcError('Wait until this Codex chat has no work in progress')
            self._reopening.add(native_id)
        try:
            with self._permission_lock:
                self.permission_store.clear(native_id)
            connection, _ = self._binding()
            lifecycle = CodexOwnedThreads(connection)
            if proof is None or not connection.owns_runtime(proof):
                # A saved mode may no longer be available. An unloaded chat
                # can resume directly with the newly selected mode.
                options = dict(record.provider_options, sandbox=policy.sandbox,
                               approval_policy=policy.approval)
                options.pop('workspace_root', None)
                options.pop('approvals_reviewer', None)
                if policy.workspace_root:
                    options['workspace_root'] = policy.workspace_root
                if policy.reviewer != 'user':
                    options['approvals_reviewer'] = policy.reviewer
                proof = lifecycle.resume(replace(record, cwd=policy.workspace_root or record.cwd,
                                                 provider_options=options))
            # Resuming an already loaded thread returns its old settings and
            # ignores overrides. Verify the requested grant in an empty,
            # ephemeral thread instead; every real turn explicitly pins it.
            # This also works before the owned chat has any saved history.
            checked = lifecycle.start(policy.workspace_root or record.cwd, record.title, ephemeral=True,
                model=proof.model, effort=proof.effort, sandbox=policy.sandbox,
                approval_policy=policy.approval, approvals_reviewer=policy.reviewer)
            if checked.policy != policy:
                raise CodexRpcError('Changed Codex workspace scope could not be verified')
            updated = replace(proof, cwd=checked.cwd, policy=checked.policy)
            connection._accept_owned_thread(updated)
            change = PreparedCodexPolicy(updated)
            with self._lock:
                if self._stop.is_set():
                    raise CodexRpcError('Codex chats are closing')
                self._policy_changes[native_id] = change
            return change
        except Exception as error:
            with self._lock:
                self._reopening.discard(native_id)
                reason = safe_reason(error)
                if isinstance(error, CodexTurnRejected) and error.code == 'sandbox_unavailable':
                    # The chat exists here, unlike New Session, so say how to clear the latch (#257).
                    reason += ' Reload this chat before sending.'
                self._problems[native_id] = reason or 'Could not verify changed permissions. Reload this chat before sending.'
            self._notify(native_id)
            raise

    def finish_policy(self, change, saved):
        if not isinstance(change, PreparedCodexPolicy) or type(saved) is not bool:
            raise ValueError('Invalid Codex permission confirmation')
        native_id = change.proof.native_id
        with self._lock:
            if self._policy_changes.get(native_id) is not change:
                raise CodexRpcError('This Codex permission change is no longer pending')
            del self._policy_changes[native_id]
            self._reopening.discard(native_id)
            if saved and not self._stop.is_set() and change.proof.connection.owns_runtime(change.proof):
                self._proofs[native_id] = change.proof
                self._problems.pop(native_id, None)
            else:
                self._problems[native_id] = 'Changed permissions were not saved or verified. Reload this chat before sending.'
        self._notify(native_id)

    def _context(self, record, *, allow_problem=False):
        if not isinstance(record, OwnSession) or record.provider != 'codex' or record.started is not True:
            raise CodexRpcError('Only owned Codex chats can send')
        with self._lock:
            proof, executor = self._proofs.get(record.cli_session_id), self._executor
            reopening = record.cli_session_id in self._reopening
            problem = self._problems.get(record.cli_session_id)
        if problem and not allow_problem:
            raise CodexRpcError(problem)
        if (record.provider != 'codex' or proof is None or executor is None
                or reopening or not proof.connection.owns_runtime(proof)):
            raise CodexRpcError('Reopen this Codex chat before sending')
        return proof, executor

    def known(self, native_id):
        with self._lock:
            return native_id in self._proofs

    def prepare(self, record):
        """Opening a known chat must not reconnect or replay its lifecycle.

        A failed readiness check is not evidence that its native turn ended.
        Keep the existing owner/stream intact, including uncertain turns (#247).
        """
        if not isinstance(record, OwnSession) or record.provider != 'codex':
            raise CodexRpcError('Only owned Codex chats can be prepared')
        if self.known(record.cli_session_id):
            if not self.ready(record):
                raise CodexRpcError('Codex connection or turn status is uncertain. Automatic resume is disabled.')
            return record
        return self.reopen(record)

    def ready(self, record):
        try:
            self._context(record)
            return True
        except CodexRpcError:
            return False

    def send(self, record, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 32000:
            raise ValueError('Enter a message of at most 32000 characters')
        proof, executor = self._context(record)
        native_id = proof.native_id
        with self._lock:
            if self._stop.is_set():
                raise CodexRpcError('Codex chats are closing')
            if self._problems.get(native_id):
                raise CodexRpcError(self._problems[native_id])
            if native_id in self._stopping:
                raise CodexRpcError('This Codex turn is still stopping')
            if (native_id in self._reopening or self._proofs.get(native_id) is not proof
                    or self._executor is not executor):
                raise CodexRpcError('Reopen this Codex chat before sending')
            active = executor.snapshot(proof).status == 'inProgress' or native_id in self._sending
            if active:
                queue = self._queues.setdefault(native_id, [])
                if len(queue) >= 20 or sum(map(len, queue)) + len(text) > 128000:
                    raise ValueError('This Codex chat has too many queued messages')
                queue.append(text)
                queued = True
            else:
                self._sending.add(native_id)
                queued = False
        if queued:
            self._notify(native_id)
        else:
            self._start(proof, executor, text)
        return queued

    def _start(self, proof, executor, text, *, automatic=False):
        native_id = proof.native_id
        self._notify(native_id)
        rejected = False
        try:
            executor.start(proof, text)
            with self._lock:
                stopping = native_id in self._stopping
            if stopping:
                executor.interrupt(proof)
        except CodexTurnRejected:
            # No prompt was sent, so a fresh verified retry is safe. Do not
            # latch an uncertain-turn problem or make this chat unrecoverable.
            rejected = True
            raise
        except CodexConnectionFailure as error:
            with self._lock:
                self._problems[native_id] = safe_reason(error)
            raise
        except Exception:
            with self._lock:
                self._problems[native_id] = 'Could not confirm this Codex turn. Reload the chat to check its reply.'
            raise CodexRpcError(self._problems[native_id]) from None
        finally:
            with self._lock:
                # Drain rejection recovery and release its reservation together.
                # A concurrent send must not join a queue after it was drained.
                remaining = tuple(self._queues.pop(native_id, ())) if rejected else ()
                self._sending.discard(native_id)
                if executor.snapshot(proof).status != 'inProgress':
                    self._stopping.discard(native_id)
            recovery = (((text,) if automatic else ()) + remaining) if rejected else ()
            self._notify(native_id, unsent=recovery)
            if not rejected:
                self._advance(proof, executor)

    def _advance(self, proof, executor):
        native_id = proof.native_id
        state = executor.snapshot(proof)
        with self._lock:
            if (self._stop.is_set() or native_id in self._sending or native_id in self._stopping
                    or native_id in self._reopening or self._proofs.get(native_id) is not proof
                    or self._executor is not executor
                    or native_id in self._problems
                    or not self._queues.get(native_id) or state.status != 'completed'):
                return
            text = self._queues[native_id].pop(0)
            if not self._queues[native_id]:
                del self._queues[native_id]
            self._sending.add(native_id)
            reservation = object()
            self._pending_dispatch[native_id] = (reservation, text)
        def send_next():
            with self._lock:
                pending = self._pending_dispatch.get(native_id)
                if pending is None or pending[0] is not reservation:
                    return  # Stop cancelled this reservation; do not touch a later send
                del self._pending_dispatch[native_id]
            try:
                self._start(proof, executor, text, automatic=True)
            except CodexTurnRejected:
                pass  # _start recovered this prompt plus its remaining queue FIFO
            except Exception:
                # An automatic queued dispatch has no send-result UI callback
                # to recover its text. Return it for deliberate reconciliation,
                # including uncertain sends, without ever replaying it.
                self._notify(native_id, unsent=(text,))
        self._launch_queued(send_next)

    def _launch_queued(self, target):
        threading.Thread(target=target, name='codex-queued-send', daemon=True).start()

    def stop(self, record):
        # A recorded send problem forbids more sends/answers, but must not
        # prevent interrupting a turn whose identity is already verified.
        proof, executor = self._context(record, allow_problem=True)
        with self._lock:
            if proof.native_id in self._reopening or self._proofs.get(proof.native_id) is not proof:
                raise CodexRpcError('Reopen this Codex chat before stopping')
            repeated = proof.native_id in self._stopping and proof.native_id not in self._sending
            pending = self._pending_dispatch.pop(proof.native_id, None)
            unsent = ((pending[1],) if pending is not None else ()) + tuple(self._queues.pop(proof.native_id, ()))
            if pending is not None:
                self._sending.discard(proof.native_id)
            if proof.native_id in self._sending or executor.snapshot(proof).status == 'inProgress':
                self._stopping.add(proof.native_id)
        try:
            if repeated:
                executor.reconcile_stop(proof)
                with self._lock:
                    self._stopping.discard(proof.native_id)
            else:
                executor.interrupt(proof)
        finally:
            if not proof.connection.owns_runtime(proof):
                with self._lock:
                    self._stopping.discard(proof.native_id)
            # Queue recovery does not depend on the interrupt acknowledgement.
            self._notify(proof.native_id, unsent=unsent)
        return unsent

    def answer(self, record, request, result):
        proof, executor = self._context(record)
        with self._permission_lock:
            key = (proof.native_id, request.turn_id, request.request_id)
            self._permission_evaluated.add(key)
            self._permission_pending.discard(key)
        executor.answer(proof, request, result)
        self._notify(proof.native_id)

    def _permission_record(self, record):
        if (not isinstance(record, OwnSession)
                or record.provider != 'codex' or record.started is not True):
            raise CodexRpcError('Remembered Codex approvals require a chat started in The Chat Place')

    def chat_rule_candidate(self, record, request):
        self._permission_record(record)
        proof, executor = self._context(record)
        if not any(r is request for r in executor.waiting(proof)):
            return None
        return candidate(request, proof.cwd)

    def chat_rules(self, record):
        self._permission_record(record)
        with self._permission_lock:
            return self.permission_store.load(record.cli_session_id)

    def remove_chat_rule(self, record, rule_id):
        self._permission_record(record)
        with self._permission_lock:
            if rule_id is None:
                self.permission_store.clear(record.cli_session_id)
                return ()
            rules = self.permission_store.load(record.cli_session_id)
            kept = tuple(r for r in rules if r.rule_id != rule_id)
            if len(kept) == len(rules):
                raise ValueError('That approval is no longer kept')
            self.permission_store.save(record.cli_session_id, kept)
            return kept

    def allow_for_chat(self, record, request):
        self._permission_record(record)
        with self._permission_lock:
            grant_lock = self._manual_permission_locks.setdefault(record.cli_session_id, threading.Lock())
        # Serialize explicit grants for this chat without holding the dispatch
        # lock during sign-in. A failed grant cannot roll back a later one.
        with grant_lock:
            return self._allow_for_chat(record, request)

    def _allow_for_chat(self, record, request):
        with self._permission_lock:
            rule = self.chat_rule_candidate(record, request)
            if rule is None:
                raise ValueError('This request cannot be remembered; open Answer again')
            proof, executor = self._context(record)
            previous = self.permission_store.load(proof.native_id)
            existing = next((r for r in previous if (r.prefix, r.folder, r.shell) == (rule.prefix, rule.folder, rule.shell)), None)
            added = existing is None
            if added:
                self.permission_store.save(proof.native_id, (*previous, rule))
            else:
                rule = existing
            key = (proof.native_id, request.turn_id, request.request_id)
            self._permission_evaluated.add(key)
            self._permission_pending.discard(key)
        try:
            executor.answer(proof, request, request.approval_answer('accept'),
                            dispatch_guard=lambda: self._approval_dispatch(proof, executor, request, rule))
        except CodexApprovalNotSent as error:
            try:
                if added:
                    with self._permission_lock:
                        current = self.permission_store.load(proof.native_id)
                        if rule in current:
                            self.permission_store.save(proof.native_id, tuple(r for r in current if r != rule))
            except Exception:
                with self._lock:
                    self._problems[proof.native_id] = CodexRememberedApprovalError.REASONS['cleanup_failed']
                self._notify(proof.native_id)
                raise CodexRememberedApprovalError('cleanup_failed') from error
            raise CodexRememberedApprovalError('not_sent') from error
        except Exception as error:
            with self._permission_lock:
                try:
                    kept = rule in self.permission_store.load(proof.native_id)
                except (OSError, ValueError, UnicodeError):
                    kept = True  # A failed read is not evidence of removal.
            problem = CodexRememberedApprovalError('kept_uncertain' if kept else 'removed_uncertain')
            with self._lock:
                self._problems[proof.native_id] = str(problem)
            self._notify(proof.native_id)
            raise problem from error
        # The explicit grant also applies to siblings already waiting now. A
        # later storage recovery or unrelated change must never reconsider one.
        with self._permission_lock:
            for sibling in executor.waiting(proof):
                if (sibling is not request and matches(rule, sibling, proof.cwd)
                        and (proof.native_id, sibling.turn_id, sibling.request_id) not in self._permission_pending):
                    self._permission_evaluated.discard((proof.native_id, sibling.turn_id, sibling.request_id))
        self._auto_answer(proof, executor)
        self._notify(proof.native_id)
        return rule

    @contextmanager
    def _approval_dispatch(self, proof, executor, request, rule, *, automatic=False):
        with self._permission_lock:
            key = (proof.native_id, request.turn_id, request.request_id)
            with self._lock:
                available = (not self._stop.is_set() and not self._problems.get(proof.native_id)
                    and proof.native_id not in self._reopening and proof.native_id not in self._stopping
                    and self._proofs.get(proof.native_id) is proof and self._executor is executor)
            if (not available or not proof.connection.owns_runtime(proof)
                    or not any(r is request for r in executor.waiting(proof))
                    or automatic and key not in self._permission_pending):
                raise CodexApprovalNotSent('Codex request is no longer eligible')
            try:
                kept = rule in self.permission_store.load(proof.native_id)
            except (ValueError, OSError, UnicodeError):
                kept = False
            if not kept:
                raise CodexApprovalNotSent('The remembered approval was removed or could not be read')
            yield  # Removal and the final wire enqueue share this short lock.

    def _launch_approval(self, target):
        threading.Thread(target=target, name='codex-remembered-approval', daemon=True).start()

    def _auto_answer(self, proof, executor):
        with self._permission_lock:
            pending = executor.waiting(proof)
            with self._lock:
                if (self._stop.is_set() or self._problems.get(proof.native_id)
                        or proof.native_id in self._reopening or proof.native_id in self._stopping
                        or executor is not self._executor or self._proofs.get(proof.native_id) is not proof):
                    return False
                # A listener from a replaced binding cannot erase the current
                # executor's already-checked requests (#338).
                live_turns = {r.turn_id for r in pending}
                self._permission_evaluated = {key for key in self._permission_evaluated
                                            if key[0] != proof.native_id or key[1] in live_turns}
                fresh = tuple(r for r in pending if (proof.native_id, r.turn_id, r.request_id) not in self._permission_evaluated)
                if not fresh:
                    return False
                keys = {(proof.native_id, r.turn_id, r.request_id) for r in fresh}
                self._permission_evaluated.update(keys)  # Even a read failure is a final evaluation.
                self._permission_pending.update(keys)
        self._launch_approval(lambda: self._answer_remembered(proof, executor, fresh))
        return True

    def _answer_remembered(self, proof, executor, pending):
        try:
            self._process_remembered(proof, executor, pending)
        except Exception as error:
            record_failure(error)
        finally:
            # Include selection and the first notification: neither can leave
            # a waiting request hidden if it fails unexpectedly (#338).
            with self._permission_lock:
                self._permission_pending.difference_update(
                    (proof.native_id, r.turn_id, r.request_id) for r in pending)
            try:
                self._notify(proof.native_id)
            except Exception as error:
                record_failure(error)

    def _process_remembered(self, proof, executor, pending):
        with self._permission_lock:
            try:
                rules = self.permission_store.load(proof.native_id)
            except (ValueError, OSError, UnicodeError):
                rules = ()
            selected = {}
            for request in pending:
                key = (proof.native_id, request.turn_id, request.request_id)
                rule = next((r for r in rules if matches(r, request, proof.cwd)), None)
                if rule is not None and key in self._permission_pending:
                    selected[key] = rule
                else:
                    self._permission_pending.discard(key)
        # Unmatched requests are available to Answer before any slow sign-in
        # check for a matching sibling begins.
        self._notify(proof.native_id)
        for request in pending:
            key = (proof.native_id, request.turn_id, request.request_id)
            try:
                rule = selected.get(key)
                if rule is None:
                    continue
                try:
                    executor.answer(proof, request, request.approval_answer('accept'), dispatch_guard=lambda:
                                    self._approval_dispatch(proof, executor, request, rule, automatic=True))
                except CodexApprovalNotSent:
                    continue
                except Exception:
                    with self._lock:
                        self._problems[proof.native_id] = 'Could not confirm an automatic approval. Reload this chat; the answer will not be resent.'
                    continue
                with self._lock:
                    notices = self._permission_notices.setdefault(proof.native_id, [])
                    notices.append((request.turn_id, request.request_id,
                                    'Allowed ' + ' '.join(rule.prefix) + ', as you said for this chat.'))
                    del notices[:-128]
            except (ValueError, OSError, UnicodeError):
                pass  # Never infer approval from unreadable state, or retry later.
            finally:
                with self._permission_lock:
                    self._permission_pending.discard(key)
                self._notify(proof.native_id)

    def snapshot(self, native_id, *, unsent=()):
        with self._lock:
            proof, executor = self._proofs.get(native_id), self._executor
            queued = tuple(self._queues.get(native_id, ()))
            problem = self._problems.get(native_id, '')
            sending = native_id in self._sending
            permission_notices = tuple(self._permission_notices.get(native_id, ()))
        if proof is None or executor is None:
            return CodexChatState(native_id, STATUS_UNAVAILABLE)
        state = executor.snapshot(proof)
        permission_notices += tuple((state.turn_id, identity, text)
                                    for identity, text in state.review_notices)
        with self._permission_lock:
            requests = tuple(r for r in state.requests if
                (native_id, r.turn_id, r.request_id) not in self._permission_pending)
        live = proof.connection.owns_runtime(proof) and not problem
        status = (STATUS_UNAVAILABLE if not live or state.status == 'unknown' else
                  NEEDS_YOU if requests else WORKING if sending or state.status == 'inProgress' else IDLE)
        return CodexChatState(native_id, status, state.turn_id, state.status,
                              state.replies, requests, queued, tuple(unsent), problem, state.file_changes,
                              state.started_at, state.activity, state.notice, state.token_usage, state.context_epoch, state.tasks, state.plan_received,
                              permission_notices, getattr(state, 'changed_paths', ()))

    def _notify(self, native_id, *, unsent=()):
        if not self._stop.is_set():
            state = self.snapshot(native_id)
            with self._lock:
                terminal = (state.turn_id, state.outcome)
                proof = self._proofs.get(native_id)
                current = self._executor.snapshot(proof) if proof is not None and self._executor is not None else None
                same_turn = current is not None and (current.turn_id, current.status) == terminal
                fresh_terminal = (same_turn and native_id not in self._sending
                    and state.outcome in ('failed', 'interrupted', 'unknown')
                    and self._flushed_turns.get(native_id) != terminal)
                if (state.problem and self._problems.get(native_id) == state.problem) or fresh_terminal:
                    self._flushed_turns[native_id] = terminal
                    pending = self._pending_dispatch.pop(native_id, None)
                    if pending is not None:
                        self._sending.discard(native_id)
                    unsent = (tuple(unsent) + ((pending[1],) if pending is not None else ())
                              + tuple(self._queues.pop(native_id, ())))
            with self._plan_lock:
                state = self.snapshot(native_id)
                reviews = tuple(row for row in state.permission_notices if row[1].startswith('auto-review:'))
                if reviews and self._saved_reviews.get(native_id) != reviews:
                    try:
                        self.review_store.remember(native_id, state.turn_id, [(row[1], row[2]) for row in reviews])
                        self._saved_reviews[native_id] = reviews
                    except (OSError, ValueError):
                        pass  # Never interrupt execution to save display history.
                if state.plan_received:
                    signature = (state.turn_id, tuple((t.task_id, t.subject, t.status) for t in state.tasks))
                    if self._saved_plans.get(native_id) != signature:
                        try:
                            self.plan_store.save(native_id, state.turn_id, state.tasks)
                            self._saved_plans[native_id] = signature
                        except (OSError, ValueError):
                            # Progress persistence must never cancel a running turn.
                            pass
            if unsent:
                state = self.snapshot(native_id, unsent=unsent)
            try:
                self._on_update(state)
            except Exception:
                pass  # a closing UI cannot change execution or replay a prompt

    def _listen(self, connection, executor):
        while not self._stop.wait(.05):
            try:
                changed = executor.drain()
            except Exception as error:
                record_failure(error)
                with self._lock:
                    affected = [p for p in self._proofs.values() if p.connection is connection]
                    for proof in affected:
                        self._stopping.discard(proof.native_id)
                        self._problems[proof.native_id] = safe_reason(error) or 'Codex connection interrupted. Reload the chat to check its reply.'
                for proof in affected:
                    self._notify(proof.native_id)
                return
            with self._lock:
                proofs = {p.native_id: p for p in self._proofs.values() if p.connection is connection}
            for native_id, proof in proofs.items():
                auto_answered = self._auto_answer(proof, executor)
                if native_id not in changed and not auto_answered:
                    continue
                if proof is not None:
                    if executor.snapshot(proof).status != 'inProgress':
                        with self._lock:
                            if native_id not in self._sending:
                                self._stopping.discard(native_id)
                    self._notify(native_id)
                    self._advance(proof, executor)

    def close(self):
        self._stop.set()
        with self._lock:
            proofs, executor = tuple(self._proofs.values()), self._executor
            self._queues.clear()
            self._pending_dispatch.clear()
            self._policy_changes.clear()
        if executor is not None:
            for proof in proofs:
                try:
                    executor.interrupt(proof)
                except Exception:
                    pass
        self._managed.close()
        for listener in self._listeners:
            listener.join(timeout=1)
