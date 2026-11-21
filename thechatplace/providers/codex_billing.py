"""Explicit billing routes at every Codex authorization boundary (#661)."""
API_BASE = 'https://api.openai.com/v1'


def is_api(connection):
    return getattr(connection, 'billing_route', 'subscription') == 'api'


def account_matches(connection, response):
    from .codex_rpc import is_chatgpt_account
    if not is_api(connection):
        return is_chatgpt_account(response)
    return (isinstance(response, dict) and response.get('account') == {'type': 'apiKey'}
            and response.get('requiresOpenaiAuth') is True)


def route_config(connection):
    if is_api(connection):
        return {'model_provider': 'openai', 'model_providers': {},
                'forced_login_method': 'api', 'openai_base_url': API_BASE,
                'cli_auth_credentials_store': 'ephemeral',
                'service_tier': 'default', 'web_search': 'disabled',
                'features': {'hooks': False, 'multi_agent': False, 'goals': False,
                             'apps': False, 'plugins': False, 'memories': False}}
    return {'model_provider': 'openai', 'model_providers': {},
            'forced_login_method': 'chatgpt', 'openai_base_url': None,
            'chatgpt_base_url': 'https://chatgpt.com/backend-api/'}


def config_matches(connection, config):
    if not isinstance(config, dict):
        return False
    providers = config.get('model_providers', {})
    common = isinstance(providers, dict) and 'openai' not in providers
    if is_api(connection):
        required = route_config(connection)
        features = config.get('features')
        return (common and isinstance(features, dict)
                and all(features.get(k) is False for k in required['features'])
                and all(config.get(k) == v for k, v in required.items()
                        if k not in ('model_providers', 'features')))
    return (common and config.get('model_provider') in (None, 'openai')
            and config.get('forced_login_method') in (None, 'chatgpt')
            and config.get('openai_base_url') is None
            and config.get('chatgpt_base_url') in (None, 'https://chatgpt.com/backend-api/'))
