from __future__ import annotations

from .fuzzy import normalize, similarity
from .models import CoaAccount

EXACT_CODE = "exact_code"
EXACT_NAME = "exact_name"
FUZZY_NAME = "fuzzy_name"
LLM = "llm"
HUMAN = "human_assign"


def exact_code(code: str, coa_index: dict[str, CoaAccount]) -> CoaAccount | None:
    return coa_index.get(code)


def exact_name(name: str, coa_index: dict[str, CoaAccount]) -> CoaAccount | None:
    target = normalize(name)
    if not target:
        return None
    for account in coa_index.values():
        if normalize(account.account_name) == target:
            return account
    return None


def best_fuzzy(name: str, coa_index: dict[str, CoaAccount]) -> tuple[CoaAccount, float] | None:
    best: tuple[CoaAccount, float] | None = None
    for account in coa_index.values():
        score = similarity(name, account.account_name)
        if best is None or score > best[1]:
            best = (account, score)
    return best
