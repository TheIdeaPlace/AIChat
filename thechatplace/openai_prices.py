"""Standard USD text-token estimates, checked 2026-11-17 (#661).

Exact IDs only. No inference from a prefix, tier, or a future model name.
Reasoning tokens are part of output, never charged twice.
"""
from decimal import Decimal

PRICE_DATE = '2026-11-17'
SOURCES = {
    'gpt-5-nano': 'https://developers.openai.com/api/docs/models/gpt-5-nano',
    'gpt-5.4-nano': 'https://developers.openai.com/api/docs/models/gpt-5.4-nano',
    'gpt-5.4-mini': 'https://developers.openai.com/api/docs/models/gpt-5.4-mini',
}
# Input, cached input, output: USD per million tokens, standard service only.
PRICES = {
    'gpt-5-nano': ('0.05', '0.005', '0.40'),
    'gpt-5-nano-2025-08-07': ('0.05', '0.005', '0.40'),
    'gpt-5.4-nano': ('0.20', '0.02', '1.25'),
    'gpt-5.4-nano-2026-03-17': ('0.20', '0.02', '1.25'),
    'gpt-5.4-mini': ('0.75', '0.075', '4.50'),
    'gpt-5.4-mini-2026-03-17': ('0.75', '0.075', '4.50'),
}


class Unpriced(ValueError):
    """Safe, user-readable reason a paid request cannot start."""


def require_price(model, service_tier=None):
    if model not in PRICES:
        raise Unpriced(f'No API-credit price is known for model {model!r}. Nothing was sent.')
    if service_tier not in (None, 'default', 'standard'):
        raise Unpriced(f'No API-credit price is known for service {service_tier!r}. Nothing was sent.')
    return tuple(Decimal(x) for x in PRICES[model])


def estimate(model, counters, service_tier=None):
    rates = require_price(model, service_tier)
    fields = ('inputTokens', 'cachedInputTokens', 'outputTokens', 'reasoningOutputTokens')
    if any(type(counters.get(k)) is not int or not 0 <= counters[k] <= 2**63-1 for k in fields):
        raise ValueError('Cost not known: invalid OpenAI token usage.')
    inp, cached, out, reasoning = (counters[k] for k in fields)
    if cached > inp or reasoning > out or counters.get('cacheWriteInputTokens', 0) != 0:
        raise ValueError('Cost not known: unsupported OpenAI token usage.')
    return float(((inp-cached)*rates[0] + cached*rates[1] + out*rates[2]) / 1_000_000)


def cost_words(amount):
    from .billing import cost_words as words
    return 'estimated ' + words(amount) if amount is not None else 'cost not known'
