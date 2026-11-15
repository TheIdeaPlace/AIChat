"""Every list box gets the app's list accessible (#648), checked in the source so it needs no
window: on Windows an empty list box without one reads as "unknown" with NVDA (#621), and one
with rows reads its row twice on arrival (#616, #647)."""
import re
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "thechatplace" / "ui"
CREATED = re.compile(r"(self\.\w+)\s*=\s*wx\.ListBox\(")
#: Any of these gives a list box its ``ListAccessible`` (ui/a11y.py).
GIVEN = ("set_accessible_name", "set_list_items_accessible", "set_empty_list_text")


def test_every_list_box_gets_a_list_accessible():
    missing = []
    found = 0
    for path in sorted(UI.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for match in CREATED.finditer(source):
            found += 1
            name = re.escape(match.group(1))
            if not any(re.search(rf"{call}\(\s*{name}\b", source) for call in GIVEN):
                line = source.count("\n", 0, match.start()) + 1
                missing.append(f"{path.name}:{line} {match.group(1)}")
    assert found >= 20  # the pattern still finds the lists
    assert not missing, ("Name these lists with set_accessible_name (or give them "
                         "set_list_items_accessible / set_empty_list_text): " + ", ".join(missing))
