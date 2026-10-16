from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .config import Config
from .llm import LLMClient, LLMError
from .loaders import group_rows
from .lints import comparables_lint, coverage_lint, trial_balance_check
from .models import CoaAccount, Defect, Escalation, EvidenceRecord, Proposal, TbRow
from .tiers import EXACT_CODE, EXACT_NAME, FUZZY_NAME, HUMAN, best_fuzzy, exact_code, exact_name


@dataclass
class RunResult:
    records: list[EvidenceRecord]
    defects: list[Defect]
    tb_check: dict[str, Any]
    coa_index: dict[str, CoaAccount]
    expected_codes: set[str] = field(default_factory=set)
    tb_codes: set[str] = field(default_factory=set)


def _resolutions_from(prior: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for entry in prior:
        if entry.get("status") == "resolved" and entry.get("resolution"):
            out[str(entry.get("account_code"))] = entry["resolution"]
    return out


def _row_dicts(rows: list[TbRow]) -> list[dict[str, str]]:
    return [
        {"account_code": r.account_code, "account_name": r.account_name, "currency": r.currency, "debit": r.debit, "credit": r.credit}
        for r in rows
    ]


def _duplicate_defect(code: str, rows: list[TbRow]) -> Defect | None:
    counts: Counter[tuple[str, str]] = Counter((r.account_code, r.currency) for r in rows)
    dupes = sorted({currency for (_code, currency), n in counts.items() if n > 1})
    if not dupes:
        return None
    return Defect(
        type="duplicate_row",
        subtype="same_code_same_currency",
        detail=(
            f"account {code} appears {counts[(code, dupes[0])]}x for currency {dupes[0]}; "
            f"rows are summed by the mapper but the balance may be double-counted"
        ),
        refs={"account_code": code, "currency": dupes[0], "rows": counts[(code, dupes[0])]},
    )


def _advisory_from_tiers(code: str, name: str, coa_index: dict[str, CoaAccount], cfg: Config) -> Proposal | None:
    target = exact_name(name, coa_index)
    if target is not None and target.account_code != code:
        return Proposal(tier=EXACT_NAME, target_code=target.account_code, confidence=1.0, rationale="name matches COA exactly")
    best = best_fuzzy(name, coa_index)
    if best is not None and best[1] >= cfg.fuzzy_floor:
        account, score = best
        return Proposal(tier=FUZZY_NAME, target_code=account.account_code, confidence=round(score, 4), rationale="best fuzzy name match")
    return None


def _llm_advisory(code: str, name: str, coa_index: dict[str, CoaAccount], llm: LLMClient | None) -> tuple[Proposal | None, str | None]:
    if llm is None:
        return None, None
    try:
        proposal = llm.propose(code, name, coa_index)
    except LLMError as exc:
        return None, str(exc)
    return proposal, None


def _mapped_record(record: EvidenceRecord, target: CoaAccount, tier: str, confidence: float, component: float, matched_against: str) -> EvidenceRecord:
    record.status = "mapped"
    record.tier = tier
    record.confidence = confidence
    record.confidence_component = component
    record.target_code = target.account_code
    record.target_name = target.account_name
    record.target_statement = target.statement
    record.target_cf_category = target.cf_category
    record.matched_against = matched_against
    return record


def _blocked_record(record: EvidenceRecord, reason: str, detail: str, proposal: Proposal | None, llm_proposal: Proposal | None, error: str | None) -> EvidenceRecord:
    record.status = "blocked"
    if error:
        detail = f"{detail} | llm_invalid: {error}"
    record.escalation = Escalation(reason=reason, detail=detail, proposal=proposal)
    record.llm_proposal = llm_proposal
    return record


def resolve_account(
    code: str,
    rows: list[TbRow],
    coa_index: dict[str, CoaAccount],
    cfg: Config,
    llm: LLMClient | None,
    resolutions: dict[str, dict[str, Any]],
) -> EvidenceRecord:
    name = rows[0].account_name
    record = EvidenceRecord(
        account_code=code,
        account_name=name,
        status="blocked",
        rows=_row_dicts(rows),
        currencies=sorted({r.currency for r in rows}),
    )
    defect = _duplicate_defect(code, rows)
    if defect is not None:
        record.defects.append(defect)

    resolution = resolutions.get(code)
    if resolution and resolution.get("action") == "assign_existing":
        target = coa_index.get(str(resolution.get("target_code", "")))
        if target is not None:
            _mapped_record(record, target, HUMAN, 1.0, 1.0, f"human resolution: {resolution.get('target_code')}")
            record.resolution = resolution
            return record

    target = exact_code(code, coa_index)
    if target is None:
        proposal = _advisory_from_tiers(code, name, coa_index, cfg)
        llm_proposal, error = _llm_advisory(code, name, coa_index, llm)
        return _blocked_record(
            record,
            "orphan_account",
            f"account {code} is absent from the COA (strict code match); mapping is blocked until a human assigns a node or adds the account",
            proposal,
            llm_proposal,
            error,
        )

    if target.cf_category == "TBD":
        return _blocked_record(
            record,
            "ambiguous_mapping",
            f"account {code} maps to {target.account_code} but its cf_category is 'TBD' (incomplete classification)",
            None,
            None,
            None,
        )

    return _mapped_record(record, target, EXACT_CODE, 1.0, 1.0, code)


def run(
    cfg: Config,
    tb_rows: list[TbRow],
    prior_rows: list[TbRow],
    coa_index: dict[str, CoaAccount],
    llm: LLMClient | None,
    prior_escalations: list[dict[str, Any]],
) -> RunResult:
    grouped = group_rows(tb_rows)
    resolutions = _resolutions_from(prior_escalations)
    records = [
        resolve_account(code, rows, coa_index, cfg, llm, resolutions)
        for code, rows in sorted(grouped.items())
    ]
    tb_codes = set(grouped)
    defects = coverage_lint(coa_index, tb_codes)
    defects.extend(comparables_lint(coa_index, prior_rows))
    return RunResult(
        records=records,
        defects=defects,
        tb_check=trial_balance_check(tb_rows),
        coa_index=coa_index,
        expected_codes=tb_codes,
        tb_codes=tb_codes,
    )


def serialize_escalations(records: list[EvidenceRecord], prior: list[dict[str, Any]]) -> list[dict[str, Any]]:
    import dataclasses

    out: list[dict[str, Any]] = []
    pending_codes = {r.account_code for r in records if r.status == "blocked"}
    for entry in prior:
        if entry.get("status") == "resolved" and str(entry.get("account_code")) not in pending_codes:
            out.append(entry)
    for record in records:
        if record.status != "blocked" or record.escalation is None:
            continue
        out.append(
            {
                "account_code": record.account_code,
                "account_name": record.account_name,
                "reason": record.escalation.reason,
                "detail": record.escalation.detail,
                "status": "pending",
                "proposal": dataclasses.asdict(record.escalation.proposal) if record.escalation.proposal else None,
                "llm_proposal": dataclasses.asdict(record.llm_proposal) if record.llm_proposal else None,
            }
        )
    return out
