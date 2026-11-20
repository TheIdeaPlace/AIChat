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
* Links keep only web, email and session targets. In-app reading additionally
  links local files (#253); the viewer intercepts them and checks safety and
  existence before opening. Exported HTML has no local file actions.
"""
from __future__ import annotations

import html
import re
import uuid

import markdown
from markdown.extensions.fenced_code import FencedCodeExtension
from markdown.extensions.sane_lists import SaneListExtension
from markdown.extensions.tables import TableExtension

from .links import parse_link
from . import platform_paths
from .local_files import file_url, local_references

_CODE = re.compile(r'<pre><code(?: class="language-([^"]+)")?>(.*?)</code></pre>', re.DOTALL)
_HREF = re.compile(r'<a href="([^"]*)"')
_SAFE_LINK = re.compile(r"(?i)^(https?:|mailto:)")
_HTML_HEADING = re.compile(r"<(/?)h([1-6])(?=[\s>])")


def opens_in_browser(href: str) -> bool:
    """Whether a link in a message may go to the default browser: http,
    https and mailto only (Tools, Links uses the same rule, #190)."""
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


def markdown_to_html(text: str, cwd: str = "", *, file_links: bool = False) -> str:
    """The body HTML for ``text``: code blocks as named regions, unsafe links
    made plain text."""
    text = text or ""
    # Protect paths before Markdown consumes their backslashes or spaces.
    # An unpredictable prefix keeps message text from impersonating a token.
    prefix = 'TCPLOCAL' + uuid.uuid4().hex
    references = local_references(text, cwd) if file_links else []
    replacements = {}
    pieces, end = [], 0
    for index, ref in enumerate(references):
        token = f'{prefix}N{index}END'
        replacements[token] = ref
        pieces.extend((text[end:ref.start], token))
        end = ref.end
    pieces.append(text[end:])
    body = _converter().convert(''.join(pieces))

    def code_region(match: "re.Match[str]") -> str:
        language, code = match.group(1) or "", html.unescape(match.group(2))
        label = html.escape(describe_code_block(language, code), quote=True)
        return f'<section role="region" aria-label="{label}">{match.group(0)}</section>'

    def link(match: "re.Match[str]") -> str:
        href = html.unescape(match.group(1))
        safe = _SAFE_LINK.match(href) or parse_link(href) is not None
        return match.group(0) if safe else "<a"

    body = _HREF.sub(link, _CODE.sub(code_region, body))

    def file_link(match):
        ref = replacements[match.group()]
        status = platform_paths.local_file_status(ref.path, quick=True)
        label = ref.words or ref.text
        name = f'{label}, file' + (f'. {status.reason}' if status.reason else '')
        return (f'<a href="{html.escape(file_url(ref.path), quote=True)}" '
                f'aria-label="{html.escape(name, quote=True)}">{html.escape(label)}</a>')

    token_pattern = re.compile(prefix + r'N\d+END')

    def original(match):
        ref = replacements[match.group()]
        return html.escape(ref.words or ref.text, quote=True)

    # Markdown can copy a token into a code fence's language attribute or
    # a reference-style link label. Restore text there, never nested links
    # or markup inside an attribute. All tags here came from the converter.
    parts = re.split(r'(<[^>]*>)', body)
    anchor_depth = 0
    for index, part in enumerate(parts):
        if index % 2:
            if re.match(r'<a(?:\s|>)', part):
                anchor_depth += 1
            elif part == '</a>':
                anchor_depth = max(0, anchor_depth - 1)
            parts[index] = token_pattern.sub(original, part)
        else:
            parts[index] = token_pattern.sub(original if anchor_depth else file_link, part)
    return ''.join(parts)


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


#: WebView2 refuses a page passed in directly past 1.57 MB of UTF-8 (measured, on
#: ASCII, CJK and emoji), leaving a blank page and saying nothing, so a longer one
#: goes through a file. A long chat with tool activity on measured 5 MB.
DIRECT_PAGE_BYTES = 1_500_000


def needs_page_file(page_html: str) -> bool:
    # surrogatepass: a tool's output cut mid-emoji leaves half of one, from JSON.
    return len(page_html.encode("utf-8", "surrogatepass")) > DIRECT_PAGE_BYTES


def page_file_bytes(page_html: str) -> bytes:
    return page_html.encode("utf-8", "replace")


def go_to_script(anchor: str) -> str:
    """Script moving the page to the heading ``anchor``: focus, so a screen
    reader's browse mode starts there, and scrolled into view for anyone looking.
    "" for anything that isn't one of ``export.message_anchor``'s."""
    if not re.fullmatch(r"m\d+", anchor or ""):
        return ""
    # By heading: a message's own code fence can carry an id of its own.
    return (f"var e=document.querySelector('h2#{anchor},h3#{anchor}');"
            "if(e){e.setAttribute('tabindex','-1');e.focus();e.scrollIntoView();}")


def message_page(title: str, text: str, heading: str = "", cwd: str = "") -> str:
    """A message's markdown as a whole page. With ``heading`` (who wrote it,
    "Claude"), the page starts with it as heading 1 and the message's own
    headings move down a level under it (#341), so H finds the top of the
    message and the outline reads speaker, then the message's sections."""
    body = markdown_to_html(text, cwd, file_links=True)
    if heading:
        body = f"<h1>{html.escape(heading)}</h1>\n{shift_html_headings(body)}"
    return html_page(title, body)
