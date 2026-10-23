"""Unit and integration tests for `finops_config`, `deploy_customer_dev`, and synthetic sample mode."""

from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from demo_serving import build_serving
from deploy_customer_dev import rebind_semantic_model_tmdl, run_customer_deployment
from finops_config import (
    DEFAULT_SUBSCRIPTION_ID,
    PROJECT_ROOT,
    load_customer_config,
    normalize_resource_id,
    resource_relative_key,
    safe_extract_path,
    validate_build_suffix,
    validate_https_url,
    validate_sha256,
    validate_uuid,
)


class CustomerDeploymentTests(unittest.TestCase):
    """Verify security validators, config loading, synthetic sample builds, and TMDL/PBIR rebinding."""

    def test_uuid_and_sha256_validators_reject_malformed_inputs(self) -> None:
        valid_id = "00000000-0000-0000-0000-000000000001"
        self.assertEqual(validate_uuid(valid_id), valid_id)
        for bad_uuid in ("not-a-uuid", "00000000-0000-0000-0000-00000000000G", "'; DROP TABLE --"):
            with self.subTest(bad_uuid=bad_uuid), self.assertRaises(ValueError):
                validate_uuid(bad_uuid)

        valid_sha = "a" * 64
        self.assertEqual(validate_sha256(valid_sha), valid_sha)
        for bad_sha in ("abc", "z" * 64, "a" * 63):
            with self.subTest(bad_sha=bad_sha), self.assertRaises(ValueError):
                validate_sha256(bad_sha)

        self.assertEqual(validate_build_suffix("75b4187f7d3f"), "75b4187f7d3f")
        with self.assertRaises(ValueError):
            validate_build_suffix("invalid_suffix!")

    def test_ssrf_url_validator_enforces_https_and_host_allowlist(self) -> None:
        good_url = "https://api.fabric.microsoft.com/v1/workspaces"
        self.assertEqual(validate_https_url(good_url), good_url)
        for bad_url in (
            "http://api.fabric.microsoft.com/v1/workspaces",
            "file:///etc/passwd",
            "https://evil.example.com/steal-token",
            "https://user:pass@api.fabric.microsoft.com/v1",
        ):
            with self.subTest(bad_url=bad_url), self.assertRaises(ValueError):
                validate_https_url(bad_url)

    def test_zipslip_guard_rejects_traversal_and_subdirectories(self) -> None:
        with TemporaryDirectory() as tmp:
            dest = Path(tmp)
            safe = safe_extract_path(dest, "financial.parquet")
            self.assertEqual(safe.parent, dest.resolve())
            for unsafe_name in ("../secret.txt", "..\\secret.txt", "nested/file.parquet", "/abs/path.parquet"):
                with self.subTest(unsafe_name=unsafe_name), self.assertRaises(ValueError):
                    safe_extract_path(dest, unsafe_name)

    def test_resource_id_normalization_is_subscription_agnostic(self) -> None:
        raw = "/subscriptions/11111111-2222-3333-4444-555555555555/resourceGroups/RG-Fabric/providers/Microsoft.Fabric/capacities/cap1"
        normalized = normalize_resource_id(raw, DEFAULT_SUBSCRIPTION_ID)
        self.assertTrue(normalized.startswith(f"/subscriptions/{DEFAULT_SUBSCRIPTION_ID}/"))
        self.assertEqual(
            resource_relative_key(raw),
            "/resourcegroups/rg-fabric/providers/microsoft.fabric/capacities/cap1",
        )

    def test_synthetic_sample_serving_build_produces_all_30_tables(self) -> None:
        with TemporaryDirectory() as tmp:
            result = build_serving(Path(tmp) / "sample-build", sample=True)
            self.assertEqual(result["tables"], 30)
            self.assertTrue(Path(result["bundleFile"]).exists())
            manifest = json.loads((Path(tmp) / "sample-build" / "serving-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["tables"]), 30)
            for required_table in (
                "Financial",
                "Reconciliation",
                "Databricks Usage",
                "Unity Catalog Inventory",
                "Fabric Estate Inventory",
                "Daily RG Anomalies",
                "Anomalies & Insights",
                "Foundry AI Usage",
            ):
                self.assertIn(required_table, manifest["tables"])
                self.assertGreater(manifest["tables"][required_table]["rows"], 0)

    def test_tmdl_rebinding_updates_onelake_url_and_table_suffixes(self) -> None:
        with TemporaryDirectory() as tmp:
            temp_model = Path(tmp) / "ZavaFinOpsDemo.SemanticModel"
            shutil.copytree(PROJECT_ROOT / "cloud-dev" / "ZavaFinOpsDemo.SemanticModel", temp_model)
            custom_ws = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            custom_lh = "11111111-2222-3333-4444-555555555555"
            custom_suffix = "abcdef012345"

            res = rebind_semantic_model_tmdl(
                workspace_id=custom_ws,
                lakehouse_id=custom_lh,
                build_suffix=custom_suffix,
                model_root=temp_model,
            )
            self.assertEqual(res["servingSuffix"], custom_suffix)
            self.assertEqual(res["tmdlTablesUpdated"], 30)
            expr_content = (temp_model / "definition" / "expressions.tmdl").read_text(encoding="utf-8")
            self.assertIn(f"https://onelake.dfs.fabric.microsoft.com/{custom_ws}/{custom_lh}", expr_content)

    def test_dry_run_customer_deployment_preserves_template_defaults(self) -> None:
        cfg = load_customer_config(prefer_local=False)
        self.assertEqual(cfg["subscriptionId"], DEFAULT_SUBSCRIPTION_ID)
        summary = run_customer_deployment(config_path=None, mode="sample", dry_run=True)
        self.assertTrue(summary["dryRun"])
        self.assertEqual(summary["servingBuild"]["tables"], 30)
        self.assertEqual(summary["pbirReport"]["pages"], 8)
        # Restore canonical v8 governed suffix (75b4187f7d3f) on repo TMDL after test
        rebind_semantic_model_tmdl(
            workspace_id="00000000-0000-0000-0000-000000000003",
            lakehouse_id="00000000-0000-0000-0000-000000000005",
            build_suffix="75b4187f7d3f",
        )


if __name__ == "__main__":
    unittest.main()
