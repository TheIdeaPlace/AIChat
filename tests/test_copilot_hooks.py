"""Finding Copilot CLI hooks that could approve its actions unasked (#352)."""
import json
from pathlib import Path

import pytest

from thechatplace import platform_paths
from thechatplace.providers.copilot_hooks import CantCheck, blocking_hooks

#: The real reader, before the fixture below replaces it.
POLICY_TEXTS = platform_paths.copilot_policy_texts
APPROVING = {"version": 1, "hooks": {"preToolUse": [{"type": "command", "bash": "echo"}]}}


@pytest.fixture(autouse=True)
def no_policy(monkeypatch):
    monkeypatch.setattr(platform_paths, "copilot_policy_texts", lambda: [])


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def home(tmp_path):
    return tmp_path / "copilot-home"


@pytest.fixture
def repo(tmp_path):
    folder = tmp_path / "repo"
    (folder / ".git").mkdir(parents=True)
    return folder


def test_nothing_found_when_there_are_no_hooks(home, repo):
    assert blocking_hooks(str(repo), home) == []


def test_a_repositorys_hook_file_counts(home, repo):
    path = write(repo / ".github" / "hooks" / "allow.json", APPROVING)
    assert blocking_hooks(str(repo), home) == [str(path)]


@pytest.mark.parametrize("name", [".github/copilot/settings.json",
                                  ".github/copilot/settings.local.json",
                                  ".claude/settings.json", ".claude/settings.local.json"])
@pytest.mark.parametrize("event", ["preToolUse", "PreToolUse", "permissionRequest",
                                   "PermissionRequest"])
def test_inline_hooks_in_a_repositorys_settings_count(home, repo, name, event):
    path = write(repo / name, {"hooks": {event: [{"command": "x"}]}})
    assert blocking_hooks(str(repo), home) == [str(path)]


@pytest.mark.parametrize("hooks", [{"postToolUse": [{"bash": "x"}]}, {"preToolUse": []},
                                   {"sessionStart": [{"type": "prompt", "prompt": "hi"}]}])
def test_the_users_own_hooks_that_cant_approve_dont_count(home, repo, hooks):
    write(home / "hooks" / "other.json", {"version": 1, "hooks": hooks})
    write(home / "installed-plugins" / "p" / "hooks.json", {"version": 1, "hooks": hooks})
    assert blocking_hooks(str(repo), home) == []


@pytest.mark.parametrize("hooks", [{"postToolUse": [{"bash": "x"}]},
                                   {"sessionStart": [{"bash": "curl example.invalid"}]},
                                   {"userPromptSubmitted": [{"command": "x"}]}])
def test_any_hook_in_the_project_counts(home, repo, hooks):
    """Copilot runs a project's hooks over ACP without the folder being
    trusted, so a cloned repository's hook would run its commands unasked."""
    path = write(repo / ".github" / "hooks" / "other.json", {"version": 1, "hooks": hooks})
    assert blocking_hooks(str(repo), home) == [str(path)]


def test_a_project_file_with_no_hooks_doesnt_count(home, repo):
    write(repo / ".github" / "hooks" / "empty.json", {"version": 1, "hooks": {"preToolUse": []}})
    write(repo / ".claude" / "settings.json", {"permissions": {"allow": []}})
    assert blocking_hooks(str(repo), home) == []


def test_the_repository_root_is_checked_from_a_folder_inside_it(home, repo):
    path = write(repo / ".github" / "hooks" / "allow.json", APPROVING)
    inner = repo / "src" / "deep"
    inner.mkdir(parents=True)
    assert blocking_hooks(str(inner), home) == [str(path)]


def test_nothing_above_the_repository_is_checked(home, repo):
    write(repo.parent / ".claude" / "settings.json", {"hooks": {"PreToolUse": [{"x": 1}]}})
    assert blocking_hooks(str(repo), home) == []


def test_outside_a_repository_only_the_folder_itself_is_checked(home, tmp_path):
    """A home folder's own .claude settings mustn't block every folder under it."""
    write(tmp_path / ".claude" / "settings.json", {"hooks": {"PreToolUse": [{"x": 1}]}})
    folder = tmp_path / "plain"
    path = write(folder / ".github" / "hooks" / "allow.json", APPROVING)
    assert blocking_hooks(str(folder), home) == [str(path)]
    assert blocking_hooks(str(tmp_path / "plain" / "sub"), home) == []


def test_the_users_hooks_and_settings_count(home, repo):
    hook = write(home / "hooks" / "mine.json", APPROVING)
    settings = write(home / "settings.json", {"hooks": {"permissionRequest": [{"bash": "x"}]}})
    assert blocking_hooks(str(repo), home) == [str(hook), str(settings)]


@pytest.mark.parametrize("where", ["market/plugin/hooks.json", "market/plugin/hooks/hooks.json",
                                   "plugin/hooks/extra.json"])
def test_an_installed_plugins_hooks_count(home, repo, where):
    path = write(home / "installed-plugins" / where, APPROVING)
    assert blocking_hooks(str(repo), home) == [str(path)]


def test_a_plugins_other_json_files_dont(home, repo):
    write(home / "installed-plugins" / "plugin" / "config.json", APPROVING)
    write(home / "installed-plugins" / "plugin" / "plugin.json", {"name": "p"})
    assert blocking_hooks(str(repo), home) == []


@pytest.mark.parametrize("manifest", ["plugin.json", ".claude-plugin/plugin.json"])
def test_hooks_in_a_plugins_manifest_count(home, repo, manifest):
    path = write(home / "installed-plugins" / "p" / manifest, APPROVING)
    assert blocking_hooks(str(repo), home) == [str(path)]


def test_a_hooks_file_a_manifest_names_counts(home, repo):
    folder = home / "installed-plugins" / "p"
    write(folder / "plugin.json", {"name": "p", "hooks": "./config/approve.json"})
    write(folder / "config" / "approve.json", APPROVING)
    assert blocking_hooks(str(repo), home) == [str(folder / "./config/approve.json")]


def test_a_big_node_modules_doesnt_hide_a_later_plugin(home, repo, monkeypatch):
    from thechatplace.providers import copilot_hooks
    monkeypatch.setattr(copilot_hooks, "_PLUGIN_ENTRIES", 100)
    modules = home / "installed-plugins" / "a-big" / "node_modules"
    modules.mkdir(parents=True)
    for n in range(300):
        (modules / f"f{n}.js").write_text("", encoding="utf-8")
    path = write(home / "installed-plugins" / "z-later" / "hooks.json", APPROVING)
    assert blocking_hooks(str(repo), home) == [str(path)]


def test_a_search_cut_short_refuses(home, repo, monkeypatch):
    from thechatplace.providers import copilot_hooks
    monkeypatch.setattr(copilot_hooks, "_PLUGIN_ENTRIES", 10)
    folder = home / "installed-plugins" / "p"
    folder.mkdir(parents=True)
    for n in range(20):
        (folder / f"f{n}.txt").write_text("", encoding="utf-8")
    with pytest.raises(CantCheck):
        blocking_hooks(str(repo), home)


def test_a_hook_file_too_big_to_read_refuses(home, repo, monkeypatch):
    monkeypatch.setattr(platform_paths, "COPILOT_SETTINGS_BYTES", 100)
    write(repo / ".github" / "hooks" / "big.json", {"version": 1, "pad": "x" * 200})
    with pytest.raises(CantCheck):
        blocking_hooks(str(repo), home)


def test_a_linked_working_folder_is_checked_where_it_leads(home, repo, tmp_path):
    path = write(repo / ".github" / "hooks" / "allow.json", APPROVING)
    (repo / "sub").mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(repo / "sub", target_is_directory=True)
    except OSError:
        pytest.skip("can't make a symbolic link here")
    found = [str(Path(where).resolve()) for where in blocking_hooks(str(link), home)]
    assert found == [str(path.resolve())]


def test_unreadable_json_counts_only_if_it_names_an_approving_hook(home, repo):
    named = write(repo / ".github" / "hooks" / "a.json", '{ // a comment\n "hooks": {"preToolUse"')
    write(repo / ".github" / "hooks" / "b.json", "{ not json, and no hook named")
    assert blocking_hooks(str(repo), home) == [str(named)]


def test_a_byte_order_mark_doesnt_hide_a_hook(home, repo):
    path = repo / ".github" / "hooks" / "bom.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(APPROVING).encode())
    assert blocking_hooks(str(repo), home) == [str(path)]


def test_an_organisations_policy_hooks_count(home, repo, monkeypatch):
    monkeypatch.setattr(platform_paths, "copilot_policy_texts", lambda: [
        ("policy-a", json.dumps(APPROVING)), ("policy-b", json.dumps({"hooks": {}})),
        ("policy-c", None)])  # one that couldn't be read can't be ruled out
    assert blocking_hooks(str(repo), home) == ["policy-a", "policy-c"]


def test_policy_files_are_read_from_their_folder(tmp_path, monkeypatch):
    if platform_paths.sys.platform != "win32":
        pytest.skip("the Windows policy folder")
    monkeypatch.setenv("ProgramData", str(tmp_path))
    write(tmp_path / "GitHub" / "Copilot" / "policy.d" / "org.json", APPROVING)
    texts = POLICY_TEXTS()
    assert any(where.endswith("org.json") and "preToolUse" in text for where, text in texts)
