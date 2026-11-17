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
    cost, held, unknown = ledger.chat_cost(chat_id)
    words = billing.cost_words(cost if cost or not unknown else None, complete=not unknown)
    return words if words == billing.COST_NOT_KNOWN else 'estimated ' + words
