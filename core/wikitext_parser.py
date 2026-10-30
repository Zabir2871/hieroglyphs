"""
Turns raw MediaWiki wikitext into a tree of headings/subheadings, each
holding its own cleaned body text.

Feature 1: long pages with multiple headings/sections become one
           sub-entry per heading, nested under the page.
Feature 6: nested headings become colon-separated headwords, e.g.
           "Abomination: Golden Son" or "Darrow: Weapon: Slingblade".
Feature 7: <br>, <li>, <div> boundaries in the source markup are flattened
           into comma-separated text instead of being lost.
"""
from __future__ import annotations
import re
import mwparserfromhell
from mwparserfromhell.nodes import Heading, Tag, Wikilink, Template

BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)


class Section:
    __slots__ = ("title", "level", "content_nodes", "children")

    def __init__(self, title: str | None, level: int):
        self.title = title
        self.level = level
        self.content_nodes = []
        self.children: list["Section"] = []

    def heading_path(self) -> list[str]:
        return [self.title] if self.title else []


def build_section_tree(wikitext: str) -> Section:
    """Parse wikitext into a nested Section tree keyed by heading level."""
    code = mwparserfromhell.parse(wikitext)
    root = Section(title=None, level=1)
    stack = [root]
    for node in code.nodes:
        if isinstance(node, Heading):
            level = node.level
            title = clean_inline(str(node.title))
            sec = Section(title=title, level=level)
            while len(stack) > 1 and stack[-1].level >= level:
                stack.pop()
            stack[-1].children.append(sec)
            stack.append(sec)
        else:
            stack[-1].content_nodes.append(node)
    return root


def _flatten_list_and_div_markup(raw: str) -> str:
    """
    Feature 7: collapse <br>, <li>, <div> boundaries to comma separation
    so list-like wiki content reads as a flat, comma-separated definition
    instead of being squashed into a run-on sentence or losing structure.
    """
    text = raw

    # <li>item</li> -> "item, "
    def li_repl(m):
        inner = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        return f"{inner}, " if inner else ""
    text = re.sub(r"<li[^>]*>(.*?)</li>", li_repl, text, flags=re.IGNORECASE | re.DOTALL)

    # <div>...</div> -> keep inner text, join siblings with ", "
    def div_repl(m):
        inner = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        return f"{inner}, " if inner else ""
    text = re.sub(r"<div[^>]*>(.*?)</div>", div_repl, text, flags=re.IGNORECASE | re.DOTALL)

    # remaining <br> / <br/> -> ", "
    text = BR_RE.sub(", ", text)

    # tidy up any leftover tags and repeated separators
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s*,\s*,+", ", ", text)
    text = re.sub(r",\s*$", "", text.strip())
    return text


BULLET_LINE_RE = re.compile(r"^[ \t]*[\*#:]+[ \t]*(.*)$")


def _flatten_wiki_bullet_lists(text: str) -> str:
    """
    Feature 7 (extended to native wiki list syntax, not just literal HTML):
    consecutive '* item' / '# item' lines are joined into one comma
    separated run instead of being left as a hard-to-read stack of
    newlines, matching the same treatment given to <li>.
    """
    lines = text.split("\n")
    out = []
    buffer = []

    def flush():
        if buffer:
            joined = ", ".join(x.strip() for x in buffer if x.strip())
            if joined:
                out.append(joined)
            buffer.clear()

    for line in lines:
        m = BULLET_LINE_RE.match(line)
        if m:
            buffer.append(m.group(1))
        else:
            flush()
            out.append(line)
    flush()
    return "\n".join(out)


def clean_inline(wikitext_fragment: str) -> str:
    """Strip a small inline fragment (e.g. a heading title) down to plain text."""
    code = mwparserfromhell.parse(wikitext_fragment)
    return code.strip_code().strip()


# --- File:/Image: link handling -----------------------------------------
# [[File:X.png|left|50px|Some caption]] and similar embeds are wikilinks
# under the hood, but mwparserfromhell only knows "title" (File:X.png) and
# "text" (everything after the first "|", i.e. "left|50px|Some caption"
# as one raw string, pipes and all). Treating that raw string as display
# text - which a naive link.text fallback does - leaks positioning/size
# keywords straight into the dictionary ("left|50px Darrow right|50px").
# This pulls out only a genuine caption, if one is present, and drops the
# image reference entirely otherwise.

_FILE_NAMESPACE_RE = re.compile(r"^(file|image|media)\s*:", re.IGNORECASE)
_IMAGE_OPTION_RE = re.compile(
    r"^(thumb|thumbnail|frame|framed|frameless|border|left|right|center|centre|"
    r"none|upright(=.*)?|\d+\s*x?\s*\d*\s*px|alt=.*|link=.*|page=.*|class=.*)$",
    re.IGNORECASE,
)


def _wikilink_display_text(link) -> str:
    """Return the right display string for a wikilink node, or '' to drop
    it entirely (used for bare image embeds with no real caption)."""
    title = str(link.title).strip()
    if _FILE_NAMESPACE_RE.match(title):
        if not link.text:
            return ""  # bare [[File:X.png]] - nothing worth keeping
        # Everything after the first "|" is packed into .text, still
        # pipe-separated - split it back out and keep only the parts that
        # aren't positioning/sizing/alt/link options. What's left (if
        # anything) is the actual caption.
        parts = [p.strip() for p in str(link.text).split("|")]
        caption_parts = [p for p in parts if p and not _IMAGE_OPTION_RE.match(p)]
        return clean_inline(caption_parts[-1]) if caption_parts else ""
    display = link.text if link.text else link.title
    return clean_inline(str(display))


def wikitext_to_text(nodes) -> str:
    """
    Convert a list of mwparserfromhell nodes (one section's body) into a
    clean, flat, comma-aware text block.
    """
    fragment = mwparserfromhell.parse("")
    for n in nodes:
        fragment.append(n)
    raw = str(fragment)

    # Flatten list/div/br structure first, while tags are still intact.
    raw = _flatten_list_and_div_markup(raw)
    raw = _flatten_wiki_bullet_lists(raw)

    # Re-parse to strip remaining wiki markup ([[links]], '''bold''', {{tmpl}})
    code = mwparserfromhell.parse(raw)

    for link in code.filter_wikilinks():
        try:
            code.replace(link, _wikilink_display_text(link))
        except ValueError:
            pass

    # Drop templates entirely from body prose (infobox data is handled
    # separately by infobox_extractor.py) but keep simple {{Small|text}}
    # style wrapper templates' inner text when it's the only argument.
    for tmpl in code.filter_templates():
        try:
            if len(tmpl.params) == 1:
                code.replace(tmpl, clean_inline(str(tmpl.params[0].value)))
            else:
                code.replace(tmpl, "")
        except ValueError:
            pass

    text = code.strip_code(normalize=True, collapse=True)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"\s*,\s*,+", ", ", text)
    return text.strip()


def extract_wikilink_targets(nodes) -> set[str]:
    """Collect every [[Page]] target referenced within a set of nodes -
    used to build the cross-reference / "mentioned in" index."""
    fragment = mwparserfromhell.parse("")
    for n in nodes:
        fragment.append(n)
    targets = set()
    for link in fragment.filter_wikilinks():
        title = clean_inline(str(link.title)).split("#")[0].strip()
        if title:
            targets.add(title)
    return targets


# --- alias / nickname extraction --------------------------------------
# Fandom wikis rarely rely only on real MediaWiki redirect pages for a
# character's nicknames - "The Jackal" for Adrius au Augustus, "Mustang"
# for Virginia au Augustus, "Goliath" for Karnus au Bellona are usually
# stated in the infobox and/or the opening paragraph rather than being
# their own redirect page. To match how Runik surfaces these, we pull
# aliases from both places directly.

INFOBOX_ALIAS_PARAMS = {
    "alias", "aliases", "also known as", "alsoknownas", "also_known_as",
    "nickname", "nicknames", "other names", "othernames", "other_names",
    "titles", "title", "epithet", "epithets", "akas", "aka",
}

PROSE_ALIAS_PATTERNS = [
    re.compile(r"\bknown as the ([A-Z][A-Za-z' \-]{1,40}?)(?:[.,;\n]|$)"),
    re.compile(r"\bknown as an? ([A-Z][A-Za-z' \-]{1,40}?)(?:[.,;\n]|$)"),
    re.compile(r"\bknown by the epithet of ([A-Z][A-Za-z' \-]{1,40}?)(?:[.,;\n]|$)"),
    re.compile(r"\balso known as ([A-Z][A-Za-z' \-]{1,40}?)(?:[.,;\n]|$)"),
    re.compile(r"\bnicknamed ([A-Z][A-Za-z' \-]{1,40}?)(?:[.,;\n]|$)"),
    re.compile(r'"([A-Z][A-Za-z\' \-]{1,30})"'),
]

# Template-name patterns that are clearly *not* an infobox, even though
# they might otherwise pass the "has several named params" heuristic
# below (citations, hatnotes, navboxes, etc.).
_NON_INFOBOX_TEMPLATE_NAME_RE = re.compile(
    r"^(quote|cite|citation|reflist|ref|main|see ?also|for|redirect|coord|"
    r"convert|lang|nihongo|sup|sub|clear|compact ?toc|toc|shortdesc|pp-|"
    r"use ?[nm]dy|use ?dmy|about|distinguish|other ?uses|hatnote|navbox|"
    r"dablink|gallery|quotebox|citation needed|fact|when|who|clarify|"
    r"dead ?link|plainlist|hlist|flatlist|ubl|unbulleted ?list|"
    r"bulleted ?list|reflist|notelist)",
    re.IGNORECASE,
)


def _is_infobox_template(tmpl) -> bool:
    """
    True if this template call looks like a character/place/item infobox.

    Not every wiki names its infobox template literally "Infobox X" -
    some use "Character", "CharacterBox", "Person", etc. Requiring the
    literal substring "infobox" was silently skipping those wikis'
    infoboxes entirely (a likely cause of "metadata missing on some
    entries"). So: templates literally named with "infobox" always count;
    otherwise, any template with several named (non-positional) params
    that isn't a known non-infobox utility template is treated as one too
    - infoboxes are reliably the most param-heavy template on a page.
    """
    name = str(tmpl.name).strip().lower()
    if "infobox" in name:
        return True
    if _NON_INFOBOX_TEMPLATE_NAME_RE.match(name):
        return False
    named_params = [p for p in tmpl.params if not str(p.name).strip().isdigit()]
    return len(named_params) >= 4


# Templates commonly used *inside* an infobox value to lay out a list
# (e.g. |starring = {{Plainlist|* Actor One|* Actor Two}}) - these need to
# be unwrapped into their list items rather than stripped to nothing,
# which is what a plain strip_code() would otherwise do to them.
_LIST_WRAPPER_TEMPLATES = {
    "plainlist", "hlist", "flatlist", "ubl", "unbulleted list",
    "unbulleted_list", "bulleted list", "bulleted_list", "collapsible list",
    "startflatlist", "stack", "nowrap",
}


def _clean_infobox_value(value) -> str:
    """Clean a single infobox parameter's value into flat text, handling
    the wiki-list-wrapper templates that a plain clean_inline() would
    otherwise silently delete."""
    code = mwparserfromhell.parse(str(value))

    for tmpl in code.filter_templates(recursive=True):
        name = str(tmpl.name).strip().lower()
        if name in _LIST_WRAPPER_TEMPLATES:
            items = [clean_inline(str(p.value)) for p in tmpl.params
                      if not str(p.name).strip().isdigit() or True]
            items = [i for i in items if i]
            try:
                code.replace(tmpl, ", ".join(items))
            except ValueError:
                pass

    for link in code.filter_wikilinks():
        try:
            code.replace(link, _wikilink_display_text(link))
        except ValueError:
            pass

    for tmpl in code.filter_templates():
        try:
            if len(tmpl.params) == 1:
                code.replace(tmpl, clean_inline(str(tmpl.params[0].value)))
            else:
                code.replace(tmpl, "")
        except ValueError:
            pass

    raw = str(code)
    raw = _flatten_list_and_div_markup(raw)
    raw = _flatten_wiki_bullet_lists(raw)
    raw = re.sub(r"<[^>]+>", "", raw)
    raw = re.sub(r"\s+", " ", raw).strip(" ,")
    return raw


def extract_infobox_aliases(wikitext: str) -> list[str]:
    """Pull alias-like parameters out of the page's infobox."""
    aliases: list[str] = []
    try:
        code = mwparserfromhell.parse(wikitext)
    except Exception:
        return aliases
    for tmpl in code.filter_templates():
        if not _is_infobox_template(tmpl):
            continue
        for param in tmpl.params:
            pname = str(param.name).strip().lower()
            if pname in INFOBOX_ALIAS_PARAMS:
                value = _clean_infobox_value(param.value)
                for piece in re.split(r"[,;\n/]| or ", value):
                    piece = piece.strip(" \t'\"")
                    if piece and len(piece) <= 40:
                        aliases.append(piece)
    return aliases


def extract_prose_aliases(clean_text: str) -> list[str]:
    """Pull epithets/nicknames out of already-cleaned intro prose, e.g.
    '...known by the epithet of The Jackal...' -> 'The Jackal'."""
    aliases: list[str] = []
    for pattern in PROSE_ALIAS_PATTERNS:
        for m in pattern.finditer(clean_text):
            candidate = m.group(1).strip()
            if candidate and len(candidate) <= 40:
                aliases.append(candidate)
    return aliases


def derive_short_name_alias(headword: str) -> str | None:
    """
    Many fantasy/sci-fi wikis (Red Rising's 'Firstname au Lastname' being
    the canonical example) use a full formal name as the article title but
    readers search by first name. If the headword looks like a multi-word
    proper name, offer its first token as an extra alias, e.g.
    'Adrius au Augustus' -> 'Adrius', 'Darrow au Andromedus' -> 'Darrow'.
    """
    words = headword.split()
    if len(words) >= 2 and words[0][:1].isupper() and words[0].isalpha():
        return words[0]
    return None


# --- infobox metadata (the "notes under the image") --------------------
# Fandom's portable infoboxes sit at the top-right of an article and show
# labelled data rows (Affiliation, Born, Species, Portrayed by...) plus an
# image with its own caption underneath. In the article's own wikitext
# this is just a template call like {{Infobox character|name=...|image=
# ...|caption=...|born=...}} - per request, we include *every* labelled
# param here (only excluding params that are pure CSS/layout, which never
# carry real-world information).

_METADATA_BLOCKLIST = {
    "align", "width", "border", "bgcolor", "color", "style",
    "css", "bodystyle", "titlestyle", "headerstyle", "imagestyle",
    "imagewidth", "imagesize", "image_size", "imgwidth", "imgsize",
}


def extract_infobox_metadata(wikitext: str) -> list[tuple[str, str]]:
    """Returns [(label, value), ...] for every informative infobox
    parameter on the page, in the order they appear in the infobox."""
    metadata: list[tuple[str, str]] = []
    try:
        code = mwparserfromhell.parse(wikitext)
    except Exception:
        return metadata

    for tmpl in code.filter_templates():
        if not _is_infobox_template(tmpl):
            continue
        for param in tmpl.params:
            raw_name = str(param.name).strip()
            pname = raw_name.lower()
            if pname in _METADATA_BLOCKLIST:
                continue

            value = _clean_infobox_value(param.value)
            if not value:
                continue

            if "caption" in pname:
                label = "Image caption"
            elif pname == "name":
                label = "Name"
            else:
                label = " ".join(w.capitalize() for w in raw_name.replace("_", " ").replace("-", " ").split())
            metadata.append((label, value))

    return metadata
