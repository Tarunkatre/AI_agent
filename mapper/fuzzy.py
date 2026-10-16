from __future__ import annotations

import re
from difflib import SequenceMatcher

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    return _NON_ALNUM.sub(" ", text.lower()).strip()


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def token_set_ratio(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na and not nb:
        return 1.0
    if na == nb:
        return 1.0
    ta, tb = set(na.split()), set(nb.split())
    inter = sorted(ta & tb)
    rest_a = sorted(ta - tb)
    rest_b = sorted(tb - ta)
    s0 = " ".join(inter)
    s1 = " ".join(inter + rest_a)
    s2 = " ".join(inter + rest_b)
    if not inter:
        return _ratio(s1, s2)
    return max(_ratio(s0, s1), _ratio(s0, s2), _ratio(s1, s2))


def similarity(a: str, b: str) -> float:
    return max(token_set_ratio(a, b), _ratio(normalize(a), normalize(b)))
