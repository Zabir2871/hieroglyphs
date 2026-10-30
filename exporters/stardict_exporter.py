"""
StarDict export.

StarDict dictionaries are the format both desktop tools (GoldenDict) and
KOReader (feature 5) understand natively - a folder containing:
  <name>.ifo   text header
  <name>.idx   sorted index: word\\0 + big-endian(offset) + big-endian(size)
  <name>.dict  concatenated definition bodies (HTML - see below)
  <name>.syn   synonyms/aliases: word\\0 + big-endian(index into .idx)  (feature 12)

Entries are written as HTML (sametypesequence=h) rather than plain text
specifically so the "See More" section can use real "bword://" links -
StarDict's standard scheme for "jump to this other headword in the same
dictionary", understood by GoldenDict, Aard2, and most other StarDict
readers. KOReader is a known exception: per KOReader's own documentation,
its dictionary lookup only triggers from holding a word in the book
you're reading, and the definition popup is a static rendered view with
no tap-navigation inside it - so "See More" will still render clearly
there (bolded headwords), just not as tappable links, in this one reader.

Redirect-only entries (Entry.redirect_to set, no body of its own) are
written purely as .syn records pointing at their target's real headword,
which is exactly how StarDict expects "see also" / alias lookups to work
(feature 10: searching "Roque" jumps straight to "Roque au Fabii").
"""
from __future__ import annotations
import os
import struct
import time
from core.models import Dictionary
from exporters.text_utils import html_body_with_see_more


def _slug(name: str) -> str:
    keep = [c if c.isalnum() else "_" for c in name.strip()]
    slug = "".join(keep).strip("_") or "dictionary"
    return slug.lower()


def export_stardict(dictionary: Dictionary, out_dir: str) -> str:
    """
    Writes the four StarDict files into out_dir/<slug>/ and returns that
    directory path. Caller is responsible for zipping it if a single
    downloadable file is wanted.
    """
    slug = _slug(dictionary.name)
    target_dir = os.path.join(out_dir, slug)
    os.makedirs(target_dir, exist_ok=True)

    # Real, defined entries only for the .idx/.dict pair.
    real_entries = [e for e in dictionary.entries if not e.redirect_to and e.body.strip()]
    real_entries.sort(key=lambda e: e.headword.casefold())

    idx_path = os.path.join(target_dir, f"{slug}.idx")
    dict_path = os.path.join(target_dir, f"{slug}.dict")
    ifo_path = os.path.join(target_dir, f"{slug}.ifo")
    syn_path = os.path.join(target_dir, f"{slug}.syn")

    headword_position = {}  # headword -> 0-based index into idx, needed for .syn
    idx_bytes = bytearray()
    dict_bytes = bytearray()

    with open(dict_path, "wb") as dict_f:
        for position, e in enumerate(real_entries):
            body = html_body_with_see_more(e).encode("utf-8")
            offset = dict_f.tell()
            dict_f.write(body)
            size = len(body)
            idx_bytes += e.headword.encode("utf-8") + b"\x00"
            idx_bytes += struct.pack(">I", offset)
            idx_bytes += struct.pack(">I", size)
            headword_position[e.headword] = position

    with open(idx_path, "wb") as f:
        f.write(bytes(idx_bytes))

    # --- .syn: every alias, plus every pure-redirect entry ---
    syn_records = []  # (synonym_word, target_index)
    for e in real_entries:
        for alias in e.aliases:
            syn_records.append((alias, headword_position[e.headword]))
    for e in dictionary.entries:
        if e.redirect_to and e.redirect_to in headword_position:
            syn_records.append((e.headword, headword_position[e.redirect_to]))

    # de-dupe, keep stable order, sort by word (StarDict convention)
    seen = set()
    deduped = []
    for word, idx in syn_records:
        key = (word.casefold(), idx)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((word, idx))
    deduped.sort(key=lambda t: t[0].casefold())

    syn_bytes = bytearray()
    for word, idx in deduped:
        syn_bytes += word.encode("utf-8") + b"\x00"
        syn_bytes += struct.pack(">I", idx)
    with open(syn_path, "wb") as f:
        f.write(bytes(syn_bytes))

    idx_file_size = os.path.getsize(idx_path)

    ifo_lines = [
        "StarDict's dict ifo file",
        "version=2.4.2",
        f"wordcount={len(real_entries)}",
        f"idxfilesize={idx_file_size}",
        f"bookname={dictionary.name}",
        f"date={time.strftime('%Y.%m.%d')}",
        "sametypesequence=h",
    ]
    if deduped:
        ifo_lines.insert(3, f"synwordcount={len(deduped)}")
    if dictionary.description:
        ifo_lines.append(f"description={dictionary.description}")

    with open(ifo_path, "w", encoding="utf-8") as f:
        f.write("\n".join(ifo_lines) + "\n")

    return target_dir


def export_syn_only(dictionary: Dictionary, out_path: str) -> str:
    """
    Standalone .syn export for people who just want the synonym/alias
    table (feature 12) without regenerating the whole dictionary, e.g. to
    merge into an existing StarDict package by hand. Uses headwords
    (not numeric idx offsets, since there's no accompanying .idx here) -
    format is "alias<TAB>target_headword" per line, which is the common
    plain-text convention tools like pyglossary accept for import as syn.
    """
    real_entries = [e for e in dictionary.entries if not e.redirect_to and e.body.strip()]
    real_headwords = {e.headword for e in real_entries}
    lines = []
    for e in real_entries:
        for alias in e.aliases:
            lines.append(f"{alias}\t{e.headword}")
    for e in dictionary.entries:
        if e.redirect_to and e.redirect_to in real_headwords:
            lines.append(f"{e.headword}\t{e.redirect_to}")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return out_path
