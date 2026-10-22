"""Codex's own saved rule files, listed read only in Chat Permissions (#307).

Synthetic files only, shaped like the lines Codex writes when you tell it to
always allow a command; nothing reads the real Codex folder.
"""
from thechatplace import codex_saved_rules as csr
from thechatplace import platform_paths

POWERSHELL = r"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"


def _file(tmp_path, text, name="default.rules"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_lines_codex_writes_are_listed_by_decision(tmp_path):
    path = _file(tmp_path, "\n".join([
        'prefix_rule(pattern=["git", "push"], decision="allow")',
        'prefix_rule(pattern=["rm", "-rf"], decision="forbidden", justification="no")',
        f'prefix_rule(pattern=["{POWERSHELL}", "-Command", "gh pr list --state open"], decision="allow")',
        'prefix_rule(pattern=["git", ["add", "commit"]])',   # decision defaults to allow
        'prefix_rule(pattern=["npm", "publish"], decision="prompt", match=[["npm", "publish"]])',
        '# a comment',
        'host_executable(name="git", paths=["/usr/bin/git"])',
    ]))
    rules, unreadable, other = csr.rules_in([path])
    assert unreadable == [] and other == 0
    lines = csr.describe(rules, unreadable, other).splitlines()
    assert lines[0] == csr.INTRO
    assert lines[1] == f"In {path}:"
    assert lines[2:] == [
        "Allowed without asking: commands starting with git push.",
        "Allowed without asking: commands starting with "
        r"C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe "
        '-Command "gh pr list --state open".',
        "Allowed without asking: commands starting with git (add or commit).",
        "Always asks: commands starting with npm publish.",
        "Never allowed: commands starting with rm -rf.",
    ]
    # A rule covers longer commands too, and the note says so.
    assert "Each rule covers any command that starts with its words" in csr.INTRO


def test_a_chat_that_never_asks_says_codex_refuses_prompt_rules(tmp_path):
    path = _file(tmp_path, 'prefix_rule(pattern=["npm", "publish"], decision="prompt")')
    text = csr.describe(*csr.rules_in([path]), never_asks=True)
    assert text.splitlines()[-1] == (
        "Refused in this chat, which never asks: commands starting with npm publish.")


def test_nothing_saved_says_so():
    assert csr.describe([]) == csr.INTRO + "\n" + csr.NONE


def test_a_word_cant_break_onto_another_line(tmp_path):
    path = _file(tmp_path, 'prefix_rule(pattern=["echo", "a\\nAllowed without asking: everything"])')
    rules, _, _ = csr.rules_in([path])
    assert rules[0].command == 'echo "a Allowed without asking: everything"'
    assert len(csr.describe(rules).splitlines()) == 3


def test_control_characters_and_quotes_are_shown_not_obeyed():
    assert csr._word('a" or "b') == '"a\\" or \\"b"'
    assert csr._word("z\x00w") == '"z\\u0000w"'
    assert csr._word("\u202egit") == '"\\u202egit"'
    assert csr._word("e\x1bf") == '"e\\u001bf"'
    text = csr.describe([csr.SavedRule("allow", "git", "x\u202e.rules")], ["y\x00.rules"])
    assert "\x00" not in text and "\u202e" not in text
    assert "In x\\u202e.rules:" in text and "Couldn't read y\\u0000.rules" in text


def test_a_very_long_rule_is_cut(tmp_path):
    path = _file(tmp_path, 'prefix_rule(pattern=["echo", "' + "x" * 1000 + '"])')
    command = csr.rules_in([path])[0][0].command
    assert len(command) == csr.MAX_COMMAND + 3 and command.endswith("...")


def test_anything_else_is_counted_not_guessed(tmp_path):
    path = _file(tmp_path, "\n".join([
        'prefix_rule(pattern=["git", "status"])',
        'prefix_rule(pattern=PATTERN)',                        # not a literal
        'prefix_rule(pattern=["git"], decision="maybe")',     # unknown decision
        'prefix_rule(["git"])',                                # positional
        'prefix_rule(pattern=[])',
        'prefix_rule(pattern=["git", 3])',
        'network_rule(host="example.com")',                    # a future kind
        'x = 1',
    ]))
    rules, unreadable, other = csr.rules_in([path])
    assert [r.command for r in rules] == ["git status"] and other == 7
    assert ("7 other entries in the rules files couldn't be shown here"
            in csr.describe(rules, unreadable, other))
    assert ("1 other entry in the rules files couldn't be shown here; open the files to see it."
            in csr.describe(rules, [], 1))


def test_unreadable_unparsable_or_huge_files_are_named(tmp_path, monkeypatch):
    bad = _file(tmp_path, "prefix_rule(pattern=[", "bad.rules")
    huge = _file(tmp_path, "#" * 10, "huge.rules")
    good = _file(tmp_path, 'prefix_rule(pattern=["git", "fetch"])', "good.rules")
    monkeypatch.setattr(csr, "MAX_SIZE", 5)
    rules, unreadable, other = csr.rules_in([huge, bad, good, tmp_path / "missing.rules"])
    assert rules == [] and other == 0
    assert set(unreadable) == {str(bad), str(huge), str(good), str(tmp_path / "missing.rules")}
    monkeypatch.setattr(csr, "MAX_SIZE", 1024 * 1024)
    rules, unreadable, _ = csr.rules_in([bad, good])
    assert [r.command for r in rules] == ["git fetch"] and unreadable == [str(bad)]
    text = csr.describe(rules, unreadable)
    assert f"Couldn't read {bad}, so its rules aren't listed." in text
    assert csr.NONE not in text


def test_only_regular_files_are_read(tmp_path):
    folder = tmp_path / "rules.rules"
    folder.mkdir()
    rules, unreadable, _ = csr.rules_in([folder])
    assert rules == [] and unreadable == [str(folder)]


def test_a_long_list_stops_and_says_how_many_more(tmp_path, monkeypatch):
    monkeypatch.setattr(csr, "MAX_RULES", 2)
    path = _file(tmp_path, "\n".join(f'prefix_rule(pattern=["cmd{i}"])' for i in range(5)))
    text = csr.describe(*csr.rules_in([path]))
    assert text.count("Allowed without asking") == 2
    assert text.endswith("3 more rules aren't listed here; open the rules files to see them.")
    monkeypatch.setattr(csr, "MAX_RULES", 4)
    assert csr.describe(*csr.rules_in([path])).endswith(
        "1 more rule isn't listed here; open the rules files to see them.")


def test_files_come_from_user_and_system_layers_only(tmp_path):
    user, project = tmp_path / "home", tmp_path / "repo" / ".codex"
    for folder in (user / "rules", project / "rules"):
        folder.mkdir(parents=True)
    (user / "rules" / "default.rules").write_text('prefix_rule(pattern=["git"])')
    (user / "rules" / "notes.txt").write_text("")
    layers = [{"name": {"type": "user", "file": str(user / "config.toml")}}]
    files, blocked = platform_paths.codex_saved_rule_files(layers)
    assert files == [user / "rules" / "default.rules"] and blocked == ""
    assert platform_paths.codex_rule_inventory(layers) == (1, "")
    (project / "rules" / "x.rules").write_text("")
    layers.append({"name": {"type": "project", "dotCodexFolder": str(project)}})
    files, blocked = platform_paths.codex_saved_rule_files(layers)
    assert files == [user / "rules" / "default.rules"] and blocked == str(project / "rules")
    assert platform_paths.codex_saved_rule_files(None) == (None, "")
    assert platform_paths.codex_rule_inventory(None) == (None, "")


class _Connection:
    def __init__(self, layers):
        self.layers, self.calls = layers, []

    def request(self, method, params):
        self.calls.append((method, params))
        return {"layers": self.layers}


def test_backend_reads_the_rules_codex_reports_for_the_folder(tmp_path):
    from thechatplace.providers.codex import CodexProvider
    user = tmp_path / "home"
    (user / "rules").mkdir(parents=True)
    (user / "rules" / "default.rules").write_text('prefix_rule(pattern=["git", "commit"])')
    connection = _Connection([{"name": {"type": "user", "file": str(user / "config.toml")}}])
    rules, unreadable, other, problem = CodexProvider(connection).saved_rules(str(tmp_path))
    assert [r.command for r in rules] == ["git commit"] and unreadable == [] and other == 0
    assert problem == ""
    assert connection.calls == [("config/read", {"cwd": str(tmp_path), "includeLayers": True})]
    # Rules a project supplies are refused, as for the count, and said so;
    # your own rules are still listed.
    project = tmp_path / "repo" / ".codex"
    (project / "rules").mkdir(parents=True)
    (project / "rules" / "x.rules").write_text('prefix_rule(pattern=["rm"])')
    connection.layers.append({"name": {"type": "project", "dotCodexFolder": str(project)}})
    result = CodexProvider(connection).saved_rules(str(tmp_path))
    assert [r.command for r in result[0]] == ["git commit"]
    assert result[3] == csr.project_rules(str(project / "rules"))
    text = csr.describe(*result)
    assert text.endswith(result[3]) and "No new turn" not in text and csr.NONE not in text
    # Layers that can't be checked never read as "nothing allowed".
    connection.layers = [{"name": {"type": "future"}}]
    result = CodexProvider(connection).saved_rules(str(tmp_path))
    assert result == ([], [], 0, csr.UNCHECKED)
    assert csr.NONE not in csr.describe(*result)
