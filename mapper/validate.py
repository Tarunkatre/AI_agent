from __future__ import annotations

from .config import Config
from .models import CoaAccount, EvidenceRecord
from .tiers import EXACT_CODE, EXACT_NAME, FUZZY_NAME, HUMAN, LLM

_PASS_TIERS = {EXACT_CODE, EXACT_NAME, HUMAN}


class ValidationError(Exception):
    def __init__(self, failures: list[str]) -> None:
        self.failures = failures
        super().__init__("; ".join(failures))


def tier_gate(tier: str | None, confidence: float | None, component: float | None, cfg: Config) -> bool:
    if tier in _PASS_TIERS:
        return True
    if tier == FUZZY_NAME:
        return confidence is not None and confidence >= cfg.fuzzy_accept
    if tier == LLM:
        return (
            confidence is not None
            and component is not None
            and confidence >= cfg.llm_self_min
            and component >= cfg.llm_floor_min
        )
    return False


def check(
    records: list[EvidenceRecord],
    to_write: list[EvidenceRecord],
    coa_index: dict[str, CoaAccount],
    cfg: Config,
    expected_codes: set[str],
    hashes_match: bool,
) -> list[str]:
    failures: list[str] = []

    codes = [r.account_code for r in records]
    if len(codes) != len(set(codes)):
        failures.append("assertion_1: duplicate Evidence Records for the same account_code")
    if set(codes) != expected_codes:
        missing = expected_codes - set(codes)
        extra = set(codes) - expected_codes
        failures.append(f"assertion_1: record coverage mismatch missing={sorted(missing)} extra={sorted(extra)}")

    for record in records:
        if record.status != "mapped":
            continue
        if record.target_code not in coa_index:
            failures.append(f"assertion_2: {record.account_code} mapped to nonexistent {record.target_code}")
        if not tier_gate(record.tier, record.confidence, record.confidence_component, cfg):
            failures.append(f"assertion_2: {record.account_code} fails tier gate for tier={record.tier}")

    blocked = [r for r in records if r.status == "blocked"]
    mapped = [r for r in records if r.status == "mapped"]
    if blocked and to_write:
        failures.append("assertion_3: unresolved escalations exist but mapped records were staged for write")
    if not blocked and len(to_write) != len(mapped):
        failures.append("assertion_3: no blockages but staged writes do not cover all mapped records")

    if not hashes_match:
        failures.append("assertion_4: manifest hashes do not match the files read")

    return failures
