# Hieroglyphs — Fandom Wiki Dictionary Builder

A local web app (Flask backend + vanilla JS frontend) that crawls any
MediaWiki/Fandom wiki and turns it into an e-reader dictionary — inspired
by [Runik](https://github.com/Runik-3/core), with per-heading sub-entries,
spoiler-safe present tense, confidence-ranked alias/redirect resolution,
StarDict/KOReader export, and import/export across several formats.

## Quick start

```bash
cd hieroglyphs
python3 -m venv venv && source venv/bin/activate   # optional but recommended
pip install -r requirements.txt
python3 app.py
```

Then open **http://127.0.0.1:5055** in your browser. Everything (the
crawl, the entry database, exports) runs locally — nothing leaves your
machine except the read-only requests to the wiki's own API.

## Using it

1. Paste a wiki's base URL (e.g. `https://redrising.fandom.com`) and hit
   **Count pages** to see how many articles it has before committing to a
   full crawl.
2. Name your dictionary, leave "rewrite past tense" checked (or turn it
   off if you'd rather keep the wiki's original wording), and hit
   **Build dictionary**. The progress bar shows pages processed / total
   and how many entries and aliases have been found so far.
3. Browse/search/edit entries in the **Entries** panel — search is live
   as you type (or press `/` from anywhere to jump into the search box),
   and a **🎲 Random** button on the dictionary panel is there for
   browsing. Anything can be hand-edited or deleted, and you can add
   entries manually.
4. Use the **Export as** buttons to download a single format, or
   **⬇ All formats (.zip)** to get everything in one download:
   - **StarDict** — the standard `.ifo/.idx/.dict/.syn` set, zipped.
   - **KOReader package** — the same StarDict set, packaged to unzip
     straight into `koreader/data/dict/` on your device.
   - **Synonyms only (.syn.txt)** — a plain-text alias→headword map.
   - **dictd** — a simple `.dict`/`.index` pair.
   - **CSV / TXT / JSON** — for spreadsheets, plain-text tools, or
     round-tripping through this app itself.
5. Use **Import dictionary file** to load an existing StarDict (`.ifo`),
   CSV, TXT, or JSON dictionary — merge it into the current project or
   replace it outright.

## Where aliases come from, and how collisions are resolved

Searching a nickname (e.g. "Jackal", "Mustang", or a specific named item
buried in a page like "Cupid's Kiss") jumps straight to the real entry's
full text — no "redirect to..." message, no extra click — with a
"Query: <what you typed>" line at the end so you can see how you got
there. Real MediaWiki redirects are fetched with
`generator=allpages&prop=redirects|revisions` in a single correlated
query (the same approach Runik's own builder uses), so MediaWiki itself
ties each redirect straight to its target page rather than this app
trying to string-match a redirect's target title back to a headword.

Aliases come from several sources, and **the same word can legitimately
mean different things depending on where it was found** — a naive "root
page always wins" or "first one found wins" rule gets this backwards in
one direction or the other:

| Source | Confidence | Example |
|---|---|---|
| Real MediaWiki redirect | Highest | a "Roque" page that redirects to "Roque au Fabii" |
| Infobox alias field | Highest | `\|aliases = Mustang` on Virginia's own page |
| A heading's own name | High | the "Cupid's Kiss" heading *inside* the "Technology" page |
| Short/first name of a multi-word title | Medium-high | "Adrius au Augustus" also answers to "Adrius" |
| Opening-paragraph phrasing ("known as...", quoted nicknames) | Lowest | a guess that can misfire on any capitalized phrase |

When two entries both claim the same word, the higher-confidence source
wins outright, regardless of which page happened to be crawled first.
This resolves both directions correctly:

- **"Mustang"** → a real page's own redirect/infobox alias (confidence:
  highest) beats a coincidental "Adrius au Augustus: Relationships:
  Mustang" subsection elsewhere (confidence: high) → correctly lands on
  **Virginia au Augustus**.
- **"Cupid's Kiss"** → a heading's own specific name (confidence: high)
  beats a vague prose mention on the unrelated "Technology" root page's
  intro (confidence: lowest) → correctly lands on **Technology: Cupid's
  Kiss**, not "Technology" itself.

An alias is also never allowed to shadow a different entry's real
headword — a dedicated page always wins outright over anyone else's
alias claim on that same word.

## Infobox metadata, without the image-placeholder garbage

Every page's entry gets a bulleted "Metadata:" section listing *every*
labelled infobox parameter verbatim — name, image caption, affiliation,
species, born, portrayed by, and so on. Infobox detection isn't limited
to templates literally named "Infobox" (many wikis call theirs
"Character", "CharacterBox", etc.) — any template with several named
parameters that isn't an obvious utility template (citations, navboxes,
list-wrappers) is treated as one. Only pure CSS/layout params (width,
style, bgcolor...) are filtered out:

```
Metadata:
- Name: Virginia au Augustus
- Image: Virginia
- Aliases: Mustang
- Affiliation: House Augustus
- Species: Gold
- Born: 720 PCE
- Portrayed By: Jane Doe
```

`[[File:...|left|50px|Caption]]`-style image embeds are handled
specially: only a genuine caption (if the wiki editor gave one) is kept;
positioning/sizing keywords like `left`, `50px`, `thumb` are recognized
and discarded rather than leaking into the text as garbage.

## See More — a real table of contents, with tappable links where readers support it

Every entry also carries a structured `see_more` list of every *other*
entry generated from the same original wiki page — an exact table of
contents, not a fuzzy "shares a link" guess. In the web app these render
as tappable pills that jump straight to that entry's own definition:

```
See More:
[ Virginia au Augustus: Biography: Pre-Saga ]  [ Virginia au Augustus: Biography: Red Rising ]
```

**StarDict export** uses StarDict's HTML entry type (`sametypesequence=h`)
with real `<a href="bword://Headword">` cross-reference links for "See
More" — this is the standard StarDict convention for "jump to another
headword in the same dictionary", and it's genuinely tappable in
**GoldenDict, Aard2, and most other StarDict-compatible readers**.

**KOReader is a documented exception.** Per KOReader's own wiki,
dictionary lookup is only ever triggered by holding a word *in the book
you're reading* — the definition popup itself is a static view rendered
by MuPDF, with no tap-navigation inside it. This is a limitation of how
KOReader's dictionary feature works, not something a StarDict file can
override. So on KOReader, "See More" still renders cleanly (bolded
headwords, properly formatted), but as plain text rather than links. The
words themselves are still real headwords in the same dictionary, so if
one appears in the book you're reading, holding it will still look it up
normally.

Flat-text export formats (dictd, TXT, CSV) can't carry hyperlinks at all,
so those get the same list appended as plain text.

## Quality-of-life additions

- **Live search-as-you-type** (debounced) instead of requiring Enter.
- **`/` keyboard shortcut** to jump into the search box from anywhere.
- **🎲 Random entry** button for browsing/discovery.
- **Copy definition** button on the quick-view panel.
- **Export everything as one zip** instead of downloading each format
  separately.
- **Alphabetically sorted** browse list.
- Dictionary stats now show total alias count alongside entry count.

## Known limitations (be aware of these)

- **Tense conversion is heuristic, not a real parser.** It handles the
  common "X was/did/killed/..." biography style well (including basic
  passive voice, e.g. "was forged" → "is forged"), but it's not a full
  grammar engine — unusual sentence structures, and negation between an
  auxiliary and its verb (e.g. "had **not** intervened"), can slip
  through unconverted or convert incorrectly. Spot-check important
  entries, or untick the checkbox and edit spoiler-prone lines by hand.
- **Templates are stripped, not expanded** in body prose (infobox data
  is handled separately, see above). Simple single-argument wrapper
  templates (e.g. `{{Small|text}}`) keep their inner text.
- **Not tested against a live Fandom wiki from this environment** (the
  sandbox that built this app has no outbound network access to
  fandom.com). It *is* tested end-to-end — page counting, crawling,
  section/heading splitting, tense conversion, alias/redirect priority
  resolution, infobox metadata extraction, and every export/import path
  — against local mock MediaWiki API servers that mirror the real API's
  response shapes, including deliberately reproducing each reported bug
  scenario before fixing it. Run it against your real wiki and report
  back anything that still looks off — the MediaWiki API surface it
  depends on (`action=query`, `generator=allpages`, `prop=redirects|
  revisions`) is standard, but wikis vary in how consistently editors
  use headings and infobox templates.
- **In-memory project state.** The currently-loaded dictionary lives in
  the Flask process's memory, not a database — export to JSON regularly
  if you're editing a large project, and reload it via Import if you
  restart the server.
