"""OpenAI credentials and conservative per-provider spending (#661)."""
from . import platform_paths
from . import billing
import re

KEY_PREFIX = 'sk-'
CONSOLE_BILLING_URL = 'https://platform.openai.com/settings/organization/billing/overview'


def looks_like_key(key):
    return isinstance(key, str) and bool(re.fullmatch(r'sk-(?!ant-)(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}', key.strip()))


def _info_path():
    return platform_paths.app_data_dir() / 'openai-api-key-info.json'


def save_api_key(key):
    billing.save_named_api_key(key, platform_paths.OPENAI_API_KEY_SECRET, _info_path(),
                              looks_like_key, 'OpenAI', KEY_PREFIX)


def api_key_status(now=None):
    return (billing.named_key_status('OpenAI', billing._key_info(_info_path()), now)
            if has_api_key() else 'OpenAI: No key')


def has_api_key():
    return platform_paths.has_secret(platform_paths.OPENAI_API_KEY_SECRET)


def remove_api_key():
    platform_paths.delete_secret(platform_paths.OPENAI_API_KEY_SECRET)
    try:
        _info_path().unlink(missing_ok=True)
    except OSError:
        pass
