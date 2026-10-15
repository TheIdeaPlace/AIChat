"""Subscription usage reports without starting or resuming chats (#184)."""
import math
import time
from dataclasses import dataclass

from ..usage import when
from .codex_rpc import CodexRpcError


def _window(window, now):
    if not isinstance(window, dict):
        return 'Usage window: not reported.'
    duration, used = window.get('windowDurationMins'), window.get('usedPercent')
    name = (f'{duration // 60}-hour limit' if type(duration) is int and duration > 0 and duration % 60 == 0
            else f'{duration}-minute limit' if type(duration) is int and duration > 0 else 'Usage limit')
    if type(used) not in (int, float) or not 0 <= used <= 100 or not math.isfinite(used):
        text = name + ': usage not reported'
    else:
        text = f'{name}: {used:g}% used, {100 - used:g}% remaining'
    reset = window.get('resetsAt')
    if type(reset) is int and 0 < reset < 253402300800:
        try:
            text += ', resets ' + when(reset, now)
        except (ValueError, OverflowError, OSError):
            text += '; reset time unavailable'
    else:
        text += '; reset time not reported'
    return text + '.'


def usage_lines(response, *, now=None):
    if not isinstance(response, dict):
        raise CodexRpcError('Codex usage report unavailable')
    lines = ['Codex account usage is shared across your chats.']
    buckets = response.get('rateLimitsByLimitId')
    if isinstance(buckets, dict) and buckets:
        if len(buckets) > 32 or any(not isinstance(b, dict) for b in buckets.values()):
            raise CodexRpcError('Codex usage report unavailable')
        values = list(buckets.values())
    elif isinstance(response.get('rateLimits'), dict):
        values = [response['rateLimits']]
    else:
        return lines + ['Codex usage limits: not reported.']
    for index, bucket in enumerate(values, 1):
        label = bucket.get('limitName')
        if not isinstance(label, str) or not label.strip() or len(label) > 80 or any(ord(c) < 32 or ord(c) == 127 for c in label):
            label = 'Codex' if len(values) == 1 else f'Codex allowance {index}'
        lines.append(label.strip() + ':')
        windows = [bucket[key] for key in ('primary', 'secondary') if isinstance(bucket.get(key), dict)]
        lines.extend(_window(window, now) for window in windows)
        if not windows:
            lines.append('Usage windows: not reported.')
    return lines


def _read_report(connection):
    account = connection.request('account/read', {'refreshToken': True})
    if (not isinstance(account.get('account'), dict) or account['account'].get('type') != 'chatgpt'
            or account.get('requiresOpenaiAuth') is not True):
        raise CodexRpcError('Codex usage requires ChatGPT sign-in')
    return connection.request('account/rateLimits/read', {})


def read_usage(connection, *, now=None):
    return usage_lines(_read_report(connection), now=now)


@dataclass(frozen=True)
class CodexUsageWarning:
    key: tuple
    text: str


def usage_warnings(response, *, now=None):
    """Warn once per allowance/window/reset and threshold, never from stale data."""
    now = time.time() if now is None else now
    # Reuse report validation and prefer named buckets over a legacy duplicate.
    usage_lines(response, now=now)
    buckets = response.get('rateLimitsByLimitId')
    if not isinstance(buckets, dict) or not buckets:
        buckets = {'codex': response.get('rateLimits')}
    warnings = []
    for limit_id, bucket in buckets.items():
        if not isinstance(limit_id, str) or len(limit_id) > 256 or not isinstance(bucket, dict):
            continue
        label = bucket.get('limitName')
        if (not isinstance(label, str) or not label.strip() or len(label) > 80
                or any(ord(c) < 32 or ord(c) == 127 for c in label)):
            label = 'Codex'
        for name in ('primary', 'secondary'):
            window = bucket.get(name)
            if not isinstance(window, dict):
                continue
            used, reset = window.get('usedPercent'), window.get('resetsAt')
            if type(used) not in (int, float) or not 80 <= used <= 100 or not math.isfinite(used):
                continue
            # An elapsed reset belongs to an old report, not the current limit.
            if type(reset) is int and 0 < reset < 253402300800:
                if reset <= now:
                    continue
            else:
                reset = None
            duration = window.get('windowDurationMins')
            duration = duration if type(duration) is int and duration > 0 else None
            level = 100 if used == 100 else 80
            warnings.append(CodexUsageWarning(
                (limit_id, name, duration, reset, level),
                f'{label.strip()} account usage: ' + _window(window, now)))
    return tuple(warnings)


def read_warnings(connection, *, now=None):
    return usage_warnings(_read_report(connection), now=now)
