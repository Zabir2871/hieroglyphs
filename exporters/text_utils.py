from __future__ import annotations
import html
import re
from core.models import Entry


def body_with_see_more(e: Entry) -> str:
    """
    Flat-text export formats (dictd, TXT, CSV) can't render tappable
    links, so bake the "See More" list into the body as a text tail here
    - the web app itself renders Entry.see_more as real clickable links
    instead of using this.
    """
    body = e.body.rstrip()
    if e.see_more:
        body += "\n\nSee More:\n" + "\n".join(f"- {h}" for h in e.see_more)
    return body


# --- HTML rendering for StarDict's sametypesequence=h ---------------------
# Plain-text StarDict entries (sametypesequence=m) can't have tappable
# cross-references at all - StarDict's HTML entry type can, using the
# "bword://" scheme that GoldenDict, Aard2, and most other StarDict-
# compatible desktop/mobile readers recognise for "jump to this other
# headword in the same dictionary".
#
# KOReader is a documented exception: its own wiki states dictionary
# lookup is triggered only by holding a word *in the book you're
# reading*, and the definition popup itself is a static MuPDF-rendered
# HTML view - there's no tap-to-navigate *within* that popup. So on
# KOReader specifically, "See More" will render as a clean, bolded list
# of plain headwords rather than clickable links - real, but inert there.
# The same HTML file works with full clickable links wherever the reader
# supports "bword://" (GoldenDict, Aard2, etc).

_METADATA_LINE_RE = re.compile(r"^- ([^:]+):\s*(.*)$")


def html_body_with_see_more(e: Entry) -> str:
    body = e.body.rstrip()
    lines = body.split("\n")
    html_lines = []
    in_metadata = False
    for line in lines:
        if line.strip() == "Metadata:":
            in_metadata = True
            html_lines.append("<br><b>Metadata:</b><br>")
            continue
        m = _METADATA_LINE_RE.match(line) if in_metadata else None
        if m:
            label, value = m.groups()
            html_lines.append(f"&bull; <b>{html.escape(label)}:</b> {html.escape(value)}<br>")
        else:
            in_metadata = False
            html_lines.append(html.escape(line) + "<br>" if line.strip() else "<br>")

    out = "\n".join(html_lines)

    if e.see_more:
        links = " &middot; ".join(
            f'<a href="bword://{html.escape(h)}">{html.escape(h)}</a>' for h in e.see_more
        )
        out += f'<br><b>See More:</b><br>{links}'

    return out

