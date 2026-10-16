from __future__ import annotations

import unittest
from pathlib import Path

from mapper.config import Config
from mapper.engine import run, serialize_escalations
from mapper.llm import FakeLLMClient
from mapper.loaders import load_coa, load_tb
from mapper.models import Proposal
from mapper.overlay import apply_overlay, load_overlay
from mapper.tiers import EXACT_CODE, HUMAN

ROOT = Path(__file__).resolve().parent.parent
INPUTS = ROOT / "AI_Agentic_Engineer_Assignment" / "inputs"


def load_bundle(cfg: Config | None = None):
    cfg = cfg or Config()
    base = load_coa(INPUTS / "chart_of_accounts.csv")
    index = {a.account_code: a for a in base}
    tb = load_tb(INPUTS / "trial_balance.csv")
    prior = load_tb(INPUTS / "prior_period_tb.csv")
    return cfg, tb, prior, index


class MappingSpec(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg, cls.tb, cls.prior, cls.index = load_bundle()
        cls.result = run(cls.cfg, cls.tb, cls.prior, cls.index, FakeLLMClient(), [])

    def record(self, code: str):
        return next(r for r in self.result.records if r.account_code == code)

    def test_one_record_per_unique_tb_account(self):
        self.assertEqual(len(self.result.records), 59)
        self.assertEqual(len(self.result.records), len(self.result.expected_codes))

    def test_golden_mapping_table(self):
        expected_blocked = {"9999", "1150", "2170"}
        blocked = set()
        mapped: dict[str, str] = {}
        for record in self.result.records:
            if record.status == "blocked":
                blocked.add(record.account_code)
            else:
                mapped[record.account_code] = record.target_code
        self.assertEqual(blocked, expected_blocked)
        self.assertEqual(len(mapped), 56)
        self.assertEqual({c: t for c, t in mapped.items() if t != c}, {})

    def test_clean_account_resolves_via_exact_code(self):
        rec = self.record("4100")
        self.assertEqual(rec.status, "mapped")
        self.assertEqual(rec.tier, EXACT_CODE)
        self.assertEqual(rec.confidence, 1.0)
        self.assertEqual(rec.target_code, "4100")

    def test_orphan_9999_blocks_as_orphan_account(self):
        rec = self.record("9999")
        self.assertEqual(rec.status, "blocked")
        self.assertEqual(rec.escalation.reason, "orphan_account")
        self.assertIsNone(rec.tier)

    def test_orphan_proposes_via_llm_advisory(self):
        client = FakeLLMClient(
            proposal=Proposal(tier="llm", target_code="1150", confidence=0.9, rationale="closest asset node")
        )
        result = run(self.cfg, self.tb, self.prior, self.index, client, [])
        rec = next(r for r in result.records if r.account_code == "9999")
        self.assertEqual(rec.escalation.reason, "orphan_account")
        self.assertIsNotNone(rec.llm_proposal)
        self.assertEqual(rec.llm_proposal.target_code, "1150")

    def test_llm_malformed_output_surfaces_as_llm_invalid(self):
        client = FakeLLMClient(error="model returned no tool_use block")
        result = run(self.cfg, self.tb, self.prior, self.index, client, [])
        rec = next(r for r in result.records if r.account_code == "9999")
        self.assertEqual(rec.escalation.reason, "orphan_account")
        self.assertIn("llm_invalid", rec.escalation.detail)

    def test_llm_target_not_in_coa_is_rejected(self):
        from mapper.llm import _parse_proposal

        with self.assertRaises(Exception):
            _parse_proposal({"target_code": "7777", "confidence": 0.95, "rationale": "made up"}, self.index)

    def test_ambiguous_target_blocks_as_ambiguous_mapping(self):
        for code in ("1150", "2170"):
            rec = self.record(code)
            self.assertEqual(rec.status, "blocked")
            self.assertEqual(rec.escalation.reason, "ambiguous_mapping", code)
            self.assertEqual(rec.target_code, None)
            self.assertIn("TBD", rec.escalation.detail)

    def test_duplicate_rows_flagged_without_blocking(self):
        rec = self.record("6310")
        self.assertEqual(rec.status, "mapped")
        self.assertEqual(len(rec.defects), 1)
        self.assertEqual(rec.defects[0].type, "duplicate_row")
        self.assertEqual(rec.defects[0].refs["rows"], 2)

    def test_multicurrency_rows_are_not_duplicate(self):
        rec = self.record("1110")
        self.assertEqual(rec.status, "mapped")
        self.assertEqual(rec.currencies, ["EUR", "GBP", "USD"])
        self.assertEqual(rec.defects, [])

    def test_coverage_lint_flags_empty_header_and_non_header_parent(self):
        subtypes = {(d.subtype, d.refs.get("account_code")) for d in self.result.defects if d.type == "coverage"}
        self.assertIn(("empty_header", "1290"), subtypes)
        self.assertIn(("non_header_parent_without_row", "8000"), subtypes)
        self.assertIn(("non_header_parent_without_row", "3300"), subtypes)

    def test_comparables_lint_flags_prior_account_missing_from_coa(self):
        comp = [d for d in self.result.defects if d.type == "comparables"]
        self.assertEqual(len(comp), 1)
        self.assertEqual(comp[0].refs["account_code"], "6905")

    def test_trial_balance_imbalance_detected(self):
        check = self.result.tb_check
        self.assertFalse(check["balanced"])
        self.assertEqual(check["imbalance"], -4800.0)

    def test_tier_gate_accepts_exact_and_fuzzy(self):
        from mapper.validate import tier_gate

        self.assertTrue(tier_gate(EXACT_CODE, 1.0, 1.0, self.cfg))
        self.assertTrue(tier_gate(HUMAN, 1.0, 1.0, self.cfg))
        self.assertTrue(tier_gate("fuzzy_name", 0.95, 0.95, self.cfg))
        self.assertFalse(tier_gate("fuzzy_name", 0.7, 0.7, self.cfg))
        self.assertTrue(tier_gate("llm", 0.85, 0.65, self.cfg))
        self.assertFalse(tier_gate("llm", 0.85, 0.4, self.cfg))
        self.assertFalse(tier_gate("llm", 0.7, 0.9, self.cfg))


class ResolutionSpec(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg, cls.tb, cls.prior, cls.index = load_bundle()

    def test_orphan_resolution_assigns_existing_target(self):
        prior = [{"account_code": "9999", "status": "resolved", "resolution": {"action": "assign_existing", "target_code": "1150"}}]
        result = run(self.cfg, self.tb, self.prior, self.index, FakeLLMClient(), prior)
        rec = next(r for r in result.records if r.account_code == "9999")
        self.assertEqual(rec.status, "mapped")
        self.assertEqual(rec.tier, HUMAN)
        self.assertEqual(rec.target_code, "1150")
        self.assertEqual(rec.resolution, prior[0]["resolution"])

    def test_overlay_closes_ambiguity(self):
        overlay = {"add_account": [], "set_field": [{"account_code": "1150", "field": "cf_category", "value": "Operating"}]}
        index = apply_overlay({a.account_code: a for a in load_coa(INPUTS / "chart_of_accounts.csv")}, overlay)
        result = run(self.cfg, self.tb, self.prior, index, FakeLLMClient(), [])
        rec = next(r for r in result.records if r.account_code == "1150")
        self.assertEqual(rec.status, "mapped")
        self.assertEqual(rec.target_cf_category, "Operating")

    def test_overlay_add_account_makes_orphan_resolvable(self):
        overlay = {
            "add_account": [{"account_code": "9999", "account_name": "Suspense - Unmapped", "account_type": "Asset", "parent_code": "1290", "statement": "BS", "cf_category": "Operating", "normal_balance": "Debit"}],
            "set_field": [],
        }
        index = apply_overlay({a.account_code: a for a in load_coa(INPUTS / "chart_of_accounts.csv")}, overlay)
        result = run(self.cfg, self.tb, self.prior, index, FakeLLMClient(), [])
        rec = next(r for r in result.records if r.account_code == "9999")
        self.assertEqual(rec.status, "mapped")
        self.assertEqual(rec.tier, EXACT_CODE)

    def test_base_coa_files_are_not_mutated_by_overlay(self):
        before = (INPUTS / "chart_of_accounts.csv").read_text(encoding="utf-8")
        overlay = {"add_account": [{"account_code": "9999", "account_name": "Suspense", "account_type": "Asset", "parent_code": "", "statement": "BS", "cf_category": "Operating", "normal_balance": "Debit"}], "set_field": []}
        apply_overlay({a.account_code: a for a in load_coa(INPUTS / "chart_of_accounts.csv")}, overlay)
        after = (INPUTS / "chart_of_accounts.csv").read_text(encoding="utf-8")
        self.assertEqual(before, after)

    def test_empty_overlay_loads_defaults(self):
        self.assertEqual(load_overlay(Path("/nonexistent/overlay.json")), {"add_account": [], "set_field": []})


class IdempotencySpec(unittest.TestCase):
    def test_two_runs_produce_identical_records(self):
        cfg, tb, prior, index = load_bundle()
        client = FakeLLMClient(proposal=Proposal(tier="llm", target_code="1150", confidence=0.9, rationale="x"))
        import json

        first = json.dumps([r.to_dict() for r in run(cfg, tb, prior, index, client, []).records], sort_keys=True)
        second = json.dumps([r.to_dict() for r in run(cfg, tb, prior, index, client, []).records], sort_keys=True)
        self.assertEqual(first, second)

    def test_serialize_carries_resolved_history_forward(self):
        cfg, tb, prior, index = load_bundle()
        prior_entries = [{"account_code": "1150", "status": "resolved", "resolution": {"action": "assign_existing", "target_code": "1150"}}]
        result = run(cfg, tb, prior, index, FakeLLMClient(), prior_entries)
        serialized = serialize_escalations(result.records, prior_entries)
        self.assertIn({"account_code": "1150", "status": "resolved", "resolution": {"action": "assign_existing", "target_code": "1150"}}, serialized)


if __name__ == "__main__":
    unittest.main()
