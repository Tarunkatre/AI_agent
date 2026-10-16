from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from mapper.config import Config
from mapper.llm import FakeLLMClient
from mapper.models import EvidenceRecord, Proposal
from mapper.runner import execute
from mapper.validate import check

ROOT = Path(__file__).resolve().parent.parent
INPUTS = ROOT / "AI_Agentic_Engineer_Assignment" / "inputs"


class RunnerSpec(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.inputs = self.tmp / "inputs"
        self.output = self.tmp / "output"
        self.overlay = self.tmp / "coa_overrides.json"
        self.escalations = self.tmp / "escalations.json"
        shutil.copytree(INPUTS, self.inputs)
        self.cfg = Config(project_root=self.tmp)
        self.client = FakeLLMClient(
            proposal=Proposal(tier="llm", target_code="1150", confidence=0.88, rationale="closest asset node")
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _execute(self, resolver=None, interactive=False):
        return execute(
            self.cfg,
            self.client,
            inputs_dir=self.inputs,
            overlay_path=self.overlay,
            escalations_path=self.escalations,
            output_root=self.output,
            interactive=interactive,
            resolver=resolver,
        )

    def _run_dir(self) -> Path:
        dirs = sorted(self.output.iterdir())
        self.assertEqual(len(dirs), 1)
        return dirs[0]

    def test_first_run_blocks_and_writes_no_mappings(self):
        summary = self._execute()
        self.assertEqual(summary["status"], "blocked")
        self.assertEqual(summary["accounts"], 59)
        self.assertEqual(summary["blocked"], 3)
        self.assertEqual(summary["reasons"], ["ambiguous_mapping", "orphan_account"])
        run_dir = self._run_dir()
        self.assertEqual((run_dir / "mappings.jsonl").read_text(encoding="utf-8"), "")
        escalations = json.loads((run_dir / "escalations.json").read_text(encoding="utf-8"))
        pending = {e["account_code"] for e in escalations["escalations"] if e["status"] == "pending"}
        self.assertEqual(pending, {"9999", "1150", "2170"})
        orphan = next(e for e in escalations["escalations"] if e["account_code"] == "9999")
        self.assertEqual(orphan["llm_proposal"]["target_code"], "1150")

    def test_defects_and_balance_check_surface(self):
        self._execute()
        payload = json.loads((self._run_dir() / "defects.json").read_text(encoding="utf-8"))
        subtypes = {d["subtype"] for d in payload["defects"]}
        self.assertIn("empty_header", subtypes)
        self.assertIn("absent_from_coa", subtypes)
        self.assertEqual(payload["tb_balance_check"]["imbalance"], -4800.0)
        dupes = [d for d in payload["defects"] if d["type"] == "duplicate_row"]
        self.assertEqual(dupes, [])
        self.assertEqual(len(payload["record_defects"]), 1)
        self.assertEqual(payload["record_defects"][0]["account_code"], "6310")
        self.assertEqual(payload["record_defects"][0]["type"], "duplicate_row")

    def test_manifest_records_hashes_and_llm(self):
        self._execute()
        manifest = json.loads((self._run_dir() / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertIn("trial_balance.csv", manifest["inputs"])
        self.assertIn("chart_of_accounts.csv", manifest["inputs"])
        self.assertEqual(manifest["llm"]["provider"], "fake")
        self.assertEqual(manifest["llm"]["degraded"], False)
        self.assertEqual(manifest["counts"]["blocked"], 3)

    def test_keyless_run_degrades_gracefully(self):
        summary = execute(
            self.cfg,
            None,
            inputs_dir=self.inputs,
            overlay_path=self.overlay,
            escalations_path=self.escalations,
            output_root=self.output,
        )
        self.assertEqual(summary["status"], "blocked")
        self.assertEqual(summary["blocked"], 3)
        run_dir = self._run_dir()
        manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertIsNone(manifest["llm"]["provider"])
        self.assertTrue(manifest["llm"]["degraded"])
        escalations = json.loads((run_dir / "escalations.json").read_text(encoding="utf-8"))
        orphan = next(e for e in escalations["escalations"] if e["account_code"] == "9999")
        self.assertIsNone(orphan["llm_proposal"])

    def test_resolution_rerun_completes_batch(self):
        def resolver(pending, overlay, entries):
            overlay.setdefault("set_field", []).extend(
                [
                    {"account_code": "1150", "field": "cf_category", "value": "Operating"},
                    {"account_code": "2170", "field": "cf_category", "value": "Intercompany"},
                ]
            )
            for entry in pending:
                if entry["account_code"] == "9999":
                    entry["status"] = "resolved"
                    entry["resolution"] = {"action": "assign_existing", "target_code": "1150"}
                    entries.append(entry)

        summary = self._execute(resolver=resolver)
        self.assertEqual(summary["status"], "complete")
        self.assertEqual(summary["mapped"], 59)
        run_dir = Path(summary["run_dir"])
        lines = (run_dir / "mappings.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 59)
        orphan = next(json.loads(line) for line in lines if json.loads(line)["account_code"] == "9999")
        self.assertEqual(orphan["tier"], "human_assign")
        self.assertEqual(orphan["target_code"], "1150")

    def test_second_rerun_without_changes_is_idempotent(self):
        def resolver(pending, overlay, entries):
            overlay.setdefault("set_field", []).extend(
                [
                    {"account_code": "1150", "field": "cf_category", "value": "Operating"},
                    {"account_code": "2170", "field": "cf_category", "value": "Intercompany"},
                ]
            )
            for entry in pending:
                if entry["account_code"] == "9999":
                    entry["status"] = "resolved"
                    entry["resolution"] = {"action": "assign_existing", "target_code": "1150"}
                    entries.append(entry)

        first = self._execute(resolver=resolver)
        first_run = Path(first["run_dir"]) / "mappings.jsonl"
        first_content = first_run.read_text(encoding="utf-8")

        second = self._execute()
        second_run = Path(second["run_dir"]) / "mappings.jsonl"
        self.assertEqual(second["status"], "complete")
        self.assertEqual(first_content, second_run.read_text(encoding="utf-8"))

    def test_inputs_and_base_coa_never_mutated(self):
        tracked = {
            name: (self.inputs / name).read_bytes()
            for name in ("trial_balance.csv", "chart_of_accounts.csv", "prior_period_tb.csv")
        }
        self._execute()
        for name, before in tracked.items():
            self.assertEqual((self.inputs / name).read_bytes(), before, name)

    def test_gate_fails_closed_when_blockages_exist(self):
        blocked = EvidenceRecord(account_code="1", account_name="x", status="blocked")
        mapped = EvidenceRecord(account_code="2", account_name="y", status="mapped", tier="exact_code", confidence=1.0, confidence_component=1.0, target_code="2")
        failures = check([blocked, mapped], [mapped], {}, self.cfg, {"1", "2"}, True)
        self.assertTrue(any("assertion_3" in f for f in failures))

    def test_gate_fails_on_nonexistent_target(self):
        mapped = EvidenceRecord(account_code="2", account_name="y", status="mapped", tier="exact_code", confidence=1.0, confidence_component=1.0, target_code="9999")
        failures = check([mapped], [mapped], {}, self.cfg, {"2"}, True)
        self.assertTrue(any("assertion_2" in f for f in failures))

    def test_gate_fails_on_hash_mismatch(self):
        mapped = EvidenceRecord(account_code="2", account_name="y", status="mapped", tier="exact_code", confidence=1.0, confidence_component=1.0, target_code="2")
        failures = check([mapped], [mapped], {}, self.cfg, {"2"}, False)
        self.assertTrue(any("assertion_4" in f for f in failures))

    def test_invalid_gate_writes_nothing(self):
        from unittest import mock

        out = self.output / "should_not_exist"
        with mock.patch("mapper.runner.check", return_value=["assertion_3: forced failure"]):
            summary = execute(
                self.cfg,
                self.client,
                inputs_dir=self.inputs,
                overlay_path=self.overlay,
                escalations_path=self.escalations,
                output_root=out,
            )
        self.assertEqual(summary["status"], "invalid")
        self.assertEqual(summary["failures"], ["assertion_3: forced failure"])
        self.assertFalse(out.exists())


class CliSpec(unittest.TestCase):
    def test_cli_runs_keyless_with_degraded_note(self):
        import os
        import tempfile
        from unittest import mock

        from mapper import cli

        with tempfile.TemporaryDirectory() as tmp:
            blank = {"ANTHROPIC_API_KEY": "", "LLM_BASE_URL": "", "LLM_API_KEY": "", "LLM_MODEL": ""}
            with mock.patch.dict(os.environ, blank):
                code = cli.main(
                    [
                        "run",
                        "--inputs",
                        str(INPUTS),
                        "--overlay",
                        f"{tmp}/coa_overrides.json",
                        "--escalations",
                        f"{tmp}/escalations.json",
                        "--output-dir",
                        f"{tmp}/output",
                    ]
                )
        self.assertEqual(code, 0)

    def test_create_client_none_without_key(self):
        import os
        from unittest import mock

        from mapper.llm import create_client

        blank = {"ANTHROPIC_API_KEY": "", "LLM_BASE_URL": "", "LLM_API_KEY": "", "LLM_MODEL": ""}
        with mock.patch.dict(os.environ, blank):
            self.assertIsNone(create_client())

    def test_create_client_openai_compatible(self):
        import os
        from unittest import mock

        from mapper.llm import OpenAICompatibleClient, create_client

        env = {
            "ANTHROPIC_API_KEY": "",
            "LLM_BASE_URL": "https://api.groq.com/openai/v1/",
            "LLM_API_KEY": "gsk_test",
            "LLM_MODEL": "llama-3.3-70b-versatile",
        }
        with mock.patch.dict(os.environ, env):
            client = create_client()
        self.assertIsInstance(client, OpenAICompatibleClient)
        self.assertEqual(client.base_url, "https://api.groq.com/openai/v1")
        self.assertEqual(client.model, "llama-3.3-70b-versatile")

    def test_create_client_prefers_anthropic(self):
        import os
        from unittest import mock

        from mapper.llm import AnthropicClient, create_client

        env = {"ANTHROPIC_API_KEY": "sk-ant-test", "LLM_BASE_URL": "https://x", "LLM_API_KEY": "y", "LLM_MODEL": "z"}
        with mock.patch.dict(os.environ, env):
            client = create_client()
        self.assertIsInstance(client, AnthropicClient)


if __name__ == "__main__":
    unittest.main()
