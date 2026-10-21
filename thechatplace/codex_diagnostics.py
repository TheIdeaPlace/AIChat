"""Bounded Codex worker failures without commands, chat text or exception bodies."""
import re
import threading
import time

from . import platform_paths
from .providers.codex_rpc import CodexTurnRejected, CodexConnectionFailure, CodexRequestTimeout, CodexRememberedApprovalError

_LOCK = threading.Lock()
_LIMIT = 65536
_RECORD = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ Codex worker failed: ([A-Za-z_][A-Za-z0-9_]{0,63}); details redacted\.')
_REASON = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ Codex worker refused turn: ([a-z_]+)\.')
_CONNECTION = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ Codex connection failure: ([a-z_]+)\.')


def safe_reason(error):
    if type(error) in (CodexTurnRejected, CodexConnectionFailure, CodexRequestTimeout, CodexRememberedApprovalError):
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
    line = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()) + f' Codex worker failed: {name}; details redacted.'
    if type(error) is CodexTurnRejected and error.code in CodexTurnRejected.REASONS:
        line = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()) + f' Codex worker refused turn: {error.code}.'
    elif type(error) is CodexConnectionFailure and error.code in CodexConnectionFailure.REASONS:
        line = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()) + f' Codex connection failure: {error.code}.'
    try:
        with _LOCK:
            path = platform_paths.codex_diagnostic_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > _LIMIT:
                kept = path.read_text(encoding='utf-8', errors='replace')[-(_LIMIT // 2):]
                path.write_text(kept[kept.find('\n') + 1:], encoding='utf-8')
            with path.open('a', encoding='utf-8') as output:
                output.write(line + '\n')
    except Exception:
        pass  # diagnostics must not affect turn execution or UI delivery


def last_failure():
    try:
        with _LOCK:
            path = platform_paths.codex_diagnostic_path()
            if path.stat().st_size > _LIMIT + 1024:
                return ''
            lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
        for line in reversed(lines):
            connection = _CONNECTION.fullmatch(line)
            if connection and connection.group(1) in CodexConnectionFailure.REASONS:
                code = connection.group(1)
                return f'Codex connection failure ({code}): {CodexConnectionFailure.REASONS[code]}'
            reason = _REASON.fullmatch(line)
            if reason and reason.group(1) in CodexTurnRejected.REASONS:
                code = reason.group(1)
                return f'Codex refused turn ({code}): {CodexTurnRejected.REASONS[code]}'
            match = _RECORD.fullmatch(line)
            if match:
                return f'Codex worker failed: {match.group(1)}; details redacted.'
    except Exception:
        pass
    return ''
