from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class CoaAccount:
    account_code: str
    account_name: str
    account_type: str
    parent_code: str
    statement: str
    cf_category: str
    normal_balance: str
    origin: str = "base"


@dataclass
class TbRow:
    account_code: str
    account_name: str
    currency: str
    debit: str
    credit: str


@dataclass
class Defect:
    type: str
    subtype: str
    detail: str
    refs: dict[str, Any] = field(default_factory=dict)


@dataclass
class Proposal:
    tier: str
    target_code: str
    confidence: float
    rationale: str


@dataclass
class Escalation:
    reason: str
    detail: str
    proposal: Proposal | None = None


@dataclass
class EvidenceRecord:
    account_code: str
    account_name: str
    status: str
    tier: str | None = None
    confidence: float | None = None
    confidence_component: float | None = None
    target_code: str | None = None
    target_name: str | None = None
    target_statement: str | None = None
    target_cf_category: str | None = None
    matched_against: str | None = None
    rows: list[dict[str, str]] = field(default_factory=list)
    currencies: list[str] = field(default_factory=list)
    defects: list[Defect] = field(default_factory=list)
    escalation: Escalation | None = None
    llm_proposal: Proposal | None = None
    resolution: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
