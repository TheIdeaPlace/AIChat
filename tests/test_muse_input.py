"""Attachments for a Muse chat started here, shaped as installed Muse Code
1.4.20 takes them: an image as an `image` part (base64Data, mediaType), and
any other file as `@"path"` in the text, which Muse reads with its file tool
(natively, inside and outside the chat's folder). Synthetic files only."""
import base64
import struct
import zlib

import pytest

from test_muse_execution import NATIVE_ID, Execution, owned, record  # noqa: F401 - fixture

from thechatplace.providers.muse_connection import MuseHistoryError
from thechatplace.providers.muse_input import MuseInput, build_input


def png(path, size=4):
    raw = b"".join(b"\x00" + b"\xdc\x14\x14" * size for _ in range(size))
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return path


def test_an_image_is_sent_as_an_image_part_and_named_in_the_text(tmp_path):
    picture = png(tmp_path / "synthetic.png")
    message = build_input("What is this?", [str(picture)])
    assert isinstance(message, MuseInput) and isinstance(message, str)
    assert str(message) == "What is this?\n\nAttached image: synthetic.png"
    (image,) = message.images
    assert image == {"type": "image", "mediaType": "image/png", "clientRef": {"name": "synthetic.png"},
                     "base64Data": base64.b64encode(picture.read_bytes()).decode("ascii")}


def test_another_file_is_mentioned_for_muse_to_read(tmp_path):
    note = tmp_path / "note.txt"
    note.write_text("synthetic", encoding="utf-8")
    message = build_input("Read this.", [str(note)])
    assert message.images == ()
    assert str(message) == f'Read this.\n\nAttached: @"{note.resolve()}"'


def test_an_image_only_message_still_has_text(tmp_path):
    message = build_input("", [str(png(tmp_path / "only.png"))])
    assert str(message) == "Attached image: only.png"


def test_a_file_named_like_an_image_but_not_one_is_mentioned(tmp_path):
    fake = tmp_path / "fake.png"
    fake.write_text("not an image", encoding="utf-8")
    message = build_input("x", [str(fake)])
    assert message.images == () and "@" in str(message)


def test_an_image_too_big_to_send_inline_is_mentioned(tmp_path, monkeypatch):
    from thechatplace.providers import muse_input
    monkeypatch.setattr(muse_input, "fits_inline", lambda size: False)
    message = build_input("x", [str(png(tmp_path / "big.png"))])
    assert message.images == () and "big.png" in str(message)


def test_a_missing_file_or_too_many_are_refused(tmp_path):
    with pytest.raises(OSError):
        build_input("x", [str(tmp_path / "gone.txt")])
    note = tmp_path / "n.txt"
    note.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="at most 20"):
        build_input("x", [str(note)] * 21)


def test_send_carries_the_images_after_the_text(owned, tmp_path):
    own = record(owned)
    owned.send(own, build_input("What is this?", [str(png(tmp_path / "s.png"))]))
    parts = [p for m, p in owned.execution.messages if m == "turn/start"][-1]["input"]
    assert parts[0] == {"type": "text", "text": "What is this?\n\nAttached image: s.png"}
    assert parts[1]["type"] == "image" and parts[1]["mediaType"] == "image/png"


def test_a_skill_message_keeps_its_attachments(owned, tmp_path):
    own = record(owned)
    owned.send(own, build_input("/synthetic-plan Plan it.", [str(png(tmp_path / "s.png"))]))
    parts = [p for m, p in owned.execution.messages if m == "turn/start"][-1]["input"]
    assert parts[0] == {"type": "skill", "selector": "synthetic-plan", "source": "bundled",
                        "arguments": "Plan it.\n\nAttached image: s.png"}
    assert parts[1]["type"] == "image"


def test_attachments_too_large_together_are_refused_before_sending(owned, tmp_path, monkeypatch):
    from thechatplace.providers import muse
    monkeypatch.setattr(muse, "MAX_INPUT_BYTES", 10)
    own = record(owned)
    with pytest.raises(MuseHistoryError, match="too large"):
        owned.send(own, build_input("x", [str(png(tmp_path / "s.png"))]))
    assert not any(m == "turn/start" for m, _ in owned.execution.messages)


def test_attachments_are_a_capability_of_chats_started_here_only(owned):
    from thechatplace.providers.muse import MuseProvider, session_from_muse
    provider = MuseProvider(connection=object(), execution=owned.execution)
    assert provider.capabilities(record(owned).to_info()).attachments is True
    assert provider.capabilities(session_from_muse({"sessionId": "external-1", "name": "x"})).attachments is False
