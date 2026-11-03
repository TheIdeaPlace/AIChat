"""Second look at the shape: chat-level fields and display messages. No chat text."""
import collections
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import idbread  # noqa: E402

SHORT = {"role", "type", "surface", "sender", "kind", "status", "name", "effort"}
for folder in idbread.find_stores():
    st = idbread.read_database(folder, "claude-chat-history")
    chats = [v for _, v in st.records.get("chats", [])]
    print("== account", folder.split("webview2\\")[1][:6], "|", folder.split("\\EBWebView")[0][-3:],
          "| chats", len(chats), "| stats", st.stats)
    top = collections.Counter()
    ui = collections.Counter()
    enum = collections.defaultdict(collections.Counter)
    surfaces = collections.Counter()
    per = []
    for c in chats:
        top.update(c.keys())
        surfaces[str(c.get("surface"))] += 1
        msgs = c["snapshot"]["ui"]["messages"]
        arch = c["snapshot"]["agent"].get("snipArchive") or []
        size = sum(len(m.get("content") or "") for m in msgs if isinstance(m.get("content"), str))
        when = c.get("updatedAt") or c.get("createdAt")
        per.append((when, len(msgs), len(c["snapshot"]["api"]["transcript"]), len(arch), size,
                    bool(c.get("title"))))
        for m in msgs:
            ui.update(m.keys())
            for k in SHORT & m.keys():
                if isinstance(m[k], str) and len(m[k]) < 40 and k != "name":
                    enum[k][m[k]] += 1
    print(" chat fields:", dict(top))
    print(" surfaces:", dict(surfaces))
    print(" ui message fields:", dict(ui))
    print(" ui enums:", {k: dict(v) for k, v in enum.items()})
    for w, a, b, c2, s, t in sorted(per, key=lambda x: x[0] or 0):
        d = datetime.datetime.fromtimestamp(w / 1000).strftime("%Y-%m-%d") if w else "?"
        print(f"   {d} ui={a} api={b} archived={c2} chars={s} titled={t}")
