"""Contracts, policy, store, drift, wording, and the rule that the shared UI names no provider."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from thechatplace import policy, ui_text
from thechatplace.contracts import (OWNER_ONLY, Capability, ChatInfo, ChatRef, Decision, Message, OwnedChat,
                                    effective_capabilities)
from thechatplace.drift import MAX_NAMES, DriftCounters
from thechatplace.integrations.agents import AGENTS
from thechatplace.integrations.claude import ClaudeIntegration
from thechatplace.integrations.codex import CodexIntegration
from thechatplace.store import Store

ROOT = Path(__file__).parent.parent / "thechatplace"
ALL = frozenset(Capability)


def test_an_agent_advertising_something_cant_establish_ownership():
    assert not (effective_capabilities(ALL, ALL, False) & OWNER_ONLY)


def test_nothing_is_on_unless_implemented_and_advertised():
    implemented = frozenset({Capability.READ, Capability.SEND})
    advertised = frozenset({Capability.SEND, Capability.USAGE})
    assert effective_capabilities(implemented, advertised, True) == {Capability.SEND}
    assert effective_capabilities(implemented, None, True) == implemented


@pytest.mark.parametrize("options", [
    None, "nope", [], [{"optionId": "a", "kind": "allow_once"}],
    [{"optionId": "a", "kind": "allow_always"}], [{"kind": "reject_once"}],
    [{"optionId": 7, "kind": "reject_once"}]])
def test_no_one_time_denial_means_the_request_is_refused(options):
    assert policy.read_acp_options(options) is None


def test_lasting_choices_are_never_offered():
    found = policy.read_acp_options([
        {"optionId": "always", "kind": "allow_always", "name": "Allow once"},
        {"optionId": "never", "kind": "reject_always"},
        {"optionId": "no", "kind": "reject_once"}])
    assert found.decisions == (Decision.DENY,)


def test_a_decision_that_wasnt_offered_counts_as_deny():
    assert policy.checked_decision(Decision.ALLOW_ONCE, (Decision.DENY,)) is Decision.DENY
    assert policy.checked_decision("allow_once", (Decision.ALLOW_ONCE, Decision.DENY)) is Decision.DENY


def test_mode_and_option_policy():
    assert policy.mode_problem("bad", {"good": 1}) and policy.mode_problem("good", {"good": 1}) is None
    assert policy.mode_problem("", {"good": 1})  # saying nothing isn't being safe
    assert policy.mode_problem("anything", {}) is None
    assert policy.option_problem({"allow_all": "on"}, {"allow_all": ("on",)})
    assert policy.option_problem({"allow_all": "off"}, {"allow_all": ("on",)}) is None


def test_drift_counts_are_kept_apart_limited_and_cleaned():
    drift = DriftCounters()
    drift.unknown("a b\nc" * 100)
    drift.ignored("x")
    drift.malformed("y")
    report = drift.snapshot()
    assert list(report["ignored"]) == ["x"] and list(report["malformed"]) == ["y"]
    name = next(iter(report["unknown"]))
    assert " " not in name and "\n" not in name and len(name) <= 80
    for index in range(MAX_NAMES * 2):
        drift.ignored(f"n{index}")
    assert len(drift.snapshot()["ignored"]) == MAX_NAMES


def test_store_round_trip_and_atomic_chats(tmp_path):
    store = Store(tmp_path)
    chat = OwnedChat(ChatRef("x", "1"), "T", "F", {"mode": "m"}, 2, 1.0, 2.0)
    store.save_chat(chat)
    again = Store(tmp_path).get_chat(chat.ref)
    assert again == chat
    assert not list(tmp_path.glob("*.tmp"))


def test_a_damaged_store_is_set_aside_not_overwritten(tmp_path):
    (tmp_path / "chats.json").write_text("{damaged", encoding="utf-8")
    store = Store(tmp_path)
    assert store.owned_keys() == set()
    store.save_chat(OwnedChat(ChatRef("x", "1"), "T", "F"))
    kept = list(tmp_path.glob("chats.bad-*"))
    assert kept and kept[0].read_text(encoding="utf-8") == "{damaged"


def test_a_removed_integrations_chats_are_kept_but_not_listed(tmp_path):
    store = Store(tmp_path)
    store.save_chat(OwnedChat(ChatRef("gone", "1"), "T", "F"))
    store.save_chat(OwnedChat(ChatRef("here", "2"), "T", "F"))
    assert [c.ref.integration for c in store.chats(["here"])] == ["here"]
    store.save_chat(OwnedChat(ChatRef("here", "3"), "T", "F"))
    assert Store(tmp_path).get_chat(ChatRef("gone", "1")) is not None


def test_kept_history_survives_a_line_cut_short(tmp_path):
    store = Store(tmp_path)
    ref = ChatRef("x", "1")
    store.append_history(ref, Message("user", "One"))
    with store._history_path(ref).open("ab") as handle:
        # Cut in the middle of a multi-byte character, as a crash can leave it.
        handle.write('{"role": "assistant", "text": "é'.encode("utf-8")[:-1])
    assert store.read_history(ref) == [Message("user", "One")]
    # And what's written next isn't lost by joining onto the cut line.
    store.append_history(ref, Message("user", "Two"))
    assert store.read_history(ref) == [Message("user", "One"), Message("user", "Two")]


def test_a_store_that_cant_be_read_or_set_aside_isnt_overwritten(tmp_path, monkeypatch):
    (tmp_path / "chats.json").write_text("{damaged", encoding="utf-8")
    real = __import__("os").replace

    def refuses(source, target):
        if str(source).endswith("chats.json"):
            raise PermissionError("held")
        return real(source, target)
    monkeypatch.setattr("thechatplace.store.os.replace", refuses)
    store = Store(tmp_path)
    with pytest.raises(OSError):
        store.save_chat(OwnedChat(ChatRef("x", "1"), "T", "F"))
    assert (tmp_path / "chats.json").read_text(encoding="utf-8") == "{damaged"


def test_a_message_read_and_held_shows_once():
    from thechatplace.service import _merged
    one, two = Message("user", "One"), Message("assistant", "Two")
    assert _merged([one, two], [two]) == [one, two]
    assert _merged([one], [two]) == [one, two]
    assert _merged([one, one], [one]) == [one, one]


def test_turn_details_say_whatever_is_known():
    assert "Nothing has been reported" in ui_text.turn_details("", (), None)
    text = ui_text.turn_details("Reading", (("Step", "completed"),), (10, 100))
    assert "Reading" in text and "Step (completed)" in text and "10%" in text


def test_enabled_defaults_and_persists(tmp_path):
    store = Store(tmp_path)
    assert store.enabled("x", True) and not store.enabled("y", False)
    store.set_enabled("y", True)
    assert Store(tmp_path).enabled("y", False)


def test_rows_say_who_where_and_state():
    info = ChatInfo(ChatRef("x", "1"), "Title", "F", "Prov", "Kind", True, capabilities=frozenset({Capability.SEND}))
    assert ui_text.chat_row(info) == "Title, Prov, Kind, Idle, F"
    assert "Working" in ui_text.chat_row(info, busy=True)
    assert "Needs you" in ui_text.chat_row(info, busy=True, needs_you=True)
    read_only = ChatInfo(ChatRef("x", "1"), "T", provider="P", kind="K")
    assert "Read only" in ui_text.chat_row(read_only)


def test_message_rows_and_reading():
    long = Message("assistant", "word " * 400)
    assert ui_text.message_row(long, "Prov").startswith("Prov: ") and len(ui_text.message_row(long, "Prov")) < 300
    assert "press Enter" in ui_text.message_spoken(long, "Prov")
    assert ui_text.author(Message("user", "x"), "Prov") == "You"
    assert ui_text.author(Message("note", "x"), "Prov") == "Note"


def test_permission_text_keeps_the_agents_words_apart():
    text = ui_text.permission_text("Asked.", "rm -rf", "Careful.")
    assert text.index("Asked.") < text.index("rm -rf") < text.index("Careful.")
    assert "What it says it wants to do" in text


def test_drift_lines_name_and_count():
    assert "x (2)" in ui_text.drift_lines("P", {"unknown": {"x": 2}})
    assert "hasn't sent" in ui_text.drift_lines("P", {})


def _provider_words():
    words = {ClaudeIntegration.name, CodexIntegration.name, "OpenAI", "Ollama", "Anthropic", "Copilot"}
    for agent in AGENTS:
        words |= {agent.name, agent.id, agent.program}
    return {w.lower() for w in words}


#: Carried over as they are from the shipped app; these only mention a
#: provider in a comment, and aren't about choosing behaviour.
_CARRIED = {"a11y.py", "gtk_a11y.py", "mac_a11y.py"}


@pytest.mark.parametrize("path", [p for p in sorted((ROOT / "ui").glob("*.py")) if p.name not in _CARRIED] +
                         [ROOT / "service.py", ROOT / "store.py", ROOT / "ui_text.py", ROOT / "contracts.py", ROOT / "policy.py",
                          ROOT / "drift.py"],
                         ids=lambda p: p.name)
def test_shared_code_names_no_provider(path):
    """#489's acceptance test: shared code can't branch on a provider by name,
    because no code there can name one: strings, docstrings and identifiers are
    all checked. Comments aren't, as they don't reach the AST, and a branch on an
    id held in a variable wouldn't be caught."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    words = _provider_words()
    found = []
    for node in ast.walk(tree):
        text = None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.Name):
            text = node.id
        elif isinstance(node, ast.Attribute):
            text = node.attr
        if text and any(word in text.lower() for word in words):
            found.append(text[:60])
    assert not found, f"{path.name} names a provider: {found}"


def test_integrations_are_only_imported_by_the_registry_and_app():
    for path in ROOT.rglob("*.py"):
        if "integrations" in path.parts or path.name == "app.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [node.module or "", *(a.name for a in node.names)]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            assert not any("integrations" in n for n in names), f"{path.name} reaches past the contracts"


def test_a_version_line_is_matched_on_whole_words():
    from thechatplace.platform_paths import version_is_checked
    assert version_is_checked("GitHub Copilot CLI 1.0.105. Run update", ("1.0.105",))
    assert version_is_checked("codex-cli 0.163.0", ("0.163.0",))
    assert not version_is_checked("codex-cli 10.163.0", ("0.163.0",))
    assert not version_is_checked("", ("0.163.0",))
