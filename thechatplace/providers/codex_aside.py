"""An ephemeral read-only Codex fork for TCP's Ask Aside dialog (#467)."""
import threading

from ..aside import AsideEvent
from .codex_execution import CodexExecutionConnection, CodexTurnExecutor
from .codex_owned import CodexOwnedThreads
from .codex_rpc import CodexRpcError


class AsideConnection(CodexExecutionConnection):
    def _accept_server_request(self, request):
        # The side-question dialog has no Answer workflow. Refuse questions
        # as well as approvals so a clarification cannot wait forever.
        return False

    def accept_aside(self, proof):
        # Permission probes remain non-executable in the normal connection.
        # This separate process only runs a verified, read-only ephemeral fork.
        if (proof.connection is not self or not proof.ephemeral
                or proof.policy.sandbox != 'read-only' or proof.policy.approval != 'never'):
            raise CodexRpcError('Invalid Codex aside configuration')
        with self._runtime_lock:
            self._runtime_threads[proof.native_id] = proof


class CodexAsideRunner:
    def __init__(self, source, question, on_event, *, factory=AsideConnection):
        self.source, self.question, self.on_event = source, question, on_event
        self.factory = factory
        self._cancel = threading.Event()
        self._event_lock = threading.Lock()
        self._terminal = False
        self._connection = None

    def start(self):
        threading.Thread(target=self._run, name='codex-aside', daemon=True).start()
        return self

    def cancel(self):
        self._cancel.set()
        self._emit(AsideEvent('failed', 'Stopped.'))
        connection = self._connection
        if connection is not None:
            threading.Thread(target=connection.close, daemon=True).start()

    def _emit(self, event):
        with self._event_lock:
            if self._terminal:
                return
            if event.kind in ('done', 'failed'):
                self._terminal = True
            self.on_event(event)

    def _run(self):
        connection = None
        try:
            connection = self._connection = self.factory()
            proof = CodexOwnedThreads(connection).fork(self.source, 'Ask Aside', ephemeral=True,
                sandbox='read-only', approval_policy='never', passive=True)
            if self._cancel.is_set():
                return
            connection.accept_aside(proof)
            executor = CodexTurnExecutor(connection)
            executor.start(proof, self.question)
            previous = ''
            while not self._cancel.wait(.05):
                executor.drain()
                state = executor.snapshot(proof)
                answer = '\n\n'.join(text for _identity, text in state.replies)
                if answer != previous:
                    previous = answer
                    self._emit(AsideEvent('text', answer))
                if state.status != 'inProgress':
                    self._emit(AsideEvent('done', answer) if state.status == 'completed'
                        else AsideEvent('failed', 'Codex did not complete this side question.'))
                    return
        except Exception:
            if not self._cancel.is_set():
                self._emit(AsideEvent('failed', 'Could not ask Codex. Check its sign-in, connection and version.'))
        finally:
            if connection is not None:
                connection.close()
            self._connection = None
