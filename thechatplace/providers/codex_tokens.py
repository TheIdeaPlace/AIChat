"""Reported Codex token counters; never infer a window from a model name."""
from dataclasses import dataclass


@dataclass(frozen=True)
class CodexTokenUsage:
    latest_input: int
    latest_output: int
    latest_total: int
    cumulative_total: int
    context_window: int | None

    @property
    def share(self):
        return self.latest_total / self.context_window if self.context_window else None


def _counter(value):
    return type(value) is int and 0 <= value <= 9223372036854775807


def decode_tokens(value):
    """Bad optional telemetry must not terminate a healthy executing turn."""
    if not isinstance(value, dict):
        return None
    last, total = value.get('last'), value.get('total')
    fields = ('inputTokens', 'outputTokens', 'cachedInputTokens', 'reasoningOutputTokens', 'totalTokens')
    if any(not isinstance(part, dict) or any(not _counter(part.get(key)) for key in fields)
           for part in (last, total)):
        return None
    window = value.get('modelContextWindow')
    if not _counter(window) or window == 0:
        window = None
    return CodexTokenUsage(last['inputTokens'], last['outputTokens'], last['totalTokens'],
                           total['totalTokens'], window)


def context_lines(tokens):
    if tokens is None:
        return ['Codex chat context: not reported on this connection yet.']
    lines = [f'Latest reported model request: {tokens.latest_total:,} tokens '
             f'({tokens.latest_input:,} input, {tokens.latest_output:,} output).']
    if tokens.context_window:
        lines.append(f'Latest request uses {round(100 * tokens.share)}% of the reported '
                     f'{tokens.context_window:,}-token context window.')
    else:
        lines.append('Codex model context window: not reported.')
    lines.append(f'Cumulative token usage: {tokens.cumulative_total:,}. This is not the current context size.')
    return lines
