import unittest
import json
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from databricks_medallion import aggregate_queries, aggregate_usage, normalize, prepare, query_sql
from finops_foundation import digest, immutable_json


class DatabricksMedallionTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {"customerId": "demo", "sourceWorkspaceId": "123", "startDate": "2026-05-01", "endDateExclusive": "2026-07-01", "observedAtUtc": "2026-10-20T00:00:00Z"}
        self.source = {"workspace_id": "123", "record_id": "one", "usage_date": "2026-05-04", "usage_quantity": "1.123456789012345678", "usage_unit": "DBU", "record_type": "ORIGINAL", "sku_name": "SQL", "billing_origin_product": "SQL", "warehouse_id": "warehouse"}

    def test_exact_signed_corrections_are_conserved(self):
        records = [self.source, {**self.source, "record_id": "two", "record_type": "RETRACTION", "usage_quantity": "-1.123456789012345678"}, {**self.source, "record_id": "three", "record_type": "RESTATEMENT", "usage_quantity": "0.123456789012345678"}]
        gold = aggregate_usage(normalize(records, self.manifest, "usage"))
        self.assertEqual(gold[0]["usage_quantity"], Decimal("0.123456789012345678"))
        self.assertEqual(gold[0]["usage_records"], 3)

    def test_source_keys_and_scope_are_blocking(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            normalize([self.source, self.source], self.manifest, "usage")
        with self.assertRaisesRegex(ValueError, "scope"):
            normalize([{**self.source, "workspace_id": "other"}], self.manifest, "usage")

    def test_missing_attribution_is_explicit(self):
        result = normalize([{**self.source, "warehouse_id": None}], self.manifest, "usage")
        self.assertEqual(result[0]["attribution_type"], "Platform/Unallocated")

    def test_query_projection_excludes_sensitive_text(self):
        sql, order = query_sql(self.manifest, "queries")
        self.assertNotIn("statement_text", sql)
        self.assertNotIn("executed_by", sql)
        self.assertIn("workspace_id = '123'", sql)
        self.assertEqual(order, "statement_id")

    def test_query_scope_rejects_sql_injection(self):
        config = {**self.manifest, "sourceWorkspaceId": "123' OR 1=1 --"}
        with self.assertRaisesRegex(ValueError, "Invalid source scope"):
            query_sql(config, "queries")

    def test_window_and_units_are_blocking(self):
        for updates in ({"usage_date": "2026-07-01"}, {"usage_unit": "TOKEN"}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                normalize([{**self.source, **updates}], self.manifest, "usage")

    def test_queries_do_not_multiply_usage_and_unknown_metrics_remain_unknown(self):
        rows = [{"customer_id": "demo", "workspace_id": "123", "start_time": "2026-05-04T00:00:00Z", "warehouse_id": "warehouse", "execution_status": "FINISHED", "execution_duration_ms": 50, "total_duration_ms": 100, "read_bytes": None}] * 3
        gold = aggregate_queries(rows)
        self.assertEqual(gold[0]["statement_count"], 3)
        self.assertEqual(gold[0]["execution_duration_ms"], 150)
        self.assertIsNone(gold[0]["read_bytes"])
        self.assertNotIn("usage_quantity", gold[0])

    def test_complete_snapshot_prepares_and_replays_identically(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            immutable_json(root / "usage.json", [{"payload": json.dumps(self.source)}])
            datasets = {name: {"source": name, "rows": 0, "parts": []} for name in ("usage", "queries", "warehouses")}
            datasets["usage"].update(rows=1, parts=[{"path": "usage.json", "sha256": digest(root / "usage.json"), "rows": 1}])
            immutable_json(root / "manifest.json", {**self.manifest, "datasets": datasets})
            original = prepare(root)
            self.assertEqual(original, prepare(root))
            self.assertEqual(original["counts"], {"usage": 1, "queries": 0, "warehouses": 0})
            self.assertEqual(original["exactNetDBUs"], self.source["usage_quantity"])


if __name__ == "__main__":
    unittest.main()
