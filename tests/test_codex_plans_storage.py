"""Synthetic saved plans never touch provider transcripts (#318)."""
import json
import pytest
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
