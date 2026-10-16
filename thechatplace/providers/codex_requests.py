"""Bounded, immutable user requests with provider-specific answer conversion."""
from dataclasses import dataclass, field

from .codex_events import _id

COMMAND = 'item/commandExecution/requestApproval'
FILES = 'item/fileChange/requestApproval'
QUESTIONS = 'item/tool/requestUserInput'


@dataclass(frozen=True)
class CodexRequestDeclined:
    """A connection-level notice with no server text or executable answer."""
    kind: str = 'request'

    def __post_init__(self):
        if self.kind not in ('request', 'command approval', 'file approval', 'question'):
            raise ValueError('Invalid Codex declined-request kind')

    @property
    def text(self):
        return f'An unsupported Codex {self.kind} was declined. No permission was granted.'


@dataclass(frozen=True)
class CodexRequestResolved:
    request_id: int | str
    thread_id: str


def decode_resolved(params):
    if not isinstance(params, dict):
        raise ValueError('Invalid Codex request resolution')
    identity = params.get('requestId')
    if not (type(identity) is int or isinstance(identity, str)):
        raise ValueError('Invalid Codex request resolution')
    if isinstance(identity, str):
        _id(identity)
    return CodexRequestResolved(identity, _id(params.get('threadId')))


def _text(value, *, optional=False, limit=8192):
    if value is None and optional:
        return ''
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError('Invalid Codex request text')
    return value


@dataclass(frozen=True)
class CodexQuestion:
    question_id: str
    header: str = field(repr=False)
    text: str = field(repr=False)
    options: tuple = field(default=(), repr=False)
    allow_other: bool = False
    secret: bool = False


@dataclass(frozen=True)
class CodexRequest:
    request_id: int | str
    method: str
    thread_id: str
    turn_id: str
    item_id: str
    command: str = field(default='', repr=False)
    cwd: str = field(default='', repr=False)
    reason: str = field(default='', repr=False)
    questions: tuple = field(default=(), repr=False)
    grant_root: str = field(default='', repr=False)
    blocking: bool = True

    def approval_answer(self, decision):
        if self.method not in (COMMAND, FILES) or decision not in ('accept', 'decline', 'cancel'):
            # No persistent scopes or session approvals are offered yet.
            raise ValueError('Unsupported Codex approval choice')
        if self.method == FILES and self.grant_root and decision == 'accept':
            raise ValueError('Codex session write grants are not supported yet')
        return {'decision': decision}

    def question_answer(self, answers):
        if self.method != QUESTIONS or not isinstance(answers, dict):
            raise ValueError('Invalid Codex question answers')
        if set(answers) != {q.question_id for q in self.questions}:
            raise ValueError('Answer every Codex question')
        result = {}
        for question in self.questions:
            answer = _text(answers[question.question_id], limit=8192)
            if not answer.strip():
                raise ValueError('Answer every Codex question')
            labels = {label for label, description in question.options}
            if question.options and not question.allow_other and answer not in labels:
                raise ValueError('Choose one of the offered Codex answers')
            result[question.question_id] = {'answers': [answer]}
        if sum(len(answer['answers'][0].encode('utf-8')) for answer in result.values()) > 16000:
            raise ValueError('Codex answers are too long')
        return {'answers': result}

    def validate_answer(self, result):
        if not isinstance(result, dict):
            raise ValueError('Invalid Codex answer')
        if self.method in (COMMAND, FILES):
            if set(result) != {'decision'}:
                raise ValueError('Invalid Codex approval answer')
            return self.approval_answer(result['decision'])
        if set(result) != {'answers'} or not isinstance(result['answers'], dict):
            raise ValueError('Invalid Codex question answers')
        answers = {}
        for identity, answer in result['answers'].items():
            if (not isinstance(answer, dict) or set(answer) != {'answers'}
                    or not isinstance(answer['answers'], list) or len(answer['answers']) != 1):
                raise ValueError('Invalid Codex question answers')
            answers[identity] = answer['answers'][0]
        return self.question_answer(answers)


def decode_request(request_id, method, params):
    if not (type(request_id) is int or isinstance(request_id, str)):
        raise ValueError('Invalid Codex request identity')
    if isinstance(request_id, str):
        _id(request_id)
    if method not in (COMMAND, FILES, QUESTIONS) or not isinstance(params, dict):
        raise ValueError('Unsupported Codex request')
    allowed = {'threadId', 'turnId', 'itemId'}
    if method == COMMAND:
        allowed |= {'startedAtMs', 'approvalId', 'command', 'cwd', 'reason', 'kind',
                    'commandActions', 'environmentId', 'networkApprovalContext',
                    'additionalPermissions', 'availableDecisions',
                    'proposedExecpolicyAmendment', 'proposedNetworkPolicyAmendments'}
    elif method == FILES:
        allowed |= {'startedAtMs', 'reason', 'grantRoot'}
    else:
        allowed |= {'questions', 'isBlocking', 'autoResolutionMs'}
    if set(params) - allowed:
        raise ValueError('Unsupported Codex request fields')
    common = dict(request_id=request_id, method=method,
                  thread_id=_id(params.get('threadId')), turn_id=_id(params.get('turnId')),
                  item_id=_id(params.get('itemId')))
    if method == COMMAND:
        # Extra permission and stdin/remote contexts need explicit UI support;
        # do not disguise them as an ordinary local command approval.
        if (params.get('kind', 'command') != 'command'
                or params.get('networkApprovalContext') is not None
                or params.get('additionalPermissions') is not None
                or params.get('availableDecisions') is not None
                or params.get('environmentId') is not None):
            raise ValueError('Unsupported Codex command approval context')
        command, cwd = _text(params.get('command')), _text(params.get('cwd'))
        if not command.strip() or not cwd.strip():
            raise ValueError('Codex command approval details are missing')
        return CodexRequest(**common, command=command, cwd=cwd,
                            reason=_text(params.get('reason'), optional=True))
    if method == FILES:
        return CodexRequest(**common, reason=_text(params.get('reason'), optional=True),
                            grant_root=_text(params.get('grantRoot'), optional=True))
    questions = params.get('questions')
    if not isinstance(questions, list) or not 1 <= len(questions) <= 16:
        raise ValueError('Invalid Codex questions')
    if type(params.get('isBlocking')) is not bool:
        raise ValueError('Invalid Codex question blocking state')
    decoded, identities = [], set()
    for question in questions:
        if not isinstance(question, dict):
            raise ValueError('Invalid Codex question')
        identity = _id(question.get('id'))
        if identity in identities:
            raise ValueError('Duplicate Codex question identity')
        identities.add(identity)
        allow_other, secret = question.get('isOther', False), question.get('isSecret', False)
        if type(allow_other) is not bool or type(secret) is not bool:
            raise ValueError('Invalid Codex question options')
        options = question.get('options')
        options = [] if options is None else options
        if not isinstance(options, list) or len(options) > 20:
            raise ValueError('Invalid Codex question options')
        choices, labels = [], set()
        for option in options:
            if not isinstance(option, dict):
                raise ValueError('Invalid Codex answer option')
            label = _text(option.get('label'), limit=256)
            if not label.strip() or label in labels:
                raise ValueError('Invalid Codex answer option')
            labels.add(label)
            choices.append((label, _text(option.get('description'))))
        decoded.append(CodexQuestion(identity, _text(question.get('header'), limit=256),
            _text(question.get('question')), tuple(choices), allow_other, secret))
    if sum(len(q.header) + len(q.text) + sum(len(label) + len(description)
           for label, description in q.options) for q in decoded) > 64 * 1024:
        raise ValueError('Codex questions are too long')
    return CodexRequest(**common, questions=tuple(decoded), blocking=params['isBlocking'])
