"""Permanent deletion uses the provider, with confirmation and honest failures."""
import pytest

wx = pytest.importorskip('wx')
from test_ui_hidden import env, frame, settle  # noqa: F401
from thechatplace.own_store import OwnSession
from thechatplace.sessions import WORKING
from thechatplace.providers.codex_runtime import CodexChatState


@pytest.fixture
def chat(frame, env, monkeypatch):
    settle(frame)
    from types import SimpleNamespace
    from thechatplace.providers.codex import CodexProvider
    monkeypatch.setitem(frame.sessions._providers, 'codex',
                        CodexProvider(object(), runtime=SimpleNamespace(close=lambda: None)))
    record = OwnSession('delete-codex', 'Synthetic Codex chat', str(env['tmp']),
                        provider='codex', started=True)
    frame.store.add(record)
    info = record.to_info()
    monkeypatch.setattr(frame, '_selected_session', lambda: info)
    work = []
    monkeypatch.setattr(frame, '_codex_work', lambda action, done: work.append((action, done)))
    return info, work


def test_cancel_is_default_and_keeps_everything(frame, env, chat, monkeypatch):
    info, work = chat
    asked = []
    def cancel(text, title, style, parent):
        asked.append((text, style))
        return wx.NO
    monkeypatch.setattr(wx, 'MessageBox', cancel)
    frame.on_delete_permanently()
    assert asked and asked[0][1] & wx.NO_DEFAULT
    # Claude's wording, with what Codex deletes (#634).
    text = asked[0][0]
    assert text.startswith(f'Delete "{info.title}" permanently?')
    assert 'conversation' in text and 'subagent chats' in text and "can't be brought back" in text
    assert not work and frame.store.get(info.cli_session_id, provider='codex')


@pytest.mark.parametrize('error', [False, True])
def test_delete_keeps_record_until_codex_confirms(frame, env, chat, monkeypatch, error):
    info, work = chat
    frame._codex_drafts[info.key] = 'Unsent synthetic draft'
    calls = []
    monkeypatch.setattr(frame.sessions, 'owned_action', lambda *args: calls.append(args) or {})
    frame.on_delete_permanently()
    assert frame.store.get(info.cli_session_id, provider='codex')
    assert len(work) == 1
    frame.on_delete_permanently()
    assert len(work) == 1
    action, done = work[0]
    assert action() == 'deleted'
    assert calls[0][0] == 'delete' and calls[0][1].provider == 'codex'
    done('Wait for this Codex chat to finish.' if error else 'deleted', error)
    assert bool(frame.store.get(info.cli_session_id, provider='codex')) == error
    assert (info.key in frame._codex_drafts) == error
    assert env['feedback'][-1] == (f"Couldn't delete {info.title}. Wait for this Codex chat to finish."
                                  if error else f'Deleted {info.title} permanently.')


def _saved_for_later(frame, native_id):
    """The task list, Allow for This Chat rules and review history The Chat
    Place saves for a Codex chat, where its own store keeps them."""
    from thechatplace.provider_storage import CodexPlanStore
    from thechatplace.providers.codex_permissions import CodexPermissionStore, CodexReviewStore
    root = frame.store.path.parent
    stores = (CodexPlanStore(root / 'provider-tasks' / 'codex'),
              CodexPermissionStore(root / 'provider-permissions' / 'codex'),
              CodexReviewStore(root / 'provider-permissions' / 'codex'))
    stores[0].save(native_id, 'turn-1', [])
    stores[1].save(native_id, ())
    stores[2].remember(native_id, 'turn-1', [('review', 'Allowed')])
    return [store._path(native_id) for store in stores]


@pytest.mark.parametrize('outcome', ['deleted', 'gone', 'failed'])
def test_what_was_saved_for_the_chat_goes_with_it(frame, env, chat, monkeypatch, outcome):
    # Deleted, or Codex no longer has it: its task list, rules and reviews
    # go with its entry (#634 review). A failed delete keeps them all.
    info, work = chat
    files = _saved_for_later(frame, info.cli_session_id)
    assert all(path.exists() for path in files)
    frame.on_delete_permanently()
    _action, done = work[0]
    done(None if outcome == 'failed' else outcome, outcome == 'failed')
    kept = outcome == 'failed'
    assert [path.exists() for path in files] == [kept] * 3
    assert bool(frame.store.get(info.cli_session_id, provider='codex')) == kept


@pytest.mark.parametrize('code', ['refused', 'unsupported', 'delete_unconfirmed', 'busy'])
def test_codexs_reason_reaches_the_window(frame, env, chat, monkeypatch, code):
    # Through the real worker path, run at once: what's said is the reason.
    from thechatplace.providers.codex_features import CodexFeatureError
    from thechatplace.ui import codex_chats
    info, _work = chat
    monkeypatch.setattr(frame, '_codex_work', codex_chats.CodexChats._codex_work.__get__(frame))
    monkeypatch.setattr(frame._pool, 'submit', lambda job: job())
    monkeypatch.setattr(codex_chats.wx, 'CallAfter', lambda job, *args: job(*args))
    def refuse(*args):
        raise CodexFeatureError(code)
    monkeypatch.setattr(frame.sessions, 'owned_action', refuse)
    frame.on_delete_permanently()
    assert env['feedback'][-1] == f"Couldn't delete {info.title}. " + CodexFeatureError.REASONS[code]
    assert frame.store.get(info.cli_session_id, provider='codex')
    assert info.key not in frame._codex_deleting


def test_cancelling_keeps_what_was_saved_for_the_chat(frame, env, chat, monkeypatch):
    info, work = chat
    files = _saved_for_later(frame, info.cli_session_id)
    monkeypatch.setattr(wx, 'MessageBox', lambda *a, **k: wx.NO)
    frame.on_delete_permanently()
    assert not work and all(path.exists() for path in files)


def test_a_chat_codex_no_longer_has_is_removed_and_said_to_be_gone(frame, env, chat, monkeypatch):
    # Its reply lost, or the app quit mid-delete: the entry used to be
    # stuck, since every try reopened the chat first (#634).
    from thechatplace.providers.codex_runtime import CodexThreadGone
    info, work = chat
    def gone(*args):
        raise CodexThreadGone()
    monkeypatch.setattr(frame.sessions, 'owned_action', gone)
    frame.on_delete_permanently()
    action, done = work[0]
    assert action() == 'gone'
    done('gone', False)
    assert not frame.store.get(info.cli_session_id, provider='codex')
    assert env['feedback'][-1] == f'{info.title} was already gone.'


def test_any_other_failure_keeps_the_chat(frame, env, chat, monkeypatch):
    # A timeout or lost connection isn't Codex saying it's gone.
    from thechatplace.providers.codex_rpc import CodexRpcError
    info, work = chat
    def lost(*args):
        raise CodexRpcError('Synthetic timeout')
    monkeypatch.setattr(frame.sessions, 'owned_action', lost)
    frame.on_delete_permanently()
    action, done = work[0]
    with pytest.raises(CodexRpcError):
        action()
    done(None, True)
    assert frame.store.get(info.cli_session_id, provider='codex')
    assert env['feedback'][-1] == (f"Couldn't delete {info.title}. Codex didn't confirm it. "
                                  'Check the chat before trying again.')
    # Nothing is left waiting: it can be tried again.
    frame.on_delete_permanently()
    assert len(work) == 2


def test_an_entry_saved_as_never_started_is_removed_after_confirming(frame, env, chat, monkeypatch):
    """A guard for an older or hand-edited entry: no Codex chat this release
    makes is saved unstarted (CodexOwnedThread.record). It has no Codex
    thread, so there's no Codex call and no "couldn't delete"."""
    info, work = chat
    frame.store.update(info.cli_session_id, provider='codex', started=False)
    asked = []
    monkeypatch.setattr(wx, 'MessageBox', lambda text, title, style, parent: asked.append(style) or wx.YES)
    monkeypatch.setattr(frame.sessions, 'owned_action', lambda *a: pytest.fail('nothing to ask Codex'))
    frame.on_delete_permanently()
    assert asked and asked[0] & wx.NO_DEFAULT
    assert not work and not frame.store.get(info.cli_session_id, provider='codex')
    assert env['feedback'][-1] == f'Deleted {info.title} permanently.'


def test_working_chat_is_not_offered_deletion(frame, env, chat, monkeypatch):
    info, work = chat
    frame._codex_states[info.key] = CodexChatState(info.cli_session_id, WORKING)
    monkeypatch.setattr(wx, 'MessageBox', lambda *a, **k: pytest.fail('must not confirm'))
    try:
        frame.on_delete_permanently()
        assert not work and 'running' in env['feedback'][-1]
    finally:
        frame._codex_states.clear()


def test_external_codex_chat_can_only_be_hidden(frame, env, chat, monkeypatch):
    from dataclasses import replace
    info, work = chat
    monkeypatch.setattr(frame, '_selected_session', lambda: replace(info, source='terminal'))
    monkeypatch.setattr(wx, 'MessageBox', lambda *a, **k: pytest.fail('must not confirm'))
    frame.on_delete_permanently()
    assert not work and frame.store.get(info.cli_session_id, provider='codex')


@pytest.mark.parametrize('dropped', [False, True], ids=['native', 'selection-dropped'])
@pytest.mark.parametrize('loaded', [False, True])
def test_completion_preserves_other_selection_or_refocuses_unloaded_chat(
        frame, env, chat, monkeypatch, loaded, dropped):
    info, work = chat
    other = frame._snapshot.sessions[0]
    frame.session_list.Set(['Deleted chat', 'Other chat'])
    frame._list_keys = [info.key, other.key]
    frame.session_list.SetSelection(0)
    monkeypatch.setattr(frame, 'refresh_sessions', lambda *a, **k: None)
    if dropped:
        # What GTK and macOS do (#634 CI): deleting a row above the selected
        # one leaves no selection, where Windows moves it up. Every platform
        # must end with the same row selected.
        delete = frame.session_list.Delete
        def drop(index):
            delete(index)
            frame.session_list.SetSelection(wx.NOT_FOUND)
        monkeypatch.setattr(frame.session_list, 'Delete', drop)
    frame._open = info if loaded else other
    frame.on_delete_permanently()
    if not loaded:
        frame.session_list.SetSelection(1)  # user moves during the RPC
    focus_rows = []
    monkeypatch.setattr(frame.session_list, 'SetFocus',
                        lambda: focus_rows.append(list(frame._list_keys)))
    work[0][1]({}, False)
    assert frame._list_keys == [other.key]
    assert frame.session_list.GetSelection() == 0
    assert frame._open is (None if loaded else other)
    assert focus_rows == ([ [other.key] ] if loaded else [])


def test_send_during_deletion_preserves_reply_box(frame, env, chat):
    info, work = chat
    frame._open = info
    frame.reply_text.SetValue('Draft while deleting')
    frame.on_delete_permanently()
    frame.on_send()
    assert len(work) == 1 and frame.reply_text.GetValue() == 'Draft while deleting'


def test_kept_terminal_chat_has_delete_in_its_menu(frame, env, monkeypatch):
    from thechatplace.sessions import KEPT, SessionInfo
    info = SessionInfo(KEPT, 'terminal:kept-menu', 'Kept terminal chat', '', 'kept-menu',
                       kept_from='terminal')
    monkeypatch.setattr(frame, '_selected_session', lambda: info)
    menu, _actions = frame._session_menu()
    try:
        assert any('Delete Session' in item.GetItemLabel() for item in menu.GetMenuItems())
    finally:
        menu.Destroy()
