import unittest
from decimal import Decimal

from demo_serving import effective_context, forecast_tables, presentation, spark_serving_path, sql_read


class DemoServingTests(unittest.TestCase):
    def history(self):
        return [{"Period": f"2026-{month:02d}", "Status": "Closed", "BilledCost": Decimal(month * 100)} for month in range(1, 10)]

    def test_backtest_never_trains_on_target_or_later_periods(self):
        original = forecast_tables(self.history())
        changed = self.history()
        changed[-1]["BilledCost"] = Decimal("99999")
        revised = forecast_tables(changed)
        self.assertEqual(original[1][0]["Predicted"], Decimal("500.0000"))
        self.assertEqual(original[1][0]["Predicted"], revised[1][0]["Predicted"])
        self.assertEqual(original[1][-1]["Predicted"], revised[1][-1]["Predicted"])
        self.assertNotEqual(original[0][0]["Baseline"], revised[0][0]["Baseline"])

    def test_horizon_rolls_year_and_retains_naive_baseline(self):
        forecast, tests, metrics = forecast_tables(self.history())
        self.assertEqual([row["Month"] for row in forecast], ["2026-10", "2026-11", "2026-12"])
        self.assertEqual(tests[0]["Naive"], Decimal("600.0000"))
        self.assertEqual(metrics["MeanAbsoluteError"], "200.0000")
        self.assertIn("not a calibrated", forecast[0]["RangeBasis"])

    def test_precision_is_explicit_and_signed(self):
        self.assertEqual(presentation("1.23445"), Decimal("1.2344"))
        self.assertEqual(presentation("-0.00016"), Decimal("-0.0002"))

    def test_gaps_and_provisional_history_are_blocked(self):
        for history in (self.history()[1:3] + self.history()[4:], [{**row, "Status": "Provisional"} for row in self.history()]):
            with self.assertRaises(ValueError):
                forecast_tables(history)

    def test_sql_source_adapter_blocks_writes_before_authentication(self):
        with self.assertRaises(ValueError):
            sql_read("unused", "unused", "DELETE FROM dbo.Example")

    def test_spark_read_uses_lakehouse_relative_path_not_driver_mount(self):
        self.assertEqual(spark_serving_path("/lakehouse/default/Files/demo-serving/version/verified/action_mode.parquet"),
                         "Files/demo-serving/version/verified/action_mode.parquet")
        with self.assertRaises(ValueError):
            spark_serving_path("/tmp/action_mode.parquet")

    def test_assignment_is_effective_dated_and_fixture_gate_is_respected(self):
        source = {"candidates": [{"ResourceId": "/test", "ResourceKey": "resource", "TenantId": "tenant"}],
                  "configuration": [],
                  "assignments": [{"TenantId": "tenant", "ResourceKey": "resource", "ContextOptionId": "context", "EffectiveFrom": "2026-06-01", "AssignmentId": 1, "IsDemoFixture": True}],
                  "options": [{"TenantId": "tenant", "ContextOptionId": "context", "BusinessUnit": "Finance", "Initiative": "Demo", "Project": "Demo", "Tool": "Demo"}]}
        self.assertIsNone(effective_context("/TEST", "2026-06-30", source))
        source["configuration"] = [{"TenantId": "tenant", "DemoFixturesEnabled": True}]
        self.assertIsNone(effective_context("/TEST", "2026-05-31", source))
        self.assertEqual(effective_context("/TEST", "2026-06-01", source)[0], "Finance")


if __name__ == "__main__":
    unittest.main()