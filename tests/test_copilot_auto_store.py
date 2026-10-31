"""What The Chat Place keeps for each Copilot chat (#426): Auto, and the
commands allowed for it, in copilot-auto.json; and the provider's use of it."""
from types import SimpleNamespace

import pytest

from thechatplace import platform_paths
from thechatplace.own_store import OwnSession
from thechatplace.providers import copilot_auto_store as store
from thechatplace.providers.copilot import CopilotProvider
from thechatplace.providers.copilot_runtime import ALLOW_ONCE, CopilotChatError

ONE = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def data(tmp_path, monkeypatch):
    folder = tmp_path / "data"
    folder.mkdir()
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: folder)
    return folder


def test_nothing_kept_is_no_auto_and_no_commands(data):
    assert store.load(ONE) == (False, (), "")


def test_auto_and_commands_are_kept_per_chat(data):
    assert store.save_auto(ONE, True) == ""
    kept, problem = store.add(ONE, "npm test")
    assert [k.command for k in kept] == ["npm test"] and not problem
    store.add(ONE, "npm test")  # once only
    store.add(ONE, "npm run build")
    auto, kept, _ = store.load(ONE)
    assert auto and [k.command for k in kept] == ["npm test", "npm run build"]
    assert store.load("other-chat")[:2] == (False, ())
    kept, _ = store.remove(ONE, "npm test")
    assert [k.command for k in kept] == ["npm run build"]
    kept, _ = store.remove(ONE, None)
    assert kept == () and store.load(ONE)[0]


def test_a_file_that_isnt_what_was_written_is_set_aside_not_overwritten(data):
    (data / "copilot-auto.json").write_text("not json", encoding="utf-8")
    auto, kept, problem = store.load(ONE)
    assert (auto, kept, problem) == (False, (), store.SET_ASIDE)
    assert [p.name.startswith("copilot-auto.json.bad-") for p in data.iterdir()] == [True]


def test_entries_that_arent_the_apps_are_left_out(data):
    (data / "copilot-auto.json").write_text(
        '{"version": 1, "chats": {"../evil": {"auto": true}, "%s": {"auto": "yes", '
        '"allowed": [{"command": "npm test"}, {"command": " padded "}, {"command": 7}, "x"]}}}'
        % ONE, encoding="utf-8")
    auto, kept, problem = store.load(ONE)
    assert not auto and [k.command for k in kept] == ["npm test"] and not problem


def test_a_file_that_cant_be_opened_is_not_saved_over(data, monkeypatch):
    (data / "copilot-auto.json").write_text('{"version": 1, "chats": {}}', encoding="utf-8")

    def locked(self):
        raise PermissionError("locked")
    monkeypatch.setattr(type(data), "read_bytes", locked)
    assert store.load(ONE)[2] == store.UNREADABLE
    assert store.add(ONE, "npm test") == ((), store.NOT_SAVED)


# -- the provider ------------------------------------------------------------------------


class Runtime:
    """A stand-in runtime: records what the provider tells it."""

    def __init__(self, candidate="npm test", fail_answer=False):
        self.calls, self.candidate, self.fail_answer = [], candidate, fail_answer
        self.kept = set()

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args))
            if name == "chat_candidate":
                return self.candidate
            if name == "allowed_here":
                return frozenset(self.kept)
            if name == "keep_allowed":
                self.kept = set(args[1])
            if name == "answer" and self.fail_answer:
                raise CopilotChatError("Copilot isn't running.")
            return None
        return call


def _record(tmp_path):
    return OwnSession(ONE, "Mine", str(tmp_path), provider="copilot", started=True)


def test_allow_for_this_chat_keeps_the_command_then_allows_it_once(tmp_path, data):
    runtime = Runtime()
    provider = CopilotProvider(tmp_path, runtime=runtime)
    request = SimpleNamespace(request_id="perm-1")
    rule = provider.chat_rule_candidate_owned(_record(tmp_path), request)
    assert rule.command == "npm test" and "npm test" in rule.description
    provider.allow_for_chat_owned(_record(tmp_path), request)
    assert runtime.kept == {"npm test"}
    assert runtime.calls[-1] == ("answer", (ONE, "perm-1", ALLOW_ONCE))
    assert [k.command for k in provider.chat_rules_owned(_record(tmp_path))] == ["npm test"]


def test_a_command_that_cant_be_kept_isnt(tmp_path, data):
    provider = CopilotProvider(tmp_path, runtime=Runtime(candidate=""))
    request = SimpleNamespace(request_id="perm-1")
    assert provider.chat_rule_candidate_owned(_record(tmp_path), request) is None
    with pytest.raises(CopilotChatError):
        provider.allow_for_chat_owned(_record(tmp_path), request)
    assert store.load(ONE)[1] == ()


def test_if_the_answer_cant_go_the_command_isnt_kept(tmp_path, data):
    runtime = Runtime(fail_answer=True)
    provider = CopilotProvider(tmp_path, runtime=runtime)
    with pytest.raises(CopilotChatError):
        provider.allow_for_chat_owned(_record(tmp_path), SimpleNamespace(request_id="perm-1"))
    assert store.load(ONE)[1] == ()
    assert runtime.kept == set()


def test_removing_tells_the_runtime_first(tmp_path, data):
    store.add(ONE, "npm test")
    store.add(ONE, "npm run build")
    runtime = Runtime()
    provider = CopilotProvider(tmp_path, runtime=runtime)
    kept = provider.remove_chat_rule_owned(_record(tmp_path), "npm test")
    assert [k.command for k in kept] == ["npm run build"]
    assert ("keep_allowed", (ONE, ["npm run build"])) in runtime.calls
    with pytest.raises(ValueError):
        provider.remove_chat_rule_owned(_record(tmp_path), "npm test")
    assert provider.remove_chat_rule_owned(_record(tmp_path), None) == ()


def test_opening_a_chat_gives_back_its_commands_and_auto(tmp_path, data):
    store.add(ONE, "npm test")
    store.save_auto(ONE, True)
    runtime = Runtime()
    provider = CopilotProvider(tmp_path, runtime=runtime)
    provider.prepare_owned(_record(tmp_path))
    names = [name for name, _ in runtime.calls]
    # The commands before the chat opens (it may ask at once); Auto after.
    assert names.index("keep_allowed") < names.index("prepare") < names.index("set_auto")
    assert ("set_auto", (ONE, True)) in runtime.calls


def test_auto_is_kept_when_turned_on_and_off(tmp_path, data):
    provider = CopilotProvider(tmp_path, runtime=Runtime())
    provider.set_auto_owned(_record(tmp_path), True)
    assert store.load(ONE)[0]
    provider.set_auto_owned(_record(tmp_path), False)
    assert not store.load(ONE)[0]


def test_auto_isnt_kept_if_the_runtime_refuses_it(tmp_path, data):
    class Refusing(Runtime):
        def __getattr__(self, name):
            if name == "set_auto":
                def refuse(*args):
                    raise CopilotChatError("not in agent mode")
                return refuse
            return super().__getattr__(name)
    provider = CopilotProvider(tmp_path, runtime=Refusing())
    with pytest.raises(CopilotChatError):
        provider.set_auto_owned(_record(tmp_path), True)
    assert not store.load(ONE)[0]


def test_a_kept_command_already_loaded_stays_when_the_file_cant_be_read(tmp_path, data,
                                                                        monkeypatch):
    """Claude's review of Auto: add() returns no commands when the file
    can't be opened; the ones the chat already had stay allowed."""
    runtime = Runtime()
    runtime.kept = {"npm run build"}
    monkeypatch.setattr(store, "add", lambda native_id, command: ((), store.NOT_SAVED))
    provider = CopilotProvider(tmp_path, runtime=runtime)
    provider.allow_for_chat_owned(_record(tmp_path), SimpleNamespace(request_id="perm-1"))
    assert runtime.kept == {"npm run build", "npm test"}


def test_auto_saved_for_a_chat_that_reopens_in_another_mode_is_turned_off(tmp_path, data):
    store.save_auto(ONE, True)

    class Refusing(Runtime):
        def __getattr__(self, name):
            if name == "set_auto":
                def refuse(*args):
                    raise CopilotChatError("not in agent mode")
                return refuse
            return super().__getattr__(name)
    runtime = Refusing()
    provider = CopilotProvider(tmp_path, runtime=runtime)
    provider.prepare_owned(_record(tmp_path))
    assert not store.load(ONE)[0]
    assert ("add_notice", (ONE, "not in agent mode")) in runtime.calls
