"""Imported chats (#436): the store in The Chat Place's folder and the
read-only provider that lists them. Windowless; synthetic histories only."""
import json
import zipfile
from pathlib import Path

import pytest

from records import assistant_block, text_block, tool_use_block, user_text
from thechatplace import backup, platform_paths
from thechatplace.backup_reader import BackupReader
from thechatplace.changes import FileEdit
from thechatplace.imported_store import (ImportedChat, ImportedStore, chat_key, import_sessions,
                                         transcript_from_json, transcript_to_json)
from thechatplace.providers.imported import IMPORTED, ImportedChats
from thechatplace.tasklist import TaskStep
from thechatplace.transcript import ASSISTANT, USER, ChatMessage, ToolCommand, Transcript
from theideaplace_appkit.difftext import Hunk


def chat(native="n1", computer="pc-id", last=100, title="Invented chat", provider="claude"):
    return ImportedChat(key=chat_key(provider, native, computer), provider=provider,
                        native_id=native, computer_id=computer, computer_name="Old PC",
                        title=title, cwd="/old/work", created_ms=50, last_activity_ms=last)


def transcript(n=2):
    t = Transcript(messages=[ChatMessage(USER if i % 2 == 0 else ASSISTANT, f"message {i}",
                                         turn=i // 2 + 1) for i in range(n)])
    t.edits = [FileEdit("a.py", "Edit", 1, [Hunk(1, 1, ["-old", "+new"])])]
    t.commands = [ToolCommand("ls", output="a.py", finished=True, turn=1)]
    t.tasks = [TaskStep("1", "Do it", status="completed")]
    t.turns, t.model = 1, "invented-model"
    return t


def test_transcript_round_trips_through_json():
    original = transcript(3)
    again = transcript_from_json(json.loads(json.dumps(transcript_to_json(original))))
    assert again == original
    # A field a newer release added is ignored, not an error.
    data = transcript_to_json(original)
    data["messages"][0]["from_the_future"] = 1
    assert transcript_from_json(data).messages[0].text == "message 0"


def test_save_list_load_and_remove(tmp_path):
    store = ImportedStore(tmp_path / "imported")
    assert store.all() == []
    assert store.save(chat(), transcript()) == "added"
    [listed] = store.all()
    assert listed.title == "Invented chat" and listed.messages == 2 and listed.imported_at
    assert store.load(listed.key).messages[1].text == "message 1"
    assert store.remove(listed.key) and store.all() == []
    with pytest.raises(LookupError):
        store.load(listed.key)
    assert not store.remove(listed.key)


def test_reimport_updates_never_duplicates_and_never_goes_backwards(tmp_path):
    store = ImportedStore(tmp_path / "imported")
    assert store.save(chat(last=100), transcript(2)) == "added"
    assert store.save(chat(last=100), transcript(2)) == "unchanged"
    assert store.save(chat(last=200), transcript(4)) == "updated"
    assert store.save(chat(last=150), transcript(3)) == "unchanged"   # an older backup
    [only] = store.all()
    assert only.messages == 4 and only.last_activity_ms == 200
    # The same chat from another computer is another chat.
    assert store.save(chat(computer="other-pc"), transcript()) == "added"
    assert len(store.all()) == 2


def test_keys_must_match_identity_and_be_safe_file_names(tmp_path):
    store = ImportedStore(tmp_path / "imported")
    bad = chat()
    bad.key = "imported:" + "0" * 64
    with pytest.raises(ValueError):
        store.save(bad, transcript())
    with pytest.raises(ValueError):
        store.load("imported:../../evil")


def test_damaged_files_dont_break_the_list(tmp_path):
    store = ImportedStore(tmp_path / "imported")
    store.save(chat(), transcript())
    key = store.all()[0].key
    (tmp_path / "imported" / f"{key.split(':')[1]}.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        store.load(key)
    (tmp_path / "imported" / "index.json").write_text("[]", encoding="utf-8")
    assert store.all() == []


def test_provider_lists_read_only_rows_and_reads_them(tmp_path):
    store = ImportedStore(tmp_path / "imported")
    store.save(chat(provider="codex"), transcript())
    provider = ImportedChats(store)
    [info] = provider.collect().sessions
    assert info.provider == IMPORTED and info.provider_label == "Codex"
    assert info.member_providers == ("codex",) and info.kind_label == "imported chat"
    assert not info.is_own and info.key.startswith("imported:")
    assert "Old PC" in info.detail
    caps = provider.capabilities(info)
    assert caps.read and caps.delete
    assert not (caps.send or caps.fork or caps.steer or caps.attachments or caps.approvals
                or caps.remote_control or caps.desktop_link or caps.share_link or caps.options)
    reader = provider.open_reader(info)
    assert reader.refresh() is True and reader.refresh() is False
    assert [m.text for m in reader.transcript.messages] == ["message 0", "message 1"]
    assert provider.last_reply(info) == "message 1"
    assert provider.remove(info) and provider.collect().sessions == []
    assert provider.open_reader(info) is None


def test_controller_routes_imported_rows_only_to_the_imported_provider(tmp_path):
    from thechatplace.providers.claude import ClaudeConversations
    from thechatplace.session_controller import SessionController
    store = ImportedStore(tmp_path / "imported")
    store.save(chat(), transcript())
    controller = SessionController([ClaudeConversations(), ImportedChats(store)])
    [info] = ImportedChats(store).collect().sessions
    assert controller.read(info).messages[0].text == "message 0"
    assert not controller.capabilities(info).send
    controller.close()


def test_a_backup_round_trips_into_readable_imported_chats(tmp_path, monkeypatch):
    """Back Up Now, then the reader through backup.opened, then the store:
    an encrypted backup of a synthetic Claude chat comes back readable."""
    claude = tmp_path / "claude"
    project = claude / "projects" / "C--old"
    project.mkdir(parents=True)
    records = [user_text("Invented question", entrypoint="cli", cwd="/old/workspace"),
               assistant_block(text_block("Invented reply"))]
    (project / "abc-123.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records),
                                           encoding="utf-8")
    monkeypatch.setattr(platform_paths, "claude_home", lambda: claude)
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path / "tcp")
    monkeypatch.setattr(platform_paths, "desktop_sessions_dirs", lambda: [])
    monkeypatch.setattr(platform_paths, "cowork_sessions_dirs", lambda: [])
    from thechatplace.providers import claude_backup
    dest = tmp_path / "dest"
    dest.mkdir()
    made = backup.create(dest, collectors=[claude_backup.inventory], passphrase="pw").path
    check = backup.verify(made, passphrase="pw")
    assert check.ok
    store = ImportedStore(tmp_path / "imported")
    with backup.opened(made, "pw") as plain, zipfile.ZipFile(plain) as archive, \
            BackupReader(archive, check.manifest) as reader:
        sessions = reader.sessions()
        counts = import_sessions(store, reader, sessions, backup_name=made.name)
    assert counts == {"added": 1, "updated": 0, "unchanged": 0, "failed": 0}
    [info] = ImportedChats(store).collect().sessions
    assert info.cli_session_id == "abc-123" and info.provider_label == "Claude"
    reader = ImportedChats(store).open_reader(info)
    reader.refresh()
    assert "Invented reply" in [m.text for m in reader.transcript.messages]
    # Importing the same backup again changes nothing.
    with backup.opened(made, "pw") as plain, zipfile.ZipFile(plain) as archive, \
            BackupReader(archive, check.manifest) as reader:
        assert import_sessions(store, reader, reader.sessions())["unchanged"] == 1


def test_restore_backup_asks_first_and_imports_everything(tmp_path, monkeypatch):
    from thechatplace.imported_store import RestorePlan, describe_plan, restore_backup
    claude = tmp_path / "claude"
    for name in ("one", "two"):
        project = claude / "projects" / f"C--{name}"
        project.mkdir(parents=True)
        records = [user_text(f"Question {name}", entrypoint="cli", cwd=f"/old/{name}"),
                   assistant_block(text_block(f"Reply {name}"))]
        (project / f"{name}-id.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records),
                                                  encoding="utf-8")
    monkeypatch.setattr(platform_paths, "claude_home", lambda: claude)
    monkeypatch.setattr(platform_paths, "app_data_dir", lambda: tmp_path / "tcp")
    monkeypatch.setattr(platform_paths, "desktop_sessions_dirs", lambda: [])
    monkeypatch.setattr(platform_paths, "cowork_sessions_dirs", lambda: [])
    from thechatplace.providers import claude_backup
    dest = tmp_path / "dest"
    dest.mkdir()
    made = backup.create(dest, collectors=[claude_backup.inventory]).path
    manifest = backup.verify(made).manifest
    store = ImportedStore(tmp_path / "imported")
    asked = []
    assert restore_backup(made, manifest, None, store=store,
                          confirm=lambda plan: asked.append(plan) or False) is None
    assert store.all() == [] and asked[0].counts == {"claude": 2}
    question = describe_plan(asked[0])
    assert question.startswith("Restore 2 chats from ") and "Claude 2" in question
    assert "read-only" in question and "Nothing is written" in question
    counts = restore_backup(made, manifest, None, store=store, confirm=lambda plan: True)
    assert counts["added"] == 2 and len(store.all()) == 2
    # Nothing was left behind in the temporary folders, and the original
    # Claude folder wasn't touched.
    assert sorted(p.name for p in (claude / "projects").iterdir()) == ["C--one", "C--two"]


def test_describe_plan_handles_odd_dates_and_notes():
    from thechatplace.imported_store import RestorePlan, describe_plan
    text = describe_plan(RestorePlan("", "not a date", {"codex": 1}, ["Codex history could not be listed."]))
    assert text.startswith("Restore 1 chat from another computer? Codex 1.")
    assert "couldn't be read" in text
