"""Bounded owned-chat worker failures (Codex, Copilot) without commands, chat
text or exception bodies."""
import calendar
import re
import threading
import time

from . import platform_paths
from .providers.codex_rpc import CodexTurnRejected, CodexSteerRefused, CodexConnectionFailure, CodexRequestTimeout, CodexRememberedApprovalError

_LOCK = threading.Lock()
_LIMIT = 65536
_RECORD = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ (Codex|Copilot) worker failed: ([A-Za-z_][A-Za-z0-9_]{0,63}); details redacted\.')
_REASON = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ Codex worker refused turn: ([a-z_]+)\.(?: Codex said: ([A-Za-z, ]{1,120})\.)?')
_CONNECTION = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ Codex connection failure: ([a-z_]+)\.')
#: A Copilot failure with the reason the app wrote for it (#464).
_WORDED = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ Copilot worker failed: ([A-Za-z_][A-Za-z0-9_]{0,63})\. It said: ([^\x00-\x1f\x7f]{1,1000})')
_STAMP = '%Y-%m-%dT%H:%M:%SZ'
#: What would make a reason unfit for a public bug report even though the app
#: wrote it: a path, an address or a home folder can come in through the text
#: Copilot's own errors carry (``CopilotChatError(str(exc))``).
_PRIVATE = re.compile(r'[\\@~\uff20\uff0f\uff3c\u2215\u2044]|\b[A-Za-z]:\S|/[\w.]'
                      r'|\b(?:https?|s?ftp|ssh|file|mailto):'
                      r'|[A-Za-z0-9-]+\.[A-Za-z0-9-]+\.[A-Za-z]{2,}', re.IGNORECASE)
#: The longest a log line can be (a worded one; UTF-8 can take 3 bytes a
#: character), so reading never gives up on a file that a write just grew.
_LINE_MAX = 4096


def _copilot_error(error):
    from .providers.copilot_runtime import CopilotChatError
    return isinstance(error, CopilotChatError)


def safe_reason(error):
    if _copilot_error(error):
        # Copilot's runtime words every reason it gives to be shown (#370).
        text = str(error)
        return text if len(text) <= 1000 and not any(ord(c) < 32 for c in text) else ''
    if type(error) in (CodexTurnRejected, CodexSteerRefused, CodexConnectionFailure, CodexRequestTimeout, CodexRememberedApprovalError):
        text = type(error).REASONS.get(error.code, '')
        if type(error) is CodexTurnRejected and error.code == 'project_rules':
            folder = getattr(error, 'rules_folder', '')
            if isinstance(folder, str) and len(folder) <= 32768 and not any(ord(c) < 32 for c in folder):
                text += ' Rules folder: ' + folder
        return text
    return ''


def record_failure(error):
    name = type(error).__name__
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', name):
        name = 'Exception'
    worker = 'Copilot' if _copilot_error(error) else 'Codex'
    line = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()) + f' {worker} worker failed: {name}; details redacted.'
    if type(error) is CodexTurnRejected and error.code in CodexTurnRejected.REASONS:
        line = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()) + f' Codex worker refused turn: {error.code}.'
        # What Codex reported, so a refusal can be explained later (#448).
        if getattr(error, 'status', ''):
            line += f' Codex said: {error.status}.'
    elif type(error) is CodexConnectionFailure and error.code in CodexConnectionFailure.REASONS:
        line = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()) + f' Codex connection failure: {error.code}.'
    elif worker == 'Copilot':
        # The words the user saw, which say what went wrong (#464); safe_reason
        # has already bounded them and refused control characters.
        reason = safe_reason(error)
        worded = time.strftime(_STAMP, time.gmtime()) + f' Copilot worker failed: {name}. It said: {reason}'
        # Only a line that reads back whole, as one line (#464): splitlines()
        # also splits on characters safe_reason lets through.
        if (reason and not _PRIVATE.search(reason) and _WORDED.fullmatch(worded)
                and len(worded.splitlines()) == 1):
            line = worded
    try:
        with _LOCK:
            path = platform_paths.codex_diagnostic_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size + len(line.encode('utf-8')) + 1 > _LIMIT:
                kept = path.read_text(encoding='utf-8', errors='replace')[-(_LIMIT // 2):]
                path.write_text(kept[kept.find('\n') + 1:], encoding='utf-8')
            with path.open('a', encoding='utf-8') as output:
                output.write(line + '\n')
    except Exception:
        pass  # diagnostics must not affect turn execution or UI delivery


def _read_lines():
    with _LOCK:
        path = platform_paths.codex_diagnostic_path()
        if path.stat().st_size > _LIMIT + _LINE_MAX:
            return []
        return path.read_text(encoding='utf-8', errors='replace').splitlines()


def _parse(line):
    """(provider, when, what) for a line this module wrote, else None. Only
    what matches a known shape comes back, so a forged or damaged line never
    reaches a report."""
    try:
        when = calendar.timegm(time.strptime(line[:20], _STAMP))
    except (ValueError, OverflowError):
        return None
    connection = _CONNECTION.fullmatch(line)
    if connection and connection.group(1) in CodexConnectionFailure.REASONS:
        code = connection.group(1)
        return 'Codex', when, f'connection failure ({code}): {CodexConnectionFailure.REASONS[code]}'
    reason = _REASON.fullmatch(line)
    if reason and reason.group(1) in CodexTurnRejected.REASONS:
        code = reason.group(1)
        said = f' Codex said: {reason.group(2)}.' if reason.group(2) else ''
        return 'Codex', when, f'refused turn ({code}): {CodexTurnRejected.REASONS[code]}{said}'
    worded = _WORDED.fullmatch(line)
    if worded and not _PRIVATE.search(worded.group(2)):
        return 'Copilot', when, f'{worded.group(1)}: {worded.group(2)}'
    match = _RECORD.fullmatch(line)
    if match:
        return match.group(1), when, f'{match.group(2)}; details redacted.'
    return None


def recent_failures(now=None, per_provider=3, within=86400):
    """{provider: [(seconds ago, what), ...]}, newest first: up to
    ``per_provider`` from the last ``within`` seconds, or the newest one if
    none is that recent, so one provider's failure never hides another's (#464)."""
    now = time.time() if now is None else now
    try:
        lines = _read_lines()
    except Exception:
        return {}
    found = {}
    for line in reversed(lines):
        parsed = _parse(line)
        if parsed:
            provider, when, what = parsed
            found.setdefault(provider, []).append((max(0.0, now - when), what))
    return {provider: ([f for f in failures if f[0] <= within][:per_provider] or failures[:1])
            for provider, failures in found.items()}


def last_failure():
    """The newest failure of any provider, named by provider."""
    try:
        lines = _read_lines()
    except Exception:
        return ''
    for line in reversed(lines):
        parsed = _parse(line)
        if parsed:
            provider, _when, what = parsed
            return f'{provider} {what}'
    return ''
