"""Write a made-up store in the real on-disk format. No real chat content."""
from __future__ import annotations

import os
import struct


def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def snappy_literal(data: bytes) -> bytes:
    """Valid Snappy that uses literals only."""
    out = bytearray(varint(len(data)))
    n = len(data) - 1
    if n < 60:
        out.append(n << 2)
    else:
        raw = n.to_bytes(4, "little")
        out.append(63 << 2)
        out += raw
    return bytes(out + data)


def v8(obj) -> bytes:
    if obj is None:
        return b"0"
    if obj is True:
        return b"T"
    if obj is False:
        return b"F"
    if isinstance(obj, int):
        return b"I" + varint((obj << 1) ^ (obj >> 63))
    if isinstance(obj, float):
        return b"N" + struct.pack("<d", obj)
    if isinstance(obj, str):
        try:
            raw = obj.encode("latin-1")
            return b'"' + varint(len(raw)) + raw
        except UnicodeEncodeError:
            raw = obj.encode("utf-16-le")
            return b"c" + varint(len(raw)) + raw
    if isinstance(obj, list):
        body = b"".join(v8(x) for x in obj)
        return b"A" + varint(len(obj)) + body + b"$" + varint(0) + varint(len(obj))
    if isinstance(obj, dict):
        body = b"".join(v8(k) + v8(v) for k, v in obj.items())
        return b"o" + body + b"{" + varint(len(obj))
    raise TypeError(type(obj))


def blink(obj) -> bytes:
    return b"\xff\x15\xfe" + b"\0" * 12 + b"\xff\x0f" + v8(obj)


def idb_str(s: str) -> bytes:
    return varint(len(s)) + s.encode("utf-16-be")


def data_key(db: int, store: int, index: int, user: bytes) -> bytes:
    return bytes([0, db, store, index]) + user


def str_key(s: str) -> bytes:
    return b"\x01" + idb_str(s)


class FakeStore:
    def __init__(self, parent: str, name="https_pivot.claude.ai_0.indexeddb"):
        self.folder = os.path.join(parent, name + ".leveldb")
        self.blobs = os.path.join(parent, name + ".blob")
        os.makedirs(self.folder, exist_ok=True)
        self.entries: list[tuple[bool, bytes, bytes]] = []
        self.next_blob = 1

    def add_database(self, db: int, name: str, stores: dict[int, str]):
        key = b"\0\0\0\0\xc9" + idb_str("https_pivot.claude.ai_0@1") + idb_str(name)
        self.entries.append((True, key, varint(db)))
        for sid, label in stores.items():
            k = data_key(db, 0, 0, b"\x32" + varint(sid) + b"\0")
            self.entries.append((True, k, label.encode("utf-16-be")))

    def put(self, db: int, store: int, key: str, value: bytes):
        self.entries.append((True, data_key(db, store, 1, str_key(key)), varint(1) + value))

    def delete(self, db: int, store: int, key: str):
        self.entries.append((False, data_key(db, store, 1, str_key(key)), b""))

    def put_blob(self, db: int, store: int, key: str, content: bytes):
        """The value goes in a file beside the store, as large values do."""
        number = self.next_blob
        self.next_blob += 1
        folder = os.path.join(self.blobs, f"{db:x}", f"{(number >> 8) & 0xff:02x}")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, f"{number:x}"), "wb") as f:
            f.write(content)
        wrapper = b"\xff\x11\x01" + varint(len(content)) + varint(0)
        self.put(db, store, key, wrapper)
        info = b"\0" + varint(number) + idb_str("") + varint(len(content))
        self.entries.append((True, data_key(db, store, 3, str_key(key)), info))

    def batch_bytes(self, seq: int = 1) -> bytes:
        body = bytearray(struct.pack("<QI", seq, len(self.entries)))
        for put, k, v in self.entries:
            body.append(1 if put else 0)
            body += varint(len(k)) + k
            if put:
                body += varint(len(v)) + v
        return bytes(body)

    def write_log(self, name="000003.log", seq=1, tail=b""):
        """A log in LevelDB's 32 KiB blocks, fragmenting the batch as needed."""
        batch = self.batch_bytes(seq)
        out = bytearray()
        pos = 0
        first = True
        while True:
            room = 32768 - len(out) % 32768
            if room < 7:
                out += b"\0" * room
                room = 32768
            take = min(room - 7, len(batch) - pos)
            last = pos + take == len(batch)
            kind = 1 if first and last else 2 if first else 4 if last else 3
            out += b"\0\0\0\0" + struct.pack("<HB", take, kind) + batch[pos:pos + take]
            pos += take
            first = False
            if last:
                break
        with open(os.path.join(self.folder, name), "wb") as f:
            f.write(out + tail)
        self.entries = []

    def write_table(self, name="000005.ldb", seq=1, compress=False):
        """A one-block table, sorted as LevelDB requires."""
        block = bytearray()
        for i, (put, k, v) in enumerate(sorted(self.entries, key=lambda e: e[1])):
            ikey = k + (((seq + i) << 8) | (1 if put else 0)).to_bytes(8, "little")
            block += varint(0) + varint(len(ikey)) + varint(len(v)) + ikey + v
        block += struct.pack("<II", 0, 1)
        data = snappy_literal(bytes(block)) if compress else bytes(block)
        out = bytearray(data) + bytes([1 if compress else 0]) + b"\0" * 4
        handle = varint(0) + varint(len(data))
        idx = bytearray(varint(0) + varint(1) + varint(len(handle)) + b"\xff" + handle)
        idx += struct.pack("<II", 0, 1)
        idx_off = len(out)
        out += idx + b"\0" + b"\0" * 4
        footer = varint(0) + varint(0) + varint(idx_off) + varint(len(idx))
        footer = footer.ljust(40, b"\0") + b"\x57\xfb\x80\x8b\x24\x75\x47\xdb"
        with open(os.path.join(self.folder, name), "wb") as f:
            f.write(out + footer)
        self.entries = []


def chat(cid: str, title: str, messages: list[dict], updated: float = 1_780_000_000_000.0,
         surface: str = "sheet", archive: list | None = None) -> dict:
    return {"id": cid, "userId": "u1", "orgId": "o1", "fileId": "f1", "surface": surface,
            "title": title, "createdAt": updated - 60000.0, "updatedAt": updated,
            "snapshot": {"ui": {"messages": messages}, "api": {"transcript": []},
                         "agent": {"snipArchive": archive or []}}}


def sample_messages() -> list[dict]:
    t = 1_780_000_000_000.0
    return [
        {"uuid": "1", "type": "user", "timestamp": t, "content": "What is the total for the made-up budget?"},
        {"uuid": "2", "type": "thinking", "timestamp": t + 1, "content": "Adding the invented column."},
        {"uuid": "3", "type": "tool", "timestamp": t + 2, "name": "get_cell_ranges", "status": "complete",
         "input": {"range": "A1:A3"}, "result": {"success": True, "csv": "1\n2\n3", "encrypted_content": "OPAQUE"}},
        {"uuid": "4", "type": "assistant", "timestamp": t + 3, "content": "The total is six. Café ✓"},
    ]


def build_demo(parent: str) -> str:
    """A store with a few made-up chats, for trying the viewer."""
    s = FakeStore(parent)
    s.add_database(2, "claude-chat-history", {1: "chats", 2: "blobs"})
    s.add_database(3, "claude-mcp-gateways", {1: "tokens"})
    s.put(3, 1, "gateway", blink({"token": "FAKE-SECRET-TOKEN"}))
    s.put(2, 1, "chat-a", blink(chat("chat-a", "Made-up budget totals", sample_messages())))
    s.put(2, 1, "chat-b", blink(chat("chat-b", "Invented travel plan", [
        {"type": "user", "timestamp": 1_779_000_000_000.0, "content": "Which train is first?"},
        {"type": "assistant", "timestamp": 1_779_000_001_000.0, "content": "The 8:15 is first.\nThe next is 9:40."},
    ], updated=1_779_000_002_000.0)))
    s.put_blob(2, 1, "chat-c", b"\xff\x11\x02" + snappy_literal(blink(chat("chat-c", "Long imaginary report", [
        {"type": "user", "timestamp": 1_778_000_000_000.0, "content": "Write a long report."},
        {"type": "assistant", "timestamp": 1_778_000_001_000.0, "content": "Paragraph about nothing. " * 400},
    ], updated=1_778_000_002_000.0))))
    s.write_log()
    return s.folder


if __name__ == "__main__":
    import sys
    print(build_demo(sys.argv[1]))
