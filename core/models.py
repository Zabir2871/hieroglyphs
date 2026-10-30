"""
Core data model shared across the whole app.

An Entry is one dictionary "headword" -> definition, e.g.:
    headword = "Darrow: Weapon: Slingblade"
    aliases  = ["Slingblade"]
    body     = "A curved, gravity-defying blade that..."
    source_page = "Darrow au Andromedus"
    source_url  = "https://redrising.fandom.com/wiki/Darrow_au_Andromedus"

Redirect / "did you mean" style aliases (Roque -> Roque au Fabii) are just
another Entry whose `redirect_to` field points at the real headword instead
of carrying its own body text. This maps 1:1 onto StarDict's .syn file and
also drives in-app search redirection (feature 10).
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional
import uuid


@dataclass
class Entry:
    headword: str
    body: str = ""
    aliases: list[str] = field(default_factory=list)
    source_page: str = ""
    source_url: str = ""
    redirect_to: Optional[str] = None  # if set, this entry is a pure alias/redirect
    see_more: list[str] = field(default_factory=list)  # sibling headwords from the same source page
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Entry":
        return Entry(
            headword=d.get("headword", ""),
            body=d.get("body", ""),
            aliases=list(d.get("aliases", [])),
            source_page=d.get("source_page", ""),
            source_url=d.get("source_url", ""),
            redirect_to=d.get("redirect_to"),
            see_more=list(d.get("see_more", [])),
            id=d.get("id") or uuid.uuid4().hex[:12],
        )


@dataclass
class Dictionary:
    name: str = "My Fandom Dictionary"
    base_url: str = ""
    description: str = ""
    entries: list[Entry] = field(default_factory=list)

    def to_dict(self):
        return {
            "name": self.name,
            "base_url": self.base_url,
            "description": self.description,
            "entries": [e.to_dict() for e in self.entries],
        }

    @staticmethod
    def from_dict(d: dict) -> "Dictionary":
        dct = Dictionary(
            name=d.get("name", "My Fandom Dictionary"),
            base_url=d.get("base_url", ""),
            description=d.get("description", ""),
        )
        dct.entries = [Entry.from_dict(e) for e in d.get("entries", [])]
        return dct

    def add(self, entry: Entry):
        self.entries.append(entry)

    def headwords_index(self) -> dict:
        """headword (lowercased) -> Entry, for fast lookup / merge checks."""
        return {e.headword.lower(): e for e in self.entries}
