"""Synthetic saved plans never touch provider transcripts (#318)."""
import json
import pytest
from markers import windows_paths
from thechatplace.provider_storage import CodexPlanStore
from thechatplace.tasklist import TaskStep


def test_saved_plans_survive_reopening_and_keep_chat_identity(tmp_path):
    store = CodexPlanStore(tmp_path)
    tasks = [TaskStep('one', 'Find the cause', status='completed'),
             TaskStep('two', 'Run the tests', status='in_progress')]
    store.save('../a', 'turn-one', tasks)
    store.save('b', 'turn-two', [TaskStep('b', 'Other chat')])
    restored = CodexPlanStore(tmp_path)
    assert restored.load('../a') == ('turn-one', tuple(tasks))
    assert restored.load('b')[0] == 'turn-two'
    assert restored.load('unknown') == ('', ())
    assert len(list(tmp_path.glob('*.json'))) == 2
    assert not list(tmp_path.glob('*.tmp'))
    store.save('../a', 'turn-one', [])
    assert restored.load('../a') == ('turn-one', ())


@pytest.mark.parametrize('contents', ['{', '[]', 'null',
    json.dumps({'provider': 'claude', 'native_id': 'one', 'turn_id': 'turn', 'tasks': []}),
    json.dumps({'provider': 'codex', 'native_id': 'other', 'turn_id': 'turn', 'tasks': []}),
    *[json.dumps({'provider': 'codex', 'native_id': 'one', 'turn_id': 'turn', 'tasks': tasks})
      for tasks in [[None], [{'task_id': 'x', 'subject': 'X', 'status': 'future'}],
                    [{'task_id': 'x', 'subject': 'X', 'status': 'pending'}] * 2]]])
def test_bad_saved_plan_is_unavailable_without_losing_chat_history(tmp_path, contents):
    store = CodexPlanStore(tmp_path)
    store.save('one', 'turn', [])
    next(tmp_path.glob('*.json')).write_text(contents, encoding='utf-8')
    assert store.load('one') == ('', ())



def test_transient_sharing_failure_is_retried_within_the_same_save(tmp_path, monkeypatch):
    from thechatplace.provider_storage import PartitionedFile
    store = CodexPlanStore(tmp_path)
    store.save('one', 'turn', [TaskStep('test', 'Run the tests')])
    replace = PartitionedFile._replace
    failures = []
    def temporarily_locked(path, contents):
        if len(failures) < 2:
            failures.append(path)
            raise PermissionError('Synthetic sharing violation')
        return replace(path, contents)
    monkeypatch.setattr(PartitionedFile, '_replace', temporarily_locked)
    store.save('one', 'turn', [TaskStep('test', 'Run the tests', status='completed')])
    assert len(failures) == 2 and store.load('one')[1][0].done


def test_concurrent_plan_readers_and_saves_keep_the_final_snapshot(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    store = CodexPlanStore(tmp_path)
    store.save('one', 'turn', [TaskStep('test', 'Run the tests')])
    started = threading.Event()
    def read_repeatedly():
        reader = CodexPlanStore(tmp_path)
        assert started.wait(5)
        for _ in range(250):
            turn, tasks = reader.load('one')
            assert turn == 'turn' and len(tasks) == 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        readers = [pool.submit(read_repeatedly) for _ in range(3)]
        started.set()
        for number in range(80):
            store.save('one', 'turn', [TaskStep('test', 'Run the tests',
                status='completed' if number == 79 else 'in_progress')])
        for reader in readers:
            reader.result(timeout=10)
    assert store.load('one')[1][0].done


def test_removing_owned_chat_removes_only_its_saved_task_text(tmp_path):
    from thechatplace.own_store import OwnSessionStore, OwnSession
    store = OwnSessionStore(tmp_path / 'sessions.json')
    plans = CodexPlanStore(tmp_path / 'provider-tasks' / 'codex')
    for provider in ('claude', 'codex'):
        store.add(OwnSession('same', 'Synthetic', '/workspace', provider=provider))
    store.add(OwnSession('other', 'Other chat', '/workspace', provider='codex'))
    for native in ('same', 'other'):
        plans.save(native, 'turn', [TaskStep('test', 'Private synthetic task text')])
    store.remove('same', provider='claude')
    assert plans.load('same')[1]
    store.remove('same', provider='codex')
    assert not plans._path('same').exists()
    assert plans.load('other')[1] and store.get('other', provider='codex')
    store.remove('same', provider='codex')  # cleanup remains safe to repeat


def test_failed_session_remove_preserves_saved_tasks_and_ownership(tmp_path, monkeypatch):
    from thechatplace.own_store import OwnSessionStore, OwnSession
    store = OwnSessionStore(tmp_path / 'sessions.json')
    store.add(OwnSession('one', 'Synthetic', '/workspace', provider='codex'))
    plans = CodexPlanStore(tmp_path / 'provider-tasks' / 'codex')
    plans.save('one', 'turn', [TaskStep('test', 'Run the tests')])
    def fail():
        raise OSError('Synthetic write failure')
    monkeypatch.setattr(store, 'save', fail)
    with pytest.raises(OSError):
        store.remove('one', provider='codex')
    assert store.get('one', provider='codex') and plans.load('one')[1]



@windows_paths
def test_windows_open_target_retries_the_actual_sharing_violation(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import os
    store = CodexPlanStore(tmp_path)
    store.save('one', 'turn', [TaskStep('test', 'Run the tests')])
    opened, collided = threading.Event(), threading.Event()
    replace = os.replace
    def observed_replace(source, target):
        try:
            return replace(source, target)
        except PermissionError:
            collided.set()
            raise
    monkeypatch.setattr(os, 'replace', observed_replace)
    def hold_target():
        with store._path('one').open('rb'):
            opened.set()
            assert collided.wait(5)
    with ThreadPoolExecutor(max_workers=1) as pool:
        reader = pool.submit(hold_target)
        assert opened.wait(5)
        store.save('one', 'turn', [TaskStep('test', 'Run the tests', status='completed')])
        reader.result(timeout=5)
    assert collided.is_set() and store.load('one')[1][0].done
