from __future__ import annotations

import argparse
import sys
from typing import Any

from .config import Config
from .llm import LLMClient, create_client
from .runner import execute

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_CONFIG = 2


def _upsert(entries: list[dict[str, Any]], entry: dict[str, Any]) -> None:
    code = entry.get("account_code")
    entries[:] = [e for e in entries if e.get("account_code") != code]
    entries.append(entry)


def interactive_resolver(pending: list[dict[str, Any]], overlay: dict[str, Any], entries: list[dict[str, Any]]) -> None:
    for entry in pending:
        print(f"\n{entry['account_code']} {entry['account_name']}")
        print(f"  reason : {entry['reason']}")
        print(f"  detail : {entry['detail']}")
        if entry.get("llm_proposal"):
            p = entry["llm_proposal"]
            print(f"  llm    : {p['target_code']} (confidence {p['confidence']}) — {p['rationale']}")
        if entry.get("proposal"):
            p = entry["proposal"]
            print(f"  tier   : {p['tier']} -> {p['target_code']} (confidence {p['confidence']})")
        try:
            answer = input("  assign <code> | new <code> <BS|PL> <name...> | <enter> keep pending > ").strip()
        except EOFError:
            continue
        if not answer:
            continue
        if answer.startswith("assign "):
            target = answer.split(None, 1)[1].strip()
            entry["status"] = "resolved"
            entry["resolution"] = {"action": "assign_existing", "target_code": target}
            _upsert(entries, entry)
        elif answer.startswith("new "):
            parts = answer.split(None, 3)
            if len(parts) < 4 or parts[2] not in {"BS", "PL"}:
                print("  format: new <code> <BS|PL> <name>")
                continue
            code, statement, name = parts[1], parts[2], parts[3]
            account_type = "Asset" if statement == "BS" else "Expense"
            overlay.setdefault("add_account", []).append(
                {
                    "account_code": code,
                    "account_name": name,
                    "account_type": account_type,
                    "parent_code": "",
                    "statement": statement,
                    "cf_category": "",
                    "normal_balance": "Debit",
                }
            )
            entry["status"] = "resolved"
            entry["resolution"] = {"action": "assign_existing", "target_code": code}
            _upsert(entries, entry)
        else:
            print("  unrecognised input, keeping pending")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mapper", description="TB -> COA mapper prototype")
    sub = parser.add_subparsers(dest="command")
    run_p = sub.add_parser("run", help="map the trial balance onto the chart of accounts")
    run_p.add_argument("--inputs", default=None, help="directory containing trial_balance.csv and friends")
    run_p.add_argument("--overlay", default=None, help="path to coa_overrides.json")
    run_p.add_argument("--escalations", default=None, help="path to escalations.json (source of truth)")
    run_p.add_argument("--output-dir", default=None, help="directory that receives run folders")
    run_p.add_argument("--period", default="2024-Q4")
    run_p.add_argument("--interactive", action="store_true", help="prompt for resolutions before finalising")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "run":
        parser.print_help()
        return EXIT_CONFIG

    cfg = Config(period=args.period)
    llm: LLMClient | None = create_client()
    if llm is None:
        print("note: no LLM key configured — orphan accounts will escalate without an advisory proposal")

    try:
        summary = execute(
            cfg,
            llm,
            inputs_dir=args.inputs,
            overlay_path=args.overlay,
            escalations_path=args.escalations,
            output_root=args.output_dir,
            interactive=args.interactive,
            resolver=interactive_resolver if args.interactive else None,
        )
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    if summary["status"] == "invalid":
        print("validation gate failed — no output written", file=sys.stderr)
        for failure in summary["failures"]:
            print(f"  - {failure}", file=sys.stderr)
        return EXIT_INVALID

    print(f"status   : {summary['status']}")
    print(f"accounts : {summary['accounts']} (mapped {summary['mapped']}, blocked {summary['blocked']})")
    if summary["reasons"]:
        print(f"reasons  : {', '.join(summary['reasons'])}")
    print(f"defects  : {summary['defects']}")
    print(f"run dir  : {summary['run_dir']}")
    if summary["status"] == "blocked":
        print("resolution required: edit escalations.json and coa_overrides.json, then re-run")
    return EXIT_OK
