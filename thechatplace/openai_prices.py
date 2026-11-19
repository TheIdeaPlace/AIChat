"""Dated standard USD text-token estimates (#661, #685).

Exact IDs only. Reasoning is included in output. Long-context families use
surcharge rates conservatively: app-server's cumulative counters cannot prove
that earlier requests in a session stayed in the short-context band.
"""
from datetime import datetime, timezone
from decimal import Decimal

PRICE_DATE = LIMITS_CHECKED = '2026-11-19'
# Input, cached input, output: USD per million tokens, standard service only.
PRICES = {
    'gpt-5-nano': ('0.05', '0.005', '0.40'),
    'gpt-5.4-nano': ('0.20', '0.02', '1.25'),
    'gpt-5.4-mini': ('0.75', '0.075', '4.50'),
    'gpt-5.4': ('2.50', '0.25', '15'),
    'gpt-5.5': ('5', '0.5', '30'),
    'gpt-5.6-sol': ('4', '0.4', '20'),
    'gpt-5.6-terra': ('2', '0.2', '12'),
    'gpt-5.6-luna': ('0.2', '0.02', '1.2'),
    'gpt-6-astra': ('10', '1', '50'),
    'gpt-6-sol': ('2', '0.2', '10'),
    'gpt-6-luna': ('0.1', '0.01', '0.5'),
    'gpt-6.1-sol': ('2', '0.1', '10'),
}
SOURCES = {model: 'https://developers.openai.com/api/docs/models/' + model for model in PRICES}
# Maximum INPUT, maximum output, not the combined context window. Checked on
# each model's own page in SOURCES, including all seven 5.6/6.x entries.
# For 5.4/5.5, input = context minus maximum output.
TOKEN_LIMITS = {
    'gpt-5-nano': (272_000, 128_000),
    'gpt-5.4-nano': (272_000, 128_000),
    'gpt-5.4-mini': (272_000, 128_000),
    'gpt-5.4': (922_000, 128_000),
    'gpt-5.5': (922_000, 128_000),
    'gpt-5.6-sol': (922_000, 128_000),
    'gpt-5.6-terra': (922_000, 128_000),
    'gpt-5.6-luna': (922_000, 128_000),
    'gpt-6-astra': (922_000, 128_000),
    'gpt-6-sol': (922_000, 128_000),
    'gpt-6-luna': (922_000, 128_000),
    'gpt-6.1-sol': (922_000, 128_000),
}
LONG_CONTEXT = set(PRICES) - {'gpt-5-nano', 'gpt-5.4-nano', 'gpt-5.4-mini'}
CACHE_WRITE_RATES = {
    model: str(Decimal(PRICES[model][0]) * Decimal('1.25'))
    for model in LONG_CONTEXT - {'gpt-5.4', 'gpt-5.5'}
}
# The model page says "until November 30, 2026", without a time or zone.
# Earliest possible start of that date is UTC+14: Nov 29 at 10:00 UTC.
EXPIRES = {'gpt-5.6-sol': datetime(2026, 11, 29, 10, tzinfo=timezone.utc)}
for alias, snapshot in {
    'gpt-5-nano': 'gpt-5-nano-2025-08-07',
    'gpt-5.4-nano': 'gpt-5.4-nano-2026-03-17',
    'gpt-5.4-mini': 'gpt-5.4-mini-2026-03-17',
    'gpt-5.4': 'gpt-5.4-2026-03-05',
    'gpt-5.5': 'gpt-5.5-2026-04-23',
}.items():
    PRICES[snapshot] = PRICES[alias]
    TOKEN_LIMITS[snapshot] = TOKEN_LIMITS[alias]
    SOURCES[snapshot] = SOURCES[alias]
    if alias in LONG_CONTEXT:
        LONG_CONTEXT.add(snapshot)


class Unpriced(ValueError):
    """No valid dated price for this exact model and service."""


def pricing_time():
    return datetime.now(timezone.utc)


def require_price(model, service_tier=None, *, as_of=None):
    if model not in PRICES or (model in EXPIRES and (as_of or pricing_time()) >= EXPIRES[model]):
        raise Unpriced(f'No API-credit price is known for model {model!r}.')
    if service_tier not in (None, 'default', 'standard'):
        raise Unpriced(f'No API-credit price is known for service {service_tier!r}.')
    return tuple(Decimal(x) for x in PRICES[model])


def is_priced(model, service_tier=None):
    try:
        require_price(model, service_tier)
        return True
    except Unpriced:
        return False


def _rates(model, as_of=None):
    inp, cached, out = require_price(model, as_of=as_of)
    write = Decimal(CACHE_WRITE_RATES.get(model, str(inp)))
    if model in LONG_CONTEXT:
        inp, cached, write, out = inp * 2, cached * 2, write * 2, out * Decimal('1.5')
    return inp, cached, write, out


def request_ceiling(model, context_window=None, *, as_of=None):
    inp, _, write, out = _rates(model, as_of)
    maximum_input, output = TOKEN_LIMITS[model]
    # Telemetry's combined context is not a maximum input override (#685).
    return float((maximum_input * max(inp, write) + output * out) / 1_000_000)


def model_choices(catalog=()):
    from dataclasses import replace
    from .providers.codex_models import CodexModel
    choices = {m.model: m for m in catalog}
    for i, model in enumerate(PRICES):
        if model not in choices and model in SOURCES and SOURCES[model].endswith('/' + model):
            choices[model] = CodexModel(model, model, ('low', 'medium', 'high'), 'low', i == 0)
    # Ultra automatically delegates work. API subagents remain blocked until
    # their usage can be attributed, even if the catalog advertises it.
    return tuple(replace(m, label=m.label.removesuffix(' (price unknown)') +
                         (' (price unknown)' if not is_priced(m.model) else ''),
                         efforts=tuple(e for e in m.efforts if e != 'ultra'),
                         default_effort=(m.default_effort if m.default_effort != 'ultra' else
                                         next((e for e in m.efforts if e != 'ultra'), 'low')),
                         is_default=m.model == 'gpt-5-nano')
                 for m in sorted(choices.values(), key=lambda m: m.model != 'gpt-5-nano')
                 if not m.efforts or any(e != 'ultra' for e in m.efforts))


def valid_counters(counters):
    fields = ('inputTokens', 'cachedInputTokens', 'outputTokens', 'reasoningOutputTokens')
    if not isinstance(counters, dict) or any(type(counters.get(k)) is not int or not 0 <= counters[k] <= 2**63-1 for k in fields):
        return False
    writes = counters.get('cacheWriteInputTokens', 0)
    return (type(writes) is int and 0 <= writes <= counters['inputTokens'] - counters['cachedInputTokens']
            and counters['cachedInputTokens'] <= counters['inputTokens']
            and counters['reasoningOutputTokens'] <= counters['outputTokens'])


def estimate(model, counters, service_tier=None, *, as_of=None):
    require_price(model, service_tier, as_of=as_of)
    if not valid_counters(counters):
        raise ValueError('Cost not known: invalid OpenAI token usage.')
    inp, cached, write, out = _rates(model, as_of)
    n, c, o = (counters[k] for k in ('inputTokens', 'cachedInputTokens', 'outputTokens'))
    w = counters.get('cacheWriteInputTokens', 0)
    if w and model not in CACHE_WRITE_RATES:
        raise ValueError('Cost not known: unsupported OpenAI cache-write usage.')
    return float(((n-c-w)*inp + c*cached + w*write + o*out) / 1_000_000)


def cost_words(amount):
    from .billing import cost_words as words
    return 'estimated ' + words(amount) if amount is not None else 'cost not known'
