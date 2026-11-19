"""OpenAI credentials and conservative per-provider spending (#661)."""
from . import platform_paths
from . import billing
import re
import threading

credentials_lock = threading.RLock()
_generation = 0


def key_generation():
    return _generation

KEY_PREFIX = 'sk-'
CONSOLE_BILLING_URL = 'https://platform.openai.com/settings/organization/billing/overview'


def looks_like_key(key):
    return isinstance(key, str) and bool(re.fullmatch(r'sk-(?!ant-)(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}', key.strip()))


def _info_path():
    return platform_paths.app_data_dir() / 'openai-api-key-info.json'


def save_api_key(key):
    global _generation
    with credentials_lock:
        billing.save_named_api_key(key, platform_paths.OPENAI_API_KEY_SECRET, _info_path(),
                                  looks_like_key, 'OpenAI', KEY_PREFIX)
        _generation += 1


def api_key_status(now=None):
    return (billing.named_key_status('OpenAI', billing._key_info(_info_path()), now)
            if has_api_key() else 'OpenAI: No key')


def has_api_key():
    return platform_paths.has_secret(platform_paths.OPENAI_API_KEY_SECRET)


def remove_api_key():
    global _generation
    with credentials_lock:
        platform_paths.delete_secret(platform_paths.OPENAI_API_KEY_SECRET)
        _generation += 1
        try:
            _info_path().unlink(missing_ok=True)
        except OSError:
            pass


def chat_cost_words(ledger, chat_id):
    totals = ledger.totals()
    if chat_id in totals.unpriced_all_time:
        return spending_words(totals, chat_id)
    cost, held, unknown = ledger.chat_cost(chat_id)
    words = billing.cost_words(cost if cost or not unknown else None, complete=not unknown)
    return words if words == billing.COST_NOT_KNOWN else 'estimated ' + words


def token_words(tokens):
    if tokens is None:
        return 'tokens not reported'
    return f"used {tokens.get('inputTokens', 0) + tokens.get('outputTokens', 0):,} tokens"


def token_details(tokens):
    return (f"{token_words(tokens)} ({tokens.get('inputTokens', 0):,} input, "
            f"{tokens.get('cachedInputTokens', 0):,} cached input, "
            f"{tokens.get('cacheWriteInputTokens', 0):,} cache writes, "
            f"{tokens.get('outputTokens', 0):,} output; reasoning included)")


def spending_words(totals, chat_id=None):
    book = totals.unpriced_all_time if chat_id else totals.unpriced_usage
    tokens = book.get(chat_id) if chat_id else {
        field: sum(row.get(field, 0) for row in book.values())
        for field in ('inputTokens', 'cachedInputTokens', 'cacheWriteInputTokens', 'outputTokens')}
    cost = totals.chat_all_time.get(chat_id, 0) if chat_id else totals.spent
    words = f'priced spending ${cost:,.2f}'
    if (chat_id in book) if chat_id else bool(book):
        missing = totals.unpriced_missing_all_time if chat_id else totals.unpriced_missing
        incomplete = chat_id in missing if chat_id else bool(missing)
        details = token_details(tokens)
        if incomplete:
            details = ('recorded ' + details.removeprefix('used ') + '; some tokens not reported'
                       if tokens.get('inputTokens', 0) + tokens.get('outputTokens', 0) else 'tokens not reported')
        words += ', plus unpriced usage: ' + details + ', cost unknown'
    return words
