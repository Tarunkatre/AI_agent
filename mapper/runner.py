from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import Config
from .engine import RunResult, run, serialize_escalations
from .llm import LLMClient
from .loaders import load_coa, load_json, load_tb, sha256_file
from .models import EvidenceRecord
from .overlay import apply_overlay, load_overlay
from .validate import ValidationError, check

REQUIRED_INPUTS = ("trial_balance.csv", "chart_of_accounts.csv", "prior_period_tb.csv")
OPTIONAL_INPUTS = ("fx_rates.csv", "manual_adjustments.json")


@dataclass
class Paths:
    inputs_dir: Path
    trial_balance: Path
    chart_of_accounts: Path
    prior_period_tb: Path
    fx_rates: Path
    manual_adjustments: Path
    overlay: Path
    escalations: Path
    output_root: Path

    @classmethod
    def build(
        cls,
        cfg: Config,
        inputs_dir: Path | None = None,
        overlay_path: Path | None = None,
        escalations_path: Path | None = None,
        output_root: Path | None = None,
    ) -> "Paths":
        inputs_dir = Path(inputs_dir) if inputs_dir is not None else None
        overlay_path = Path(overlay_path) if overlay_path is not None else None
        escalations_path = Path(escalations_path) if escalations_path is not None else None
        output_root = Path(output_root) if output_root is not None else None
        if inputs_dir is None:
            candidates = [
                cfg.project_root / "AI_Agentic_Engineer_Assignment" / "inputs",
                cfg.project_root / "inputs",
            ]
            inputs_dir = next((c for c in candidates if (c / "trial_balance.csv").exists()), candidates[0])
        return cls(
            inputs_dir=inputs_dir,
            trial_balance=inputs_dir / "trial_balance.csv",
            chart_of_accounts=inputs_dir / "chart_of_accounts.csv",
            prior_period_tb=inputs_dir / "prior_period_tb.csv",
            fx_rates=inputs_dir / "fx_rates.csv",
            manual_adjustments=inputs_dir / "manual_adjustments.json",
            overlay=overlay_path or cfg.project_root / "coa_overrides.json",
            escalations=escalations_path or cfg.project_root / "escalations.json",
            output_root=output_root or cfg.project_root / "output",
        )

    def input_hashes(self) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for name in REQUIRED_INPUTS + OPTIONAL_INPUTS:
            path = self.inputs_dir / name
            if path.exists():
                hashes[name] = sha256_file(path)
        if self.overlay.exists():
            hashes[self.overlay.name] = sha256_file(self.overlay)
        return hashes


def _build_coa(paths: Paths, overlay: dict[str, Any]) -> dict:
    base = load_coa(paths.chart_of_accounts)
    index = {account.account_code: account for account in base}
    return apply_overlay(index, overlay)


def _load_prior_escalations(paths: Paths) -> list[dict[str, Any]]:
    data = load_json(paths.escalations)
    if not data:
        return []
    return list(data.get("escalations", []))


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _partition(result: RunResult) -> tuple[list[EvidenceRecord], list[EvidenceRecord]]:
    mapped = [r for r in result.records if r.status == "mapped"]
    blocked = [r for r in result.records if r.status == "blocked"]
    return mapped, blocked


def _summary(result: RunResult, run_dir: Path | None, status: str) -> dict[str, Any]:
    mapped, blocked = _partition(result)
    return {
        "status": status,
        "accounts": len(result.records),
        "mapped": len(mapped),
        "blocked": len(blocked),
        "reasons": sorted({r.escalation.reason for r in blocked if r.escalation}),
        "defects": len(result.defects),
        "run_dir": str(run_dir) if run_dir else None,
    }


def execute(
    cfg: Config,
    llm: LLMClient | None,
    *,
    inputs_dir: Path | None = None,
    overlay_path: Path | None = None,
    escalations_path: Path | None = None,
    output_root: Path | None = None,
    interactive: bool = False,
    resolver: Callable[[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]], None] | None = None,
) -> dict[str, Any]:
    paths = Paths.build(cfg, inputs_dir, overlay_path, escalations_path, output_root)
    for name in REQUIRED_INPUTS:
        if not (paths.inputs_dir / name).exists():
            raise FileNotFoundError(f"missing required input: {paths.inputs_dir / name}")

    overlay = load_overlay(paths.overlay)
    prior_entries = _load_prior_escalations(paths)

    tb_rows = load_tb(paths.trial_balance)
    prior_rows = load_tb(paths.prior_period_tb)
    coa_index = _build_coa(paths, overlay)
    result = run(cfg, tb_rows, prior_rows, coa_index, llm, prior_entries)

    if (interactive or resolver is not None) and any(r.status == "blocked" for r in result.records):
        pending = [e for e in serialize_escalations(result.records, prior_entries) if e["status"] == "pending"]
        updated_entries = list(prior_entries)
        if resolver is not None:
            resolver(pending, overlay, updated_entries)
        base_overlay = load_overlay(paths.overlay)
        overlay_changed = overlay != base_overlay
        entries_changed = updated_entries != prior_entries
        if overlay_changed:
            _write_json(paths.overlay, {"add_account": overlay.get("add_account", []), "set_field": overlay.get("set_field", [])})
        if entries_changed:
            _write_json(paths.escalations, {"escalations": updated_entries})
        if overlay_changed or entries_changed:
            prior_entries = updated_entries
            coa_index = _build_coa(paths, load_overlay(paths.overlay))
            result = run(cfg, tb_rows, prior_rows, coa_index, llm, prior_entries)

    mapped, blocked = _partition(result)
    to_write = [] if blocked else mapped

    hashes = paths.input_hashes()
    run_dir = paths.output_root / _utc_stamp()
    manifest = {
        "run_id": run_dir.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": cfg.period,
        "functional_currency": cfg.functional_currency,
        "status": "blocked" if blocked else "complete",
        "inputs": {k: v for k, v in hashes.items()},
        "overlay": str(paths.overlay) if paths.overlay.exists() else None,
        "config": cfg.gates(),
        "llm": {
            "provider": getattr(llm, "provider", None),
            "model": getattr(llm, "model", None),
            "degraded": llm is None,
        },
        "counts": {
            "accounts": len(result.records),
            "mapped": len(mapped),
            "blocked": len(blocked),
            "defects": len(result.defects),
        },
    }

    rehash = {k: sha256_file(paths.inputs_dir / k) for k in hashes if (paths.inputs_dir / k).exists()}
    hashes_match = all(rehash.get(k) == v for k, v in hashes.items() if not k.endswith("overrides.json"))

    failures = check(result.records, to_write, coa_index, cfg, result.expected_codes, hashes_match)
    if failures:
        return {"status": "invalid", "failures": failures, **_summary(result, None, "invalid")}

    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "mappings.jsonl").write_text(
        "".join(json.dumps(r.to_dict(), sort_keys=True) + "\n" for r in to_write),
        encoding="utf-8",
    )
    record_defects = [
        {"account_code": r.account_code, "account_name": r.account_name, **dataclasses.asdict(d)}
        for r in result.records
        for d in r.defects
    ]
    _write_json(
        run_dir / "defects.json",
        {
            "record_defects": record_defects,
            "defects": [dataclasses.asdict(d) for d in result.defects],
            "tb_balance_check": result.tb_check,
        },
    )
    escalation_payload = {"period": cfg.period, "escalations": serialize_escalations(result.records, prior_entries)}
    _write_json(run_dir / "escalations.json", escalation_payload)
    _write_json(run_dir / "run_manifest.json", manifest)
    _write_json(paths.escalations, escalation_payload)

    status = "blocked" if blocked else "complete"
    summary = _summary(result, run_dir, status)
    summary["run_dir"] = str(run_dir)
    return summary
