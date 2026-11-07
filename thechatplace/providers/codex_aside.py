"""An ephemeral read-only Codex fork for TCP's Ask Aside dialog (#467)."""
import threading

from .. import platform_paths
from ..aside import NOTHING_FINISHED, AsideEvent
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


#: How far back the turn list is followed for a completed turn.
_TURN_PAGES = 20


def last_finished_turn(connection, native_id):
    """(id of the newest completed turn, whether a newer one isn't), from
    the thread's own turn list (#559), as Rewind's verify_anchor reads it.
    Completed only: a failed or interrupted turn can leave your message
    unanswered, as a running one does. ("", False) for no turns."""
    cursor, seen, unfinished = None, set(), False
    for _ in range(_TURN_PAGES):
        params = {'threadId': native_id, 'sortDirection': 'desc', 'limit': 20,
                  'itemsView': 'notLoaded'}
        if cursor is not None:
            params['cursor'] = cursor
        page = connection.request('thread/turns/list', params)
        turns = page.get('data') if isinstance(page, dict) else None
        if not isinstance(turns, list):
            raise CodexRpcError('Codex turn list unreadable')
        for turn in turns:
            turn_id = turn.get('id') if isinstance(turn, dict) else None
            if not isinstance(turn_id, str) or not platform_paths.is_safe_id(turn_id):
                raise CodexRpcError('Codex turn list unreadable')
            if turn.get('status') == 'completed':
                return turn_id, unfinished
            unfinished = True
        cursor = page.get('nextCursor')
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            break
        seen.add(cursor)
    return '', unfinished


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
            # The copy always ends with the newest completed turn (#559):
            # Codex could otherwise take up an unfinished turn's message in
            # the aside, as Claude did, and a turn that starts between this
            # and the fork is left out too. Asked of the thread's own list,
            # so a chat started elsewhere is cut as well.
            finished, unfinished = last_finished_turn(connection, self.source.cli_session_id)
            if unfinished and not finished:
                self._emit(AsideEvent('failed', NOTHING_FINISHED))
                return
            cut = {'last_turn_id': finished} if finished else {}
            if self._cancel.is_set():
                return
            proof = CodexOwnedThreads(connection).fork(self.source, 'Ask Aside', ephemeral=True,
                sandbox='read-only', approval_policy='never', passive=True, **cut)
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
