"""Windowless tests for the reader, on made-up stores written in the real format."""
import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fake_store as fs  # noqa: E402
import idbread  # noqa: E402
import officechats as oc  # noqa: E402

DB = 2


def base(tmp_path) -> fs.FakeStore:
    s = fs.FakeStore(str(tmp_path))
    s.add_database(DB, "claude-chat-history", {1: "chats", 2: "blobs"})
    s.add_database(3, "claude-mcp-gateways", {1: "tokens"})
    return s


def snapshot(folder):
    out = {}
    for d, _, files in os.walk(folder):
        for f in files:
            p = os.path.join(d, f)
            st = os.stat(p)
            out[p] = (st.st_mtime_ns, st.st_size, hashlib.sha256(open(p, "rb").read()).hexdigest())
    return out


def test_reads_a_chat_from_a_log(tmp_path):
    s = base(tmp_path)
    s.put(DB, 1, "a", fs.blink(fs.chat("a", "Title é ✓", fs.sample_messages())))
    s.write_log()
    lib = oc.load([s.folder])
    assert [c.title for c in lib.chats] == ["Title é ✓"]
    assert [m.kind for m in lib.chats[0].messages] == ["you", "thinking", "tool", "claude"]
    assert lib.chats[0].app == "Excel"
    assert lib.stores[0]["stats"] == {}


def test_other_databases_are_never_kept(tmp_path):
    s = base(tmp_path)
    s.put(3, 1, "gateway", fs.blink({"token": "FAKE-SECRET-TOKEN"}))
    s.put(DB, 1, "a", fs.blink(fs.chat("a", "T", [])))
    s.write_log()
    store = idbread.read_database(s.folder, "claude-chat-history")
    assert "FAKE-SECRET-TOKEN" not in repr(store)
    assert list(store.records) == ["chats"]


def test_reading_changes_nothing_on_disk(tmp_path):
    s = base(tmp_path)
    s.put_blob(DB, 1, "a", fs.blink(fs.chat("a", "T", fs.sample_messages())))
    s.write_log()
    before = snapshot(str(tmp_path))
    oc.load([s.folder])
    assert snapshot(str(tmp_path)) == before


@pytest.mark.parametrize("wrap", ["plain", "snappy", "blob", "blob+snappy"])
def test_value_wrappers(tmp_path, wrap):
    s = base(tmp_path)
    value = fs.blink(fs.chat("a", "T", fs.sample_messages()))
    if "snappy" in wrap:
        value = b"\xff\x11\x02" + fs.snappy_literal(value)
    if "blob" in wrap:
        s.put_blob(DB, 1, "a", value)
    else:
        s.put(DB, 1, "a", value)
    s.write_log()
    assert len(oc.load([s.folder]).chats[0].messages) == 4


def test_snappy_back_references():
    # "abcd" then a copy of 8 bytes reaching back 4, which overlaps itself.
    data = fs.varint(12) + bytes([3 << 2]) + b"abcd" + bytes([((8 - 1) << 2) | 2]) + b"\x04\x00"
    assert idbread.snappy_decompress(data) == b"abcdabcdabcd"


def test_batch_spanning_blocks(tmp_path):
    s = base(tmp_path)
    long = [{"type": "assistant", "timestamp": 1.0, "content": "x" * 100_000}]
    s.put(DB, 1, "a", fs.blink(fs.chat("a", "T", long)))
    s.write_log()
    assert len(oc.load([s.folder]).chats[0].messages[0].text) == 100_000


@pytest.mark.parametrize("compress", [False, True])
def test_tables(tmp_path, compress):
    s = base(tmp_path)
    s.put(DB, 1, "a", fs.blink(fs.chat("a", "T", fs.sample_messages())))
    s.write_table(compress=compress)
    assert oc.load([s.folder]).chats[0].title == "T"


def test_newest_write_wins_and_deletes_hold(tmp_path):
    s = base(tmp_path)
    s.put(DB, 1, "a", fs.blink(fs.chat("a", "Old", [])))
    s.put(DB, 1, "b", fs.blink(fs.chat("b", "Deleted", [])))
    s.write_table(seq=1)
    s.put(DB, 1, "a", fs.blink(fs.chat("a", "New", [])))
    s.delete(DB, 1, "b")
    s.write_log(name="000009.log", seq=100)
    assert [c.title for c in oc.load([s.folder]).chats] == ["New"]


def test_torn_log_tail_keeps_what_came_before(tmp_path):
    s = base(tmp_path)
    s.put(DB, 1, "a", fs.blink(fs.chat("a", "T", [])))
    s.write_log(tail=b"\0\0\0\0\xff\x7f\x01partial")
    assert oc.load([s.folder]).chats[0].title == "T"


def test_garbage_files_and_values_are_counted(tmp_path):
    s = base(tmp_path)
    s.put(DB, 1, "bad", b"\xff\x0f\xee\xee")
    s.put(DB, 1, "gone", b"\xff\x11\x01\x05\x00")
    s.put(DB, 1, "a", fs.blink(fs.chat("a", "T", [])))
    s.write_log()
    with open(os.path.join(s.folder, "000007.ldb"), "wb") as f:
        f.write(b"garbage" * 100)
    lib = oc.load([s.folder])
    assert [c.title for c in lib.chats] == ["T"]
    assert lib.stores[0]["stats"]["bad_values"] == 2
    assert lib.stores[0]["stats"]["files_unreadable"] == 1


def test_blob_file_missing_is_counted(tmp_path):
    s = base(tmp_path)
    s.put_blob(DB, 1, "a", fs.blink(fs.chat("a", "T", [])))
    s.write_log()
    for d, _, files in os.walk(s.blobs):
        for f in files:
            os.remove(os.path.join(d, f))
    lib = oc.load([s.folder])
    assert lib.chats == [] and lib.stores[0]["stats"]["blob_missing"] == 1


def test_store_without_the_database(tmp_path):
    s = fs.FakeStore(str(tmp_path))
    s.add_database(3, "claude-mcp-gateways", {1: "tokens"})
    s.write_log()
    assert oc.load([s.folder]).chats == []


def test_empty_and_vanished_stores(tmp_path):
    empty = tmp_path / "e.indexeddb.leveldb"
    empty.mkdir()
    assert oc.load([str(empty)]).chats == []
    lib = oc.load([str(tmp_path / "gone.indexeddb.leveldb")])
    assert lib.chats == [] and lib.stores[0]["stats"]["store_unreadable"]
    assert oc.load([]).chats == []


def test_unexpected_shapes_are_skipped_and_counted(tmp_path):
    s = base(tmp_path)
    s.put(DB, 1, "x", fs.blink({"id": "x", "no": "snapshot"}))
    s.put(DB, 1, "n", fs.blink("just a string"))
    s.put(DB, 1, "a", fs.blink(fs.chat("a", "T", [
        "not a dict", {"type": "future_kind", "content": "?"},
        {"type": "user", "content": "Hello"}, {"type": "assistant"},
        {"type": "tool"}, {"type": "assistant", "error": "Boom"},
    ])))
    s.write_log()
    lib = oc.load([s.folder])
    chat = lib.chats[0]
    assert [m.kind for m in chat.messages] == ["you", "tool", "claude"]
    assert "Error: Boom" in chat.messages[2].text
    assert lib.stores[0]["stats"]["chats_not_understood"] == 2
    assert lib.stores[0]["stats"]["messages_not_understood"] == 2


def test_same_chat_in_two_stores_keeps_the_newer(tmp_path):
    a = base(tmp_path / "1")
    a.put(DB, 1, "a", fs.blink(fs.chat("a", "Older copy", [], updated=1000.0)))
    a.write_log()
    b = base(tmp_path / "2")
    b.put(DB, 1, "a", fs.blink(fs.chat("a", "Newer copy", [], updated=2000.0)))
    b.write_log()
    assert [c.title for c in oc.load([a.folder, b.folder]).chats] == ["Newer copy"]
    assert [c.title for c in oc.load([b.folder, a.folder]).chats] == ["Newer copy"]


def test_opaque_values_stay_out_of_tool_text():
    m = oc.to_message({"type": "tool", "name": "t", "status": "complete", "input": {"a": 1},
                       "result": {"csv": "1", "encrypted_content": "OPAQUE", "b": b"xx", "u": idbread.UNDEFINED}})
    assert "OPAQUE" not in m.text and "<2 bytes>" in m.text and "undefined" not in m.text


def test_search_is_case_blind_and_respects_details():
    chat = oc.Chat("i", "t", "Excel", None, None, [oc.Message("you", "Budget"), oc.Message("tool", "secret BUDGET")])
    assert len(chat.matching("budget", True)) == 2
    assert len(chat.matching("budget", False)) == 1


def test_markdown_and_file_names():
    chat = oc.Chat("abcdefgh-1", 'Q3: "plan"/x?', "Excel", None, 5e11, [oc.Message("you", "Hi", 5e11), oc.Message("tool", "T")])
    md = oc.to_markdown(chat)
    assert md.startswith('# Q3: "plan"/x?') and "## You" in md and "## Tool" not in md
    assert "## Tool" in oc.to_markdown(chat, True)
    assert oc.file_name(chat) == "Q3 plan x (abcdefgh).md"
    assert oc.file_name(oc.Chat("abcdefgh", "CON", "", None, None, [])) == "Chat CON (abcdefgh).md"
    assert oc.file_name(oc.Chat("abcdefgh", "...", "", None, None, [])) == "Chat (abcdefgh).md"


def test_bad_dates_do_not_crash():
    assert oc.date_text(None) == "no date"
    assert oc.date_text(1e30) == "no date"
    assert oc.date_text(float("nan")) == "no date"
