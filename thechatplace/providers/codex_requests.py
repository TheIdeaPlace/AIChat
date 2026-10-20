"""Bounded, immutable user requests with provider-specific answer conversion.

Command approvals follow the app-server's experimental schema (codex-cli
0.162.1, ``generate-json-schema --experimental``): current Codex sends
``environmentId: "local"`` and ``availableDecisions`` on every command
approval, and may add ``additionalPermissions`` (extra folders or network) or
``networkApprovalContext`` (a host, with no command). Whatever can be shown in
plain words is shown; anything else is declined, with a fixed reason (#284).
"""
from dataclasses import dataclass, field
import json
import unicodedata

from .codex_events import _id

COMMAND = 'item/commandExecution/requestApproval'
FILES = 'item/fileChange/requestApproval'
QUESTIONS = 'item/tool/requestUserInput'

ACCEPT, ACCEPT_SESSION, DECLINE, CANCEL = 'accept', 'acceptForSession', 'decline', 'cancel'
#: The command decisions this app can send. Codex lists the ones it allows
#: for each request (``availableDecisions``); only those are offered.
#: ``acceptForSession`` is decoded but never offered or sent: Codex keeps
#: that approval for its whole in-memory session, and Change Permission Mode
#: on a loaded chat doesn't start a new one, so it would outlive a switch to
#: read-only (#284, follow-up #298).
COMMAND_DECISIONS = (ACCEPT, DECLINE, CANCEL)
#: Decisions that save a lasting Codex rule ("always allow this command" or
#: "always allow/block this host"). Left out on purpose (#284): a saved rule
#: outlives the chat and is exactly what the rule-file checks guard (#257).
SAVED_RULE_DECISIONS = frozenset({'acceptWithExecpolicyAmendment', 'applyNetworkPolicyAmendment'})
#: Codex offers that keep allowing something after this one request.
LASTING_DECISIONS = SAVED_RULE_DECISIONS | {ACCEPT_SESSION}
LOCAL_ENVIRONMENT = 'local'
NETWORK_PROTOCOLS = {'http': 'HTTP', 'https': 'HTTPS', 'socks5Tcp': 'SOCKS5 TCP', 'socks5Udp': 'SOCKS5 UDP'}
ACCESS_WORDS = {'read': 'Read access to', 'write': 'Write access to', 'deny': 'No access to'}
SPECIAL_PATHS = {'root': 'every file on this computer',
                 'minimal': 'the basic system files programs need to run',
                 'project_roots': "the chat's project folder",
                 'tmpdir': 'the temporary folder', 'slash_tmp': '/tmp'}
MAX_EXTRA_ACCESS = 64
SAVED_RULE_NOTE = ('Codex also offered to keep allowing this, for the rest of the session or from now on. '
                   'The Chat Place leaves that out: your answer covers this request only.')
#: A command approval with no extra access listed is normally a request to
#: run outside the sandbox altogether (on-request policy), so approving it
#: gives the command full access. Codex's own reason text may not say so.
SANDBOX_NOTE = ("If you allow it, this command may run outside the chat's folder sandbox, "
                "where it can change any file you can and use the network.")


class CodexUnsupportedRequest(ValueError):
    """A request that can't be shown honestly, with a fixed reason code.

    The code, never the server's text, picks what the chat says about it.
    """
    def __init__(self, message, reason=''):
        super().__init__(message)
        self.reason = reason if reason in DECLINED_REASONS else ''


#: What the chat says about a declined request, by reason (#284). Fixed text
#: only: never the server's command, paths or method name.
DECLINED_REASONS = {
    'terminal_input': ("Codex asked to type into a command that's already running. The Chat Place "
                       "can't show that kind of request yet, so it was declined."),
    'remote': ('Codex asked to run a command somewhere other than this computer. The Chat Place '
               'only answers for this computer, so it was declined.'),
    'permissions': ("Codex asked for a kind of extra access The Chat Place can't describe yet, "
                    "so it was declined."),
    'choices': "Codex offered no answers The Chat Place can give, so the request was declined.",
    'rules': "Codex offered a saved rule The Chat Place couldn't read, so the request was declined.",
}


@dataclass(frozen=True)
class CodexRequestDeclined:
    """A connection-level notice with no server text or executable answer.

    ``thread_id`` is the chat the request named, when it named one validly,
    so only that chat shows the notice.
    """
    kind: str = 'request'
    reason: str = ''
    thread_id: str = ''

    def __post_init__(self):
        if self.kind not in ('request', 'command approval', 'file approval', 'question'):
            raise ValueError('Invalid Codex declined-request kind')
        if self.reason and self.reason not in DECLINED_REASONS:
            raise ValueError('Invalid Codex declined-request reason')
        if self.thread_id:
            _id(self.thread_id)

    @property
    def text(self):
        if self.reason:
            return DECLINED_REASONS[self.reason] + ' No permission was granted.'
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
    #: Command decisions Codex allows, in its order; empty means the legacy
    #: one-time set (accept, decline, cancel) of servers without the list.
    decisions: tuple = ()
    #: Extra access the command needs, one plain-words line each.
    extra_access: tuple = field(default=(), repr=False)
    network_host: str = field(default='', repr=False)
    network_protocol: str = ''
    saved_rule_offered: bool = False
    proposed_prefix: tuple = field(default=(), repr=False)
    permission_scope: str = field(default='', repr=False)

    def offered(self):
        """The decisions to offer, in order. Never one Codex didn't list."""
        if self.method == COMMAND:
            return self.decisions or (ACCEPT, DECLINE, CANCEL)
        if self.method == FILES:
            # A session write grant can't be shown or limited here (#255).
            return (DECLINE, CANCEL) if self.grant_root else (ACCEPT, DECLINE, CANCEL)
        return ()

    def reviewable(self, file_detail=''):
        """Whether there's enough to review for an allowing answer."""
        if self.method == COMMAND:
            return bool(self.command.strip() or self.network_host)
        return self.method == FILES and bool(file_detail) and not self.grant_root

    @property
    def outside_sandbox(self):
        """A command approval naming no extra access: likely a full escalation."""
        return self.method == COMMAND and not self.extra_access and not self.network_host

    def choice_text(self, decision):
        """The button label for a decision, with its access key."""
        labels = {ACCEPT: '&Allow Once', DECLINE: '&Deny', CANCEL: 'Deny and &Stop Turn'}
        if decision not in labels:
            raise ValueError('Unsupported Codex approval choice')
        return labels[decision]

    def detail(self, file_detail=''):
        """The whole request in plain words, as the approval dialog shows it."""
        if self.method == COMMAND:
            parts = []
            if self.network_host:
                parts.append(f'Codex wants network access to:\n{self.network_host} ({self.network_protocol})')
            if self.command:
                parts.append('Command:\n' + self.command)
            if self.cwd:
                parts.append('Folder:\n' + self.cwd)
            if self.extra_access:
                parts.append('This also needs:\n' + '\n'.join(self.extra_access))
            if self.outside_sandbox:
                parts.append(SANDBOX_NOTE)
        else:
            parts = [file_detail or "The requested file changes aren't available to review. Deny or stop this turn."]
        if self.reason:
            parts.append('Reason:\n' + self.reason)
        if self.grant_root:
            parts.append('Codex requested session write access under:\n' + self.grant_root
                         + "\nThe Chat Place can't grant that, so Allow isn't offered.")
        if self.saved_rule_offered:
            parts.append(SAVED_RULE_NOTE)
        return '\n\n'.join(parts)

    def summary(self):
        """A short spoken line about what's wanted."""
        if self.method == QUESTIONS:
            return 'Question: ' + self.questions[0].text if self.questions else 'Question.'
        if self.method != COMMAND:
            return 'File changes need approval.'
        if self.network_host and not self.command.strip():
            return f'Network access to {self.network_host}.'
        if self.network_host:
            return f'Command needing network access to {self.network_host}: ' + self.command
        if self.extra_access:
            return 'Command with extra access: ' + self.command
        return 'Command that may run outside the sandbox: ' + self.command

    def approval_answer(self, decision):
        if (self.method not in (COMMAND, FILES) or not isinstance(decision, str)
                or decision not in self.offered()):
            # Only what Codex listed, and never a lasting approval (#284).
            raise ValueError('Unsupported Codex approval choice')
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


def _permission_error():
    return CodexUnsupportedRequest('Unsupported Codex extra permissions', 'permissions')


def _keys(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise _permission_error()
    return value


def _shown(value, *, limit=8192, spaces=True):
    """Server text shown as one line of the request: never blank, and never
    a control or format character, so a path can't fake lines in the dialog.
    """
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(unicodedata.category(c) in ('Cc', 'Cf', 'Zl', 'Zp') for c in value)
            or not spaces and any(c.isspace() for c in value)):
        raise _permission_error()
    return value


def _path_words(path):
    """One FileSystemPath in plain words; anything unknown is refused."""
    kind = path.get('type') if isinstance(path, dict) else None
    if kind == 'path':
        return _shown(_keys(path, ('type', 'path'), ('type', 'path'))['path'])
    if kind == 'glob_pattern':
        return 'files matching ' + _shown(_keys(path, ('type', 'pattern'), ('type', 'pattern'))['pattern'])
    if kind == 'special':
        value = _keys(path, ('type', 'value'), ('type', 'value'))['value']
        special = value.get('kind') if isinstance(value, dict) else None
        special = special if isinstance(special, str) else None
        if special == 'project_roots':
            subpath = _keys(value, ('kind', 'subpath'), ('kind',)).get('subpath')
            words = SPECIAL_PATHS[special]
            return words if subpath is None else f'{_shown(subpath)} in {words}'
        # Kind "unknown" carries a token, not a place anyone can check.
        if special in SPECIAL_PATHS:
            _keys(value, ('kind',), ('kind',))
            return SPECIAL_PATHS[special]
    raise _permission_error()


def _extra_access(value):
    """AdditionalPermissionProfile as plain-words lines; refuse what can't be said."""
    if value is None:
        return (), False
    profile = _keys(value, ('fileSystem', 'network'))
    lines = []
    files = profile.get('fileSystem')
    if files is not None:
        files = _keys(files, ('entries', 'globScanMaxDepth', 'read', 'write'))
        depth = files.get('globScanMaxDepth')
        if depth is not None and (type(depth) is not int or depth < 1):
            raise _permission_error()
        for access in ('read', 'write'):
            paths = files.get(access)
            if paths is None:
                continue
            if not isinstance(paths, list):
                raise _permission_error()
            for path in paths:
                lines.append(f'{ACCESS_WORDS[access]} {_shown(path)}')
        entries = files.get('entries')
        if entries is not None:
            if not isinstance(entries, list):
                raise _permission_error()
            for entry in entries:
                entry = _keys(entry, ('access', 'path'), ('access', 'path'))
                if not isinstance(entry['access'], str) or entry['access'] not in ACCESS_WORDS:
                    raise _permission_error()
                lines.append(f"{ACCESS_WORDS[entry['access']]} {_path_words(entry['path'])}")
    network = profile.get('network')
    if network is not None:
        enabled = _keys(network, ('enabled',)).get('enabled')
        if enabled is not None and type(enabled) is not bool:
            raise _permission_error()
        if enabled:
            lines.append('Network access to any site')
    if len(lines) > MAX_EXTRA_ACCESS or sum(len(line) for line in lines) > 64 * 1024:
        raise _permission_error()
    return tuple(lines), True


def _network_context(value):
    if value is None:
        return '', ''
    context = _keys(value, ('host', 'protocol'), ('host', 'protocol'))
    if not isinstance(context['protocol'], str) or context['protocol'] not in NETWORK_PROTOCOLS:
        raise _permission_error()
    return _shown(context['host'], limit=512, spaces=False), NETWORK_PROTOCOLS[context['protocol']]


def _decisions(value, network, extra):
    """The offered decisions, and whether Codex also offered a lasting one.

    Without a list, follow Codex's own defaults for the request (minus the
    lasting ones); an ordinary command keeps this app's one-time set.
    """
    if value is None:
        if network or extra:
            return (ACCEPT, CANCEL), False
        return (ACCEPT, DECLINE, CANCEL), False
    if not isinstance(value, list) or not 1 <= len(value) <= 16:
        raise CodexUnsupportedRequest('Invalid Codex decisions', 'choices')
    offered, saved = [], False
    for decision in value:
        if isinstance(decision, str):
            # A decision newer than this release is never offered.
            if decision in COMMAND_DECISIONS and decision not in offered:
                offered.append(decision)
            saved = saved or decision == ACCEPT_SESSION
        elif isinstance(decision, dict) and len(decision) == 1:
            saved = saved or next(iter(decision)) in SAVED_RULE_DECISIONS
        else:
            raise CodexUnsupportedRequest('Invalid Codex decisions', 'choices')
    if not {DECLINE, CANCEL} & set(offered):
        # Every prompt must have a way to say no.
        raise CodexUnsupportedRequest('No refusal among Codex decisions', 'choices')
    return tuple(offered), saved


def _decode_command(common, params):
    kind = params.get('kind', 'command')
    if kind != 'command':
        raise CodexUnsupportedRequest('Unsupported Codex command approval kind',
                                      'terminal_input' if kind == 'writeStdin' else '')
    if params.get('environmentId') not in (None, LOCAL_ENVIRONMENT):
        raise CodexUnsupportedRequest('Unsupported Codex command environment', 'remote')
    host, protocol = _network_context(params.get('networkApprovalContext'))
    extra, has_extra = _extra_access(params.get('additionalPermissions'))
    decisions, saved = _decisions(params.get('availableDecisions'), bool(host), has_extra)
    for name, kinds in (('proposedExecpolicyAmendment', str), ('proposedNetworkPolicyAmendments', dict)):
        proposed = params.get(name)
        if proposed is not None and (not isinstance(proposed, list) or len(proposed) > 256
                                     or any(not isinstance(item, kinds) for item in proposed)):
            raise CodexUnsupportedRequest('Invalid Codex proposed rule', 'rules')
    # A network approval names a host and may carry no command at all.
    command = _text(params.get('command'), optional=bool(host))
    cwd = _text(params.get('cwd'), optional=bool(host))
    if not host and (not command.strip() or not cwd.strip()):
        raise ValueError('Codex command approval details are missing')
    return CodexRequest(**common, command=command, cwd=cwd,
                        reason=_text(params.get('reason'), optional=True),
                        decisions=decisions, extra_access=extra, network_host=host,
                        network_protocol=protocol, saved_rule_offered=saved,
                        proposed_prefix=tuple(params.get('proposedExecpolicyAmendment') or ()),
                        permission_scope=json.dumps({
                            'permissions': params.get('additionalPermissions'),
                            'network': params.get('networkApprovalContext'),
                            'outside_sandbox': not extra and not host,
                        }, sort_keys=True, separators=(',', ':')))


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
        return _decode_command(common, params)
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
