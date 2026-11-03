"""Read one database out of a Chromium IndexedDB folder without writing to it.

Spike for TheIdeaPlace/AIChat#541. Standard library only.

The files are opened for reading and parsed here, never handed to a LevelDB
library, because opening a store normally can write to it and Office may have
it open. Only the named database's records are kept: the same LevelDB holds
other databases, including connector tokens, and those values are dropped as
soon as their key shows they belong elsewhere.
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field


class Corrupt(Exception):
    pass


def varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(buf):
            raise Corrupt("varint past end")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7
        if shift > 70:
            raise Corrupt("varint too long")


def snappy_decompress(src: bytes) -> bytes:
    expected, pos = varint(src, 0)
    out = bytearray()
    n = len(src)
    while pos < n:
        tag = src[pos]
        pos += 1
        kind = tag & 3
        if kind == 0:
            length = tag >> 2
            if length >= 60:
                extra = length - 59
                length = int.from_bytes(src[pos:pos + extra], "little")
                pos += extra
            length += 1
            out += src[pos:pos + length]
            pos += length
            continue
        if kind == 1:
            length = 4 + ((tag >> 2) & 7)
            offset = ((tag >> 5) << 8) | src[pos]
            pos += 1
        elif kind == 2:
            length = (tag >> 2) + 1
            offset = int.from_bytes(src[pos:pos + 2], "little")
            pos += 2
        else:
            length = (tag >> 2) + 1
            offset = int.from_bytes(src[pos:pos + 4], "little")
            pos += 4
        if offset == 0 or offset > len(out):
            raise Corrupt("bad snappy offset")
        for _ in range(length):
            out.append(out[-offset])
    if len(out) != expected:
        raise Corrupt("snappy length mismatch")
    return bytes(out)


# ---- LevelDB files -------------------------------------------------------

def _log_records(path: str):
    """Yield (seq, is_put, key, value). A torn tail ends the file quietly."""
    with open(path, "rb") as f:
        data = f.read()
    pos = 0
    pending = bytearray()
    block = 32768
    while pos + 7 <= len(data):
        left = block - pos % block
        if left < 7:
            pos += left
            continue
        length, kind = struct.unpack_from("<HB", data, pos + 4)
        if kind == 0 and length == 0:
            pos += left
            continue
        body = data[pos + 7:pos + 7 + length]
        if len(body) < length:
            break
        pos += 7 + length
        if kind == 1:
            yield from _batch(bytes(body))
            pending.clear()
        elif kind == 2:
            pending[:] = body
        elif kind == 3:
            pending += body
        elif kind == 4:
            pending += body
            yield from _batch(bytes(pending))
            pending.clear()


def _batch(b: bytes):
    if len(b) < 12:
        return
    seq, count = struct.unpack_from("<QI", b, 0)
    pos = 12
    try:
        for i in range(count):
            tag = b[pos]
            pos += 1
            klen, pos = varint(b, pos)
            key = b[pos:pos + klen]
            pos += klen
            if tag == 1:
                vlen, pos = varint(b, pos)
                val = b[pos:pos + vlen]
                pos += vlen
                yield seq + i, True, key, val
            else:
                yield seq + i, False, key, b""
    except (Corrupt, IndexError):
        return


def _block_entries(block: bytes):
    if len(block) < 4:
        return
    restarts = struct.unpack_from("<I", block, len(block) - 4)[0]
    limit = len(block) - 4 - 4 * restarts
    pos = 0
    key = b""
    while pos < limit:
        shared, pos = varint(block, pos)
        unshared, pos = varint(block, pos)
        vlen, pos = varint(block, pos)
        key = key[:shared] + block[pos:pos + unshared]
        pos += unshared
        yield key, block[pos:pos + vlen]
        pos += vlen


def _read_block(data: bytes, off: int, size: int) -> bytes:
    raw = data[off:off + size]
    kind = data[off + size]
    if kind == 0:
        return raw
    if kind == 1:
        return snappy_decompress(raw)
    raise Corrupt(f"block compression {kind}")


def _table_records(path: str):
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 48:
        return
    foot = data[-48:]
    if foot[-8:] != b"\x57\xfb\x80\x8b\x24\x75\x47\xdb":
        raise Corrupt("not an sstable")
    _, p = varint(foot, 0)
    _, p = varint(foot, p)
    idx_off, p = varint(foot, p)
    idx_size, p = varint(foot, p)
    for _, handle in _block_entries(_read_block(data, idx_off, idx_size)):
        off, q = varint(handle, 0)
        size, q = varint(handle, q)
        for ikey, val in _block_entries(_read_block(data, off, size)):
            if len(ikey) < 8:
                continue
            trailer = int.from_bytes(ikey[-8:], "little")
            yield trailer >> 8, (trailer & 0xFF) == 1, ikey[:-8], val


def _all_records(folder: str, stats: dict):
    names = sorted(os.listdir(folder))
    for name in names:
        path = os.path.join(folder, name)
        try:
            if name.endswith(".log"):
                yield from _log_records(path)
            elif name.endswith((".ldb", ".sst")):
                yield from _table_records(path)
        except FileNotFoundError:
            stats["files_vanished"] = stats.get("files_vanished", 0) + 1
        except (Corrupt, IndexError, struct.error):
            stats["files_unreadable"] = stats.get("files_unreadable", 0) + 1


# ---- IndexedDB keys ------------------------------------------------------

def _prefix(key: bytes):
    """(database id, object store id, index id, rest of key), or None."""
    if not key:
        return None
    first = key[0]
    dlen = (first >> 5) + 1
    slen = ((first >> 2) & 7) + 1
    ilen = (first & 3) + 1
    end = 1 + dlen + slen + ilen
    if len(key) < end:
        return None
    a = 1
    db = int.from_bytes(key[a:a + dlen], "little")
    a += dlen
    store = int.from_bytes(key[a:a + slen], "little")
    a += slen
    index = int.from_bytes(key[a:a + ilen], "little")
    return db, store, index, key[end:]


def _str_len(buf: bytes, pos: int) -> tuple[str, int]:
    n, pos = varint(buf, pos)
    raw = buf[pos:pos + n * 2]
    return raw.decode("utf-16-be", "replace"), pos + n * 2


def decode_key(buf: bytes, pos: int = 0):
    kind = buf[pos]
    pos += 1
    if kind == 1:
        return _str_len(buf, pos)
    if kind in (2, 3):
        return struct.unpack_from("<d", buf, pos)[0], pos + 8
    if kind == 4:
        n, pos = varint(buf, pos)
        items = []
        for _ in range(n):
            item, pos = decode_key(buf, pos)
            items.append(item)
        return items, pos
    if kind == 5:
        n, pos = varint(buf, pos)
        return buf[pos:pos + n], pos + n
    return None, pos


# ---- Values --------------------------------------------------------------

class _Undefined:
    def __repr__(self):
        return "undefined"


UNDEFINED = _Undefined()


@dataclass
class HostObject:
    """A Blob or File stored beside the value (an attachment)."""
    kind: str
    index: int | None = None


class V8Reader:
    def __init__(self, data: bytes, pos: int = 0):
        self.d = data
        self.p = pos
        self.ids: list = []

    def _varint(self) -> int:
        v, self.p = varint(self.d, self.p)
        return v

    def _tag(self) -> str:
        while True:
            if self.p >= len(self.d):
                raise Corrupt("value past end")
            t = self.d[self.p]
            self.p += 1
            if t != 0:
                return chr(t)

    def read(self):
        t = self._tag()
        d = self.d
        if t == '"':
            n = self._varint()
            s = d[self.p:self.p + n].decode("latin-1")
            self.p += n
            return s
        if t == "c":
            n = self._varint()
            s = d[self.p:self.p + n].decode("utf-16-le", "replace")
            self.p += n
            return s
        if t == "S":
            n = self._varint()
            s = d[self.p:self.p + n].decode("utf-8", "replace")
            self.p += n
            return s
        if t == "o":
            obj: dict = {}
            self.ids.append(obj)
            self._props(obj, "{")
            self._varint()
            return obj
        if t == "A":
            n = self._varint()
            arr: list = []
            self.ids.append(arr)
            for _ in range(n):
                if self.p < len(d) and d[self.p] == ord("-"):
                    self.p += 1  # a hole
                    arr.append(None)
                else:
                    arr.append(self.read())
            extra: dict = {}
            self._props(extra, "$")
            self._varint()
            self._varint()
            return arr
        if t == "a":
            n = self._varint()
            arr = [None] * n
            self.ids.append(arr)
            extra = {}
            self._props(extra, "@")
            self._varint()
            self._varint()
            for k, v in extra.items():
                if isinstance(k, int) and 0 <= k < n:
                    arr[k] = v
            return arr
        if t == "I":
            v = self._varint()
            return (v >> 1) ^ -(v & 1)
        if t == "U":
            return self._varint()
        if t == "N":
            v = struct.unpack_from("<d", d, self.p)[0]
            self.p += 8
            return v
        if t == "D":
            v = struct.unpack_from("<d", d, self.p)[0]
            self.p += 8
            self.ids.append(v)
            return v
        if t == "T":
            return True
        if t == "F":
            return False
        if t == "_":
            return UNDEFINED
        if t == "0":
            return None
        if t == "^":
            return self.ids[self._varint()]
        if t == "Z":
            bits = self._varint()
            n = bits >> 1
            v = int.from_bytes(d[self.p:self.p + n], "little")
            self.p += n
            return -v if bits & 1 else v
        if t == ";":
            m: dict = {}
            self.ids.append(m)
            while d[self.p] != ord(":"):
                k = self.read()
                m[k if isinstance(k, (str, int, float)) else repr(k)] = self.read()
            self.p += 1
            self._varint()
            return m
        if t == "'":
            s2: list = []
            self.ids.append(s2)
            while d[self.p] != ord(","):
                s2.append(self.read())
            self.p += 1
            self._varint()
            return s2
        if t == "B":
            n = self._varint()
            b = d[self.p:self.p + n]
            self.p += n
            self.ids.append(b)
            if self.p < len(d) and d[self.p] == ord("V"):
                self.p += 1
                self.p += 1  # subtag
                self._varint()
                self._varint()
                self._varint()  # flags, wire version 14 and up
            return b
        if t == "\\":
            return self._host()
        if t == "n":
            v = struct.unpack_from("<d", d, self.p)[0]
            self.p += 8
            return v
        if t == "x":
            return False
        if t == "y":
            return True
        if t == "s":
            return self.read()
        if t == "z":
            return self.read()
        raise Corrupt(f"unknown V8 tag {t!r}")

    def _props(self, obj: dict, end: str):
        d = self.d
        while True:
            while self.p < len(d) and d[self.p] == 0:
                self.p += 1
            if self.p >= len(d):
                raise Corrupt("object past end")
            if d[self.p] == ord(end):
                self.p += 1
                return
            k = self.read()
            obj[k if isinstance(k, (str, int, float)) else repr(k)] = self.read()

    def _host(self):
        t = chr(self.d[self.p])
        self.p += 1
        if t == "i":
            return HostObject("blob", self._varint())
        if t == "e":
            return HostObject("file", self._varint())
        raise Corrupt(f"unknown host tag {t!r}")


def _strip_envelope(data: bytes) -> int:
    """Where the V8 wire data starts, past Blink's own header."""
    pos = 0
    if data[:1] == b"\xff":
        ver, p = varint(data, 1)
        if ver >= 16 and data[p:p + 1] != b"\xff":
            # Blink wrote its version; a trailer pointer can follow (0xFE).
            if data[p:p + 1] == b"\xfe":
                p += 1 + 8 + 4
            pos = p
        elif data[p:p + 1] == b"\xff":
            pos = p
    if data[pos:pos + 1] == b"\xff":
        _, pos = varint(data, pos + 1)
    return pos


def decode_value(data: bytes, blobs: list, blob_path, depth: int = 0):
    """Unwrap IDBValueWrapper's layers and deserialize."""
    if depth > 4:
        raise Corrupt("too many wrappers")
    if data[:2] == b"\xff\x11" and len(data) > 2:
        if data[2] == 1:  # replaced with a blob beside the value
            size, p = varint(data, 3)
            index, p = varint(data, p)
            if index >= len(blobs):
                raise Corrupt("missing blob entry")
            with open(blob_path(blobs[index]), "rb") as f:
                return decode_value(f.read(), blobs, blob_path, depth + 1)
        if data[2] == 2:  # Snappy compressed
            return decode_value(snappy_decompress(data[3:]), blobs, blob_path, depth + 1)
    return V8Reader(data, _strip_envelope(data)).read()


def _external_objects(value: bytes) -> list[int]:
    out = []
    pos = 0
    while pos < len(value):
        is_file = value[pos] != 0
        pos += 1
        number, pos = varint(value, pos)
        _, pos = _str_len(value, pos)
        _, pos = varint(value, pos)
        if is_file:
            _, pos = _str_len(value, pos)
            _, pos = varint(value, pos)
        out.append(number)
    return out


# ---- Store ---------------------------------------------------------------

@dataclass
class Store:
    folder: str
    databases: dict = field(default_factory=dict)
    stores: dict = field(default_factory=dict)
    records: dict = field(default_factory=dict)  # store name -> list of (key, value)
    stats: dict = field(default_factory=dict)


def list_databases(folder: str) -> dict[str, int]:
    latest: dict[bytes, tuple[int, bool, bytes]] = {}
    for seq, put, key, val in _all_records(folder, {}):
        # Global metadata only (database 0, store 0, index 0, type 201).
        if key[:4] == b"\x00\x00\x00\x00" and key[4:5] == b"\xc9":
            if key not in latest or seq > latest[key][0]:
                latest[key] = (seq, put, val)
    out = {}
    for key, (_, put, val) in latest.items():
        if not put:
            continue
        try:
            _origin, p = _str_len(key, 5)
            name, _ = _str_len(key, p)
            out[name] = varint(val, 0)[0]
        except Corrupt:
            continue
    return out


def read_database(folder: str, name: str) -> Store:
    st = Store(folder)
    st.databases = list_databases(folder)
    db = st.databases.get(name)
    if db is None:
        return st
    latest: dict[bytes, tuple[int, bool, bytes]] = {}
    for seq, put, key, val in _all_records(folder, st.stats):
        pre = _prefix(key)
        # Everything from another database is dropped right here.
        if pre is None or pre[0] != db:
            continue
        if key not in latest or seq > latest[key][0]:
            latest[key] = (seq, put, val)
    blob_root = folder[: -len(".leveldb")] + ".blob"

    def blob_path(number: int) -> str:
        return os.path.join(blob_root, f"{db:x}", f"{(number >> 8) & 0xff:02x}", f"{number:x}")

    names: dict[int, str] = {}
    blobs_by_key: dict[tuple[int, bytes], list[int]] = {}
    for key, (_, put, val) in latest.items():
        if not put:
            continue
        _, store, index, rest = _prefix(key)
        if store == 0 and index == 0 and rest[:1] == b"\x32":
            sid, p = varint(rest, 1)
            if rest[p:p + 1] == b"\x00":
                names[sid] = val.decode("utf-16-be", "replace")
        elif index == 3:
            try:
                blobs_by_key[(store, rest)] = _external_objects(val)
            except (Corrupt, IndexError):
                st.stats["bad_blob_entries"] = st.stats.get("bad_blob_entries", 0) + 1
    st.stores = names
    for key, (_, put, val) in latest.items():
        if not put:
            continue
        _, store, index, rest = _prefix(key)
        if store == 0 or index != 1:
            continue
        label = names.get(store, str(store))
        try:
            ukey, _ = decode_key(rest)
            _, p = varint(val, 0)
            obj = decode_value(val[p:], blobs_by_key.get((store, rest), []), blob_path)
            st.records.setdefault(label, []).append((ukey, obj))
        except FileNotFoundError:
            st.stats["blob_missing"] = st.stats.get("blob_missing", 0) + 1
        except (Corrupt, IndexError, struct.error, UnicodeError, RecursionError) as e:
            st.stats["bad_values"] = st.stats.get("bad_values", 0) + 1
            st.stats.setdefault("bad_reasons", {}).setdefault(str(e)[:60], 0)
            st.stats["bad_reasons"][str(e)[:60]] += 1
    return st


def find_stores() -> list[str]:
    root = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Office\16.0\Wef\webview2")
    out = []
    for d, subdirs, _ in os.walk(root):
        for s in subdirs:
            if s == "https_pivot.claude.ai_0.indexeddb.leveldb":
                out.append(os.path.join(d, s))
    return sorted(out)
