"""Hindi phoneme catalogue + Devanagari tokenisation.

Phonemes are detected from *spelling* (graphemes). Every pattern is matched against
NFC-normalised text. NFC splits the precomposed nukta letters (U+0958-095F) into
base + nukta (U+093C), so "ड़" typed either way is matched by the same pattern.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

NUKTA = "\u093c"


def normalize(text: str) -> str:
    return unicodedata.normalize("NFC", text)


@dataclass(frozen=True)
class Phoneme:
    id: str
    label: str
    ipa: str
    pattern: str
    tip: str

    @property
    def regex(self) -> re.Pattern:
        return _COMPILED[self.id]


CATALOG: list[Phoneme] = [
    Phoneme("ksha", "क्ष (Ksha)", "kʂ", "क्ष", "Start with a hard k, flow straight into a retroflex sh. Not 'chh'."),
    Phoneme("gya", "ज्ञ (Gya)", "ɡj̃", "ज्ञ", "Say 'g' then a nasal 'y' glide: g-ya, not 'dnya' or 'gna'."),
    Phoneme("tra", "त्र (Tra)", "t̪r", "त्र", "Dental t with the tongue at the teeth, then a light r."),
    Phoneme("shra", "श्र (Shra)", "ʃr", "श्र", "Soft sh into a light r; keep both sounds distinct."),
    Phoneme("rha", "ड़ (Rha)", "ɽ", "ड" + NUKTA, "Flap the tongue tip back and down quickly. It is not a d."),
    Phoneme("rhha", "ढ़ (Rhha)", "ɽʱ", "ढ" + NUKTA, "Same flap as ड़ with a breathy release."),
    Phoneme("za", "ज़ (Za)", "z", "ज" + NUKTA, "A buzzing z, not j."),
    Phoneme("fa", "फ़ (Fa)", "f", "फ" + NUKTA, "Teeth on lower lip, not a puffed p."),
    Phoneme("khha", "ख़ (Kha)", "x", "ख" + NUKTA, "A scrape at the back of the throat, like German 'ch'."),
    Phoneme("ghha", "ग़ (Gha)", "ɣ", "ग" + NUKTA, "A voiced throat friction, close to a gargled r."),
    Phoneme("ssa", "ष (Ssa)", "ʂ", "(?<!क्)ष", "Retroflex sh: tongue curled back."),
    Phoneme("nna", "ण (Nna)", "ɳ", "ण", "Retroflex n: tongue curled back."),
]

BY_ID: dict[str, Phoneme] = {p.id: p for p in CATALOG}
_COMPILED: dict[str, re.Pattern] = {p.id: re.compile(normalize(p.pattern)) for p in CATALOG}

# Devanagari letters + marks + digits (excluding danda/double danda), Latin alnum, ZWJ/ZWNJ.
WORD_RE = re.compile(r"[\u0900-\u0963\u0966-\u097F\u200c\u200dA-Za-z0-9]+")


@dataclass(frozen=True)
class Token:
    index: int
    text: str
    start: int
    end: int


def tokenize(text: str) -> list[Token]:
    """Tokenise already-normalised text. Offsets refer to the string passed in."""
    return [Token(i, m.group(), m.start(), m.end()) for i, m in enumerate(WORD_RE.finditer(text))]


def phonemes_in(word: str) -> list[str]:
    word = normalize(word)
    return [p.id for p in CATALOG if p.regex.search(word)]


def label_for(phoneme_id: str) -> str:
    p = BY_ID.get(phoneme_id)
    return p.label if p else phoneme_id
