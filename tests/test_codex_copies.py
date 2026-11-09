"""Codex copies keep the original's settings, a chat on Codex's default
follows it, and Change Permission Mode reaches Plan (#596, decisions 3, 17
and 19). Windowless: the runtime against a fake app-server."""
from dataclasses import replace

import pytest

from thechatplace.own_store import OwnSession
from thechatplace.providers.codex_events import CodexEvent
from thechatplace.codex_diagnostics import safe_reason
from thechatplace.providers.codex_features import CodexFeatureError
from thechatplace.providers.codex_rpc import CodexRpcError
from test_codex_runtime import policy_runtime, wait

PLAN_PRESETS = {'data': [{'name': 'Plan', 'mode': 'plan'}, {'name': 'Default', 'mode': 'default'}]}


def _models(*names, default=None):
    return {'data': [dict(id=name, model=name, displayName=name, hidden=False,
                          isDefault=name == (default or names[0]), defaultReasoningEffort='medium',
                          supportedReasoningEfforts=[{'reasoningEffort': e} for e in ('low', 'medium', 'high')])
                     for name in names], 'nextCursor': None}


def _service(tmp_path, *, model=None, config=None, catalog=('gpt-default', 'gpt-other')):
    """A runtime whose fake Codex answers with what it was asked for, knows
    Plan, lists ``catalog`` and reads ``config`` as the user's settings."""
    settings = {} if config is None else config
    service, connection, record = policy_runtime(tmp_path, model=model)
    original = connection.request

    def request(method, params):
        if method == 'collaborationMode/list':
            return PLAN_PRESETS
        if method == 'model/list':
            return _models(*catalog)
        if method == 'thread/turns/list':
            return {'data': [{'id': 'native-turn', 'status': 'completed'}]}
        answer = original(method, params)
        if method == 'config/read':
            answer = {**answer, 'config': dict(settings)}
        return answer
    connection.request = request
    return service, connection, record, settings


def _fork_params(connection):
    return [p for m, p in connection.calls if m in ('thread/fork', 'thread/start') and not p.get('ephemeral')][-1]


def _set_up_original(service, record, tmp_path):
    """An original with a writable grant, a model and effort, instructions,
    a tool and Plan mode: everything a copy should keep."""
    change = service.prepare_policy(record, 'workspace-write', 'on-request', 'auto_review')
    record = change.record()
    service.finish_policy(change, True)
    change = service.prepare_settings(record, 'gpt-other', 'high')
    record = change.record()
    service.finish_policy(change, True)
    change = service.prepare_chat_options(record, 'Be brief.', ())
    record = change.record()
    service.finish_policy(change, True)
    change = service.prepare_mode(record, 'plan')
    record = change.record()
    service.finish_policy(change, True)
    return record


KEPT = ('sandbox', 'approval_policy', 'approvals_reviewer', 'workspace_root', 'model', 'effort',
        'instructions', 'collaboration')


@pytest.mark.parametrize('before', [False, True], ids=['branch', 'rewind'])
def test_branch_and_rewind_copies_keep_the_originals_settings(tmp_path, before):
    service, connection, record, _ = _service(tmp_path)
    try:
        record = _set_up_original(service, record, tmp_path)
        assert record.provider_options['collaboration'] == 'plan'
        copy = service.fork_at(record.to_info(), 'Copy', 'native-turn', before)
        assert copy.cli_session_id != record.cli_session_id
        for key in KEPT:
            assert copy.provider_options.get(key) == record.provider_options.get(key), key
        params = _fork_params(connection)
        assert params['sandbox'] == 'workspace-write' and params['model'] == 'gpt-other'
        assert params['config']['model_reasoning_effort'] == 'high'
        assert params['config']['approvals_reviewer'] == 'auto_review'
        assert params['beforeTurnId' if before else 'lastTurnId'] == 'native-turn'
        # The copy's turns run with what it kept.
        service.send(copy, 'Go on')
        turn = [p for m, p in connection.calls if m == 'turn/start'][-1]
        assert turn['threadId'] == copy.cli_session_id and turn['model'] == 'gpt-other'
        assert turn['sandboxPolicy']['type'] == 'workspaceWrite' and turn['collaborationMode']['mode'] == 'plan'
        assert turn['additionalContext']['tcp_chat_instructions']['value'] == 'Be brief.'
    finally:
        service.close()


def test_a_copy_starts_with_no_remembered_approvals(tmp_path):
    # Allow for This Chat is kept by chat: the copy is given its grant, not
    # the original's remembered answers.
    service, connection, record, _ = _service(tmp_path)
    try:
        record = _set_up_original(service, record, tmp_path)
        service.permission_store.load = lambda native: (
            ['kept'] if native == record.cli_session_id else [])
        copy = service.fork_at(record.to_info(), 'Copy', 'native-turn')
        assert service.permission_store.load(copy.cli_session_id) == []
    finally:
        service.close()


def test_a_chat_not_loaded_here_is_copied_from_its_saved_record(tmp_path):
    service, connection, record, _ = _service(tmp_path)
    try:
        record = _set_up_original(service, record, tmp_path)
        with service._lock:
            service._proofs.pop(record.cli_session_id)
        saved = OwnSession(record.cli_session_id, record.title, record.cwd, provider='codex',
                           started=True, provider_options=dict(record.provider_options))
        copy = service.fork_at(record.to_info(), 'Copy', 'native-turn', own=saved)
        for key in KEPT:
            assert copy.provider_options.get(key) == record.provider_options.get(key), key
        # Without the record there is nothing to keep: Codex's defaults, as before.
        bare = service.fork_at(record.to_info(), 'Bare', 'native-turn')
        assert bare.provider_options['sandbox'] == 'read-only' and 'model' not in bare.provider_options
    finally:
        service.close()


def test_a_tools_copy_differs_only_in_its_tools(tmp_path):
    service, connection, record, _ = _service(tmp_path)
    original = connection.request

    def request(method, params):
        answer = original(method, params)
        if method == 'config/read':
            answer = {**answer, 'config': {'mcp_servers': {'docs': {}}}}
        if method == 'mcpServerStatus/list' and params.get('threadId') != record.cli_session_id:
            return {'data': [{'name': 'docs', 'tools': {}, 'runtimeStatus': 'connected'}], 'nextCursor': None}
        return answer
    try:
        record = _set_up_original(service, record, tmp_path)
        service.send(record, 'A turn, so the copy is a fork')
        connection.events.append(CodexEvent('turn_completed', record.cli_session_id, 'turn-1', status='completed'))
        wait(lambda: service.snapshot(record.cli_session_id).outcome == 'completed')
        connection.request = request
        copy = service.copy_with_tools(record.to_info(), 'Tools copy', ['docs'])
        assert copy.provider_options['mcp_servers'] == ['docs']
        assert 'mcp_servers' not in record.provider_options
        for key in KEPT:
            assert copy.provider_options.get(key) == record.provider_options.get(key), key
        assert [m for m, p in connection.calls if m in ('thread/fork', 'thread/start')
                and not p.get('ephemeral')][-1] == 'thread/fork'
    finally:
        service.close()


def test_a_tools_copy_of_an_empty_chat_keeps_its_settings_too(tmp_path):
    service, connection, record, _ = _service(tmp_path)
    try:
        record = _set_up_original(service, record, tmp_path)
        copy = service.copy_with_tools(record.to_info(), 'Tools copy', [])
        assert _fork_params(connection)['ephemeral'] is False
        assert [m for m, p in connection.calls if m in ('thread/fork', 'thread/start')
                and not p.get('ephemeral')][-1] == 'thread/start'
        for key in KEPT:
            assert copy.provider_options.get(key) == record.provider_options.get(key), key
    finally:
        service.close()


def test_a_copy_keeps_a_model_codex_has_retired_since(tmp_path):
    service, connection, record, _ = _service(tmp_path, model='gpt-default')
    original = connection.request
    try:
        # The catalog no longer lists the original's model.
        connection.request = lambda method, params: (
            _models('gpt-now') if method == 'model/list' else original(method, params))
        copy = service.fork_at(record.to_info(), 'Copy', 'native-turn')
        assert copy.provider_options['model'] == 'gpt-default'
    finally:
        service.close()


def test_a_copy_isnt_made_when_the_folder_isnt_the_one_allowed(tmp_path):
    service, connection, record, _ = _service(tmp_path)
    try:
        record = _set_up_original(service, record, tmp_path)
        moved = OwnSession(record.cli_session_id, record.title, record.cwd, provider='codex', started=True,
                           provider_options=dict(record.provider_options, workspace_root=str(tmp_path / 'elsewhere')))
        with service._lock:
            service._proofs.pop(record.cli_session_id)
        before = len(connection.calls)
        with pytest.raises(CodexFeatureError) as refused:
            service.fork_at(record.to_info(), 'Copy', 'native-turn', own=moved)
        # Said in words, not "Codex could not create the branch."
        assert safe_reason(refused.value).startswith("This chat's folder, or one of its additional folders, "
                                                     "isn't the one it was allowed to edit any more")
        assert not any(m == 'thread/fork' for m, _ in connection.calls[before:])
    finally:
        service.close()


def test_a_copy_isnt_made_when_an_extra_folder_has_moved(tmp_path, monkeypatch):
    # Loaded or not, a saved additional folder that now resolves somewhere
    # else must not become writable in the copy.
    from thechatplace import platform_paths
    extra = tmp_path / 'shared'
    extra.mkdir()
    service, connection, record, _ = _service(tmp_path)
    try:
        change = service.prepare_policy(record, 'workspace-write', 'on-request')
        record = change.record()
        service.finish_policy(change, True)
        change = service.prepare_chat_options(record, None, [str(extra.resolve())])
        record = change.record()
        service.finish_policy(change, True)
        assert record.provider_options['extra_roots'] == [str(extra.resolve())]
        resolve = platform_paths.codex_workspace_root
        monkeypatch.setattr(platform_paths, 'codex_workspace_root', lambda p: (
            str(tmp_path / 'elsewhere') if p == str(extra.resolve()) else resolve(p)))
        before = len(connection.calls)
        with pytest.raises(CodexFeatureError) as refused:
            service.fork_at(record.to_info(), 'Copy', 'native-turn', own=record)
        assert refused.value.code == 'copy_folder_moved'
        assert not any(m == 'thread/fork' for m, _ in connection.calls[before:])
    finally:
        service.close()


def test_a_grant_that_failed_to_save_never_reaches_a_copy(tmp_path):
    # A chat not loaded here resumes with the new grant before it's saved;
    # if the save fails, the loaded thread holds a grant only Stop's recheck
    # may clear (#184). A copy then must not take it (review of #609).
    service, connection, record, _ = _service(tmp_path)
    try:
        with service._lock:
            service._proofs.pop(record.cli_session_id)
        change = service.prepare_policy(record, 'workspace-write', 'never')
        service.finish_policy(change, False)
        assert record.provider_options['sandbox'] == 'read-only'
        before = len(connection.calls)
        for copy in (lambda: service.fork_at(record.to_info(), 'Copy', 'native-turn', own=record),
                     lambda: service.copy_with_tools(record.to_info(), 'Tools copy', [], own=record)):
            with pytest.raises(CodexFeatureError) as refused:
                copy()
            assert refused.value.code == 'copy_unconfirmed'
            assert 'Press Stop' in safe_reason(refused.value)
        assert not any(m in ('thread/fork', 'thread/start') and not p.get('ephemeral')
                       for m, p in connection.calls[before:])
    finally:
        service.close()


def test_a_pending_change_or_a_saved_record_that_disagrees_makes_no_copy(tmp_path):
    service, connection, record, _ = _service(tmp_path)
    try:
        change = service.prepare_policy(record, 'workspace-write', 'on-request')
        with pytest.raises(CodexFeatureError):
            service.fork_at(record.to_info(), 'Copy', 'native-turn', own=record)
        service.finish_policy(change, True)
        # The store still has the old grant: loaded and saved disagree.
        with pytest.raises(CodexFeatureError) as refused:
            service.fork_at(record.to_info(), 'Copy', 'native-turn', own=record)
        assert refused.value.code == 'copy_unconfirmed'
        copy = service.fork_at(record.to_info(), 'Copy', 'native-turn', own=change.record())
        assert copy.provider_options['sandbox'] == 'workspace-write'
    finally:
        service.close()


def test_an_empty_tools_copy_keeps_a_plan_this_codex_lacks(tmp_path):
    service, connection, record, _ = _service(tmp_path)
    try:
        change = service.prepare_mode(record, 'plan')
        record = change.record()
        service.finish_policy(change, True)
        original = connection.request
        connection.request = lambda method, params: (
            {'data': [{'name': 'Default', 'mode': 'default'}]} if method == 'collaborationMode/list'
            else original(method, params))
        copy = service.copy_with_tools(record.to_info(), 'Tools copy', [], own=record)
        assert copy.provider_options['collaboration'] == 'plan'
        assert service._proofs[copy.cli_session_id].unavailable_mode == 'plan'
    finally:
        service.close()


# -- decision 19: Codex's default is followed -----------------------------------------


def test_a_chat_on_codexs_default_saves_no_model_and_follows_a_new_default(tmp_path):
    service, connection, record, settings = _service(tmp_path)
    try:
        assert 'model' not in record.provider_options and 'effort' not in record.provider_options
        service.send(record, 'First')
        assert [p for m, p in connection.calls if m == 'turn/start'][-1]['model'] == 'gpt-default'
        connection.events.append(CodexEvent('turn_completed', record.cli_session_id, 'turn-1', status='completed'))
        wait(lambda: service.snapshot(record.cli_session_id).outcome == 'completed')
        # The user's Codex settings now name another model, with an effort.
        settings.update(model='gpt-other', model_reasoning_effort='high')
        service.send(record, 'Second')
        turn = [p for m, p in connection.calls if m == 'turn/start'][-1]
        assert (turn['model'], turn['effort']) == ('gpt-other', 'high')
        assert service.snapshot(record.cli_session_id).model == 'gpt-other'
        # Still nothing saved: it goes on following.
        assert 'model' not in service._proofs[record.cli_session_id].record().provider_options
    finally:
        service.close()


def test_a_default_named_only_by_the_catalog_is_followed_with_its_own_effort(tmp_path):
    service, connection, record, _ = _service(tmp_path)
    original = connection.request
    try:
        connection.request = lambda method, params: (
            _models('gpt-default', 'gpt-next', default='gpt-next') if method == 'model/list'
            else original(method, params))
        service.send(record, 'First')
        turn = [p for m, p in connection.calls if m == 'turn/start'][-1]
        assert (turn['model'], turn['effort']) == ('gpt-next', 'medium')
    finally:
        service.close()


def test_stop_while_the_default_is_looked_up_still_stops_the_turn(tmp_path):
    # Stop pressed while a turn is about to start, during the lookup, must
    # neither be refused nor lost (review of #609).
    import threading
    service, connection, record, settings = _service(tmp_path)
    original = connection.request
    entered, release = threading.Event(), threading.Event()

    def request(method, params):
        if method == 'config/read' and params.get('cwd') and 'includeLayers' not in params:
            entered.set()
            assert release.wait(5)
        return original(method, params)
    try:
        settings.update(model='gpt-other')
        connection.request = request
        sender = threading.Thread(target=service.send, args=(record, 'Go'))
        sender.start()
        assert entered.wait(5)
        service.stop(record)
        release.set()
        sender.join(5)
        assert any(m == 'turn/interrupt' for m, _ in connection.calls)
        # Stop came first, so the turn went on the model it had.
        assert [p for m, p in connection.calls if m == 'turn/start'][-1]['model'] == 'gpt-default'
    finally:
        release.set()
        service.close()


def test_a_chosen_model_is_kept_when_codexs_default_changes(tmp_path):
    service, connection, record, settings = _service(tmp_path, model='gpt-default')
    try:
        assert record.provider_options['model'] == 'gpt-default'
        settings.update(model='gpt-other')
        service.send(record, 'First')
        assert [p for m, p in connection.calls if m == 'turn/start'][-1]['model'] == 'gpt-default'
    finally:
        service.close()


def test_a_default_that_cant_be_read_doesnt_stop_a_turn(tmp_path):
    service, connection, record, _ = _service(tmp_path)
    original = connection.request

    def request(method, params):
        if method == 'model/list':
            raise CodexRpcError('catalog unavailable')
        return original(method, params)
    try:
        connection.request = request
        service.send(record, 'First')
        assert [p for m, p in connection.calls if m == 'turn/start'][-1]['model'] == 'gpt-default'
    finally:
        service.close()


def test_changing_permissions_keeps_a_chat_on_codexs_default(tmp_path):
    service, connection, record, settings = _service(tmp_path)
    try:
        settings.update(model='gpt-other')
        change = service.prepare_policy(record, 'workspace-write', 'on-request')
        assert 'model' not in change.record().provider_options
        checked = [p for m, p in connection.calls if m == 'thread/start' and p.get('ephemeral')][-1]
        assert 'model' not in checked
        service.finish_policy(change, True)
        # Choosing Codex's default in Change Model saves no model either.
        change = service.prepare_settings(change.record(), 'gpt-other', None)
        assert change.record().provider_options['model'] == 'gpt-other'
        service.finish_policy(change, True)
        change = service.prepare_settings(change.record(), None, None)
        assert 'model' not in change.record().provider_options
    finally:
        service.close()


def test_a_chat_saved_on_a_model_before_keeps_it_when_reopened(tmp_path):
    # Chats saved before 0.5 always named the model: no migration, they keep it.
    service, connection, record, settings = _service(tmp_path)
    try:
        saved = replace(record, provider_options=dict(record.provider_options, model='gpt-default'))
        with service._lock:
            service._proofs.pop(record.cli_session_id)
        settings.update(model='gpt-other')
        reopened = service.reopen(saved)
        assert reopened.provider_options['model'] == 'gpt-default'
    finally:
        service.close()


# -- decision 17: Plan from Change Permission Mode -------------------------------------


def test_leaving_plan_for_another_grant_is_one_permission_change(tmp_path):
    service, connection, record, _ = _service(tmp_path)
    cleared = []
    try:
        change = service.prepare_mode(record, 'plan')
        record = change.record()
        service.finish_policy(change, True)
        service.permission_store.clear = cleared.append
        change = service.prepare_policy(record, 'workspace-write', 'on-request', 'user', collaboration='default')
        assert change.subject == 'permissions' and cleared == [record.cli_session_id]
        proposed = change.record().provider_options
        assert proposed['sandbox'] == 'workspace-write' and proposed['collaboration'] == 'default'
        service.finish_policy(change, True)
    finally:
        service.close()


def test_leaving_plan_on_the_same_grant_is_a_mode_change(tmp_path):
    service, connection, record, _ = _service(tmp_path)
    cleared = []
    try:
        change = service.prepare_mode(record, 'plan')
        record = change.record()
        service.finish_policy(change, True)
        service.permission_store.clear = cleared.append
        change = service.prepare_policy(record, 'read-only', 'on-request', 'user', collaboration='default')
        assert change.subject == 'mode' and not cleared
    finally:
        service.close()


def test_the_mode_label_says_plan(tmp_path):
    from thechatplace.ui.permission_hosts import codex_mode_label
    assert codex_mode_label({}) == 'Read-only; asks before allowing changes'
    assert codex_mode_label({'sandbox': 'workspace-write', 'collaboration': 'plan'}) == (
        'Can edit files in this folder; asks before anything else, in Plan mode')
    assert codex_mode_label({'collaboration': 'default'}) == 'Read-only; asks before allowing changes'


def test_properties_name_the_model_a_default_chat_is_on_now():
    from thechatplace.properties import session_properties
    from thechatplace.sessions import SessionInfo, OWN
    info = SessionInfo(OWN, 'codex:x', 'Chat', '/w', 'x', provider='codex')
    own = OwnSession('x', 'Chat', '/w', provider='codex', started=True, provider_options={})
    lines = session_properties(info, own=own, now_ms=0, running_model='gpt-5.5')
    assert "Model chosen: Codex's default (last used gpt-5.5)" in lines
    assert "Model chosen: Codex's default" in session_properties(info, own=own, now_ms=0)
    own.provider_options['model'] = 'gpt-other'
    assert 'Model chosen: gpt-other' in session_properties(info, own=own, now_ms=0, running_model='gpt-other')
