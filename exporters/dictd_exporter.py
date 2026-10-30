"""
Plain-text "dictd" style export: a <name>.dict text file (one entry per
block, headword line + indented body) plus a <name>.index tab file
(headword, base64-ish dictd offset codes are overkill for local use, so
we emit simple byte offset/length in decimal - readable and good enough
for round-tripping through the app's own importer or dictfmt).
"""
from __future__ import annotations
import os
from core.models import Dictionary
from exporters.text_utils import body_with_see_more


def export_dict(dictionary: Dictionary, out_dir: str, base_name: str) -> tuple[str, str]:
    dict_path = os.path.join(out_dir, f"{base_name}.dict")
    index_path = os.path.join(out_dir, f"{base_name}.index")

    entries = [e for e in dictionary.entries if not e.redirect_to and e.body.strip()]
    entries.sort(key=lambda e: e.headword.casefold())

    index_lines = []
    with open(dict_path, "w", encoding="utf-8") as df:
        for e in entries:
            block = f"{e.headword}\n{body_with_see_more(e)}\n\n"
            offset = df.tell()
            df.write(block)
            length = len(block.encode("utf-8"))
            aliases = ";".join(e.aliases)
            index_lines.append(f"{e.headword}\t{offset}\t{length}\t{aliases}")

    with open(index_path, "w", encoding="utf-8") as f:
        f.write("\n".join(index_lines) + "\n")

    return dict_path, index_path
