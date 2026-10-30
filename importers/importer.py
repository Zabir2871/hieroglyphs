from __future__ import annotations
import csv
import json
import os
import struct
import glob
from core.models import Dictionary, Entry


def import_json(path: str) -> Dictionary:
    with open(path, "r", encoding="utf-8") as f:
        return Dictionary.from_dict(json.load(f))


def import_csv(path: str, dict_name: str | None = None) -> Dictionary:
    dictionary = Dictionary(name=dict_name or os.path.splitext(os.path.basename(path))[0])
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            aliases = [a.strip() for a in (row.get("aliases") or "").split(";") if a.strip()]
            see_more = [s.strip() for s in (row.get("see_more") or "").split(";") if s.strip()]
            dictionary.add(Entry(
                headword=row.get("headword", "").strip(),
                body=row.get("body", ""),
                aliases=aliases,
                redirect_to=(row.get("redirect_to") or "").strip() or None,
                see_more=see_more,
                source_page=row.get("source_page", ""),
                source_url=row.get("source_url", ""),
            ))
    return dictionary


def import_txt(path: str, dict_name: str | None = None) -> Dictionary:
    """Parses the app's own TXT export format: 'headword | alias | alias'
    line, followed by body lines, blank line between entries. Falls back
    to treating each non-blank line as 'word<TAB>definition' if no blank
    separators are found (common simple word-list format)."""
    dictionary = Dictionary(name=dict_name or os.path.splitext(os.path.basename(path))[0])
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    if "\n\n" in raw:
        blocks = [b for b in raw.split("\n\n") if b.strip()]
        for block in blocks:
            lines = block.split("\n")
            head_line = lines[0]
            body = "\n".join(lines[1:]).strip()
            if head_line.strip().startswith("see:"):
                continue
            headwords = [h.strip() for h in head_line.split("|") if h.strip()]
            if not headwords:
                continue
            if body.startswith("see:"):
                dictionary.add(Entry(headword=headwords[0], redirect_to=body[4:].strip()))
                continue
            dictionary.add(Entry(headword=headwords[0], body=body, aliases=headwords[1:]))
    else:
        for line in raw.splitlines():
            if "\t" in line:
                word, definition = line.split("\t", 1)
                dictionary.add(Entry(headword=word.strip(), body=definition.strip()))
    return dictionary


def import_stardict(folder_or_ifo_path: str) -> Dictionary:
    """Reads a StarDict .ifo/.idx/.dict/.syn set back into a Dictionary."""
    if os.path.isdir(folder_or_ifo_path):
        ifo_candidates = glob.glob(os.path.join(folder_or_ifo_path, "*.ifo"))
        if not ifo_candidates:
            raise FileNotFoundError("No .ifo file found in folder")
        ifo_path = ifo_candidates[0]
    else:
        ifo_path = folder_or_ifo_path

    base = ifo_path[:-4]  # strip ".ifo"
    idx_path, dict_path, syn_path = base + ".idx", base + ".dict", base + ".syn"

    meta = {}
    with open(ifo_path, "r", encoding="utf-8") as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                meta[k] = v

    dictionary = Dictionary(name=meta.get("bookname", os.path.basename(base)))

    with open(dict_path, "rb") as f:
        dict_data = f.read()

    idx_entries = []  # ordered list of (headword, offset, size)
    with open(idx_path, "rb") as f:
        idx_data = f.read()
    pos = 0
    while pos < len(idx_data):
        nul = idx_data.index(b"\x00", pos)
        word = idx_data[pos:nul].decode("utf-8", errors="replace")
        offset, size = struct.unpack(">II", idx_data[nul + 1:nul + 9])
        idx_entries.append((word, offset, size))
        pos = nul + 9

    for word, offset, size in idx_entries:
        body = dict_data[offset:offset + size].decode("utf-8", errors="replace")
        dictionary.add(Entry(headword=word, body=body))

    if os.path.exists(syn_path):
        with open(syn_path, "rb") as f:
            syn_data = f.read()
        pos = 0
        while pos < len(syn_data):
            nul = syn_data.index(b"\x00", pos)
            word = syn_data[pos:nul].decode("utf-8", errors="replace")
            (target_idx,) = struct.unpack(">I", syn_data[nul + 1:nul + 5])
            pos = nul + 5
            if 0 <= target_idx < len(idx_entries):
                target_headword = idx_entries[target_idx][0]
                match = next((e for e in dictionary.entries if e.headword == target_headword), None)
                if match and word not in match.aliases:
                    match.aliases.append(word)

    return dictionary


def import_any(path: str, dict_name: str | None = None) -> Dictionary:
    lower = path.lower()
    if lower.endswith(".json"):
        return import_json(path)
    if lower.endswith(".csv"):
        return import_csv(path, dict_name)
    if lower.endswith(".txt"):
        return import_txt(path, dict_name)
    if lower.endswith(".ifo"):
        return import_stardict(path)
    if os.path.isdir(path):
        return import_stardict(path)
    raise ValueError(f"Unsupported file type for import: {path}")
