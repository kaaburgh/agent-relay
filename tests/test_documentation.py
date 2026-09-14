from __future__ import annotations

import importlib
import re
import unittest
from pathlib import Path

from agent_relay.config import load_global_config, load_task_spec


ROOT = Path(__file__).resolve().parents[1]


class DocumentationAcceptanceTests(unittest.TestCase):
    def test_required_documentation_set_exists_and_readme_is_current(self) -> None:
        required = [
            ROOT / "README.md",
            ROOT / "docs" / "architecture.md",
            ROOT / "docs" / "state-machine.md",
            ROOT / "docs" / "providers.md",
            ROOT / "docs" / "simulation.md",
            ROOT / "docs" / "recovery.md",
            ROOT / "docs" / "shadps4-example.md",
        ]
        for path in required:
            self.assertTrue(path.is_file(), f"required documentation missing: {path.relative_to(ROOT)}")
            self.assertGreater(len(path.read_text(encoding="utf-8").strip()), 200)

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("## Five-minute simulation quick start", readme)
        self.assertIn("run --simulation", readme)
        self.assertNotIn("Implementation of the orchestrator itself has not started", readme)
        self.assertIn("shared filesystem", readme)

    def test_readme_local_markdown_links_resolve(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", readme)
        local_links = [link for link in links if "://" not in link and not link.startswith("#")]
        self.assertGreater(len(local_links), 5)
        for link in local_links:
            target = (ROOT / link.split("#", 1)[0]).resolve()
            self.assertTrue(target.exists(), f"README local link does not resolve: {link}")

    def test_readme_quick_start_targets_are_real_test_methods(self) -> None:
        targets = [
            (
                "tests.test_orchestrator_happy_path",
                "HappyPathIntegrationTests",
                "test_writer_validator_independent_reviewer_reaches_done_with_exact_provenance",
            ),
            (
                "tests.test_orchestrator_rework",
                "ReworkIntegrationTests",
                "test_request_changes_flows_to_fresh_rework_generation_and_fresh_review",
            ),
            (
                "tests.test_writer_recovery",
                "WriterRecoveryTests",
                "test_separate_worker_finishes_after_store_closes_and_new_store_recovers_once",
            ),
        ]
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for module_name, class_name, method_name in targets:
            module = importlib.import_module(module_name)
            test_class = getattr(module, class_name)
            self.assertTrue(callable(getattr(test_class, method_name)))
            dotted = f"{module_name}.{class_name}.{method_name}"
            self.assertIn(dotted, readme)

    def test_example_configuration_is_credential_free_and_schema_valid(self) -> None:
        simulated = load_global_config(ROOT / "examples" / "config.simulated.yaml")
        self.assertEqual(simulated.writer.provider, "simulated")
        self.assertEqual(simulated.reviewer.provider, "simulated")
        self.assertTrue(simulated.runners["sim"].options.get("simulated_validator"))
        self.assertEqual(simulated.resources["bloodborne-runtime"].capacity, 1)

        real = load_global_config(ROOT / "examples" / "config.real.example.yaml")
        self.assertEqual(real.writer.provider, "codex")
        self.assertEqual(real.reviewer.provider, "claude")
        self.assertEqual(real.runners["gpu-node"].kind, "ssh")
        self.assertEqual(real.resources["bloodborne-runtime"].capacity, 1)

        for path in (ROOT / "examples").glob("*.yaml"):
            lower = path.read_text(encoding="utf-8").lower()
            self.assertNotIn("sk-", lower)
            self.assertNotIn("api_key:", lower)
            self.assertNotIn("password:", lower)
            self.assertNotIn("private_key", lower)

    def test_example_tasks_are_schema_valid_and_document_runtime_boundaries(self) -> None:
        simulation = load_task_spec(ROOT / "examples" / "task.simulated.example.yaml")
        self.assertEqual(len(simulation.validation), 1)
        self.assertEqual(simulation.validation[0].runner, "sim")

        shad = load_task_spec(ROOT / "examples" / "task.shadps4.example.yaml")
        self.assertIn("bloodborne-runtime", shad.runtime_resources)
        self.assertIn("bloodborne-runtime", shad.validation[0].resources)
        self.assertEqual(shad.validation[0].runner, "gpu-node")
        self.assertIn("{run_id}", shad.validation[0].argv)
        self.assertIn("{requested_cycles}", shad.validation[0].argv)
        self.assertIn("{evidence_dir}", shad.validation[0].argv)

        shad_doc = (ROOT / "docs" / "shadps4-example.md").read_text(encoding="utf-8").lower()
        providers_doc = (ROOT / "docs" / "providers.md").read_text(encoding="utf-8").lower()
        for text in (shad_doc, providers_doc):
            self.assertIn("shared", text)
            self.assertIn("artifact", text)
            self.assertIn("ssh", text)
        self.assertIn("does **not** copy evidence back", (ROOT / "docs" / "shadps4-example.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
