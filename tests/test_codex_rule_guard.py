"""Synthetic execution-rule inventories never read the user's Codex home."""
from pathlib import Path

import pytest

from thechatplace import platform_paths
from thechatplace.providers.codex_owned import CodexOwnedThreads
from thechatplace.providers.codex_rpc import CodexRpcError
from tests.test_codex_owned import Connection


def layer(folder, kind='project', **extra):
    name = {'type': kind}
    name['dotCodexFolder' if kind == 'project' else 'file'] = str(
        folder if kind == 'project' else folder / 'config.toml')
    return {'name': name, **extra}


@pytest.mark.parametrize('kind', ['project', 'user', 'system'])
def test_every_supported_active_layer_refuses_any_rule_file(tmp_path, kind):
    rules = tmp_path / 'rules'; rules.mkdir()
    assert platform_paths.codex_rule_layers_clear([layer(tmp_path, kind)])
    (rules / 'saved.rules').write_text('prefix_rule(pattern=["synthetic"],decision="allow")')
    assert not platform_paths.codex_rule_layers_clear([layer(tmp_path, kind)])
    assert platform_paths.codex_rule_layers_clear([layer(tmp_path, kind, disabledReason='untrusted')])


@pytest.mark.parametrize('layers', [None, [], [{}], [{'name': []}],
    [{'name': {'type': 'future'}}], [{'name': {'type': []}}],
    [{'name': {'type': 'project', 'dotCodexFolder': 'relative'}}],
    [{'name': {'type': 'project', 'dotCodexFolder': '//remote/share'}}],
    [{'name': {'type': 'project', 'dotCodexFolder': '/path'}, 'disabledReason': False}],
    [{'name': {'type': 'sessionFlags'}}] * 65])
def test_uncertain_or_unbounded_inventory_is_refused(layers):
    assert not platform_paths.codex_rule_layers_clear(layers)


def test_missing_rules_and_nonrecursive_unrelated_files_are_allowed(tmp_path):
    inventory = [layer(tmp_path)]
    assert platform_paths.codex_rule_layers_clear(inventory)
    rules = tmp_path / 'rules'; rules.mkdir()
    (rules / 'readme.txt').write_text('not a rule')
    child = rules / 'nested'; child.mkdir()
    (child / 'not-loaded.rules').write_text('synthetic')
    assert platform_paths.codex_rule_layers_clear(inventory)


def test_rules_directory_scan_is_bounded(tmp_path, monkeypatch):
    class Entry:
        name = 'irrelevant'
    class Entries:
        def __enter__(self): return iter([Entry()] * 4097)
        def __exit__(self, *args): pass
    monkeypatch.setattr(platform_paths.os, 'scandir', lambda path: Entries())
    assert not platform_paths.codex_rule_layers_clear([layer(tmp_path)])


def test_unreadable_rule_inventory_is_refused(tmp_path, monkeypatch):
    def denied(path): raise PermissionError('private path must not escape')
    monkeypatch.setattr(platform_paths.os, 'scandir', denied)
    assert not platform_paths.codex_rule_layers_clear([layer(tmp_path)])


@pytest.mark.parametrize('after_lifecycle', [False, True])
def test_rules_never_grant_runtime_ownership(tmp_path, after_lifecycle):
    class RulesConnection(Connection):
        def request(self, method, params):
            result = super().request(method, params)
            if method == 'config/read':
                result['layers'] = [layer(tmp_path)]
                if after_lifecycle and any(m == 'thread/start' for m, p in self.calls):
                    rules = tmp_path / 'rules'; rules.mkdir(exist_ok=True)
                    (rules / 'new.rules').write_text('synthetic')
            return result
    if not after_lifecycle:
        rules = tmp_path / 'rules'; rules.mkdir()
        (rules / 'existing.rules').write_text('synthetic')
    connection = RulesConnection()
    with pytest.raises(CodexRpcError, match='saved command rules'):
        CodexOwnedThreads(connection).start(str(tmp_path), 'Synthetic')
    assert any(m == 'thread/start' for m, p in connection.calls) is after_lifecycle


def test_symlinked_rule_directory_is_uncertain(tmp_path, monkeypatch):
    original = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda self: (
        True if self == tmp_path / 'rules' else original(self)))
    assert not platform_paths.codex_rule_layers_clear([layer(tmp_path)])


def test_rules_added_after_lifecycle_are_refused_before_next_turn(tmp_path):
    from tests.test_codex_execution import runtime_connection
    c, proof, params = runtime_connection()
    original = c.request

    def request(method, arguments):
        result = original(method, arguments)
        if method == 'config/read':
            result['layers'] = [layer(tmp_path)]
        return result

    c.request = request
    c._verify_turn_route(params)
    rules = tmp_path / 'rules'; rules.mkdir()
    (rules / 'added.rules').write_text('synthetic')
    with pytest.raises(CodexRpcError, match='saved command rules'):
        c._verify_turn_route(params)


@pytest.mark.parametrize('kind', ['packagedDefaults', 'mdm', 'enterpriseManaged',
    'sessionFlags', 'legacyManagedConfigTomlFromFile', 'legacyManagedConfigTomlFromMdm'])
def test_recognized_sources_without_rule_folders_do_not_scan_disk(kind, monkeypatch):
    def unexpected(path): raise AssertionError('source has no rules directory')
    monkeypatch.setattr(platform_paths.os, 'scandir', unexpected)
    assert platform_paths.codex_rule_layers_clear([{'name': {'type': kind}}])
