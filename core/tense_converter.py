"""
Best-effort past-tense -> present-tense converter.

Wiki bios are written retrospectively ("Pax was a student...", "He died
fighting..."), which spoils outcomes (deaths, betrayals, endings) for a
reader looking a name up mid-book. We rewrite finite past-tense verbs to
present tense so dictionary entries read like an in-universe reference
rather than a plot recap ("Pax is a student...").

This is a heuristic, not a full POS tagger - it will not be perfect on
every sentence, but it's tuned for the very common 3rd-person-singular
biography style ("X is/does/has...") that fandom wiki intros use, which
is exactly the pattern in the request's own example.
"""
from __future__ import annotations
import re

# Irregular verbs: past form -> 3rd person singular present form.
# Deliberately biased towards verbs common in character/plot summaries.
IRREGULAR = {
    "was": "is", "were": "are",
    "had": "has",
    "did": "does",
    "went": "goes", "gone": "gone",
    "saw": "sees", "seen": "seen",
    "knew": "knows", "known": "known",
    "became": "becomes",
    "began": "begins", "begun": "begun",
    "came": "comes",
    "took": "takes", "taken": "taken",
    "made": "makes",
    "gave": "gives", "given": "given",
    "found": "finds",
    "thought": "thinks",
    "told": "tells",
    "felt": "feels",
    "left": "leaves",
    "led": "leads",
    "kept": "keeps",
    "brought": "brings",
    "sent": "sends",
    "spent": "spends",
    "built": "builds",
    "grew": "grows", "grown": "grown",
    "won": "wins",
    "lost": "loses",
    "died": "dies",
    "killed": "kills",
    "fought": "fights",
    "held": "holds",
    "met": "meets",
    "ran": "runs",
    "rose": "rises", "risen": "risen",
    "fell": "falls", "fallen": "fallen",
    "broke": "breaks", "broken": "broken",
    "wrote": "writes", "written": "written",
    "spoke": "speaks", "spoken": "spoken",
    "chose": "chooses", "chosen": "chosen",
    "swore": "swears", "sworn": "sworn",
    "bore": "bears", "born": "born",
    "stood": "stands",
    "sat": "sits",
    "understood": "understands",
    "meant": "means",
    "paid": "pays",
    "sought": "seeks",
    "taught": "teaches",
    "caught": "catches",
    "bought": "buys",
    "drove": "drives", "driven": "driven",
    "rode": "rides", "ridden": "ridden",
    "flew": "flies", "flown": "flown",
    "threw": "throws", "thrown": "thrown",
    "hid": "hides", "hidden": "hidden",
    "forgave": "forgives", "forgiven": "forgiven",
    "betrayed": "betrays",
}

AUX_WONT_CONVERT = {"would", "could", "should", "might", "must", "can", "will"}

VOWELS = set("aeiou")

# Common verb stems that drop a silent 'e' before adding '-d' (forge -> forged,
# not forg + ed). Without a full lemma dictionary this can't be derived
# perfectly from spelling alone, so we keep a curated list of stems that
# show up often in wiki character/plot prose.
SILENT_E_STEMS = {
    "forg", "chang", "liv", "lov", "mov", "notic", "forc", "rais", "caus",
    "clos", "continu", "describ", "decid", "provid", "serv", "us", "requir",
    "declar", "escap", "captur", "injur", "honor", "surviv", "arriv",
    "believ", "achiev", "creat", "releas", "replac", "produc", "reduc",
    "introduc", "advanc", "chas", "eras", "phas", "prais", "revis",
    "promis", "surpris", "realiz", "recogniz", "organiz", "wag",
}


def _third_person_s(base: str) -> str:
    if base.endswith(("s", "x", "z", "ch", "sh")):
        return base + "es"
    if base.endswith("y") and len(base) > 1 and base[-2] not in VOWELS:
        return base[:-1] + "ies"
    return base + "s"


def _regular_past_to_present(word: str) -> str | None:
    """Handle regular '-ed' past tense verbs heuristically. Returns None if
    this doesn't look like a regular past-tense verb."""
    lower = word.lower()
    if not lower.endswith("ed") or len(lower) < 4:
        return None
    # studied -> study(+ies), died -> die(+s), etc.
    if lower.endswith("ied"):
        base = lower[:-3] + "y"
        return _third_person_s(base[:-1])  # studied -> studies
    if lower.endswith("eed"):
        return None  # freed/agreed are ambiguous, leave untouched
    stem = lower[:-2]
    # doubled consonant, e.g. stopped -> stop
    if len(stem) >= 2 and stem[-1] == stem[-2] and stem[-1] not in VOWELS:
        base = stem[:-1]
        return _third_person_s(base)
    # curated silent-e verbs, e.g. forged -> forge, lived -> live
    if stem in SILENT_E_STEMS:
        return _third_person_s(stem + "e")
    base = stem
    return _third_person_s(base)


def _convert_word(word: str) -> str:
    lower = word.lower()
    if lower in IRREGULAR:
        repl = IRREGULAR[lower]
    else:
        repl = _regular_past_to_present(word)
        if repl is None:
            return word
    # preserve capitalisation of the original word
    if word[0].isupper():
        repl = repl[0].upper() + repl[1:]
    return repl


_WORD_RE = re.compile(r"[A-Za-z]+")


PASSIVE_COPULAS = {"was", "were", "is", "are", "been", "being", "be"}


def convert_text_to_present(text: str) -> str:
    """Rewrite past-tense verbs in `text` to present tense, word by word.

    Two situations are deliberately left untouched so the grammar stays
    coherent:
      - modal auxiliaries (would/could/should/might have...) keep their
        following past participle as-is.
      - passive voice ("was forged", "is killed") - the copula itself
        still flips to present (was -> is), but the participle after it
        must stay a participle, not become a 3rd-person present verb
        ("is forged", not "is forges").
    """
    out = []
    last_word_orig = ""
    for token in re.split(r"(\W+)", text):
        if _WORD_RE.fullmatch(token):
            lw = last_word_orig.lower()
            if lw in AUX_WONT_CONVERT or lw in ("have", "had") or lw in PASSIVE_COPULAS:
                out.append(token)
            else:
                out.append(_convert_word(token))
            last_word_orig = token
        else:
            out.append(token)
            if token.strip():
                last_word_orig = ""
    return "".join(out)
