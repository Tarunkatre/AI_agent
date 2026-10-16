from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from .models import CoaAccount, TbRow


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def load_coa(path: Path) -> list[CoaAccount]:
    accounts = []
    for row in read_csv(path):
        accounts.append(
            CoaAccount(
                account_code=row["account_code"].strip(),
                account_name=row["account_name"].strip(),
                account_type=row.get("account_type", "").strip(),
                parent_code=row.get("parent_code", "").strip(),
                statement=row.get("statement", "").strip(),
                cf_category=row.get("cf_category", "").strip(),
                normal_balance=row.get("normal_balance", "").strip(),
            )
        )
    return accounts


def load_tb(path: Path) -> list[TbRow]:
    rows = []
    for row in read_csv(path):
        rows.append(
            TbRow(
                account_code=row["account_code"].strip(),
                account_name=row["account_name"].strip(),
                currency=row["currency"].strip(),
                debit=row.get("debit", "0").strip(),
                credit=row.get("credit", "0").strip(),
            )
        )
    return rows


def load_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def group_rows(rows: list[TbRow]) -> dict[str, list[TbRow]]:
    grouped: dict[str, list[TbRow]] = {}
    for row in rows:
        grouped.setdefault(row.account_code, []).append(row)
    return grouped
