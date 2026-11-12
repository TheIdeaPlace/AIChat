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


def test_a_chat_codex_no_longer_has_is_removed_and_said_to_be_gone(frame, env, chat, monkeypatch):
    # Its reply lost, the app quit mid-delete, or never saved by Codex: the
    # entry used to be stuck, since every try reopened the chat first (#634).
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


def test_a_chat_never_sent_to_is_removed_after_confirming(frame, env, chat, monkeypatch):
    # No Codex thread to delete, so no Codex call and no "could not confirm".
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


@pytest.mark.parametrize('loaded', [False, True])
def test_completion_preserves_other_selection_or_refocuses_unloaded_chat(
        frame, env, chat, monkeypatch, loaded):
    info, work = chat
    other = frame._snapshot.sessions[0]
    frame.session_list.Set(['Deleted chat', 'Other chat'])
    frame._list_keys = [info.key, other.key]
    frame.session_list.SetSelection(0)
    monkeypatch.setattr(frame, 'refresh_sessions', lambda *a, **k: None)
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
