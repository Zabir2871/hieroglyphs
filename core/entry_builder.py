from __future__ import annotations
from core.models import Entry
from core.wikitext_parser import (
    Section, wikitext_to_text,
    extract_infobox_aliases, extract_prose_aliases, derive_short_name_alias,
    extract_infobox_metadata,
)
from core.tense_converter import convert_text_to_present

MIN_SECTION_CHARS = 40  # skip near-empty sections (nav boxes, stubs, etc.)

# --- alias confidence levels ---------------------------------------------
# Different sources of an alias carry very different certainty that the
# word really means "this exact entry", and a naive "root beats sub-entry"
# or "first-come-wins" rule gets it backwards in at least one direction:
#
#   - A real MediaWiki redirect or an infobox's own "aliases" field is a
#     deliberate, explicit statement by the wiki's editors ("Mustang" IS
#     Virginia au Augustus) - highest confidence.
#   - A heading's own name is equally deliberate for *that specific
#     sub-topic* ("Technology: Cupid's Kiss" - the heading literally names
#     the thing being defined) - also high confidence, just scoped to one
#     sub-entry rather than a whole page.
#   - A short/first name derived from a multi-word title is a solid but
#     slightly weaker inference.
#   - An epithet/nickname guessed from opening prose via regex is the
#     least reliable - it can misfire on any capitalized phrase near
#     "known as", including ones that are actually *about* something else
#     entirely (e.g. a "Technology" page's intro mentioning a specific
#     gadget by name shouldn't let that gadget's name get claimed by the
#     whole "Technology" page instead of its own dedicated subsection).
#
# When the same word is claimed by two different entries, the higher
# priority wins outright; equal priority keeps whichever claimed it first.
PRIORITY_REDIRECT = 100
PRIORITY_INFOBOX = 95
PRIORITY_SHORT_NAME = 90
PRIORITY_HEADING_SELF = 70
PRIORITY_PROSE_GUESS = 20


def build_entries_for_page(
    page_title: str,
    root_section: Section,
    article_url: str,
    apply_tense_fix: bool = True,
    wikitext: str = "",
) -> tuple[list[Entry], list[tuple[Entry, str, int]]]:
    """
    Walk a page's Section tree and emit:
      - one Entry for the page itself (its intro / root content), headword = page title
      - one Entry per heading, headword = "Page: Heading" (feature 1)
      - one Entry per nested subheading, headword = "Page: Heading: Subheading" (feature 6)

    Returns (entries, alias_candidates). alias_candidates is
    [(entry, alias_string, priority), ...] for every alias this page's
    content suggests - nothing is written to entry.aliases directly here,
    because a word suggested on this page might collide with a
    higher-confidence claim from a completely different page crawled
    later. The caller (crawler.py) collects candidates from every page
    first, then resolves all of them together so the win always goes to
    the most confident source, regardless of crawl order.
    """
    entries: list[Entry] = []
    candidates: list[tuple[Entry, str, int]] = []

    def make_entry(headword: str, nodes) -> Entry | None:
        body = wikitext_to_text(nodes)
        if len(body) < MIN_SECTION_CHARS:
            return None
        if apply_tense_fix:
            body = convert_text_to_present(body)
        e = Entry(
            headword=headword,
            body=body,
            source_page=page_title,
            source_url=article_url,
        )
        entries.append(e)
        return e

    # root/intro content -> main page entry
    root_entry = make_entry(page_title, root_section.content_nodes)

    def walk(section: Section, path: list[str]):
        for child in section.children:
            new_path = path + [child.title]
            headword = f"{page_title}: " + ": ".join(new_path)
            sub_entry = make_entry(headword, child.content_nodes)
            if sub_entry is not None:
                # The heading's own name is a deliberate, specific label
                # for exactly this sub-topic (feature 6/10: "Cupid's Kiss"
                # should find "Technology: Cupid's Kiss" directly).
                leaf = child.title.strip()
                if leaf and leaf.lower() != headword.lower():
                    candidates.append((sub_entry, leaf, PRIORITY_HEADING_SELF))
            walk(child, new_path)

    walk(root_section, [])

    # If the page had *no* heading structure at all and its intro was too
    # short to qualify, still keep a minimal stub so the page isn't lost.
    if not entries:
        raw = wikitext_to_text(root_section.content_nodes)
        if apply_tense_fix:
            raw = convert_text_to_present(raw)
        root_entry = Entry(headword=page_title, body=raw or "(no extractable content)",
                            source_page=page_title, source_url=article_url)
        entries.append(root_entry)

    # --- root-page aliases/nicknames, independent of real wiki redirects
    # (feature 10, matching Runik's behaviour where typing "Jackal" jumps
    # straight to "Adrius au Augustus") ---
    if root_entry is not None:
        if wikitext:
            for alias in extract_infobox_aliases(wikitext):
                candidates.append((root_entry, alias, PRIORITY_INFOBOX))

        for alias in extract_prose_aliases(root_entry.body):
            candidates.append((root_entry, alias, PRIORITY_PROSE_GUESS))

        short_name = derive_short_name_alias(page_title)
        if short_name:
            candidates.append((root_entry, short_name, PRIORITY_SHORT_NAME))

        # "The Jackal" -> also offer bare "Jackal" at the same confidence
        # as whatever produced "The Jackal" in the first place.
        for entry, alias, priority in list(candidates):
            if entry is root_entry and alias.lower().startswith("the "):
                bare = alias[4:].strip()
                if bare:
                    candidates.append((root_entry, bare, priority))

        # --- infobox metadata: the labelled notes shown beside/under the
        # infobox image (Affiliation, Born, Species, image caption, etc.)
        # get appended as a bulleted list on the page's entry, verbatim.
        if wikitext:
            metadata = extract_infobox_metadata(wikitext)
            if metadata:
                lines = [f"- {label}: {value}" for label, value in metadata]
                root_entry.body = root_entry.body.rstrip() + "\n\nMetadata:\n" + "\n".join(lines)

    return entries, candidates


def attach_see_more_sections(entries: list[Entry]):
    """
    "See More" - every entry gets a structured list of every *other*
    entry that came from the same original wiki page (kept as data on
    Entry.see_more, not baked into body text, so the web UI can render
    them as tappable links). Exporters that only support flat text
    append a formatted version at export time instead.

    (This replaces an earlier, buggier "mentioned alongside" heuristic
    that grouped entries by shared wikilinks - that produced noisy,
    near-random groupings whenever two unrelated pages both happened to
    link to the same popular page. Grouping by source_page instead is
    exact: it always reflects the real table of contents of the page an
    entry came from.)
    """
    by_source_page: dict[str, list[Entry]] = {}
    for e in entries:
        by_source_page.setdefault(e.source_page, []).append(e)

    for source_page, group in by_source_page.items():
        if len(group) < 2:
            continue
        for e in group:
            e.see_more = [g.headword for g in group if g.id != e.id]
