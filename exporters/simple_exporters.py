from __future__ import annotations
import csv
import json
from core.models import Dictionary
from exporters.text_utils import body_with_see_more


def export_csv(dictionary: Dictionary, out_path: str) -> str:
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["headword", "body", "aliases", "redirect_to", "see_more", "source_page", "source_url"])
        for e in dictionary.entries:
            w.writerow([e.headword, e.body, "; ".join(e.aliases), e.redirect_to or "",
                        "; ".join(e.see_more), e.source_page, e.source_url])
    return out_path


def export_txt(dictionary: Dictionary, out_path: str) -> str:
    """Simple word<TAB>definition per block, blank line between entries -
    readable plain text, and importable by most 3rd-party dict tools."""
    with open(out_path, "w", encoding="utf-8") as f:
        for e in dictionary.entries:
            if e.redirect_to:
                f.write(f"{e.headword}\n\tsee: {e.redirect_to}\n\n")
                continue
            headwords = [e.headword] + e.aliases
            f.write(" | ".join(headwords) + "\n")
            f.write(body_with_see_more(e) + "\n\n")
    return out_path


def export_json(dictionary: Dictionary, out_path: str) -> str:
    """Full-fidelity project save format - re-importable losslessly by
    this app (feature 9)."""
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(dictionary.to_dict(), f, ensure_ascii=False, indent=2)
    return out_path
