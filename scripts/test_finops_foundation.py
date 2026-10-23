import csv
import json
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pyarrow.parquet as parquet

from finops_foundation import build, digest, number, promote, schema_digest
from prepare_foundation_bundle import prepare
from run_foundation_fabric import validate_and_stage


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.root = self.directory / "foundation"
        self.source = self.directory / "charges.csv"
        self.fields = ["SubscriptionId", "billingCurrency", "billingPeriodStartDate", "date", "costInBillingCurrency", "ResourceId", "consumedService", "chargeType"]
        self.fields += [f"unused{index}" for index in range(65 - len(self.fields))]
        self.selection = self.directory / "selection.json"
        self.write_source(["1.1234567890123456789012345678", "-0.1"])

    def write_source(self, amounts):
        with self.source.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=self.fields)
            writer.writeheader()
            for amount in amounts:
                writer.writerow({"SubscriptionId": "subscription", "billingCurrency": "USD", "billingPeriodStartDate": "2025-12-01", "date": "2025-12-01",
                                 "costInBillingCurrency": amount, "ResourceId": "/resource", "consumedService": "Compute", "chargeType": "Usage"})
        self.config = {"customerId": "demo", "subscriptionId": "subscription", "currency": "USD", "snapshots": [{
            "period": "2025-12", "adapter": "actualcost-2021-10-01", "status": "Closed", "observedAtUtc": "2026-10-19T00:00:00Z", "runId": digest(self.source),
            "schemaSha256": schema_digest(self.source, "actualcost-2021-10-01"), "partCount": 1, "rows": len(amounts), "nativeCost": "1.0234567890123456789012345678",
            "parts": [{"path": self.source.name, "sha256": digest(self.source), "bytes": self.source.stat().st_size, "rows": len(amounts)}]}]}
        self.save_selection()

    def save_selection(self):
        self.selection.write_text(json.dumps(self.config), encoding="utf-8")

    def test_exact_decimal_and_unknown_effective_cost(self):
        result = build(self.selection, self.root)
        self.assertEqual(result["exactBilledCost"], "1.0234567890123456789012345678")
        rows = parquet.read_table(self.root / "versions" / result["versionId"] / "silver.parquet").to_pylist()
        self.assertEqual(rows[0]["BilledCost"], Decimal("1.1234567890123456789012345678"))
        self.assertTrue(all(row["EffectiveCost"] is None for row in rows))

    def test_replay_is_idempotent(self):
        original = build(self.selection, self.root)
        ledger_count = len(list((self.root / "ledger").glob("*.json")))
        replay = build(self.selection, self.root)
        self.assertEqual(original["versionId"], replay["versionId"])
        self.assertEqual(ledger_count, len(list((self.root / "ledger").glob("*.json"))))
        self.assertFalse((self.root / "published.json").exists())

    def test_missing_legacy_period_requires_matching_charge_date(self):
        with self.source.open(newline="", encoding="utf-8") as source:
            records = list(csv.DictReader(source))
        for charge_date in ("2025-12-01", "2026-01-01"):
            for record in records:
                record.update(billingPeriodStartDate="", date=charge_date)
            with self.source.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(output, fieldnames=self.fields)
                writer.writeheader()
                writer.writerows(records)
            part = self.config["snapshots"][0]["parts"][0]
            part.update(sha256=digest(self.source), bytes=self.source.stat().st_size)
            self.save_selection()
            if charge_date == "2025-12-01":
                result = build(self.selection, self.root)
                rows = parquet.read_table(self.root / "versions" / result["versionId"] / "silver.parquet").to_pylist()
                self.assertEqual(rows[0]["PeriodBasis"], "SelectedManifestAndChargeDate")
            else:
                with self.assertRaisesRegex(ValueError, "Charge date conflicts"):
                    build(self.selection, self.root)

    def test_duplicate_period_is_blocked(self):
        self.config["snapshots"].append(self.config["snapshots"][0].copy())
        self.save_selection()
        with self.assertRaisesRegex(ValueError, "one complete snapshot"):
            build(self.selection, self.root)

    def test_interrupted_candidate_can_replay(self):
        with patch("finops_foundation.write_table", side_effect=OSError("Interrupted write")):
            with self.assertRaises(OSError):
                build(self.selection, self.root)
        self.assertFalse((self.root / "published.json").exists())
        result = build(self.selection, self.root)
        self.assertEqual(result["rows"], 2)
        self.assertEqual(len(list((self.root / "versions").iterdir())), 1)
        self.assertEqual(len(list((self.root / "staging").iterdir())), 1)

    def test_failed_candidate_keeps_prior_publication(self):
        original = build(self.selection, self.root)
        promote(self.root, original["versionId"], "operator", "Reviewed reconciliation", "none")
        pointer = (self.root / "published.json").read_bytes()
        for field, value in [("nativeCost", "100"), ("schemaSha256", "bad"), ("rows", 999), ("partCount", 2)]:
            with self.subTest(field=field):
                prior = self.config["snapshots"][0][field]
                self.config["snapshots"][0][field] = value
                self.save_selection()
                with self.assertRaises(ValueError):
                    build(self.selection, self.root)
                self.assertEqual(pointer, (self.root / "published.json").read_bytes())
                self.config["snapshots"][0][field] = prior

    def test_corrupt_source_is_quarantined(self):
        self.source.write_text("modified", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash or byte"):
            build(self.selection, self.root)
        records = [json.loads(path.read_text()) for path in (self.root / "ledger").glob("*.json")]
        self.assertEqual(records[-1]["status"], "Quarantined")

    def test_scope_and_currency_are_blocking(self):
        for field, value in [("subscriptionId", "different"), ("currency", "EUR")]:
            with self.subTest(field=field):
                prior = self.config[field]
                self.config[field] = value
                self.save_selection()
                with self.assertRaisesRegex(ValueError, "scope or currency"):
                    build(self.selection, self.root)
                self.config[field] = prior

    def test_full_snapshot_replaces_partial_not_additive(self):
        self.write_source(["1.0234567890123456789012345678"])
        partial = build(self.selection, self.root)
        self.write_source(["1.1234567890123456789012345678", "-0.1"])
        complete = build(self.selection, self.root)
        self.assertNotEqual(partial["versionId"], complete["versionId"])
        self.assertEqual(complete["rows"], 2)
        self.assertEqual(complete["exactBilledCost"], "1.0234567890123456789012345678")

    def test_identical_charges_are_preserved(self):
        self.write_source(["0.5", "0.5"])
        self.config["snapshots"][0]["nativeCost"] = "1"
        self.save_selection()
        self.assertEqual(build(self.selection, self.root)["rows"], 2)

    def test_promotion_checks_current_and_integrity(self):
        result = build(self.selection, self.root)
        with self.assertRaisesRegex(ValueError, "changed"):
            promote(self.root, result["versionId"], "operator", "Reviewed reconciliation", "stale")
        artifact = self.root / "versions" / result["versionId"] / "validation.json"
        artifact.write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "artifact hash"):
            promote(self.root, result["versionId"], "operator", "Reviewed reconciliation", "none")

    def test_float_and_nonfinite_rejected(self):
        for value in (0.1, "NaN", "Infinity"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                number(value)

    def test_bundle_preserves_identity_and_is_repeatable(self):
        candidate = build(self.selection, self.root)
        original = prepare(self.selection, self.root, self.directory / "deployment")
        replay = prepare(self.selection, self.root, self.directory / "deployment")
        self.assertEqual(original, replay)
        self.assertEqual(original["expectedVersionId"], candidate["versionId"])
        self.assertEqual(original["expectedExactBilledCost"], candidate["exactBilledCost"])
        self.assertIn("finops_foundation.py", original["files"])

    def test_bundle_rejects_changed_recovery_source(self):
        build(self.selection, self.root)
        self.source.write_text("changed after validation", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Bundle source hash"):
            prepare(self.selection, self.root, self.directory / "deployment")

    def test_cloud_adapter_rebuilds_then_replays_without_publication(self):
        build(self.selection, self.root)
        receipt = prepare(self.selection, self.root, self.directory / "deployment")
        bundle = self.directory / "deployment" / receipt["bundleFile"]
        target = self.directory / "cloud"
        original = validate_and_stage(bundle, receipt["bundleSha256"], receipt["coreSha256"], target)
        event_count = len(list((target / "ledger").glob("*.json")))
        replay = validate_and_stage(bundle, receipt["bundleSha256"], receipt["coreSha256"], target)
        self.assertEqual(original["versionId"], replay["versionId"])
        self.assertEqual(replay["status"], "Validated replay")
        self.assertEqual(len(list((target / "ledger").glob("*.json"))), event_count)
        self.assertFalse(replay["published"])

    def test_cloud_adapter_rejects_unapproved_bundle(self):
        build(self.selection, self.root)
        receipt = prepare(self.selection, self.root, self.directory / "deployment")
        bundle = self.directory / "deployment" / receipt["bundleFile"]
        with self.assertRaisesRegex(ValueError, "Bundle hash mismatch"):
            validate_and_stage(bundle, "unapproved", receipt["coreSha256"], self.directory / "cloud")


if __name__ == "__main__":
    unittest.main()