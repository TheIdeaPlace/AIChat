"""What Orca can find in a window (Linux): every control you can move to, with
its role and the name a screen reader gets for it, from the accessibility
tree Orca reads (AT-SPI), and the ones that have no name.

    python3 tools/atspi_audit.py "Settings" [--app thechatplace] [--json out.json]

The visual probe runs it for each surface on Linux (``tools/ui_probe.py``),
while the window is open. It needs PyGObject and AT-SPI's introspection
data (python3-gi and gir1.2-atspi-2.0 on Debian and Ubuntu); a run without
them says so and finds nothing.

A control's name is its own, or its label's (a ``labelled-by`` relation), as
Orca takes it. A list's or a table's rows are left out: their text is their
name.
"""
from __future__ import annotations

import argparse
import json
import sys

#: Roles that need no name: their text, or what they contain, is read.
_NAMELESS_OK = {"table cell", "list item", "text", "paragraph", "terminal", "document web",
                "document frame", "scroll bar", "filler", "panel", "viewport", "scroll pane",
                "menu bar", "separator", "tree item"}


def _name(acc) -> str:
    name = (acc.get_name() or "").strip()
    if name:
        return name
    try:
        for relation in acc.get_relation_set():
            if relation.get_relation_type().value_nick == "labelled-by":
                for index in range(relation.get_n_targets()):
                    label = (relation.get_target(index).get_name() or "").strip()
                    if label:
                        return label
    except Exception:  # noqa: BLE001
        pass
    return ""


def audit(title: str, app_name: str = "thechatplace") -> dict:
    """The window titled ``title`` in ``app_name``: its focusable controls,
    and which of them have no name."""
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except (ImportError, ValueError) as exc:
        return {"error": f"AT-SPI isn't available to Python: {exc}", "controls": [],
                "unnamed": []}
    desktop = Atspi.get_desktop(0)
    window = None
    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        if app is None or (app.get_name() or "") != app_name:
            continue
        for j in range(app.get_child_count()):
            child = app.get_child_at_index(j)
            if child is not None and (child.get_name() or "") == title:
                window = child
    if window is None:
        return {"error": f"no window {title!r} in {app_name}", "controls": [], "unnamed": []}
    controls = []

    def walk(acc, depth):
        if depth > 30:
            return
        try:
            states = acc.get_state_set()
            role = acc.get_role_name()
        except Exception:  # noqa: BLE001 - it went away
            return
        if not states.contains(Atspi.StateType.SHOWING) and depth > 1:
            return
        if states.contains(Atspi.StateType.FOCUSABLE) and role not in ("table cell", "list item"):
            controls.append({"role": role, "name": _name(acc)})
        if role in ("table", "list", "tree table", "document web", "menu bar"):
            return  # rows and pages read their own text
        for index in range(min(acc.get_child_count(), 200)):
            child = acc.get_child_at_index(index)
            if child is not None:
                walk(child, depth + 1)

    walk(window, 0)
    unnamed = [c for c in controls if not c["name"] and c["role"] not in _NAMELESS_OK]
    return {"window": title, "controls": controls, "unnamed": unnamed}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("title")
    parser.add_argument("--app", default="thechatplace")
    parser.add_argument("--json")
    args = parser.parse_args(argv)
    result = audit(args.title, args.app)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
    else:
        for control in result["controls"]:
            print(f"{control['role']}: {control['name'] or '(no name)'}")
        if result.get("error"):
            print(result["error"], file=sys.stderr)
    return 1 if result["unnamed"] or result.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
