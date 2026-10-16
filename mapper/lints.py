from __future__ import annotations

from .models import CoaAccount, Defect, TbRow


def _children_map(coa_index: dict[str, CoaAccount]) -> dict[str, list[str]]:
    children: dict[str, list[str]] = {}
    for account in coa_index.values():
        if account.parent_code:
            children.setdefault(account.parent_code, []).append(account.account_code)
    return children


def _subtree(code: str, children: dict[str, list[str]]) -> list[str]:
    out: list[str] = []
    for child in children.get(code, []):
        out.append(child)
        out.extend(_subtree(child, children))
    return out


def coverage_lint(coa_index: dict[str, CoaAccount], tb_codes: set[str]) -> list[Defect]:
    children = _children_map(coa_index)
    defects: list[Defect] = []
    for account in coa_index.values():
        kids = children.get(account.account_code, [])
        is_header = account.account_type == "Header"
        if is_header:
            if not kids:
                defects.append(
                    Defect(
                        type="coverage",
                        subtype="empty_header",
                        detail=f"header {account.account_code} '{account.account_name}' has no children",
                        refs={"account_code": account.account_code},
                    )
                )
                continue
            if not any(code in tb_codes for code in _subtree(account.account_code, children)):
                defects.append(
                    Defect(
                        type="coverage",
                        subtype="header_without_mapped_children",
                        detail=f"header {account.account_code} '{account.account_name}' has no TB-covered descendants",
                        refs={"account_code": account.account_code},
                    )
                )
            continue
        if kids:
            if account.account_code not in tb_codes:
                defects.append(
                    Defect(
                        type="coverage",
                        subtype="non_header_parent_without_row",
                        detail=(
                            f"node {account.account_code} '{account.account_name}' has children but no own "
                            f"TB row and is not typed as a Header"
                        ),
                        refs={"account_code": account.account_code},
                    )
                )
            continue
        if account.account_code not in tb_codes:
            defects.append(
                Defect(
                    type="coverage",
                    subtype="uncovered_account",
                    detail=f"leaf account {account.account_code} '{account.account_name}' has no TB rows",
                    refs={"account_code": account.account_code},
                )
            )
    return defects


def comparables_lint(coa_index: dict[str, CoaAccount], prior_rows: list[TbRow]) -> list[Defect]:
    defects: list[Defect] = []
    seen: set[str] = set()
    for row in prior_rows:
        code = row.account_code
        if code in seen:
            continue
        seen.add(code)
        if code not in coa_index:
            defects.append(
                Defect(
                    type="comparables",
                    subtype="absent_from_coa",
                    detail=(
                        f"prior-period account {code} '{row.account_name}' has no counterpart in the "
                        f"current COA"
                    ),
                    refs={"account_code": code, "account_name": row.account_name},
                )
            )
    return defects


def trial_balance_check(rows: list[TbRow]) -> dict[str, float | bool]:
    debits = sum(float(row.debit or 0) for row in rows)
    credits = sum(float(row.credit or 0) for row in rows)
    return {
        "debits": round(debits, 2),
        "credits": round(credits, 2),
        "imbalance": round(debits - credits, 2),
        "balanced": abs(debits - credits) < 0.005,
    }
