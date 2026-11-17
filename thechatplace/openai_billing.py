"""OpenAI credentials and conservative per-provider spending (#661)."""
from . import platform_paths


def save_api_key(key):
    if not isinstance(key, str) or not key.strip().startswith('sk-') or len(key.strip()) < 20:
        raise ValueError('An OpenAI API key starts with sk-.')
    key = key.strip()
    platform_paths.save_secret(platform_paths.OPENAI_API_KEY_SECRET, key)
    from .provider_storage import atomic_write_json
    from datetime import date
    atomic_write_json(platform_paths.app_data_dir() / 'openai-api-key-info.json',
                      {'ending': key[-4:], 'added': date.today().isoformat()})


def has_api_key():
    return platform_paths.has_secret(platform_paths.OPENAI_API_KEY_SECRET)


def remove_api_key():
    platform_paths.delete_secret(platform_paths.OPENAI_API_KEY_SECRET)
    (platform_paths.app_data_dir() / 'openai-api-key-info.json').unlink(missing_ok=True)
