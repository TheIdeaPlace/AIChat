"""Worker-owned Codex lifecycle, streaming and queued sends for the controller."""
from dataclasses import dataclass, field, replace
import threading

from .codex_connection import ManagedCodexConnection
from .codex_execution import CodexExecutionConnection, CodexTurnExecutor
from .codex_owned import CodexOwnedThreads
from .codex_policy import CodexPolicy
from .codex_rpc import CodexRpcError, CodexTurnRejected, CodexConnectionFailure
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


@dataclass(frozen=True)
class PreparedCodexPolicy:
    proof: object = field(repr=False)

    def record(self):
        return self.proof.record()


class CodexChatRuntime:
    """No wx calls or persisted state: callers run actions off the UI thread.

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
        self._queues = {}
        self._sending = set()
        self._stopping = set()
        self._reopening = set()
        self._policy_changes = {}
        self._pending_dispatch = {}
        self._problems = {}
        self._stop = threading.Event()
        self._listeners = []

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

    def _remember(self, proof):
        with self._lock:
            if self._stop.is_set():
                raise CodexRpcError('Codex chats are closing')
            self._proofs[proof.native_id] = proof
            self._problems.pop(proof.native_id, None)
        self._notify(proof.native_id)
        return proof.record()

    def create(self, cwd, title, *, model=None, effort=None, sandbox='read-only', approval_policy='on-request'):
        connection, _ = self._binding()
        return self._remember(CodexOwnedThreads(connection).start(cwd, title, model=model, effort=effort,
            sandbox=sandbox, approval_policy=approval_policy))

    def fork(self, source, title, *, model=None, effort=None, sandbox='read-only', approval_policy='on-request'):
        connection, _ = self._binding()
        return self._remember(CodexOwnedThreads(connection).fork(source, title, model=model, effort=effort,
            sandbox=sandbox, approval_policy=approval_policy))

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

    def prepare_policy(self, record, sandbox, approval_policy):
        """Verify an idle chat's new grant, keeping sends blocked until saved.

        The UI persists the returned record on its own thread and then confirms
        it. A failed save must never enable a broader runtime permission (#184).
        """
        if not isinstance(record, OwnSession) or record.provider != 'codex' or record.started is not True:
            raise CodexRpcError('Only owned Codex chats can change permissions')
        if record.provider_options.get('sandbox') == 'workspace-write':
            CodexPolicy('workspace-write', workspace_root=record.provider_options.get('workspace_root', '')).verify_workspace(record.cwd)
        policy = CodexPolicy.choose(record.cwd, sandbox, approval_policy)
        options = dict(record.provider_options)
        options.update(sandbox=sandbox, approval_policy=approval_policy)
        options.pop('workspace_root', None)
        if policy.workspace_root:
            options['workspace_root'] = policy.workspace_root
        proposed = replace(record, cwd=policy.workspace_root or record.cwd, provider_options=options)
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
            connection, _ = self._binding()
            change = PreparedCodexPolicy(CodexOwnedThreads(connection).resume(proposed))
            with self._lock:
                if self._stop.is_set():
                    raise CodexRpcError('Codex chats are closing')
                self._policy_changes[native_id] = change
            return change
        except Exception:
            with self._lock:
                self._reopening.discard(native_id)
                self._problems[native_id] = 'Could not verify changed permissions. Reload this chat before sending.'
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
            pending = self._pending_dispatch.pop(proof.native_id, None)
            unsent = ((pending[1],) if pending is not None else ()) + tuple(self._queues.pop(proof.native_id, ()))
            if pending is not None:
                self._sending.discard(proof.native_id)
            if proof.native_id in self._sending or executor.snapshot(proof).status == 'inProgress':
                self._stopping.add(proof.native_id)
        try:
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
        executor.answer(proof, request, result)
        self._notify(proof.native_id)

    def snapshot(self, native_id, *, unsent=()):
        with self._lock:
            proof, executor = self._proofs.get(native_id), self._executor
            queued = tuple(self._queues.get(native_id, ()))
            problem = self._problems.get(native_id, '')
            sending = native_id in self._sending
        if proof is None or executor is None:
            return CodexChatState(native_id, STATUS_UNAVAILABLE)
        state = executor.snapshot(proof)
        live = proof.connection.owns_runtime(proof) and not problem
        status = (STATUS_UNAVAILABLE if not live or state.status == 'unknown' else
                  NEEDS_YOU if state.requests else WORKING if sending or state.status == 'inProgress' else IDLE)
        return CodexChatState(native_id, status, state.turn_id, state.status,
                              state.replies, state.requests, queued, tuple(unsent), problem, state.file_changes,
                              state.started_at, state.activity, state.notice, state.token_usage, state.context_epoch)

    def _notify(self, native_id, *, unsent=()):
        if not self._stop.is_set():
            state = self.snapshot(native_id)
            if state.problem or state.outcome in ('failed', 'interrupted', 'unknown'):
                with self._lock:
                    pending = self._pending_dispatch.pop(native_id, None)
                    if pending is not None:
                        self._sending.discard(native_id)
                    unsent = (tuple(unsent) + ((pending[1],) if pending is not None else ())
                              + tuple(self._queues.pop(native_id, ())))
                state = self.snapshot(native_id)
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
            for native_id in changed:
                proof = proofs.get(native_id)
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
