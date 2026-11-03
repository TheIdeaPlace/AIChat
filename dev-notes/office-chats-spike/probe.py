"""Report the shape of the chat store: field names and types, never chat text."""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import idbread  # noqa: E402

ENUMS = {"role", "type", "surface", "sender"}


def walk(obj, path, shapes, enums, depth=0):
    if depth > 6:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}"
            shapes[p][type(v).__name__] += 1
            if k in ENUMS and isinstance(v, str) and len(v) < 30:
                enums[p][v] += 1
            walk(v, p, shapes, enums, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            shapes[path + "[]"][type(v).__name__] += 1
            walk(v, path + "[]", shapes, enums, depth + 1)


for folder in idbread.find_stores():
    tail = folder.split("webview2\\")[1].split("\\EBWebView")[0]
    print("==", tail.split("\\", 1)[1] if "\\" in tail else tail, "account", tail[:6] + "...")
    print(" databases:", sorted(idbread.list_databases(folder)))
    st = idbread.read_database(folder, "claude-chat-history")
    print(" object stores:", st.stores, " stats:", st.stats)
    for name, recs in st.records.items():
        print(f" store {name!r}: {len(recs)} records; key types",
              collections.Counter(type(k).__name__ for k, _ in recs))
        shapes = collections.defaultdict(collections.Counter)
        enums = collections.defaultdict(collections.Counter)
        for _, v in recs:
            walk(v, "", shapes, enums)
        for p in sorted(shapes)[:70]:
            print("   ", p, dict(shapes[p]), dict(enums[p]) if p in enums else "")
