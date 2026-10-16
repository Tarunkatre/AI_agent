from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import CoaAccount


def load_overlay(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {"add_account": [], "set_field": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("add_account", [])
    data.setdefault("set_field", [])
    return data


def apply_overlay(coa_index: dict[str, CoaAccount], overlay: dict[str, Any]) -> dict[str, CoaAccount]:
    for spec in overlay.get("add_account", []):
        account = CoaAccount(
            account_code=str(spec["account_code"]).strip(),
            account_name=str(spec["account_name"]).strip(),
            account_type=str(spec.get("account_type", "")).strip(),
            parent_code=str(spec.get("parent_code", "")).strip(),
            statement=str(spec.get("statement", "")).strip(),
            cf_category=str(spec.get("cf_category", "")).strip(),
            normal_balance=str(spec.get("normal_balance", "")).strip(),
            origin="overlay",
        )
        coa_index[account.account_code] = account
    for spec in overlay.get("set_field", []):
        code = str(spec["account_code"]).strip()
        account = coa_index.get(code)
        if account is None:
            continue
        field = spec["field"]
        if hasattr(account, field):
            setattr(account, field, spec["value"])
            account.origin = "overlay" if account.origin == "base" else account.origin
    return coa_index
