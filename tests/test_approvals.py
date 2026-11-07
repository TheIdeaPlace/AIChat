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
