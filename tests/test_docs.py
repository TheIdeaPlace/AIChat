"""docs/ keeps up with the app's own keyboard shortcuts list and user guide."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _make_docs():
    spec = importlib.util.spec_from_file_location("make_docs", ROOT / "tools" / "make_docs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_documents_match_the_app():
    for name, text in _make_docs().documents().items():
        path = ROOT / "docs" / name
        assert path.is_file(), f"run python tools/make_docs.py (docs/{name} is missing)"
        assert path.read_text(encoding="utf-8") == text, \
            f"run python tools/make_docs.py (docs/{name} is out of date)"


def test_the_published_guide_meets_wcag_basics():
    # #102: the page on GitHub Pages. One h1, a viewport for reflow on a phone,
    # a Contents list whose every link has a target, and ids that are unique.
    import re
    page = _make_docs().documents()["user-guide.html"]
    assert page.count("<h1>") == 1 and '<html lang="en">' in page
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in page
    assert '<nav aria-labelledby="contents"><h2 id="contents">Contents</h2>' in page
    assert page.index("<h1>") < page.index("<nav")  # the title comes first
    ids = re.findall(r' id="([^"]*)"', page)
    assert len(ids) == len(set(ids))
    targets = re.findall(r'href="#([^"]+)"', page)
    assert targets and set(targets) <= set(ids)
    sections = re.findall(r"<h2 id=", page)
    assert len(targets) == len(sections) - 1  # every section but Contents itself
    assert "" not in ids


def test_contents_links_survive_awkward_headings():
    contents = _make_docs()._with_contents
    assert contents("<p>no sections</p>") == "<p>no sections</p>"
    body = contents("<h1>T</h1><h2>Groups</h2><h2>Groups</h2><h2>Contents</h2>"
                    "<h2>Use <code>claude -p</code></h2><h2>!!!</h2><h2>Über &amp; more</h2>")
    import re
    ids = re.findall(r'<h2 id="([^"]*)"', body)
    assert ids == ["contents", "groups", "groups-2", "contents-2", "use-claude-p",
                   "section-5", "über-more"]
    assert body.index("<h1>") < body.index("<nav")



def test_the_guides_lists_and_tables_are_never_joined_onto_one_line():
    """Each list item and table row starts its own line: a reflow once joined
    them, and the guide then read as run-on text (#336)."""
    import re
    guide = (ROOT / "thechatplace" / "assets" / "user-guide.md").read_text(encoding="utf-8")
    joined = [line for line in guide.splitlines()
              if re.search(r"\S\s+- \*\*", line) or line.count(" | ") > 8]
    assert joined == []
