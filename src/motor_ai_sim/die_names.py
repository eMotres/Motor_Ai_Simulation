"""Short, readable names for new motors (dies): ``<family base> <user suffix> <n>``.

Owner decision 2026-10-26: "normal names, just add the user's suffix".  A variant
made from ``CIANO56 400_80 Ø400 48s56p opt3`` is called

    CIANO56 400_80 vadim        (the user's first variant)
    CIANO56 400_80 vadim 2      (the next one)
    CIANO56 400_80 vadim 3 ...

The diameter token (Ø400), the slots/poles token (48s56p) and free words (opt3,
edited, new) are NOT part of a new name - they are visible in the parameters.
Existing names are never renamed by this module; it only proposes names.

Pure functions, no I/O, no imports from the routes: every path that creates a die
(duplicate, create from the live geometry, save as a new die, the automatic die
transition, a copy into "My motors") calls the same three helpers.

THE FAMILY-BASE RULE (``family_base``).  The base is the leading *family token*
plus, when it follows, ONE *size token*:

* a family token is letters with an optional trailing number (``CIANO56``,
  ``CIANO``, ``CILN28``, ``M1``);
* a size token is a plain number or numbers joined by ``_``, ``-``, ``.`` or ``x``
  (``400_80``, ``85``, ``150_35``, ``50``);
* everything after that - ``Ø400``, ``48s56p``, ``opt3``, ``edited``, ``new``,
  ``20RSW175``, an earlier user suffix and its number - is dropped;
* a legacy name that starts with a bare size and a unit (``100 mm · 24s-28p
  mid-torque``) keeps ``100 mm``.

Examples (the real catalog names):

    CIANO56 400_80 Ø400 48s56p opt3  ->  CIANO56 400_80
    CIANO28 85 20RSW175              ->  CIANO28 85
    CIANO28 150_35 new               ->  CIANO28 150_35
    CIANO14 40 new                   ->  CIANO14 40
    CIANO14 50 edited 12s16p         ->  CIANO14 50
    CIANO10 200 opt                  ->  CIANO10 200
    CIANO 150_40                     ->  CIANO 150_40
    CIANO14 30_10                    ->  CIANO14 30_10
    CILN28                           ->  CILN28
    M1 850Nm                         ->  M1
    40 mm · 12s-14p high-speed       ->  40 mm
"""
from __future__ import annotations

import re
import unicodedata
from typing import Callable, Optional

#: ``CIANO56``, ``CIANO``, ``CILN28``, ``M1``
_FAMILY_RE = re.compile(r"^[A-Za-z][A-Za-z\-]*\d*$")
#: ``400_80``, ``85``, ``150_35``, ``30_10``
_SIZE_RE = re.compile(r"^\d+(?:[_.\-xX]\d+)*$")
_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")
_UNITS = frozenset({"mm", "cm", "m"})

#: The user's suffix is at most this long (the whole name must stay <= 64 characters).
MAX_SUFFIX = 16
_DEFAULT_SUFFIX = "user"

_CYR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
    "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def family_base(name: str) -> str:
    """The family base of a motor name (see the module docstring).  ``""`` for an empty name."""
    tokens = str(name or "").replace("·", " ").split()
    if not tokens:
        return ""
    first = tokens[0]
    if _NUMBER_RE.match(first):
        # a legacy "100 mm ..." name: the size and its unit are the base
        if len(tokens) > 1 and tokens[1].lower() in _UNITS:
            return f"{first} {tokens[1]}"
        return first
    if _FAMILY_RE.match(first):
        if len(tokens) > 1 and _SIZE_RE.match(tokens[1]):
            return f"{first} {tokens[1]}"
        return first
    # An unknown shape: keep the first word, never the whole decorated name.
    return first


def ascii_slug(text: str, limit: int = MAX_SUFFIX) -> str:
    """Lower-case ASCII ``[a-z0-9._-]`` rendering of ``text`` (Cyrillic transliterated,
    accents dropped), at most ``limit`` characters, no leading/trailing separators."""
    out = []
    for ch in str(text or "").strip().lower():
        if ch in _CYR:
            out.append(_CYR[ch])
        else:
            decomposed = unicodedata.normalize("NFKD", ch)
            out.append("".join(c for c in decomposed if ord(c) < 128))
    s = "".join(out)
    s = re.sub(r"[^a-z0-9._\-]+", "-", s)
    s = re.sub(r"[-._]{2,}", lambda m: m.group(0)[0], s)
    return s.strip("-._")[:limit].strip("-._")


def user_suffix(display_name: Optional[str], email: Optional[str]) -> str:
    """The creating account's short name: the FIRST NAME of the profile name, lower-case,
    else the local part of the e-mail address; ASCII only, at most ``MAX_SUFFIX`` characters.
    ``"user"`` when nothing usable is known."""
    words = str(display_name or "").split()
    first = ascii_slug(words[0]) if words else ""
    if first:
        return first
    local = str(email or "").split("@")[0].split("+")[0]
    return ascii_slug(local) or _DEFAULT_SUFFIX


def variant_name(base: str, suffix: str, taken: Callable[[str], bool]) -> str:
    """``"<base> <suffix>"``, else ``"<base> <suffix> 2"``, ``"... 3"`` ... - the first name
    ``taken`` does not claim.  The first variant carries no number."""
    base = str(base or "").strip()
    suffix = str(suffix or "").strip() or _DEFAULT_SUFFIX
    base = base[:max(1, 60 - len(suffix))].rstrip()          # the whole name stays <= 64 characters
    stem = f"{base} {suffix}".strip()
    if not taken(stem):
        return stem
    n = 2
    while taken(f"{stem} {n}"):
        n += 1
    return f"{stem} {n}"


def suggest(source_name: str, display_name: Optional[str], email: Optional[str],
            taken: Callable[[str], bool]) -> str:
    """The proposed name of a new variant of ``source_name`` made by this account."""
    base = family_base(source_name) or "Motor"
    return variant_name(base, user_suffix(display_name, email), taken)
