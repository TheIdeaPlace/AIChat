"""Approval notices: read back, listed in Lists, kept in the history (#564).
Windowless."""
import pytest

from thechatplace import approvals, lists
from thechatplace.providers.codex_events import decode_event
from thechatplace.providers.codex_permissions import CodexReviewStore


def review(status, command='git status --short', reason='Synthetic reason.', review_id='1'):
    """A notice exactly as codex_events makes it."""
    event = decode_event('item/autoApprovalReview/completed', {
        'threadId': 'owned', 'turnId': 'turn-1', 'reviewId': review_id,
        'action': {'type': 'command', 'command': command},
        'review': {'status': status, 'rationale': reason}})
    return event.item_id, event.text


RULE = 'Allowed git diff, as you said for this chat.'


@pytest.mark.parametrize('status,outcome,routine', [
    ('approved', approvals.ALLOWED, True), ('denied', approvals.DENIED, False),
    ('timedOut', approvals.TIMED_OUT, False), ('aborted', approvals.STOPPED, False)])
def test_review_notices_read_back(status, outcome, routine):
    identity, text = review(status)
    parsed = approvals.parse(identity, text)
    assert parsed.outcome == outcome and parsed.by == approvals.BY_REVIEW
    assert parsed.command == 'git status --short' and parsed.reason == 'Synthetic reason.'
    assert parsed.routine is routine
    assert parsed.row().startswith(outcome.capitalize() + ': git status --short.')


def test_saved_rule_notice_reads_back_as_routine():
    parsed = approvals.parse(7, RULE)
    assert (parsed.outcome, parsed.by, parsed.command) == (approvals.ALLOWED, approvals.BY_RULE, 'git diff')
    assert parsed.row() == 'Allowed: git diff. Saved rule for this chat.'


def test_unknown_text_is_never_hidden():
    assert not approvals.is_routine('x', 'Something new Codex says.')


def test_a_long_command_is_shortened_in_the_row_but_copied_whole():
    identity, text = review('approved', command='x' * 300)
    item = lists.approval_items([('turn-1', identity, text)])[0]
    assert len(item.row) < 200 and item.copy_text == 'x' * 300 and item.copy_what == 'the command'


def test_merge_keeps_history_then_this_runs_new_notices_once():
    old = review('approved', review_id='1')
    saved = [('turn-1', old[0], old[1]), ('turn-1', 'saved-rule:7', RULE)]
    new = review('denied', review_id='2')
    live = [('turn-1', old[0], old[1]), ('turn-1', 7, RULE), ('turn-2', new[0], new[1])]
    merged = approvals.merge(saved, live)
    assert [row[1] for row in merged] == [old[0], 'saved-rule:7', new[0]]


def test_count_text_counts_and_never_names_commands():
    rows = [('t', *review('approved', review_id=str(i))) for i in range(12)]
    rows.append(('t', *review('denied', review_id='99')))
    text = approvals.count_text(rows)
    assert text == '12 requests allowed automatically. 1 not allowed.'
    assert approvals.count_text([('t', *review('denied'))]) == '1 request not allowed.'
    assert approvals.count_text([]) == ''


def test_lists_approvals_newest_first_and_whole_chat_in_either_scope():
    first, second = review('approved', review_id='1'), review('denied', review_id='2')
    rows = [('turn-1', *first), ('turn-2', *second)]
    for scope in (lists.SCOPE_MESSAGE, lists.SCOPE_CHAT):
        items = lists.build(lists.APPROVALS, scope, [], None, [], [], lambda f: '', approvals=rows)
        assert [i.row.split(':')[0] for i in items] == ['Denied', 'Allowed']
    assert lists.empty_text(lists.APPROVALS, lists.SCOPE_MESSAGE) == 'No approvals in this chat.'
    assert lists.TITLES[lists.APPROVALS] == 'Approvals'


def test_saved_rule_notices_fit_the_history_file(tmp_path):
    store = CodexReviewStore(tmp_path)
    store.remember('owned', 'turn-1', [(approvals.history_identity(7), RULE)])
    store.remember('owned', 'turn-1', [(approvals.history_identity('auto-review:1'), 'Codex automatic review allowed the request.')])
    assert [row[1] for row in store.load('owned')] == ['saved-rule:7', 'auto-review:1']


def test_a_command_over_several_lines_is_kept_whole():
    """The review's finding on #564: a heredoc or a commit body was cut to
    its first line, and a line starting "Reason: " was taken as the reason."""
    command = 'git commit -F - <<END\nFix the list\nReason: not the reviewer\nEND'
    identity, text = review('approved', command=command, reason='Commits in the workspace.')
    parsed = approvals.parse(identity, text)
    assert parsed.command == command and parsed.reason == 'Commits in the workspace.'
    assert '\n' not in parsed.row()
    assert lists.approval_items([('t', identity, text)])[0].copy_text == command


def _runtime(tmp_path):
    from types import SimpleNamespace
    from thechatplace.providers.codex_runtime import CodexChatRuntime
    runtime = object.__new__(CodexChatRuntime)  # only the history-saving part is used
    runtime.review_store, runtime._saved_reviews = CodexReviewStore(tmp_path), {}
    return runtime, lambda notices: runtime._save_for_later(
        'owned', SimpleNamespace(permission_notices=tuple(notices), plan_received=False))


def test_a_long_session_keeps_the_newest_128_in_order(tmp_path):
    """The review's finding on #564: 100 saved-rule approvals over 100
    turns, then 40 reviews in one turn, scrambled the file and lost new ones."""
    runtime, save = _runtime(tmp_path)
    notices = [(f'turn-{n}', n, f'Allowed git status {n}, as you said for this chat.') for n in range(100)]
    save(notices)
    for number in range(40):
        notices.append(('turn-last', *review('approved', review_id=str(number))))
        save(notices)
    kept = runtime.review_store.load('owned')
    expected = [approvals.history_identity(row[1]) for row in notices][-128:]
    assert [row[1] for row in kept] == expected


def test_saving_the_same_notices_again_writes_nothing(tmp_path, monkeypatch):
    runtime, save = _runtime(tmp_path)
    notices = [('turn', *review('approved'))]
    save(notices)
    writes = []
    monkeypatch.setattr(runtime.review_store, 'remember', lambda *a: writes.append(a))
    save(notices)
    assert writes == []


def test_rows_the_history_file_would_refuse_are_left_out(tmp_path):
    runtime, save = _runtime(tmp_path)
    save([('', 1, 'Allowed x, as you said for this chat.'), ('turn', 2, ''), ('turn', 3, 'y' * 10001),
          ('turn', 4, 'Allowed git log, as you said for this chat.')])
    assert [row[1] for row in runtime.review_store.load('owned')] == ['saved-rule:4']


def test_a_cut_short_command_copies_without_the_display_note():
    identity, text = review('approved', command='x' * 5000)
    parsed = approvals.parse(identity, text)
    assert 'Display truncated' not in parsed.command and parsed.command.startswith('x' * 4000)
    assert 'Display truncated' in parsed.text
