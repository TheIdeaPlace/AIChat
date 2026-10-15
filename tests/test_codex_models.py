from copy import deepcopy

import pytest

from thechatplace.providers.codex_models import read_models
from thechatplace.providers.codex_rpc import CodexRpcError


def model(name='model-one', **changes):
    return dict(id=name, model=name, displayName='Synthetic model', hidden=False,
                isDefault=True, defaultReasoningEffort='future-effort',
                supportedReasoningEfforts=[{'reasoningEffort': 'future-effort'}], **changes)


class Catalog:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.calls = []

    def request(self, method, params):
        self.calls.append((method, deepcopy(params)))
        return next(self.pages)


def test_complete_catalog_uses_advertised_efforts_and_opaque_pagination():
    connection = Catalog([{'data': [model()], 'nextCursor': 'opaque-next'},
                          {'data': [model('two')], 'nextCursor': None}])
    choices = read_models(connection)
    assert [choice.model for choice in choices] == ['model-one', 'two']
    assert choices[0].efforts == ('future-effort',)
    assert choices[0].default_effort == 'future-effort'
    assert connection.calls == [('model/list', {'cursor': None, 'limit': 100, 'includeHidden': False}),
                                ('model/list', {'cursor': 'opaque-next', 'limit': 100, 'includeHidden': False})]


def test_hidden_and_non_text_models_are_not_picker_choices():
    hidden, audio = model('hidden'), model('audio')
    hidden['hidden'] = True
    audio['inputModalities'] = ['audio']
    assert read_models(Catalog([{'data': [hidden, audio, model()]}]))[0].model == 'model-one'


@pytest.mark.parametrize('field,value', [
    ('model', 'injected\nname'), ('isDefault', 1), ('hidden', 'false'),
    ('defaultReasoningEffort', 'unadvertised'), ('inputModalities', 'text'),
    ('supportedReasoningEfforts', [{'reasoningEffort': 'future-effort'},
                                  {'reasoningEffort': 'future-effort'}]),
])
def test_invalid_choices_are_rejected_before_publishing(field, value):
    row = model()
    row[field] = value
    with pytest.raises(CodexRpcError):
        read_models(Catalog([{'data': [model('valid')], 'nextCursor': 'next'},
                            {'data': [row]}]))


@pytest.mark.parametrize('last', [
    {'data': [model()], 'nextCursor': None},
    {'data': [], 'nextCursor': 'next'}, {'data': [], 'nextCursor': 3},
    {'data': None},
])
def test_repeated_choices_and_invalid_pagination_never_publish_partial_catalog(last):
    with pytest.raises(CodexRpcError):
        read_models(Catalog([{'data': [model()], 'nextCursor': 'next'}, last]))


def test_catalog_page_limit_is_bounded():
    pages = [{'data': [], 'nextCursor': str(index)} for index in range(101)]
    connection = Catalog(pages)
    with pytest.raises(CodexRpcError, match='supported size'):
        read_models(connection)
    assert len(connection.calls) == 100


def test_provider_catalog_discovery_never_starts_owned_execution():
    from thechatplace.providers.codex import CodexProvider
    connection = Catalog([{'data': [model()]}])
    provider = CodexProvider(connection)
    assert provider.model_choices()[0].model == 'model-one'
    assert provider.runtime is None
    assert [method for method, _ in connection.calls] == ['model/list']
