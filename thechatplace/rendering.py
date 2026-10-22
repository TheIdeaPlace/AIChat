"""A message's markdown as a small HTML page, for a screen reader's browse
mode (issue #190). Windows shows it in Edge WebView2, a Mac in the system's
WebKit (#119), where VoiceOver moves through it with the rotor and VO+Command
keys.

Claude writes markdown: headings, lists, tables, code. In a plain text box a
table is a run of pipes and a heading is "##". As a web page the screen
reader can move by heading (H), table and cell (T, Ctrl+Alt+arrows), list
(L) and region (R in JAWS, D in NVDA; each code block is one, named "Code
block, Python, 14 lines").

The page is built to be inert:

* Raw HTML in a message is shown as text, never rendered (Python-Markdown's
  HTML block and inline HTML handling is switched off).
* Images aren't loaded: the image syntax is switched off, so it reads as a
  link instead.
* A Content-Security-Policy allows nothing but the page's own styles, so
  nothing remote is fetched and no script in the page can run.
* Links keep only http, https and mailto targets, which the viewer opens in
  the default browser, never in itself, and links to sessions
  (``thechatplace://session/<id>``, #144), which The Chat Place opens itself.
"""
from __future__ import annotations

import html
import re

import markdown
from markdown.extensions.fenced_code import FencedCodeExtension
from markdown.extensions.sane_lists import SaneListExtension
from markdown.extensions.tables import TableExtension

from .links import parse_link

_CODE = re.compile(r'<pre><code(?: class="language-([^"]+)")?>(.*?)</code></pre>', re.DOTALL)
_HREF = re.compile(r'<a href="([^"]*)"')
_SAFE_LINK = re.compile(r"(?i)^(https?:|mailto:)")
_HTML_HEADING = re.compile(r"<(/?)h([1-6])(?=[\s>])")


def opens_in_browser(href: str) -> bool:
    """Whether a link in a message may go to the default browser: http,
    https and mailto only (View, Links uses the same rule, #190)."""
    return bool(_SAFE_LINK.match(href or ""))

LANGUAGE_NAMES = {
    "py": "Python", "python": "Python", "js": "JavaScript", "javascript": "JavaScript",
    "ts": "TypeScript", "typescript": "TypeScript", "json": "JSON", "html": "HTML",
    "xml": "XML", "css": "CSS", "sh": "Shell", "bash": "Bash", "shell": "Shell",
    "powershell": "PowerShell", "ps1": "PowerShell", "pwsh": "PowerShell", "cs": "C#",
    "csharp": "C#", "c": "C", "cpp": "C++", "sql": "SQL", "yaml": "YAML", "yml": "YAML",
    "md": "Markdown", "markdown": "Markdown", "diff": "Diff", "text": "Text", "toml": "TOML",
}

#: WCAG 2.2 AA, checked for the published user guide (#102): the reader's own
#: text size (100%, not px), no sideways scrolling at 320px (long words and
#: paths wrap; not overflow-wrap: anywhere, which squeezes table columns),
#: a focus ring that shows on every link, and dark-mode links, visited ones
#: too, at 6.6:1 or better (the browser's default visited purple was 1.6:1).
#: color-scheme lets the browser draw its scroll bars dark on a dark page; under High Contrast a code block, which only a background marks,
#: gets a border in the theme's text colour (#155). tests/test_visual_style.py
#: measures all of it.
_STYLE = """
:root { color-scheme: light dark; }
body { font: 100%/1.5 "Segoe UI", -apple-system, sans-serif; margin: 1em 1.5em; max-width: 60em;
       overflow-wrap: break-word; }
pre { white-space: pre-wrap; background: #f3f3f3; padding: .6em; border-radius: 4px; }
code { font-family: Consolas, monospace; }
table { border-collapse: collapse; margin: .5em 0; }
th, td { border: 1px solid #888; padding: .25em .6em; text-align: left; vertical-align: top; }
a:focus-visible { outline: 3px solid; outline-offset: 2px; }
@media (prefers-color-scheme: dark) {
  body { background: #1e1e1e; color: #e8e8e8; }
  pre { background: #2b2b2b; }
  a { color: #8ab4f8; }
  a:visited { color: #c58af9; }
}
@media (forced-colors: active) {
  pre { border: 1px solid CanvasText; }
}
"""


def language_name(tag: str) -> str:
    """'py' -> 'Python'; unknown tags as written."""
    tag = (tag or "").strip()
    return LANGUAGE_NAMES.get(tag.lower(), tag)


def describe_code_block(language: str, code: str) -> str:
    """'Code block, Python, 14 lines' (the name a screen reader hears)."""
    lines = len(code.rstrip("\n").split("\n")) if code.strip() else 0
    parts = ["Code block"]
    if language:
        parts.append(language_name(language))
    parts.append(f"{lines} line" + ("" if lines == 1 else "s"))
    return ", ".join(parts)


def _converter() -> markdown.Markdown:
    md = markdown.Markdown(extensions=[FencedCodeExtension(), TableExtension(),
                                       SaneListExtension()],
                           output_format="html")
    md.preprocessors.deregister("html_block")
    for name in ("html", "image_link", "image_reference", "short_image_ref"):
        md.inlinePatterns.deregister(name)
    return md


def markdown_to_html(text: str) -> str:
    """The body HTML for ``text``: code blocks as named regions, unsafe links
    made plain text."""
    body = _converter().convert(text or "")

    def code_region(match: "re.Match[str]") -> str:
        language, code = match.group(1) or "", html.unescape(match.group(2))
        label = html.escape(describe_code_block(language, code), quote=True)
        return f'<section role="region" aria-label="{label}">{match.group(0)}</section>'

    def link(match: "re.Match[str]") -> str:
        href = html.unescape(match.group(1))
        safe = _SAFE_LINK.match(href) or parse_link(href) is not None
        return match.group(0) if safe else "<a"

    return _HREF.sub(link, _CODE.sub(code_region, body))


def shift_html_headings(body_html: str, by: int = 1) -> str:
    """Every heading in ``body_html`` ``by`` levels deeper, at most level 6.
    Safe on the converter's output: raw HTML is switched off, so a "<h1" in a
    message's text or code is already escaped and never matches."""
    return _HTML_HEADING.sub(lambda m: f"<{m.group(1)}h{min(int(m.group(2)) + by, 6)}",
                             body_html)


def html_page(title: str, body_html: str, *, landmark: bool = False) -> str:
    """A whole page around ``body_html``, which must already be safe HTML.
    ``title`` is the window's and the page's name.

    In the app the page is the whole window, so it has no landmark: one
    around everything only made the screen reader say "Message: Claude, main
    region" before the message (#341). ``landmark`` wraps it in ``<main>``
    for the pages published on the web, where it sits among other pages."""
    name = html.escape(title, quote=True)
    body = f"<main>{body_html}</main>" if landmark else body_html
    return ("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<meta http-equiv=\"Content-Security-Policy\" "
            "content=\"default-src 'none'; style-src 'unsafe-inline'\">"
            f"<title>{name}</title><style>{_STYLE}</style></head>"
            f"<body>{body}</body></html>")


def message_page(title: str, text: str, heading: str = "") -> str:
    """A message's markdown as a whole page. With ``heading`` (who wrote it,
    "Claude"), the page starts with it as heading 1 and the message's own
    headings move down a level under it (#341), so H finds the top of the
    message and the outline reads speaker, then the message's sections."""
    body = markdown_to_html(text)
    if heading:
        body = f"<h1>{html.escape(heading)}</h1>\n{shift_html_headings(body)}"
    return html_page(title, body)
